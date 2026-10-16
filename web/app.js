const token = document.querySelector('meta[name="session-token"]').content;
const $ = id => document.getElementById(id);
let catalog = {games: [], providers: []};
let latest = null;
let clockOffset = 0;
let choice = {provider: null, shadows: [], policy: false};
let lastRowsSignature = '';
let lastCallsSignature = '';
let lastViewSignature = '';
let lastConnectedKey = '';
let lastFrameAt = null;
let frameUrl = null;
const openCalls = new Set();
const loadedViews = new Set();
let polling = false;

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}

// Game views: games/<id>/view.js calls GameViews.register(id, render) -----------

const viewHelpers = {
  esc,
  // A labelled bar; value/max decide the fill. kind: good | warn | bad | '' (accent).
  bar(label, value, max, kind = '') {
    const ratio = max ? Math.max(0, Math.min(1, value / max)) : 0;
    const auto = kind || (ratio <= 0.25 ? 'bad' : ratio <= 0.5 ? 'warn' : 'good');
    return `<div class="gv-bar"><span>${esc(label)}</span><div class="gv-track-bar"><div class="gv-fill ${auto}" data-w="${ratio * 100}"></div></div><b>${esc(value)}${max ? ' / ' + esc(max) : ''}</b></div>`;
  },
  // A row of tiles: [{cls: 'player enemy exit cover hazard goal dim', text: '@', title: '...'}].
  track(cells, label = '') {
    return `<div class="gv-row">${label ? `<span class="gv-row-label">${esc(label)}</span>` : ''}<div class="gv-tiles">${cells.map(c =>
      `<div class="gv-tile ${esc(c.cls || '')}" title="${esc(c.title || '')}">${esc(c.text || '')}</div>`).join('')}</div></div>`;
  },
  chips(items) {
    return `<div class="gv-chips">${items.filter(Boolean).map(i => typeof i === 'string' ? `<span class="gv-chip">${esc(i)}</span>` :
      `<span class="gv-chip ${esc(i.cls || '')}">${esc(i.text)}</span>`).join('')}</div>`;
  },
  stat(label, value, cls = '') { return `<div class="gv-stat ${esc(cls)}"><small>${esc(label)}</small><b>${esc(value)}</b></div>`; },
  stats(items) { return `<div class="gv-stats">${items.join('')}</div>`; },
};
window.GameViews = {
  views: {},
  register(id, render) { this.views[id] = render; lastViewSignature = ''; },
};

function genericView(state) {
  if (!state || typeof state !== 'object') return `<div class="state-box">${esc(state)}</div>`;
  return `<table class="gv-table">${Object.entries(state).map(([k, v]) =>
    `<tr><th>${esc(k)}</th><td>${esc(typeof v === 'object' ? JSON.stringify(v) : v)}</td></tr>`).join('')}</table>`;
}

function loadView(game) {
  if (!game?.view || loadedViews.has(game.id)) return;
  loadedViews.add(game.id);
  const script = document.createElement('script');
  script.src = `/games/${encodeURIComponent(game.id)}/view.js`;
  document.head.appendChild(script);
}

function applyWidths(root) {
  root.querySelectorAll('[data-w]').forEach(el => { el.style.width = el.dataset.w + '%'; });
}

// API ----------------------------------------------------------------------------

async function api(path, data) {
  const options = {headers: {'X-Session-Token': token}};
  if (data !== undefined) {
    options.method = 'POST';
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(data);
  }
  const response = await fetch(path, options);
  if ((path === '/api/export' || path === '/api/frame') && response.ok) return response.blob();
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '요청에 실패했습니다.');
  return result;
}

async function command(path, data) {
  try {
    await api(path, data);
    if (data && data.key) $('api-key').value = '';
    await poll();
  } catch (error) {
    showMessage(error.message, true);
  }
}

function showMessage(message, error = false) {
  $('status-message').textContent = message;
  document.querySelector('.status-light').classList.toggle('off', error);
}

const gameById = id => catalog.games.find(g => g.id === id);
const selectedGame = () => gameById($('game').value);
const selectedInterface = () => (selectedGame()?.interfaces || []).find(i => i.name === $('interface').value);
const connectedGame = () => latest?.connection?.game ? gameById(latest.connection.game) : null;
const isRealtime = game => game?.timing === 'realtime';

function ago(ms) {
  if (ms === null || ms === undefined) return '—';
  return ms < 1000 ? `${ms} ms 전` : ms < 60000 ? `${(ms / 1000).toFixed(1)}초 전` : `${Math.floor(ms / 60000)}분 전`;
}
function duration(seconds) {
  if (seconds < 60) return `${Math.floor(seconds)}초`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}분 ${Math.floor(seconds % 60)}초`;
  return `${Math.floor(seconds / 3600)}시간 ${Math.floor(seconds % 3600 / 60)}분`;
}
const serverNow = () => Date.now() / 1000 - clockOffset;

// Game and interface ---------------------------------------------------------

function fillGames() {
  const select = $('game');
  select.innerHTML = '';
  const groups = {};
  catalog.games.forEach(game => {
    const label = game.type_label || '설정 오류';
    if (!groups[label]) {
      groups[label] = document.createElement('optgroup');
      groups[label].label = label;
      select.appendChild(groups[label]);
    }
    const usable = !game.error && game.interfaces.some(i => !i.error);
    const option = new Option(`${game.id} · ${game.name || game.error}${usable ? '' : ' (연결 불가)'}`, game.id);
    option.disabled = !usable;
    groups[label].appendChild(option);
  });
  const first = catalog.games.find(g => !g.error && g.interfaces.some(i => !i.error));
  if (first) select.value = first.id;
  onGameChange();
}

function onGameChange() {
  const game = selectedGame();
  const select = $('interface');
  select.innerHTML = '';
  (game?.interfaces || []).forEach(item => {
    const option = new Option(`${item.name} (${item.connector || '오류'})${item.name === game.default_interface ? ' · 기본' : ''}`, item.name);
    option.disabled = !!item.error;
    select.appendChild(option);
  });
  if (game?.default_interface) select.value = game.default_interface;
  if (game) {
    choice = {provider: game.providers.live, shadows: [...game.providers.shadow], policy: false};
    $('game-meta').textContent = `${game.type} · ${isRealtime(game) ? `실시간 · 지연 예산 ${game.latency_budget_ms}ms` : `턴제 · 판단 예산 ${game.latency_budget_ms}ms`} · live ${game.providers.live}${game.providers.shadow.map(p => ' +' + p).join('')}`;
    loadView(game);
  }
  renderModelControls(game);
  renderRunHow();
  renderGameKind(game);
}

function renderGameKind(game) {
  $('run').innerHTML = isRealtime(game) ? '▶ &nbsp; 한 틱 실행' : '▶ &nbsp; 한 턴 실행';
  $('stat-latency-label').textContent = game ? `LAST LATENCY · 예산 ${game.latency_budget_ms}ms` : 'LAST LATENCY';
}

function commandText(command) {
  return command.map(part => part === '{python}' ? 'python' : part).join(' ');
}

function renderRunHow() {
  const item = selectedInterface();
  const how = $('run-how');
  const manual = $('run-manual');
  $('kbm-options').hidden = item?.connector !== 'kbm';
  if (!item) { how.textContent = '연결할 인터페이스가 없습니다. 이 게임은 play.py compare로 비교만 할 수 있습니다.'; manual.hidden = true; return; }
  if (item.error) { how.innerHTML = `<span class="bad">${esc(item.error)}</span>`; manual.hidden = true; return; }
  const run = item.run;
  if (run.mode === 'launch') {
    how.innerHTML = `연결을 누르면 콘솔이 게임을 실행하고, 연결을 끊으면 종료합니다.<code>${esc(commandText(run.command))} ${esc(run.socket_arg)} &lt;임시 소켓&gt;</code>` +
      (run.build?.kind === 'binary' && !run.build.exists ? `<small class="bad">실행 파일이 없습니다. ${esc(run.hint || '먼저 빌드하세요.')}</small>` :
       run.hint ? `<small>${esc(run.hint)}</small>` : '');
  } else if (run.mode === 'attach') {
    how.innerHTML = `이미 실행 중인 게임의 소켓에 붙습니다. 콘솔은 게임을 띄우거나 끄지 않습니다.<code>${esc(run.socket)}</code>`;
  } else {
    how.innerHTML = `열려 있는 게임 창에 붙습니다. KbmAgent가 창을 캡처하고 키보드·마우스 입력을 보냅니다.<code>창: ${esc(run.owner)}${run.title_contains ? ` · 제목에 "${esc(run.title_contains)}" 포함` : ''}</code>`;
  }
  const steps = run.manual || (run.mode === 'window' ? `${run.owner} 창을 열고 게임 화면까지 들어간 뒤 연결하세요.` :
                               run.mode === 'attach' ? '게임을 먼저 실행한 뒤 연결하세요.' : '');
  manual.hidden = !steps;
  manual.innerHTML = steps ? `<b>먼저 할 일</b>${esc(steps)}` : '';
  if (latest && latest.connection.status === 'disconnected') updateHealth(latest);
}

// Model controls -------------------------------------------------------------

function renderModelControls(game) {
  const providers = catalog.providers;
  $('provider-buttons').innerHTML = providers.map(p =>
    `<button data-provider="${esc(p)}" class="${p === choice.provider ? 'selected' : ''}">${esc(p)}<span>${p === 'jev' ? 'API' : p === 'laya-mlx' ? 'GPU' : 'LOCAL'}</span></button>`).join('');
  $('shadow-boxes').innerHTML = providers.map(p =>
    `<label class="check ${p === choice.provider ? 'muted' : ''}"><input type="checkbox" data-shadow="${esc(p)}" ${choice.shadows.includes(p) ? 'checked' : ''} ${p === choice.provider ? 'disabled' : ''}><span>${esc(p)}</span></label>`).join('');
  $('policy-field').hidden = !game?.has_policy;
  $('policy').checked = choice.policy;
  $('api-key').closest('label').classList.toggle('dim', choice.provider !== 'jev' && !choice.shadows.includes('jev'));
}

function modelPayload() {
  const key = $('api-key').value.trim();
  return {provider: choice.provider, shadows: choice.shadows.filter(p => p !== choice.provider), policy: choice.policy, ...(key ? {key} : {})};
}

function setupControls() {
  $('game').addEventListener('change', onGameChange);
  $('interface').addEventListener('change', renderRunHow);
  $('connect').addEventListener('click', () => command('/api/connect', {
    game: $('game').value, interface: $('interface').value, dry_run: $('dry-run').checked, agent: $('agent').value}));
  $('disconnect').addEventListener('click', () => command('/api/disconnect', {}));
  $('provider-buttons').addEventListener('click', event => {
    const button = event.target.closest('[data-provider]');
    if (!button) return;
    choice.provider = button.dataset.provider;
    choice.shadows = choice.shadows.filter(p => p !== choice.provider);
    renderModelControls(connectedGame() || selectedGame());
  });
  $('shadow-boxes').addEventListener('change', event => {
    const p = event.target.dataset.shadow;
    if (!p) return;
    choice.shadows = event.target.checked ? [...new Set([...choice.shadows, p])] : choice.shadows.filter(x => x !== p);
    renderModelControls(connectedGame() || selectedGame());
  });
  $('policy').addEventListener('change', event => { choice.policy = event.target.checked; });
  $('api-key').addEventListener('change', event => {
    if (event.target.value.trim()) command('/api/key', {key: event.target.value.trim()});
  });
  $('run').addEventListener('click', () => command('/api/run', {...modelPayload(), apply: true}));
  $('analyze').addEventListener('click', () => command('/api/run', {...modelPayload(), apply: false}));
  $('auto').addEventListener('click', () => command('/api/auto', {...modelPayload(), enabled: !(latest && latest.auto)}));
  $('reset').addEventListener('click', () => command('/api/reset', {scenario: $('scenario').value || null}));
  $('scenario').addEventListener('change', event => command('/api/reset', {scenario: event.target.value || null}));
  $('manual-actions').addEventListener('click', event => {
    const button = event.target.closest('[data-action]');
    if (button) command('/api/manual', {action: button.dataset.action});
  });
  $('calls').addEventListener('toggle', event => {
    const id = event.target.dataset?.call;
    if (id) event.target.open ? openCalls.add(id) : openCalls.delete(id);
  }, true);
  $('export').addEventListener('click', async event => {
    event.preventDefault();
    try {
      const blob = await api('/api/export');
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${latest?.connection?.game || 'game'}-decisions.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { showMessage(error.message, true); }
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && latest && latest.auto) command('/api/auto', {enabled: false});
  });
}

// Connection, health and build -------------------------------------------------

function updateConnection(data) {
  const c = data.connection;
  const connected = c.status === 'connected';
  const text = {disconnected: '연결 안 됨', connecting: `${c.game} 연결 중…`, connected: `${c.game} · ${c.interface} (${c.connector})`, error: `${c.game || ''} 연결 문제`}[c.status];
  $('connection-text').textContent = text;
  document.querySelector('.pulse').classList.toggle('off', !connected);
  document.querySelector('.pulse').classList.toggle('wait', c.status === 'connecting');
  const linked = connected || c.status === 'connecting';
  ['game', 'interface', 'dry-run', 'agent'].forEach(id => { $(id).disabled = linked; });
  $('connect').disabled = linked;
  $('disconnect').disabled = c.status === 'disconnected';

  const key = connected ? `${c.game}/${c.interface}` : '';
  if (key !== lastConnectedKey) {
    lastConnectedKey = key;
    lastViewSignature = '';
    if (connected) {
      $('game').value = c.game;
      onGameChange();
      $('interface').value = c.interface;
      renderRunHow();
      if (data.options.provider) {
        choice = {provider: data.options.provider, shadows: data.options.shadows, policy: data.options.policy};
        renderModelControls(connectedGame());
      }
    }
    const game = connectedGame();
    $('manual-actions').innerHTML = connected && game ? game.options.map(a => `<button data-action="${esc(a)}">${esc(a)}</button>`).join('') : '<span class="meta">연결하면 이 게임의 선택지가 나옵니다</span>';
    const scenarios = c.scenarios || [];
    $('scenario').innerHTML = '<option value="">다음 상황</option>' + scenarios.map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join('');
    $('scenario-field').hidden = !scenarios.length;
    $('reset').hidden = connected && c.connector === 'kbm';
    const kbm = connected && c.connector === 'kbm';
    $('kbm-warning').hidden = !kbm;
    $('kbm-warning').innerHTML = !kbm ? '' : data.options.dry_run
      ? '<b>DRY-RUN</b>화면은 읽지만 키보드·마우스 입력은 보내지 않습니다. 실제로 조작하려면 연결을 끊고 DRY-RUN을 끈 뒤 다시 연결하세요.'
      : '<b>실제 입력</b>게임 창에 키보드·마우스 입력을 보냅니다. 멈추려면 마우스를 화면 왼쪽 위 모서리로 옮기세요.';
  }

  const perms = c.permissions && typeof c.permissions === 'object' && !c.permissions.error
    ? Object.entries(c.permissions).map(([name, ok]) => `${ok ? '✓' : '!'} ${name}`).join(' · ') : '';
  $('permissions').textContent = c.status === 'error' ? c.error : [perms, c.halted ? '입력 멈춤: ' + c.halted : ''].filter(Boolean).join(' · ');
  $('permissions').classList.toggle('bad', c.status === 'error' || !!c.halted);
}

function card(id, state, title, detail) {
  const el = $(id);
  el.className = `health-card ${el.classList.contains('wide') ? 'wide' : ''} ${state}`;
  el.querySelector('strong').innerHTML = title;
  el.querySelector('.health-detail').innerHTML = detail;
}

function buildHtml(build, label = '') {
  if (!build) return ['—', ''];
  const git = build.git ? `git ${esc(build.git.commit)} (${esc(build.git.branch)})${build.git.dirty?.length ? ` · <span class="warn">커밋 안 된 수정: ${esc(build.git.dirty.join(', '))}</span>` : ''}` : '';
  if (build.kind === 'source') {
    return [`소스 실행${label}`, `python -m ${esc(build.module || '?')} · 이 저장소의 코드를 그대로 실행<br>${git}`];
  }
  if (build.exists === false) return [`<span class="bad">실행 파일 없음</span>`, esc(build.path)];
  const stale = build.stale_sources?.length ? `<br><span class="warn">⚠ 빌드 뒤에 바뀐 소스: ${esc(build.stale_sources.join(', '))} — 다시 빌드하세요</span>` : '';
  return [`빌드한 실행 파일${label}`, `<code>${esc(build.path)}</code>빌드 ${esc((build.built_at || '').replace('T', ' '))} · sha256 ${esc(build.sha256 || '—')}${build.size ? ` · ${(build.size / 1048576).toFixed(1)}MB` : ''}${stale}${git ? '<br>저장소 지금: ' + git : ''}`];
}

function updateHealth(data) {
  const c = data.connection;
  const h = data.health || {};
  const r = data.runtime || {};
  const busy = data.phase?.busy;
  if (c.status === 'disconnected' || c.status === 'connecting') {
    const item = selectedInterface();
    const run = item?.run;
    card('card-process', c.status === 'connecting' ? 'wait' : '', c.status === 'connecting' ? '시작하는 중…' : '연결 안 됨',
      run?.mode === 'launch' ? '연결하면 콘솔이 실행합니다' : run?.mode === 'attach' ? '이미 떠 있는 게임에 붙습니다' : run?.mode === 'window' ? '게임 창을 찾습니다' : '');
    card('card-link', '', '—', '연결하면 응답 시간이 표시됩니다');
    const [title, detail] = run?.mode === 'launch' ? buildHtml(run.build, ' · 연결 전 미리보기') :
      run?.mode === 'window' ? buildHtml(run.agent_build && {kind: 'binary', ...run.agent_build}, ' · KbmAgent') : ['—', '이미 떠 있는 게임은 연결 후에 확인합니다'];
    card('card-build', '', title, detail);
    return;
  }
  // Process
  const uptime = h.started_at ? duration(serverNow() - h.started_at) : '';
  if (r.mode === 'attach') {
    card('card-process', c.status === 'error' ? 'bad' : 'good', c.status === 'error' ? '응답 없음' : '외부에서 실행 중',
      `콘솔이 띄운 게임이 아니라 PID를 모릅니다<br><code>${esc(r.socket)}</code>`);
  } else if (h.alive === false) {
    card('card-process', 'bad', `종료됨 (exit ${esc(h.exit_code)})`, `PID ${esc(h.pid)} · ${esc(c.error || '')}`);
  } else if (r.mode === 'window') {
    const wb = r.window_build;
    card('card-process', 'good', `KbmAgent PID ${esc(h.pid)}`, `${esc(r.agent === 'dev' ? '현재 Python' : 'KbmAgent.app')} · ${uptime}<br>` +
      (wb ? `게임 창 PID ${esc(wb.pid)} · <code>${esc(wb.executable || '알 수 없음')}</code>` : '<span class="warn">게임 창을 아직 찾지 못했습니다</span>'));
  } else {
    card('card-process', c.status === 'error' ? 'bad' : 'good', `실행 중 · PID ${esc(h.pid)}`, `가동 ${uptime}<br><code>${esc(commandText(r.command || []))}</code>`);
  }
  // Link
  const age = h.last_ok_ago_ms;
  const silent = age !== null && age > 3000;
  const state = h.failures ? 'bad' : silent && !busy ? 'warn' : 'good';
  const title = h.failures ? `응답 없음 · ${h.failures}회 실패` : age === null ? '—' : `${ago(age)} 응답`;
  card('card-link', state, title,
    `왕복 ${h.rtt_ms ?? '—'} ms · 마지막 요청 ${esc(h.last_op || '—')}` +
    (busy && silent ? '<br>모델이 판단하는 동안은 게임에 요청하지 않습니다' : '') +
    (h.last_error ? `<br><span class="${h.failures ? 'bad' : 'warn'}">${esc(h.last_error)}</span>` : ''));
  // Build
  let [btitle, bdetail] = r.mode === 'window'
    ? (r.window_build?.exists ? buildHtml({kind: 'binary', ...r.window_build}, ' · 게임 창의 앱') : ['게임 빌드 정보 없음', 'kbm은 게임을 띄우지 않으므로, 창을 찾으면 그 프로세스의 실행 파일을 표시합니다'])
    : r.mode === 'attach' ? ['외부에서 실행한 게임', '콘솔이 띄우지 않아 빌드 파일을 모릅니다. 게임이 info에 build를 보내면 아래에 표시됩니다.']
    : buildHtml(r.build);
  if (r.mode === 'window' && r.agent_build) bdetail += `<br>KbmAgent: ${esc(r.agent_build.path)}${r.agent_build.built_at ? ' · 빌드 ' + esc(r.agent_build.built_at.replace('T', ' ')) : ''}`;
  if (r.game_build) bdetail += `<br>게임이 알려 준 빌드: <code>${esc(JSON.stringify(r.game_build))}</code>`;
  const stale = r.build?.stale_sources?.length || r.build?.exists === false;
  card('card-build', stale ? 'warn' : '', btitle, bdetail);
}

// Game view ------------------------------------------------------------------

function pretty(value) {
  if (value === null || value === undefined) return '—';
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}

function updateObserved(data) {
  const o = data.observed;
  const game = connectedGame();
  const holder = $('game-view');
  if (!o || !game) {
    if (lastViewSignature !== 'none') {
      lastViewSignature = 'none';
      holder.innerHTML = `<div class="empty">${data.connection.status === 'connected' ? '상태를 읽는 중입니다.' : '연결하면 게임 상태가 표시됩니다.'}</div>`;
      $('state-text').textContent = '—';
      $('truth').textContent = '—';
      $('view-kind').textContent = '';
    }
    return;
  }
  if (!o.ok) { holder.innerHTML = `<div class="empty bad">관찰 실패: ${esc(o.error)}</div>`; lastViewSignature = ''; return; }
  const signature = JSON.stringify([game.id, o.state, o.done, !!GameViews.views[game.id]]);
  if (signature === lastViewSignature) return;
  lastViewSignature = signature;
  const render = GameViews.views[game.id];
  $('view-kind').textContent = render ? `games/${game.id}/view.js` : 'view.js 없음 · 상태 표';
  let html;
  try {
    html = render ? render(o.state, {...viewHelpers, truth: o.truth, options: game.options, done: o.done}) : genericView(o.state);
  } catch (error) {
    html = `<div class="bad">view.js 오류: ${esc(error.message)}</div>${genericView(o.state)}`;
  }
  holder.innerHTML = (o.done ? '<div class="gv-done">판 끝</div>' : '') + html;
  applyWidths(holder);
  $('state-text').textContent = o.description || pretty(o.state);
  $('truth').textContent = o.truth ? pretty(o.truth) : '이 게임은 정답을 보내지 않습니다.';
}

async function updateFrame(data) {
  const kbm = data.connection.connector === 'kbm' && data.connection.status === 'connected';
  $('frame-box').hidden = !kbm;
  if (!kbm || !data.frame_at || data.frame_at === lastFrameAt) return;
  lastFrameAt = data.frame_at;
  try {
    const blob = await api('/api/frame');
    if (frameUrl) URL.revokeObjectURL(frameUrl);
    frameUrl = URL.createObjectURL(blob);
    $('frame').src = frameUrl;
    $('frame-note').textContent = `KbmAgent가 캡처한 화면 · ${new Date(data.frame_at * 1000).toLocaleTimeString()}`;
  } catch (error) { $('frame-note').textContent = error.message; }
}

// Pipeline and model calls ------------------------------------------------------

function updatePipeline(data) {
  const phase = data.phase || {};
  const steps = ['게임 관찰', '모델 판단', '행동 전송'];
  const active = data.connection.status === 'connected' && phase.busy;
  const elapsed = phase.since ? Math.max(0, serverNow() - phase.since) : 0;
  const running = (data.calls || []).filter(c => !c.finished_at);
  $('pipeline').innerHTML = `<div class="pipe-steps">${steps.map(s => `<span class="pipe-step ${active && phase.name === s ? 'on' : ''}">${esc(s)}</span>`).join('<i>→</i>')}</div>` +
    `<div class="pipe-now">${active ? `지금: <b>${esc(phase.name)}</b> · ${elapsed.toFixed(1)}초째` : data.connection.status === 'connected' ? `대기 중${data.auto ? ' · 자동 플레이' : ''}` : '연결 안 됨'}</div>` +
    running.map(c => `<div class="pipe-call"><span class="role ${esc(c.role)}">${esc(c.role)}</span><b>${esc(c.provider)}</b> ${esc(c.model || '')} · ${esc(c.runtime || '')}<em>${(serverNow() - c.started_at).toFixed(1)}s</em></div>`).join('');
}

function usageText(usage) {
  if (!usage || typeof usage !== 'object') return '';
  return Object.entries(usage).filter(([, v]) => typeof v === 'number').map(([k, v]) => `${k} ${v}`).join(' · ');
}

function callRequestJson(call) {
  const body = JSON.stringify(call.request, null, 2);
  if (call.provider !== 'jev') return `${call.runtime} · Router.predict(state, questions${call.model && call.model !== 'auto' ? `, model="${call.model}"` : ''})\n\n${body}`;
  return `POST ${call.endpoint}\nAuthorization: Bearer ••••••••\nContent-Type: application/json\ntimeout: ${call.timeout_s}s\n\n${body}`;
}

function updateCalls(data) {
  const calls = data.calls || [];
  const game = connectedGame();
  const signature = JSON.stringify(calls.map(c => [c.id, c.finished_at]));
  if (signature === lastCallsSignature) return;
  lastCallsSignature = signature;
  if (!calls.length) { $('calls').innerHTML = '<div class="empty">모델을 실행하면 호출이 이곳에 표시됩니다.</div>'; return; }
  const budget = game?.latency_budget_ms;
  $('calls').innerHTML = calls.map(c => {
    const done = !!c.finished_at;
    const over = done && budget && c.ms > budget;
    const status = !done ? '<span class="warn">진행 중</span>' : c.status === 'ok' ? `→ <b>${esc(c.action)}</b>` : `<span class="bad">${esc(c.error)}</span>`;
    const usage = usageText(c.usage);
    return `<details class="call ${done ? '' : 'running'} ${c.status && c.status !== 'ok' ? 'failed' : ''}" data-call="${c.id}" ${openCalls.has(String(c.id)) ? 'open' : ''}>
      <summary><span class="role ${esc(c.role)}">${esc(c.role)}</span><b>${esc(c.provider)}</b><span class="call-model">${esc(c.model || '')}</span>${status}<em class="${over ? 'bad' : ''}">${done ? `${Math.round(c.ms)} ms${over ? ' · 예산 초과' : ''}` : ''}</em></summary>
      <div class="call-body">
        <div class="call-meta">${esc(c.runtime || '')}${c.endpoint ? ` · <code>${esc(c.endpoint)}</code>` : ''} · 입력 ${esc(c.input_chars)}자${c.routed_model ? ` · 응답 모델 ${esc(c.routed_model)}` : ''}${c.confidence !== undefined && c.confidence !== null ? ` · 신뢰도 ${esc(c.confidence)}` : ''}${usage ? ` · ${esc(usage)}` : ''}</div>
        <div class="call-cols"><div><small>보낸 요청</small><pre>${esc(callRequestJson(c))}</pre></div><div><small>받은 응답</small><pre>${esc(done ? JSON.stringify(c.response ?? {error: c.error}, null, 2) : '기다리는 중…')}</pre></div></div>
      </div></details>`;
  }).join('');
}

// Decisions ------------------------------------------------------------------

function pct(value) { return value === null || value === undefined ? '—' : `${(value * 100).toFixed(1)}%`; }
function num(value, digits = 2) { return typeof value === 'number' ? value.toFixed(digits) : '—'; }

function answerLines(answers, grade, questions) {
  return Object.entries(questions || {}).filter(([, q]) => q.type !== 'choice').map(([qid, q]) => {
    const a = (answers || {})[qid] || {};
    const g = (grade || {})[qid];
    if (q.type === 'score') {
      const top = (q.criteria || []).length - 1;
      const truth = g && !g.status ? ` <em class="${g.exact ? 'good' : 'bad'}">정답 ${esc(g.truth)} · 오차 ${num(g.abs_error)}</em>` : '';
      return `<div class="answer"><span>${esc(qid)}</span><b>${num(a.score)} / ${top}</b>${truth}</div>`;
    }
    const truthLabel = g && !g.status ? (typeof g.truth === 'boolean' ? (g.truth ? '참' : '거짓') : num(g.truth)) : null;
    const truth = truthLabel !== null ? ` <em class="${g.correct ? 'good' : 'bad'}">정답 ${truthLabel} · Brier ${num(g.brier, 3)}</em>` : '';
    return `<div class="answer"><span>${esc(qid)}</span><b>${pct(a.noul)}</b>${truth}</div>`;
  }).join('');
}

function liveBlock(row, game) {
  const d = row.decision;
  if (d.status !== 'ok') return `<div class="model-block"><div class="model-name"><span>${esc(d.provider)}</span></div><div class="decision-event bad">${esc(d.error)}</div></div>`;
  const truthActions = row.grade?.action?.truth || [];
  const over = game.latency_budget_ms && d.model_ms > game.latency_budget_ms;
  const bars = game.options.map(option => {
    const value = Math.max(0, Math.min(1, Number(d.probabilities?.[option] || 0)));
    const winner = d.action === option;
    const sent = row.action === option && row.action !== d.action;
    return `<div class="choice-row ${winner ? 'winner' : ''} ${sent ? 'sent' : ''}"><span>${esc(option)}${truthActions.includes(option) ? ' <i class="good">✓</i>' : ''}</span><div class="choice-track"><div class="choice-fill" data-w="${value * 100}"></div></div><span class="score">${(value * 100).toFixed(0)}%</span></div>`;
  }).join('');
  return `<div class="model-block"><div class="model-name"><span>LIVE · ${esc(d.provider.toUpperCase())}</span><span class="${over ? 'bad' : ''}">${esc(Math.round(d.model_ms))} ms${over ? ' · 예산 초과' : ''}</span></div>${bars}${answerLines(d.answers, row.grade, game.questions)}</div>`;
}

function shadowBlock(s, game) {
  if (s.status !== 'ok') return `<div class="shadow-line"><b>${esc(s.provider)}</b><span class="bad">${esc(s.status === 'timeout' ? '시간 초과 (틱 안에 답하지 못함)' : s.error)}</span></div>`;
  const correct = s.grade?.action?.correct;
  return `<div class="model-block secondary-block"><div class="model-name secondary"><span>SHADOW · ${esc(s.provider.toUpperCase())} → ${esc(s.action)} ${correct === undefined ? '' : correct ? '✓' : '✗'}</span><span>${s.agrees ? 'live와 같음' : 'live와 다름'} · ${esc(Math.round(s.model_ms))} ms</span></div>${answerLines(s.answers, s.grade, game.questions)}</div>`;
}

function rowBadge(row) {
  if (row.analysis_only) return ['ANALYSIS ONLY', '게임 상태 유지', ''];
  if (row.applied) return ['GAME ACTION', '게임에 적용됨', ''];
  if (row.stale) return ['STALE', row.error || '판단 중 상황이 바뀜', 'warn'];
  return ['NOT APPLIED', row.error || '적용 안 됨', 'error'];
}

function updateDecisions(data) {
  const game = connectedGame();
  const signature = JSON.stringify([data.steps, data.rows.length && data.rows[0].step, latest?.connection?.game]);
  if (signature === lastRowsSignature) return;
  lastRowsSignature = signature;
  $('decision-count').textContent = String(data.steps);
  const holder = $('decisions');
  if (!data.rows.length || !game) { holder.innerHTML = '<div class="empty">모델을 실행하면 질문별 판단과 정답이 이곳에 쌓입니다.</div>'; return; }
  holder.innerHTML = data.rows.map(row => {
    const [badge, note, kind] = rowBadge(row);
    const ok = row.decision.status === 'ok';
    const correct = row.grade?.action?.correct;
    const policy = row.policy || {};
    const override = policy.override ? ` · policy ${esc(policy.override.rule)}: ${esc(policy.override.from)} → ${esc(policy.override.to)}` : '';
    const fired = !policy.override && policy.fired?.length ? ` · policy 발동(기록만): ${esc(policy.fired.join(', '))}` : '';
    const title = ok ? `${esc(row.decision.provider.toUpperCase())} → ${esc(row.action)} ${correct === undefined ? '' : correct ? '<i class="good">✓</i>' : '<i class="bad">✗</i>'}` : '모델 호출 실패';
    return `<article class="decision-card ${ok ? 'primary' : 'error'} ${kind}"><div class="decision-head"><span class="decision-turn">${isRealtime(game) ? 'TICK' : 'STEP'} ${String(row.step).padStart(2, '0')}</span><span>판 ${row.episode + 1} · ${badge}</span><span>${esc(Math.round(row.loop_ms))} ms</span></div><div class="decision-summary"><strong>${title}</strong><span>${esc(note)}${override}${fired}</span></div>${liveBlock(row, game)}${(row.shadow || []).map(s => shadowBlock(s, game)).join('')}</article>`;
  }).join('');
  applyWidths(holder);
}

function summaryText(grades) {
  return Object.entries(grades || {}).map(([qid, s]) => {
    if (!s.n) return `${qid} 채점 불가`;
    if (s.type === 'choice') return `${qid} 정답률 ${pct(s.accuracy)}`;
    if (s.type === 'score') return `${qid} 평균오차 ${num(s.mae)} · 단계일치 ${pct(s.exact)}`;
    return `${qid} Brier ${num(s.brier, 3)} · 정답률 ${pct(s.accuracy)}`;
  }).join(' &nbsp;·&nbsp; ');
}

function updateSummary(data) {
  const s = data.summary;
  const game = connectedGame();
  $('stat-steps').textContent = String(data.steps);
  const last = data.rows[0];
  $('stat-action').textContent = last?.action || '—';
  $('stat-latency').textContent = last ? `${Math.round(last.decision.model_ms)} ms` : '—';
  $('stat-latency').classList.toggle('bad', !!(last && game && last.decision.model_ms > game.latency_budget_ms));
  $('stat-accuracy').textContent = pct(s?.live?.grades?.action?.accuracy);
  const box = $('summary');
  const lines = [];
  if (s?.live?.grades && Object.keys(s.live.grades).length) lines.push(`<div><b>live ${esc(s.live.provider)}</b>${summaryText(s.live.grades)}</div>`);
  Object.entries(s?.shadows || {}).forEach(([p, g]) => lines.push(`<div><b>shadow ${esc(p)}</b>${summaryText(g)}</div>`));
  if (s?.policy && Object.keys(s.policy.fired).length) lines.push(`<div><b>policy</b>${Object.entries(s.policy.fired).map(([k, v]) => `${esc(k)} ${v}회`).join(', ')} · 행동 교체 ${s.policy.overrides}회 (정답으로 ${s.policy.helped}, 오답으로 ${s.policy.hurt})</div>`);
  if (s?.outcomes?.length) lines.push(`<div><b>판 결과</b>${s.outcomes.map(esc).join(', ')}</div>`);
  box.hidden = !lines.length;
  box.innerHTML = lines.join('');
  $('session-note').textContent = data.session ? `기록: ${data.session}` : '최근 판단이 위에 표시됩니다';
}

function updateMeta(data) {
  const c = data.connection;
  const connected = c.status === 'connected';
  $('key-status').textContent = data.key_set ? '키가 메모리에 설정됨' : '키가 설정되지 않았습니다';
  $('auto').textContent = data.auto ? '자동 플레이 중지' : '자동 플레이';
  const message = data.error || (c.status === 'connecting' ? '게임에 연결하는 중입니다…' :
    c.status === 'error' ? '연결 문제: ' + c.error :
    !connected ? '게임을 고르고 연결하세요.' :
    data.busy ? (data.auto ? '자동 플레이 중 · Esc로 멈춥니다' : '모델이 판단하는 중입니다…') : '연결됨. 모델을 실행하거나 직접 조작해 보세요.');
  showMessage(message, !!data.error || c.status === 'error' || !connected);
  ['run', 'analyze', 'reset', 'scenario'].forEach(id => { $(id).disabled = !connected || data.busy; });
  $('auto').disabled = !connected || (data.busy && !data.auto);
  document.querySelectorAll('#manual-actions button').forEach(button => { button.disabled = !connected || data.busy; });
  $('events').innerHTML = data.events.map(event => `<div class="event"><time>${esc(event.at)}</time><span>${esc(event.text)}</span></div>`).join('');
}

async function poll() {
  if (polling) return;
  polling = true;
  try {
    const data = await api('/api/status');
    clockOffset = Date.now() / 1000 - data.now;
    latest = data;
    updateConnection(data);
    updateHealth(data);
    updateObserved(data);
    updateFrame(data);
    updatePipeline(data);
    updateCalls(data);
    updateDecisions(data);
    updateSummary(data);
    updateMeta(data);
  } catch (error) { showMessage('WebView 연결 실패: ' + error.message, true); }
  finally { polling = false; }
}

async function start() {
  setupControls();
  try {
    catalog = await api('/api/games');
    fillGames();
  } catch (error) { showMessage('게임 목록을 읽지 못했습니다: ' + error.message, true); }
  await poll();
  setInterval(poll, 500);
}

start();
