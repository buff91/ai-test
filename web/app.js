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
let lesson = 'play';
let playModeChosen = false;
let settingsSchema = {values: {}, editable: [], fields: {}};
let activeSettingsKey = '';
const connectionDrafts = new Map();
const preparedWindows = new Map();
const managedSocketAssignments = new Map();
const managedKbmAssignments = new Map();
const targetPreferences = new Map();
let windowPreparing = false;
let windowPreparationError = '';

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
    if (path === '/api/connect') $('connection-settings-error').textContent = error.message;
    showMessage(error.message, true);
  }
}

function showMessage(message, error = false) {
  $('status-message').textContent = message;
  document.querySelector('.status-light').classList.toggle('off', error);
}

const gameById = id => catalog.games.find(g => g.id === id);
const selectedGame = () => gameById($('game').value);
const selectedInterface = () => selectedGame()?.interfaces.find(i => !i.error && `${i.version}/${i.name}` === $('target').value);
const modeOf = item => item.run?.target?.mode === 'attach' ? 'attach' : item.run?.target?.display || 'unknown';
const modeLabels = {visible: '게임 창 열기', headless: '화면 없이 실행', attach: '실행 중 게임 연결', unknown: '게임 연결'};
const displayLabels = {visible: '게임 창 있음', headless: '게임 창 없음', unknown: '화면 정보 없음'};
const connectorLabels = {socket: '게임 데이터 · 소켓', mod: '게임 플러그인 · Mod', kbm: '화면 · 키보드·마우스'};
const controlRoute = item => item?.connector === 'kbm' ? 'kbm' : ['socket', 'mod'].includes(item?.connector) ? 'internal' : null;
const connectedGame = () => latest?.connection?.game ? gameById(latest.connection.game) : null;
const isRealtime = game => game?.timing === 'realtime';
const providerName = p => ({laya: 'Laya', jev: 'Jev', 'laya-mlx': 'Laya-MLX'}[p] || p);
const windowFor = (game, version) => preparedWindows.get(`${game?.id}/${version}`);
const visibleAttach = item => item.connector === 'socket' && item.run?.mode === 'attach' && item.run?.target?.display === 'visible';
const preparedAttach = (game, version) => (game?.interfaces || []).find(item => !item.error && visibleAttach(item) &&
  (!version || item.version === version) && windowFor(game, item.version)?.alive);

function rememberTargetPreference() {
  if (selectedInterface()) targetPreferences.set($('game').value, $('target').value);
}

function syncPreparedTarget(data) {
  const game = selectedGame();
  const current = selectedInterface();
  if (!game || !current || windowPreparing || data.busy || ['connected', 'connecting'].includes(data.connection?.status) ||
      targetPreferences.has(game.id)) return;
  // A first status response can arrive after the catalog chose its initial launch profile.
  if (!visibleAttach(current) && !(current.connector === 'socket' && current.run?.mode === 'launch' && current.run?.target?.display === 'visible')) return;
  const attach = preparedAttach(game, current.version) || preparedAttach(game);
  if (!attach) return;
  if (attach !== current) {
    $('target').value = `${attach.version}/${attach.name}`;
    renderRunHow();
  } else {
    applyPreparedSocket(attach, windowFor(game, attach.version));
  }
}

function routeTarget(route, game = selectedGame(), current = selectedInterface()) {
  const items = (game?.interfaces || []).filter(item => !item.error && controlRoute(item) === route);
  if (items.includes(current)) return current;
  const sameVersion = items.filter(item => item.version === current?.version);
  const candidates = sameVersion.length ? sameVersion : items;
  return candidates.find(item => visibleAttach(item) && windowFor(game, item.version)?.alive) ||
    candidates.find(item => item.run?.target?.display === 'visible' && item.run.mode === 'launch') ||
    candidates.find(item => item.run?.target?.display === 'visible') || candidates[0];
}

function selectControlRoute(route) {
  if (windowPreparing || latest?.busy || latest?.auto || ['connected', 'connecting'].includes(latest?.connection?.status)) return;
  const item = routeTarget(route);
  if (!item) return;
  lesson = route === 'kbm' ? 'kbm' : 'socket';
  if (item === selectedInterface()) { renderLesson(); return; }
  $('target').value = `${item.version}/${item.name}`;
  rememberTargetPreference();
  renderRunHow();
}

function applyPreparedSocket(item, prepared, force = false) {
  if (!item || !visibleAttach(item) || !prepared?.alive) return;
  const key = `${$('game').value}/${item.version}/${item.name}`;
  const current = $('socket-path').value;
  // Refresh an earlier automatic address after a window restart; keep explicit custom addresses.
  if (force || !current || current === item.run.socket || current === managedSocketAssignments.get(key)) {
    $('socket-path').value = prepared.socket;
    managedSocketAssignments.set(key, prepared.socket);
  }
}

function preparedKbmWindow(item, settings) {
  const prepared = windowFor(selectedGame(), item?.version);
  const defaults = item?.configuration?.values?.window;
  if (item?.connector !== 'kbm' || !prepared?.alive || !defaults) return null;
  const configured = {...defaults, ...(settings.window || {})};
  const priorPids = managedKbmAssignments.get(`${$('game').value}/${item.version}/${item.name}`);
  return configured.owner === defaults.owner && (configured.title_contains || '') === (defaults.title_contains || '') &&
    (configured.pid == null || configured.pid === prepared.pid || priorPids?.has(configured.pid)) ? prepared : null;
}

function rememberManagedKbm(item, pid) {
  const key = `${$('game').value}/${item.version}/${item.name}`;
  const pids = managedKbmAssignments.get(key) || new Set();
  pids.add(pid);
  managedKbmAssignments.set(key, pids);
}

function connectionPayload() {
  const item = selectedInterface();
  if (!item) throw new Error('연결할 인터페이스를 선택하세요.');
  const settings = connectionSettingsPayload();
  const prepared = preparedKbmWindow(item, settings);
  if (prepared) {
    rememberManagedKbm(item, prepared.pid);
    if (settings.window?.pid != null) settings.window = {...settings.window, pid: prepared.pid};
  }
  return {game: $('game').value, version: item.version, mode: modeOf(item), interface: item.name,
    dry_run: $('dry-run').checked, agent: $('agent').value, settings,
    ...(prepared ? {prepared_window_pid: prepared.pid} : {})};
}

function renderWindowPreparation() {
  const game = selectedGame();
  const item = selectedInterface();
  const version = game?.versions?.find(candidate => candidate.id === item?.version);
  const prepared = windowFor(game, version?.id);
  const locked = windowPreparing || ['connected', 'connecting'].includes(latest?.connection?.status);
  $('window-preparation').hidden = !version?.window_interface;
  $('prepare-window').disabled = locked || !!latest?.busy || !version?.window_interface;
  $('prepare-window').textContent = windowPreparing ? '게임 창 준비 중…' : prepared?.alive ? '준비된 게임 창 사용 ↗' : '게임 창 준비 ↗';
  $('window-preparation-note').textContent = windowPreparationError || (windowPreparing ? '게임 창을 실행하고 연결 주소를 확인합니다.' :
    prepared?.alive ? '창이 준비되었습니다. 연결을 끊어도 이 창은 유지됩니다. 두 제어 방식을 같은 창에서 번갈아 연결하세요.' :
    '먼저 게임 창을 여세요. 게임 인터페이스와 화면·키마가 같은 창을 사용하며, 연결을 끊어도 창은 유지됩니다.');
  if (item?.connector === 'kbm' && prepared?.alive && !windowPreparationError) {
    let binding = null;
    try { binding = preparedKbmWindow(item, JSON.parse($('connection-json').value || '{}')); } catch {}
    $('window-preparation-note').textContent += binding ? ` 키마도 준비한 게임 창에 연결합니다 (PID ${binding.pid}).` :
      ' 창 설정을 변경했으므로 키마는 입력한 창 조건으로 연결합니다.';
  }
  $('window-preparation-note').classList.toggle('bad', !!windowPreparationError);
  $('game').disabled = $('target').disabled = locked;
}

async function prepareGameWindow() {
  const game = selectedGame();
  const item = selectedInterface();
  if (windowPreparing || latest?.busy || ['connected', 'connecting'].includes(latest?.connection?.status) ||
      !game?.versions.some(version => version.id === item?.version && version.window_interface)) return;
  windowPreparing = true;
  windowPreparationError = '';
  renderControlRoutes();
  try {
    const response = await api('/api/game-window', {game: game.id, version: item.version});
    const prepared = response.game_window;
    if (!prepared?.alive || prepared.game !== game.id || prepared.version !== item.version) throw new Error('준비한 게임 창의 정보를 확인할 수 없습니다.');
    preparedWindows.set(`${game.id}/${item.version}`, prepared);
    if (controlRoute(item) === 'internal') {
      const attach = game.interfaces.find(candidate => !candidate.error && candidate.version === item.version && visibleAttach(candidate));
      if (attach) {
        $('target').value = `${attach.version}/${attach.name}`;
        renderRunHow();
        applyPreparedSocket(attach, prepared, true);
        rememberTargetPreference();
        saveConnectionDraft();
      }
    }
    await poll();
  } catch (error) {
    windowPreparationError = error.message;
    showMessage(error.message, true);
  } finally {
    windowPreparing = false;
    renderControlRoutes();
  }
}

function renderControlRoutes() {
  const game = selectedGame();
  const item = selectedInterface();
  const route = controlRoute(item);
  const locked = windowPreparing || latest?.busy || latest?.auto || ['connected', 'connecting'].includes(latest?.connection?.status);
  document.querySelectorAll('[data-route]').forEach(button => {
    const available = !!routeTarget(button.dataset.route);
    const selected = button.dataset.route === route;
    button.disabled = locked || !available;
    button.classList.toggle('selected', selected);
    button.setAttribute('aria-pressed', String(selected));
    button.title = !available ? '이 게임에 등록된 연결이 없습니다.' : locked ? '연결을 끊으면 제어 방식을 변경할 수 있습니다.' : '';
  });
  const observation = game?.learning?.observation || '게임별 관찰 코드가 캡처를 상태 설명으로 바꿉니다. 지원하는 화면은 관찰 코드에 따라 달라집니다.';
  $('control-route-note').textContent = route === 'kbm' ? observation :
    route === 'internal' ? '게임이 보내는 내부 상태를 읽고, 모델이 선택한 행동을 소켓 또는 Mod로 전달합니다.' : '게임을 선택하면 지원하는 제어 방식을 표시합니다.';
  if (locked) $('control-route-note').textContent += ' 연결을 끊으면 제어 방식을 변경할 수 있습니다.';
  const steps = route === 'kbm' ? ['게임 화면', '화면 관찰 코드', '모델 판단', '키보드·마우스'] : ['게임 내부 상태', '모델 판단', '행동 명령'];
  $('route-flow').innerHTML = steps.map(step => `<span>${esc(step)}</span>`).join('<i aria-hidden="true">→</i>');
  $('target-summary').textContent = item ? `${item.version} · ${modeLabels[modeOf(item)]}` : '연결 선택 전';
  $('connect').textContent = item?.connector === 'kbm' ? ($('dry-run').checked ? '화면 관찰 연결 ↗' : '실제 키마 연결 ↗') :
    item?.run?.mode === 'launch' && item.run.target?.display === 'visible' ? '게임 창 열고 연결 ↗' : '게임 인터페이스 연결 ↗';
  $('connect').disabled = locked || !item;
  renderWindowPreparation();
  renderLesson();
}

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

// Independent play modes share the existing connection selection and its locks.
function playModeBlock(mode) {
  if (!['play', 'socket', 'kbm'].includes(mode)) return '지원하지 않는 플레이 방식입니다.';
  const route = mode === 'kbm' ? 'kbm' : 'internal';
  if (mode !== 'play' && !routeTarget(route)) return '이 게임에 등록된 연결이 없습니다.';
  if (windowPreparing) return '게임 창 준비가 끝나면 플레이 방식을 선택할 수 있습니다.';
  if (latest?.busy || latest?.auto) return '현재 실행을 중지하면 플레이 방식을 변경할 수 있습니다.';
  if (latest?.connection?.status === 'connecting') return '연결이 끝나면 플레이 방식을 선택할 수 있습니다.';
  if (latest?.connection?.status === 'connected' && mode !== 'play' && controlRoute(selectedInterface()) !== route) {
    return '연결을 끊으면 다른 제어 방식으로 변경할 수 있습니다.';
  }
  return '';
}

function selectPlayMode(mode) {
  if (playModeBlock(mode)) return;
  playModeChosen = true;
  if (mode !== 'play' && latest?.connection?.status !== 'connected') {
    selectControlRoute(mode === 'kbm' ? 'kbm' : 'internal');
  }
  lesson = mode;
  renderLesson();
  renderSurface();
}

function renderLesson() {
  const game = selectedGame();
  const item = selectedInterface();
  const visible = item?.run?.target?.display === 'visible';
  const hasKbm = game?.interfaces.some(i => !i.error && i.connector === 'kbm');
  const hasSocket = game?.interfaces.some(i => !i.error && ['socket', 'mod'].includes(i.connector));
  const canPrepare = game?.versions?.some(version => version.id === item?.version && version.window_interface);
  const learning = game?.learning || {};
  const route = controlRoute(item);
  if (route && (lesson !== 'play' || latest?.auto || latest?.busy)) lesson = route === 'kbm' ? 'kbm' : 'socket';
  const lessons = {
    play: {
      label: '직접 플레이', title: '내가 직접 행동을 선택하고 조작합니다.',
      copy: learning.objective || '게임의 목표와 선택지를 확인하세요. 행동을 직접 골라 보고, 게임 상태가 어떻게 달라지는지 살펴보세요.',
      task: visible ? `${canPrepare ? '「게임 창 준비」로 창을 열면 모델 연결 없이 플레이할 수 있습니다. ' : ''}${learning.controls || '게임 창의 조작 안내에 따라 직접 플레이하세요.'}` :
        '이 실행 대상은 별도 게임 창이 없습니다. 「게임 창 열기」 대상을 고르거나, 연결 후 「콘솔에서 행동 하나 보내기」를 사용하세요.',
    },
    socket: {
      label: '모델 제어 · 게임 데이터', title: '게임 내부 상태로 모델이 판단합니다.',
      copy: '게임이나 Mod가 보낸 상태 설명과 선택지를 모델에 전달합니다. 제어 모델의 Choice를 인터페이스의 행동 매핑으로 바꿔 전송합니다. 비교 모델은 같은 상황을 판단하고 기록만 남깁니다.',
      task: hasSocket ? `${canPrepare ? '「게임 창 준비」 → ' : ''}「게임 인터페이스」 선택 → 연결 → 「입력 없이 판단만 보기」 → 판단 1회 실행. 게임 창의 변화와 아래 전송 명령·응답을 함께 확인하세요. 외부 게임은 연결 주소와 행동 매핑을 먼저 확인하세요.` :
        '이 게임은 데이터 연결을 제공하지 않습니다. 화면 관찰과 키보드·마우스 연결을 선택하세요.',
    },
    kbm: {
      label: '모델 제어 · 화면 + 키마', title: '화면을 관찰하고 모델의 선택을 키보드·마우스로 입력합니다.',
      copy: `KbmAgent가 게임 창을 캡처하고 관찰 코드가 화면을 상태 설명으로 바꿉니다. ${learning.observation || '화면을 읽는 관찰 코드는 게임별로 준비해야 합니다.'} 모델은 그 설명을 읽어 행동을 선택합니다.`,
      task: hasKbm ? `${canPrepare ? '「게임 창 준비」' : '안내에 따라 게임 창 실행'} → 「화면 + 키보드·마우스」 선택 → 입력 미리보기로 연결·관찰 확인 → 연결을 끊고 미리보기 해제 → 다시 연결해 실제 입력 확인. ${canPrepare ? '준비한 게임 창은 유지됩니다.' : '관찰 코드와 키·클릭 매핑은 게임에 맞게 설정하세요.'}` :
        '이 게임은 화면·키보드·마우스 연결을 지원하지 않습니다. 이 연결 방식을 지원하는 다른 게임을 선택하세요.',
    },
  };
  const selected = lessons[lesson] || lessons.play;
  $('lesson-label').textContent = selected.label;
  $('lesson-title').textContent = selected.title;
  $('lesson-copy').textContent = selected.copy;
  $('lesson-task').textContent = selected.task;
  document.querySelectorAll('[data-lesson]').forEach(button => {
    const active = button.dataset.lesson === lesson;
    button.classList.toggle('selected', active);
    button.setAttribute('aria-pressed', String(active));
    const reason = playModeBlock(button.dataset.lesson);
    button.disabled = !!reason;
    button.title = reason;
  });
  const manual = lesson === 'play';
  ['model-settings', 'comparison-settings', 'run', 'auto', 'analyze', 'model-shortcut', 'control-route-label', 'control-routes', 'control-route-note', 'route-flow'].forEach(id => { $(id).hidden = manual; });
  $('latency-tool').disabled = manual;
  $('latency-tool').title = manual ? '모델 제어를 선택하면 응답 시간과 추가 지연을 비교할 수 있습니다.' : '';
  $('play-mode-note').textContent = playModeBlock(lesson) || (manual
    ? '직접 플레이는 모델이 필요 없습니다. 게임 창의 키와 버튼으로 조작하세요.'
    : latest?.connection?.status === 'connected' ? '선택한 방식으로 연결되어 있습니다. 다른 연결 방식으로 바꾸려면 연결을 끊으세요.'
    : '선택한 방식에 맞춰 연결 설정을 준비했습니다. 게임 연결 후 실행하세요.');
}

function renderSurface() {
  const connected = latest?.connection?.status === 'connected';
  const item = selectedInterface();
  const target = connected ? latest.connection.target : item?.run?.target;
  const kbm = (connected ? latest.connection.connector : item?.connector) === 'kbm';
  const dryRun = connected ? latest.options.dry_run : $('dry-run').checked;
  const visible = target?.display === 'visible';
  const headless = target?.display === 'headless';
  $('surface-badge').textContent = lesson === 'play' ? '직접 플레이' : kbm ? (dryRun ? '화면 관찰 · 입력 미리보기' : '화면 관찰 · 실제 입력') : visible ? '별도 게임 창' : headless ? '화면 없이 실행 · 데이터 요약' : '게임 상태 관찰';
  if (lesson === 'play') {
    $('foreground-title').textContent = headless ? '콘솔에서 직접 행동을 선택하세요.' : '게임 창에서 직접 조작하세요.';
    $('foreground-copy').textContent = headless ? '연결 후 「콘솔에서 행동 하나 보내기」를 사용하세요. 별도 창에서 플레이하려면 실행 대상을 「게임 창 열기」로 선택하세요.' :
      '게임 창의 키와 버튼으로 플레이합니다. 모델은 실행하지 않습니다. 지원하는 게임은 「게임 창 준비」로 창을 열 수 있습니다.';
  } else if (kbm) {
    $('foreground-title').textContent = dryRun ? '게임 화면을 읽고 판단만 기록합니다.' : '열려 있는 게임 창을 보고 제어합니다.';
    $('foreground-copy').textContent = dryRun ? '아래 화면은 마지막 캡처입니다. 입력 미리보기에서는 키보드·마우스 입력을 보내지 않습니다.' : '아래 화면은 마지막 캡처입니다. 실제 움직임과 키보드·마우스 입력 결과는 게임 창에서 확인하세요.';
  } else if (visible) {
    $('foreground-title').textContent = connected ? '열린 게임 창에서 플레이를 확인하세요.' : target?.mode === 'attach' ? '이미 실행 중인 게임 창에 연결합니다.' : '게임 창에서 직접 플레이할 수 있습니다.';
    $('foreground-copy').textContent = '게임 창이 실제 플레이 화면입니다. 아래 도식은 수신한 상태 데이터의 요약이며 화면 캡처가 아닙니다. 게임 창과 이 콘솔을 나란히 두고 입력 결과를 확인하세요.';
  } else if (headless) {
    $('foreground-title').textContent = '이 실행 방식은 별도 게임 창을 사용하지 않습니다.';
    $('foreground-copy').textContent = '아래 내용은 관찰한 데이터의 요약입니다. 별도 게임 창을 지원하는 게임은 「게임 창 열기」 대상으로 실행할 수 있습니다.';
  } else {
    $('foreground-title').textContent = '연결한 게임의 마지막 상태를 확인합니다.';
    $('foreground-copy').textContent = '게임 창 표시 여부가 확인되지 않았습니다. 아래에는 마지막으로 관찰한 상태와 모델 판단을 보여 줍니다.';
  }
}

function updateMeasurements(data) {
  const game = connectedGame() || selectedGame();
  const row = data.rows?.[0];
  const ms = value => typeof value === 'number' && Number.isFinite(value) ? `${Math.round(value).toLocaleString()} <em>ms</em>` : '—';
  $('measure-model').innerHTML = ms(row?.decision?.model_ms);
  $('measure-loop').innerHTML = ms(row?.loop_ms);
  $('measure-budget').innerHTML = ms(game?.latency_budget_ms);
  $('measure-stale').innerHTML = `${(data.rows || []).filter(r => r.stale).length} <em>회</em>`;
  $('measure-model').classList.toggle('bad', !!(row && row.decision.model_ms > game?.latency_budget_ms));
  $('measure-provider').textContent = row ? `${row.decision.provider.toUpperCase()} · 최근 ${row.step}번째 판단` : '아직 실행하지 않음';
  $('measure-note').textContent = row
    ? `모델 응답 시간은 실제 호출에 걸린 시간입니다. ${row.observation_to_input_ms != null ? `관찰 → 입력 시도 ${Math.round(row.observation_to_input_ms)} ms. ` : ''}전체 처리 시간에는 비교 모델을 기다린 시간도 포함됩니다. 실험용 추가 대기: ${row.decision_delay_ms || 0} ms${row.analysis_only ? ' · 입력 없이 판단만 기록' : row.cancelled ? ' · 중지 요청으로 입력 취소' : ''}. 오래된 관찰로 인한 입력 취소는 최근 ${data.rows.length}개 기록 기준입니다.`
    : '모델을 실행하면 응답 시간과 관찰부터 기록 완료까지의 전체 처리 시간을 표시합니다. 실험용 추가 대기는 모델 응답 시간에 포함하지 않습니다.';
}

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
  const usableGames = catalog.games.filter(g => !g.error && g.interfaces.some(i => !i.error));
  const first = usableGames.find(g => g.featured) || usableGames[0];
  if (first) select.value = first.id;
  onGameChange();
}

function onGameChange(preferred) {
  windowPreparationError = '';
  const game = selectedGame();
  if (!playModeChosen) lesson = isRealtime(game) ? 'socket' : 'play';
  const items = (game?.interfaces || []).filter(i => !i.error);
  $('target').replaceChildren(...items.map(i => new Option(`${modeLabels[modeOf(i)]} · ${connectorLabels[i.connector] || i.connector} · ${i.version}/${i.name}`, `${i.version}/${i.name}`)));
  // Revisit the same prepared instance unless the user explicitly chose another target.
  const first = items.find(i => `${i.version}/${i.name}` === preferred) ||
    items.find(i => `${i.version}/${i.name}` === targetPreferences.get(game?.id)) ||
    (lesson !== 'play' && routeTarget(lesson === 'kbm' ? 'kbm' : 'internal', game, null)) ||
    preparedAttach(game) ||
    items.find(i => modeOf(i) === 'visible' && i.run.mode === 'launch') ||
    items.find(i => modeOf(i) === 'visible') ||
    items.find(i => i.version === game.default_version && i.name === game.default_interface) || items[0];
  if (first) $('target').value = `${first.version}/${first.name}`;
  if (game) {
    choice = {provider: game.providers.live, shadows: [...game.providers.shadow], policy: false};
    $('game-meta').textContent = `${game.type_label || game.type} · 목표 응답 시간 ${game.latency_budget_ms} ms`;
    loadView(game);
  }
  renderModelControls(game);
  renderRunHow();
  renderGameKind(game);
}

function renderGameKind(game) {
  $('run').innerHTML = '▶ &nbsp; 판단 1회 실행';
  $('stat-latency-label').textContent = game ? `최근 응답 · 목표 ${game.latency_budget_ms} ms` : '최근 모델 응답 시간';
}

function commandText(command) {
  return command.map(part => part === '{python}' ? 'python' : part).join(' ');
}

// The catalog declares editable settings. No game IDs or transport defaults live here.
function saveConnectionDraft() {
  if (!activeSettingsKey) return;
  connectionDrafts.set(activeSettingsKey, {
    schema: settingsSchema,
    json: $('connection-json').value,
    socket: $('socket-path').value,
    integers: Object.fromEntries((settingsSchema.editable || []).filter(key => settingsSchema.fields?.[key]?.type === 'integer')
      .map(key => [key, $(`setting-${key}`).value])),
    dryRun: $('dry-run').checked,
    agent: $('agent').value,
  });
}

function renderConnectionSettings(values) {
  saveConnectionDraft();
  const item = selectedInterface();
  activeSettingsKey = item ? `${$('game').value}/${item.version}/${item.name}` : '';
  settingsSchema = selectedInterface()?.configuration || {values: {}, editable: [], fields: {}};
  const saved = connectionDrafts.get(activeSettingsKey);
  const draft = values === undefined && saved?.schema === settingsSchema ? saved : null;
  const editable = settingsSchema.editable || [];
  const fields = settingsSchema.fields || {};
  const current = values || settingsSchema.values || {};
  const prepared = windowFor(selectedGame(), item?.version);
  if (values && prepared?.alive && preparedKbmWindow(item, current)?.pid === current.window?.pid) {
    rememberManagedKbm(item, prepared.pid);
  }
  $('timing-settings').innerHTML = editable.filter(key => fields[key]?.type === 'integer').map(key => {
    const field = fields[key];
    return `<label for="setting-${esc(key)}">${esc(field.label || key)}<input id="setting-${esc(key)}" data-setting="${esc(key)}" type="number" step="1" min="${esc(field.min ?? 1)}" max="${esc(field.max ?? 600000)}" value="${esc(draft?.integers[key] ?? current[key] ?? '')}"></label>`;
  }).join('');
  const mappings = Object.fromEntries(editable.filter(key => fields[key]?.type !== 'integer' && key !== 'socket').map(key => [key, current[key]]));
  $('socket-path').value = draft?.socket ?? current.socket ?? item?.run?.socket ?? '';
  $('connection-json').value = draft?.json ?? JSON.stringify(mappings, null, 2);
  if (draft) {
    $('dry-run').checked = draft.dryRun;
    $('agent').value = draft.agent;
  } else if (item?.connector === 'kbm' && values === undefined) {
    $('dry-run').checked = true;
  }
  $('connection-json').hidden = $('mapping-label').hidden = $('mapping-help').hidden = !Object.keys(mappings).length;
  $('connection-settings').hidden = !editable.length;
  $('connection-settings-error').textContent = '';
  $('connection-settings-note').textContent = latest?.connection?.status === 'connected'
    ? '현재 연결에 적용된 값입니다. 변경하려면 연결을 끊고 다시 연결하세요.'
    : '변경한 값은 다음 연결부터 적용합니다. 인터페이스 파일의 원본 설정은 유지됩니다.';
}

function connectionSettingsPayload() {
  const editable = settingsSchema.editable || [];
  const fields = settingsSchema.fields || {};
  let mappings;
  try { mappings = JSON.parse($('connection-json').value || '{}'); }
  catch { throw new Error('연결 설정 JSON 문법을 확인하세요.'); }
  if (!mappings || typeof mappings !== 'object' || Array.isArray(mappings)) throw new Error('연결 설정은 JSON 객체여야 합니다.');
  for (const key of Object.keys(mappings)) {
    if (!editable.includes(key) || fields[key]?.type === 'integer') throw new Error(`이 인터페이스에서 수정할 수 없는 설정: ${key}`);
  }
  const settings = {...mappings};
  for (const key of editable) {
    const field = fields[key];
    if (field?.type !== 'integer') continue;
    const input = $(`setting-${key}`);
    const value = Number(input.value);
    if (!input.value.trim() || !Number.isInteger(value) || value < (field.min ?? 1) || value > (field.max ?? 600000)) {
      throw new Error(`${field.label || key}: ${field.min ?? 1}~${field.max ?? 600000} 사이의 정수를 입력하세요.`);
    }
    settings[key] = value;
  }
  if (editable.includes('socket')) settings.socket = $('socket-path').value.trim();
  return settings;
}

function renderRunHow() {
  const item = selectedInterface();
  const version = selectedGame()?.versions.find(v => v.id === item?.version);
  const how = $('run-how');
  const manual = $('run-manual');
  $('target-note').textContent = [version?.name, version?.description].filter(Boolean).join(' · ').replace(/\bsocket\b/g, '소켓').replace(/키마/g, '키보드·마우스');
  $('kbm-options').hidden = item?.connector !== 'kbm';
  $('socket-field').hidden = !(item?.connector === 'socket' && item.run?.mode === 'attach');
  renderConnectionSettings();
  applyPreparedSocket(item, windowFor(selectedGame(), item?.version));
  renderControlRoutes();
  renderSurface();
  if (!item) { how.textContent = '사용할 수 있는 연결 방식이 없습니다. 터미널에서 play.py compare로 모델을 비교할 수 있습니다.'; manual.hidden = true; return; }
  if (item.error) { how.innerHTML = `<span class="bad">${esc(item.error)}</span>`; manual.hidden = true; return; }
  const run = item.run;
  const target = run.target;
  $('workspace-title').textContent = selectedGame()?.name || '게임 상태';
  $('game-run-context').textContent = `${target.version_name} · ${connectorLabels[item.connector] || item.connector} · ${displayLabels[target.display]}`;
  if (run.mode === 'launch') {
    how.innerHTML = `연결을 누르면 콘솔이 게임을 실행하고, 연결을 끊으면 종료합니다.<code>${esc(commandText(run.command))} ${esc(run.socket_arg)} &lt;임시 소켓&gt;</code>` +
      (run.build?.kind === 'binary' && !run.build.exists ? `<small class="bad">실행 파일이 없습니다. ${esc(run.hint || '먼저 빌드하세요.')}</small>` :
       run.hint ? `<small>${esc(run.hint)}</small>` : '');
  } else if (run.mode === 'attach') {
    how.innerHTML = `실행 중인 게임${item.connector === 'mod' ? '의 Mod 브리지' : '의 소켓'}에 연결합니다. 연결을 끊어도 게임은 계속 실행됩니다.<code>${esc(item.connector === 'mod' ? JSON.stringify(run.endpoint || item.configuration?.values?.endpoint || {}) : $('socket-path').value)}</code>`;
  } else {
    how.innerHTML = `열려 있는 게임 창에 연결합니다. KbmAgent가 화면을 캡처하며, 입력 미리보기를 끄면 키보드·마우스로 조작합니다.<code>창: ${esc(run.owner)}${run.title_contains ? ` · 제목에 "${esc(run.title_contains)}" 포함` : ''}</code>`;
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
    `<button data-provider="${esc(p)}" aria-pressed="${p === choice.provider}" class="${p === choice.provider ? 'selected' : ''}">${esc(providerName(p))}<span>${p === 'jev' ? '온라인 API' : p === 'laya-mlx' ? '이 기기 · GPU' : '이 기기 · 로컬'}</span></button>`).join('');
  $('shadow-boxes').innerHTML = providers.map(p =>
    `<label class="check ${p === choice.provider ? 'muted' : ''}"><input type="checkbox" data-shadow="${esc(p)}" ${choice.shadows.includes(p) ? 'checked' : ''} ${p === choice.provider ? 'disabled' : ''}><span>${esc(p)}</span></label>`).join('');
  $('policy-field').hidden = !game?.has_policy;
  $('policy').checked = choice.policy;
  $('api-key').closest('label').classList.toggle('dim', choice.provider !== 'jev' && !choice.shadows.includes('jev'));
  renderAdvancedSummary();
  renderProviderExplainer();
}

function renderAdvancedSummary() {
  const shadows = choice.shadows.filter(p => p !== choice.provider);
  $('advanced-summary').textContent = `비교 모델 ${shadows.length ? shadows.join(', ') : '없음'} · 보조 규칙 ${choice.policy ? '켜짐' : '꺼짐'} · 추가 대기 ${Number($('decision-delay').value)} ms`;
}

function renderProviderExplainer() {
  const provider = choice.provider;
  const game = connectedGame() || selectedGame();
  const requested = game?.providers?.models?.[provider] || '기본 모델';
  const explanation = provider === 'jev'
    ? '상태와 질문을 HTTPS로 보냅니다. 응답 시간에는 네트워크와 서버 대기가 포함됩니다.'
    : `${provider === 'laya-mlx' ? 'MLX' : 'PyTorch'}로 이 컴퓨터에서 평가합니다. 처음 호출할 때 가중치 로딩이 응답 시간에 포함될 수 있습니다.`;
  $('provider-explainer').innerHTML = `<b>${esc(providerName(provider || '모델 선택'))}</b><span>${esc(requested)}</span><p>${esc(explanation)}</p>`;
}

function updateComparisonControls(data) {
  const a = $('compare-a').value, b = $('compare-b').value;
  const connected = data?.connection?.status === 'connected';
  const needsKey = [a, b].includes('jev') && !data?.key_set;
  $('compare').disabled = !connected || !!data?.busy || a === b || needsKey;
  $('compare-a').disabled = $('compare-b').disabled = !!data?.busy;
  $('compare-note').textContent = a === b ? '서로 다른 두 모델을 선택하세요.' : needsKey
    ? 'Jev API 키가 필요합니다. 키 없이도 Laya 단독 실행이나 Laya-MLX와의 비교를 할 수 있습니다.'
    : !connected ? '게임을 연결하면 같은 관찰로 비교할 수 있습니다.'
    : data?.activity === 'compare' ? '같은 입력으로 두 응답을 기다립니다. 게임에는 입력하지 않습니다.'
    : '보조 규칙과 추가 지연을 끄고 두 모델의 원래 응답을 비교합니다.';
}

function modelPayload() {
  const key = $('api-key').value.trim();
  return {provider: choice.provider, shadows: choice.shadows.filter(p => p !== choice.provider), policy: choice.policy,
    decision_delay_ms: Number($('decision-delay').value), ...(key ? {key} : {})};
}

function setupControls() {
  window.DecisionLearning?.init();
  window.LayaTraining?.init({
    api, getGame: selectedGame, getStatus: () => latest,
    getVersion: () => connectedGame()?.id === selectedGame()?.id ? latest?.connection?.target?.version : selectedInterface()?.version,
    onApplied: applied => {
      const version = connectedGame()?.id === selectedGame()?.id ? latest?.connection?.target?.version : selectedInterface()?.version;
      if (selectedGame()?.id !== applied.game || version !== applied.version || !['laya', 'laya-mlx'].includes(applied.provider)) return;
      choice = {...choice, provider: applied.provider, shadows: choice.shadows.filter(provider => provider !== applied.provider)};
      renderModelControls(connectedGame() || selectedGame());
      renderAdvancedSummary();
    },
  });
  ['compare-a', 'compare-b'].forEach(id => $(id).addEventListener('change', () => updateComparisonControls(latest)));
  $('compare').addEventListener('click', () => command('/api/compare', {providers: [$('compare-a').value, $('compare-b').value]}));
  $('stop').addEventListener('click', () => command('/api/auto', {enabled: false}));
  document.querySelector('.glossary-link').addEventListener('click', () => { $('glossary').open = true; });
  document.querySelector('.play-modes').addEventListener('click', event => {
    const button = event.target.closest('[data-lesson]');
    if (!button || button.disabled) return;
    selectPlayMode(button.dataset.lesson);
    document.querySelector('.lesson-guide').open = true;
    if (lesson === 'socket') $('learning-question-step').open = true;
  });
  $('latency-tool').addEventListener('click', () => {
    $('advanced-settings').open = true;
    $('advanced-settings').scrollIntoView({block: 'center'});
  });
  $('game').addEventListener('change', () => onGameChange());
  $('target').addEventListener('change', () => { windowPreparationError = ''; rememberTargetPreference(); renderRunHow(); });
  $('prepare-window').addEventListener('click', prepareGameWindow);
  $('control-routes').addEventListener('click', event => {
    const button = event.target.closest('[data-route]');
    if (button && !button.disabled) {
      playModeChosen = true;
      selectControlRoute(button.dataset.route);
    }
  });
  $('connection-json').addEventListener('input', () => { rememberTargetPreference(); renderWindowPreparation(); });
  $('socket-path').addEventListener('input', rememberTargetPreference);
  $('timing-settings').addEventListener('input', rememberTargetPreference);
  $('dry-run').addEventListener('change', () => { renderControlRoutes(); renderSurface(); });
  $('connect').addEventListener('click', () => {
    const item = selectedInterface();
    if (!item) return;
    try {
      const payload = connectionPayload();
      $('connection-settings-error').textContent = '';
      command('/api/connect', payload);
    } catch (error) {
      $('connection-settings-error').textContent = error.message;
      showMessage(error.message, true);
    }
  });
  const switchTab = tab => {
    document.querySelectorAll('[data-tab]').forEach(button => {
      const selected = button.dataset.tab === tab;
      button.classList.toggle('selected', selected);
      button.setAttribute('aria-selected', String(selected));
      button.tabIndex = selected ? 0 : -1;
      $(`panel-${button.dataset.tab}`).hidden = !selected;
    });
  };
  document.querySelector('.trace-tabs').addEventListener('click', event => {
    const button = event.target.closest('[data-tab]');
    if (button) switchTab(button.dataset.tab);
  });
  document.querySelector('.trace-tabs').addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const buttons = [...document.querySelectorAll('[data-tab]')];
    const index = buttons.indexOf(document.activeElement);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 :
      (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
    event.preventDefault();
    switchTab(buttons[next].dataset.tab);
    buttons[next].focus();
  });
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
  $('policy').addEventListener('change', event => { choice.policy = event.target.checked; renderAdvancedSummary(); });
  $('decision-delay').addEventListener('change', renderAdvancedSummary);
  $('api-key').addEventListener('change', event => {
    if (event.target.value.trim()) command('/api/key', {key: event.target.value.trim()});
  });
  $('run').addEventListener('click', () => command('/api/run', {...modelPayload(), apply: true}));
  $('analyze').addEventListener('click', () => command('/api/run', {...modelPayload(), apply: false}));
  $('auto').addEventListener('click', () => command('/api/auto', {...modelPayload(), enabled: !(latest && latest.auto)}));
  $('realtime-demo').addEventListener('click', () => command('/api/auto', {...modelPayload(), enabled: true, demonstration: true}));
  $('live-jev-choice').addEventListener('change', event => {
    if (choice.provider === 'jev' || latest?.busy || latest?.auto) return;
    choice.shadows = event.target.checked ? [...new Set([...choice.shadows, 'jev'])] : choice.shadows.filter(p => p !== 'jev');
    renderModelControls(connectedGame() || selectedGame());
    updateLiveDecisions(latest || {});
  });
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
      link.download = `${latest?.connection?.game || 'game'}-${latest?.connection?.target?.version || 'default'}-decisions.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { showMessage(error.message, true); }
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && latest && latest.auto) command('/api/auto', {enabled: false});
  });
}

// Connection, health and build -------------------------------------------------

function manualActionButtons(game) {
  return (game?.options || []).map(action => {
    const label = game.learning?.actions?.[action] || action;
    return `<button data-action="${esc(action)}" title="${esc(action)}" aria-label="${esc(label)} (${esc(action)})">${esc(label)}</button>`;
  }).join('');
}

function updateConnection(data) {
  if (Array.isArray(data.game_windows)) {
    preparedWindows.clear();
    data.game_windows.forEach(item => preparedWindows.set(`${item.game}/${item.version}`, item));
  }
  syncPreparedTarget(data);
  const c = data.connection;
  const connected = c.status === 'connected';
  const text = {disconnected: '연결 안 됨', connecting: `${c.game} 연결 중…`, connected: `${c.game} / ${c.target?.version} · ${c.interface}`, error: `${c.game || ''} 연결 문제`}[c.status];
  $('connection-text').textContent = text;
  document.querySelector('.pulse').classList.toggle('off', !connected);
  document.querySelector('.pulse').classList.toggle('wait', c.status === 'connecting');
  const linked = connected || c.status === 'connecting';
  ['game', 'target', 'socket-path', 'dry-run', 'agent', 'connection-json'].forEach(id => { $(id).disabled = linked; });
  document.querySelectorAll('[data-setting]').forEach(input => { input.disabled = linked; });
  $('connect').disabled = linked || !selectedInterface();
  $('disconnect').disabled = c.status === 'disconnected';

  const key = connected ? `${c.game}/${c.target?.version}/${c.interface}` : '';
  if (key !== lastConnectedKey) {
    lastConnectedKey = key;
    lastViewSignature = '';
    if (connected) {
      $('game').value = c.game;
      onGameChange(`${c.target.version}/${c.interface}`);
      renderConnectionSettings(c.settings);
      document.querySelectorAll('[data-setting]').forEach(input => { input.disabled = true; });
      if (data.runtime.socket) $('socket-path').value = data.runtime.socket;
      $('dry-run').checked = !!data.options.dry_run;
      $('agent').value = data.options.agent || 'app';
      const delay = String(data.options.decision_delay_ms || 0);
      if (![...$('decision-delay').options].some(option => option.value === delay)) {
        $('decision-delay').add(new Option(`${Number(delay).toLocaleString()} ms · 현재 대기`, delay));
      }
      $('decision-delay').value = delay;
      if (data.options.provider) {
        choice = {provider: data.options.provider, shadows: data.options.shadows, policy: data.options.policy};
        renderModelControls(connectedGame());
      }
      renderAdvancedSummary();
    }
    const game = connectedGame();
    $('manual-actions').innerHTML = connected && game ? manualActionButtons(game) : '<span class="meta">게임을 연결하면 실행할 행동이 표시됩니다</span>';
    const scenarios = c.scenarios || [];
    $('scenario').innerHTML = '<option value="">다음 상황</option>' + scenarios.map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join('');
    $('scenario-field').hidden = !scenarios.length;
    $('reset').hidden = connected && c.connector === 'kbm';
    const kbm = connected && c.connector === 'kbm';
    $('kbm-warning').hidden = !kbm;
    $('kbm-warning').innerHTML = !kbm ? '' : data.options.dry_run
      ? '<b>입력 미리보기</b>화면을 읽고 판단을 기록합니다. 실제 조작은 연결을 끊고 입력 미리보기를 해제한 뒤 다시 연결하면 시작됩니다.'
      : '<b>실제 입력</b>게임 창에 키보드·마우스 입력을 보냅니다. 멈추려면 마우스를 화면 왼쪽 위 모서리로 옮기세요.';
  }
  const check = c.version_check;
  $('version-check').className = `version-check ${check?.status === 'verified' ? 'good' : check?.status === 'mismatch' || c.status === 'error' ? 'bad' : 'warn'}`;
  $('version-check').textContent = check ? `${check.status === 'verified' ? '✓ 버전 일치' : check.status === 'mismatch' ? '버전 불일치' : '실행 버전 미확인'} · ${check.message}` :
    c.status === 'error' ? c.error : c.status === 'connecting' ? '실행 중인 게임 버전을 확인하는 중…' : '연결 후 게임이 보고한 버전과 비교합니다.';

  const perms = c.permissions && typeof c.permissions === 'object' && !c.permissions.error
    ? Object.entries(c.permissions).map(([name, ok]) => `${ok ? '✓' : '!'} ${name}`).join(' · ') : '';
  $('permissions').textContent = c.status === 'error' ? c.error : [perms, c.halted ? '입력 멈춤: ' + c.halted : ''].filter(Boolean).join(' · ');
  $('permissions').classList.toggle('bad', c.status === 'error' || !!c.halted);
  renderControlRoutes();
  renderSurface();
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
      run?.mode === 'launch' ? '연결하면 콘솔이 실행합니다' : run?.mode === 'attach' ? '실행 중인 게임에 연결합니다' : run?.mode === 'window' ? '게임 창을 찾습니다' : '');
    card('card-link', '', '—', '연결하면 응답 시간이 표시됩니다');
    const [title, detail] = run?.mode === 'launch' ? buildHtml(run.build, ' · 연결 전 미리보기') :
      run?.mode === 'window' ? buildHtml(run.agent_build && {kind: 'binary', ...run.agent_build}, ' · KbmAgent') : ['—', '실행 중인 게임 정보는 연결 후 확인합니다'];
    card('card-build', '', title, detail);
    return;
  }
  // Process
  const uptime = h.started_at ? duration(serverNow() - h.started_at) : '';
  if (r.mode === 'attach') {
    const address = r.endpoint ? `${r.endpoint.host}:${r.endpoint.port}` : r.socket;
    card('card-process', c.status === 'error' ? 'bad' : 'good', c.status === 'error' ? '응답 없음' : '외부에서 실행 중',
      `외부에서 실행한 게임으로, 프로세스 ID를 확인할 수 없습니다<br><code>${esc(address)}</code>`);
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
    ? (r.window_build?.exists ? buildHtml({kind: 'binary', ...r.window_build}, ' · 게임 창의 앱') : ['게임 빌드 정보 없음', '게임 창을 찾으면 해당 프로세스의 실행 파일을 표시합니다'])
    : r.mode === 'attach' ? ['외부에서 실행한 게임', '외부에서 실행한 게임입니다. 게임이 보고한 빌드 정보 (info.build)가 있으면 아래에 표시합니다.']
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

function referenceNote(kind) {
  if (kind === 'instructional_rules') return '교육용 규칙 기준입니다. 최적 행동의 증명이 아닙니다.';
  if (kind === 'sampled_next_second_search') return '관찰 순간의 상태를 칸·박자 단위로 바꿔 탐색한 교육용 기준입니다. 실제 게임의 연속 이동·재사용 대기시간·입력 시점에 따른 최적 행동을 보장하지 않습니다.';
  return '';
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
  $('view-kind').textContent = data.connection.connector === 'kbm' ? '화면에서 읽은 데이터 · 캡처는 위에 표시' : data.connection.target?.display === 'visible' ? '데이터 도식 · 실제 플레이는 별도 게임 창' : data.connection.target?.display === 'headless' ? '데이터 도식 · 게임 창 없음' : '데이터 도식 · 게임 창 정보 없음';
  let html;
  try {
    html = render ? render(o.state, {...viewHelpers, truth: o.truth, options: game.options, done: o.done}) : genericView(o.state);
  } catch (error) {
    html = `<div class="bad">view.js 오류: ${esc(error.message)}</div>${genericView(o.state)}`;
  }
  holder.innerHTML = (o.done ? '<div class="gv-done">판 끝</div>' : '') + html;
  applyWidths(holder);
  $('state-text').textContent = o.description || pretty(o.state);
  const reference = referenceNote(o.reference_kind);
  $('truth').textContent = o.truth ? (reference ? reference + '\n' : '') + pretty(o.truth) : '이 게임은 채점 기준을 보내지 않습니다.';
}

async function updateFrame(data) {
  const kbm = data.connection.connector === 'kbm' && data.connection.status === 'connected';
  $('frame-box').hidden = !kbm;
  if (!kbm || !data.frame_at) {
    if (frameUrl) URL.revokeObjectURL(frameUrl);
    frameUrl = null;
    lastFrameAt = null;
    $('frame').removeAttribute('src');
    $('frame').hidden = true;
    $('frame-note').textContent = kbm ? '아직 캡처가 없습니다. 연결된 게임 창을 관찰하면 실제 캡처가 표시됩니다.' : '';
    return;
  }
  if (data.frame_at === lastFrameAt) return;
  try {
    const blob = await api('/api/frame');
    if (latest && (latest.connection.status !== 'connected' || latest.connection.connector !== 'kbm' || latest.frame_at !== data.frame_at)) return;
    if (frameUrl) URL.revokeObjectURL(frameUrl);
    frameUrl = URL.createObjectURL(blob);
    $('frame').src = frameUrl;
    $('frame').hidden = false;
    lastFrameAt = data.frame_at;
    $('frame-note').textContent = `KbmAgent가 캡처한 화면 · ${new Date(data.frame_at * 1000).toLocaleTimeString()}`;
  } catch (error) { $('frame-note').textContent = error.message; }
}

function updateInputTrace(data) {
  const row = data.rows?.[0];
  const trace = data.input_trace || row?.input_trace;
  const holder = $('input-trace');
  if (!trace) {
    $('input-trace-note').textContent = row ? '이 기록에는 전송 명령이 없습니다. 판단 결과만으로 입력 실행을 추정하지 않습니다.' : '판단 또는 직접 조작을 실행하면 기록이 표시됩니다.';
    holder.innerHTML = '<div class="empty">아직 전송 기록이 없습니다.</div>';
    return;
  }
  const result = trace.result || {};
  const cancelled = result.cancelled || result.stale;
  const kbm = trace.connector === 'kbm';
  const confirmed = result.application_confirmed === true || (!kbm && result.applied);
  const label = result.dry_run ? '입력 미리보기 · 실제 입력 없음' : cancelled ? '입력 취소 · 폐기' :
    result.error || result.ok === false ? (kbm && result.input_sent ? '키마 전송 후 오류 · 게임 적용 미확인' : '입력 실패') :
    confirmed ? '게임 적용 확인' : kbm && result.input_sent ? '키마 입력 전송 · 게임 적용 미확인' :
    kbm && result.applied ? '키마 처리 완료 · 게임 적용 미확인' : '입력 미적용';
  $('input-trace-note').textContent = `${connectorLabels[trace.connector] || trace.connector || '인터페이스'} · ${label}`;
  holder.innerHTML = `<div class="input-trace-choice"><span>선택한 행동</span><b>${esc(trace.action ?? '없음')}</b><span class="${cancelled ? 'warn' : confirmed && !result.dry_run ? 'good' : ''}">${esc(label)}</span></div>` +
    `<div class="input-trace-columns"><div><h3>전송 명령</h3><pre>${esc(trace.command == null ? '전송한 명령 없음' : pretty(trace.command))}</pre></div><div><h3>실제 처리 응답</h3><pre>${esc(pretty(result))}</pre></div></div>` +
    (trace.mapped_command != null ? `<details class="state-details"><summary>설정에 정의된 행동 매핑 <span>전송 여부는 위의 명령·응답으로 확인</span></summary><pre class="state-box">${esc(pretty(trace.mapped_command))}</pre></details>` : '');
}

// Live decision demonstration ---------------------------------------------------

function callRoleLabel(call) {
  if (call.role === 'warmup') return '모델 준비 · 입력 없음';
  if (call.comparison || call.role === 'comparison') return '비교 실험 · 기록만';
  return call.role === 'live' ? '제어 모델' : call.role === 'shadow' ? '비교 모델 · 기록만' : call.role || '모델';
}

function liveProviderState(data, provider, configuration) {
  const selected = provider === configuration.provider || (configuration.shadows || []).includes(provider);
  const calls = (data.calls || []).filter(call => call.provider === provider);
  const running = calls.find(call => !call.finished_at);
  const completed = calls.find(call => call.finished_at && call.role !== 'warmup');
  let row = null, decision = null, shadow = false;
  for (const candidate of data.rows || []) {
    const match = [candidate.decision, ...(candidate.shadow || [])].find(item => item?.provider === provider &&
      (completed ? item.call_id === completed.id : item.status === 'ok'));
    if (match) { row = candidate; decision = match; shadow = match !== candidate.decision; break; }
  }
  const preparation = (data.preparations || []).find(item => item.provider === provider && ['preparing', 'warming', 'error'].includes(item.status));
  const role = running ? callRoleLabel(running) : completed ? callRoleLabel(completed) :
    provider === configuration.provider ? '제어 모델' : '비교 모델 · 기록만';
  const elapsed = running && Number.isFinite(running.started_at) ? Math.max(0, serverNow() - running.started_at) : null;
  let status = !selected ? '선택 안 됨' : provider === 'jev' && !data.key_set ? 'API 키 필요' : '판단 대기';
  if (selected && preparation) status = preparation.status === 'warming' ? '첫 추론 준비 중 · 입력 없음' : preparation.status === 'error' ? '모델 준비 실패' : '모델 준비 중';
  if (selected && completed) status = completed.status === 'ok' ? '응답 완료' : '호출 실패';
  if (selected && running) status = `${running.role === 'warmup' ? '첫 추론 준비 중' : '판단 중'} · ${elapsed == null ? '경과 측정 중' : elapsed.toFixed(1) + '초'}`;
  let effect = !selected ? '모델 설정에서 선택하면 실제 호출을 표시합니다.' : '아직 이 모델의 게임 입력 기록이 없습니다.';
  if (selected && completed) {
    if (completed.status !== 'ok') effect = completed.error || '응답 실패 · 게임 입력 없음';
    else if (completed.role !== 'live' || completed.comparison || shadow) effect = '판단만 기록 · 게임 입력 없음';
    else if (row) effect = rowBadge(row)[0];
    else effect = '응답 수신 · 게임 입력 결과 대기';
  }
  if (selected && running?.role === 'warmup') effect = '준비용 추론 · 게임에 입력하지 않습니다.';
  const requiresKey = selected && provider === 'jev' && !data.key_set;
  if (requiresKey) {
    status = 'API 키 필요';
    effect = completed ? '이전 응답 · 새 요청에는 API 키가 필요합니다.' : 'API 키를 입력하면 실제 Jev 판단을 비교할 수 있습니다.';
  }
  const action = selected ? completed?.action ?? decision?.action ?? null : null;
  const latency = selected ? completed?.ms ?? decision?.model_ms ?? null : null;
  return {provider, selected, role, running: !!running && selected, status, effect, action, latency,
    pendingSourceStep: selected ? running?.step ?? null : null,
    completedSourceStep: selected ? completed?.step ?? row?.step ?? null : null, requiresKey,
    stale: !!row?.stale && !shadow, warmup: running?.role === 'warmup', error: selected && ((completed && completed.status !== 'ok') || preparation?.status === 'error')};
}

function updateLiveDecisions(data) {
  const game = connectedGame() || selectedGame();
  const panel = $('live-decisions');
  const realtime = isRealtime(game);
  const active = !!data.busy || !!data.auto;
  const modelView = realtime && lesson !== 'play';
  $('top').classList.toggle('realtime-model-view', modelView);
  $('top').classList.toggle('demonstration-running', modelView && !!data.demonstration?.enabled && active);
  panel.hidden = !modelView;
  if (panel.hidden) return;
  const connected = data.connection?.status === 'connected';
  const configuration = active ? (data.active_options || data.options || choice) : choice;
  const state = data.observed?.state || {};
  const elapsed = Number.isFinite(state.elapsed) ? ` · ${state.elapsed.toFixed(1)}초` : '';
  const result = {won: '목표 달성', lost: '게임 종료', draw: '시간 종료'}[state.outcome];
  $('live-world-status').textContent = !connected ? '게임 연결 전' : !data.observed?.ok ? '게임 상태 관찰 대기' : result ? result + elapsed : state.paused ? '게임 일시정지' + elapsed : '게임 진행 중' + elapsed;
  $('live-world-status').classList.toggle('running', connected && !!data.observed?.ok && !result && !state.paused);
  const demonstration = data.demonstration || {};
  const demoStatus = {preparing: '선택한 모델을 준비하고 있습니다. 게임은 계속 진행됩니다.',
    warming: '첫 추론을 준비하고 있습니다. 준비가 끝나면 같은 시작 상황에서 시연합니다.',
    running: '실시간 시연 중 · 모델이 판단하는 동안에도 게임은 계속 진행됩니다.',
    stopped: '시연을 중지했습니다.', error: demonstration.started_at ? '시연 중 오류가 발생했습니다. 실행 로그를 확인하세요.' : '시연 준비에 실패했습니다. 실행 로그를 확인하세요.'}[demonstration.status];
  $('live-demo-status').textContent = demoStatus || (active ? '각 모델의 실제 요청과 응답을 확인하세요. 게임 시계는 모델을 기다리지 않습니다.' : '선택한 제어·비교 모델을 준비한 뒤 현재 시작 상황에서 연속 판단을 보여 줍니다.');
  const dryRun = data.connection?.connector === 'kbm' && data.options?.dry_run;
  const canReset = data.connection?.connector === 'socket' && (data.connection?.scenarios || []).length;
  const keyMissing = !data.key_set && !$('api-key').value.trim();
  const needsKey = configuration.provider === 'jev' && keyMissing;
  const shadowNeedsKey = (configuration.shadows || []).includes('jev') && keyMissing;
  const supportsDemonstration = data.demonstration && typeof data.demonstration === 'object';
  const blocked = !connected ? '게임을 먼저 연결하세요.' : !supportsDemonstration ? '콘솔 재시작 필요 · 실행 중인 서버를 재시작하면 실시간 시연을 사용할 수 있습니다.' : active ? '현재 실행을 중지하면 새 시연을 시작할 수 있습니다.' :
    dryRun ? '입력 미리보기는 판단을 기록하며 실제 게임을 제어하지 않습니다.' : !canReset ? '이 연결은 「자동 플레이」로 모델의 연속 판단을 확인하세요.' :
    needsKey ? '선택한 Jev를 호출하려면 모델 설정에서 API 키를 입력하세요.' : '';
  $('realtime-demo').disabled = !!blocked;
  $('realtime-demo').title = blocked;
  $('live-demo-note').textContent = blocked || (shadowNeedsKey ? '로컬 모델로 시연합니다. Jev 비교에는 API 키가 필요합니다.' : '로컬 모델은 준비용 추론 후 새 판에서 시작합니다. 제어 모델만 입력하고 다른 모델은 같은 관찰을 판단해 비교합니다.');
  $('live-jev-choice').checked = configuration.provider === 'jev' || (configuration.shadows || []).includes('jev');
  $('live-jev-choice').disabled = active || configuration.provider === 'jev';
  const providers = [...new Set(['laya', 'jev', configuration.provider, ...(configuration.shadows || [])].filter(Boolean))];
  $('live-provider-cards').innerHTML = providers.map(provider => {
    const card = liveProviderState(data, provider, configuration);
    const action = card.action == null ? '—' : game?.learning?.actions?.[card.action] || card.action;
    return `<article class="live-provider-card ${card.running ? 'thinking' : ''} ${!card.selected ? 'inactive' : ''} ${card.error ? 'failed' : ''}"><div class="live-provider-title"><b>${esc(providerName(provider))}</b><span>${esc(card.selected ? card.role : '선택 안 됨')}</span></div><div class="live-provider-status" role="status">${esc(card.status)}</div><dl>${card.pendingSourceStep != null ? `<div><dt>판단 중인 관찰</dt><dd>${esc(card.pendingSourceStep)}번째 관찰</dd></div>` : ''}${card.completedSourceStep != null ? `<div><dt>응답한 관찰</dt><dd>${esc(card.completedSourceStep)}번째 관찰</dd></div>` : ''}<div><dt>${card.requiresKey ? '이전 선택' : card.running ? '직전 선택' : '선택한 행동'}</dt><dd>${esc(action)}</dd></div><div><dt>${card.requiresKey ? '이전 응답' : card.running ? '직전 응답' : '실측 응답'}</dt><dd>${Number.isFinite(card.latency) ? esc(Math.round(card.latency).toLocaleString()) + ' ms' : '—'}</dd></div></dl><p class="${card.stale ? 'warn' : ''}">${esc(card.effect)}</p></article>`;
  }).join('');
}

// Pipeline and model calls ------------------------------------------------------

function updatePipeline(data) {
  const phase = data.phase || {};
  const steps = ['모델 준비', '게임 관찰', '모델 판단', ...(data.activity === 'compare' ? ['비교 기록'] : ['행동 전송'])];
  const active = data.connection.status === 'connected' && phase.busy;
  const elapsed = phase.since ? Math.max(0, serverNow() - phase.since) : 0;
  const running = (data.calls || []).filter(c => !c.finished_at);
  $('pipeline').innerHTML = `<div class="pipe-steps">${steps.map(s => `<span class="pipe-step ${active && phase.name === s ? 'on' : ''}">${esc(s)}</span>`).join('<i>→</i>')}</div>` +
    `<div class="pipe-now">${active ? `지금: <b>${esc(phase.name)}</b> · ${elapsed.toFixed(1)}초째` : data.connection.status === 'connected' ? `대기 중${data.auto ? ' · 자동 플레이' : ''}` : '연결 안 됨'}</div>` +
    running.map(c => `<div class="pipe-call"><span class="role ${esc(c.role)}">${esc(callRoleLabel(c))}</span><b>${esc(c.provider)}</b> ${esc(c.model || '')} · ${esc(c.runtime || '')}<em>${(serverNow() - c.started_at).toFixed(1)}초</em></div>`).join('');
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
  $('call-count').textContent = String(calls.length);
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
      <summary><span class="role ${esc(c.role)}">${esc(callRoleLabel(c))}</span><b>${esc(c.provider)}</b><span class="call-model">${esc(c.model || '')}</span>${status}<em class="${over ? 'bad' : ''}">${done ? `${Math.round(c.ms)} ms${over ? ' · 목표 초과' : ''}` : ''}</em></summary>
      <div class="call-body">
        <div class="call-meta">${esc(c.runtime || '')}${c.endpoint ? ` · <code>${esc(c.endpoint)}</code>` : ''} · 입력 ${esc(c.input_chars)}자${c.routed_model ? ` · 응답 모델 ${esc(c.routed_model)}` : ''}${c.confidence !== undefined && c.confidence !== null ? ` · 분포 집중도 ${esc(c.confidence)}` : ''}${usage ? ` · ${esc(usage)}` : ''}</div>
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
      const truth = g && !g.status ? ` <em class="${g.exact ? 'good' : 'bad'}">기준 ${esc(g.truth)} · 점수 오차 ${num(g.abs_error)}</em>` : '';
      return `<div class="answer"><span>점수 평가 (Score) · ${esc(qid)}</span><b>${num(a.score)} / ${top}</b>${truth}</div>`;
    }
    const truthLabel = g && !g.status ? (typeof g.truth === 'boolean' ? (g.truth ? '참' : '거짓') : pct(g.truth)) : null;
    const truth = truthLabel !== null ? ` <em class="${g.correct ? 'good' : 'bad'}">기준 ${truthLabel} · 확률 오차 (Brier) ${num(g.brier, 3)}</em>` : '';
    return `<div class="answer"><span>확률 예측 (Noul) · ${esc(qid)}</span><b>${pct(a.noul)}</b>${truth}</div>`;
  }).join('');
}

function liveBlock(row, game) {
  const d = row.decision;
  if (d.status !== 'ok') return `<div class="model-block"><div class="model-name"><span>${esc(d.provider)}</span></div><div class="decision-event bad">${esc(d.error)}</div></div>`;
  const truthActions = row.grade?.action?.truth || [];
  const over = game.latency_budget_ms && d.model_ms > game.latency_budget_ms;
  const bars = game.options.map(option => {
    const raw = d.probabilities?.[option];
    const known = typeof raw === 'number' && Number.isFinite(raw);
    const value = known ? Math.max(0, Math.min(1, raw)) : 0;
    const winner = d.action === option;
    const sent = row.action === option && row.action !== d.action;
    return `<div class="choice-row ${winner ? 'winner' : ''} ${sent ? 'sent' : ''}"><span>${esc(option)}${truthActions.includes(option) ? ' <i class="good">✓</i>' : ''}</span><div class="choice-track"><div class="choice-fill" data-w="${value * 100}"></div></div><span class="score">${known ? (value * 100).toFixed(0) + '%' : '—'}</span></div>`;
  }).join('');
  const reference = referenceNote(row.reference_kind);
  return `<div class="model-block"><div class="model-name"><span>${row.comparison ? '비교 기준 모델' : '제어 모델'} · ${esc(d.provider.toUpperCase())}</span><span class="${over ? 'bad' : ''}">${esc(Math.round(d.model_ms))} ms${over ? ' · 목표 초과' : ''}</span></div>${reference ? `<p class="field-note">${esc(reference)}</p>` : ''}${bars}${answerLines(d.answers, row.grade, game.questions)}</div>`;
}

function shadowBlock(s, game, comparison = false) {
  if (s.status !== 'ok') {
    const pending = s.status === 'running';
    const source = s.source_step != null ? ` · ${s.source_step}번째 관찰` : '';
    const status = pending ? `기록 시점에 판단 중${source}` : s.status === 'timeout' ? '대기 시간 초과 (이번 판단에 응답 미포함)' : s.error || '모델 호출 실패';
    return `<div class="shadow-line"><b>${esc(s.provider)}</b><span class="${pending ? 'warn' : 'bad'}">${esc(status)}</span></div>`;
  }
  const correct = s.grade?.action?.correct;
  return `<div class="model-block secondary-block"><div class="model-name secondary"><span>비교 모델 · ${esc(s.provider.toUpperCase())} → ${esc(s.action)} ${correct === undefined ? '' : correct ? '✓' : '✗'}</span><span>${s.agrees ? (comparison ? '기준 모델과 같음' : '최종 행동과 같음') : (comparison ? '기준 모델과 다름' : '최종 행동과 다름')} · ${esc(Math.round(s.model_ms))} ms</span></div>${answerLines(s.answers, s.grade, game.questions)}</div>`;
}

function rowBadge(row) {
  if (row.comparison) return ['같은 관찰 비교', '두 모델의 응답만 기록 · 게임 입력 없음', ''];
  if (row.analysis_only) return ['판단만 기록', '게임에 입력을 보내지 않음', ''];
  if (row.cancelled) return ['입력 취소', '중지 요청으로 입력 취소', 'warn'];
  if (row.input_trace?.result?.dry_run) return ['입력 미리보기', '실제 키보드·마우스 입력 없음', ''];
  if (row.stale) return ['입력 취소', row.error || '관찰이 오래되었거나 게임 상황이 바뀜', 'warn'];
  if (row.input_trace?.connector === 'kbm' && row.input_trace.result?.application_confirmed !== true) {
    if (row.input_trace.result?.input_sent) return ['키마 입력 전송', row.error ? '전송 후 오류 · 게임 적용 미확인' : '게임 적용 미확인', row.error ? 'error' : ''];
    if (row.applied) return ['키마 처리 완료', '게임 적용 미확인', ''];
  }
  if (row.applied) return ['게임 적용 확인', '게임이 행동 적용을 보고함', ''];
  return ['입력 미적용', row.error || '게임에 행동을 적용하지 못함', 'error'];
}

function updateDecisions(data) {
  const game = connectedGame();
  const signature = JSON.stringify([data.steps, data.rows.length && data.rows[0].step, latest?.connection?.game]);
  if (signature === lastRowsSignature) return;
  lastRowsSignature = signature;
  $('decision-count').textContent = String(data.steps);
  const holder = $('decisions');
  if (!data.rows.length || !game) { holder.innerHTML = '<div class="empty">모델을 실행하면 판단 결과와 채점 기준이 이곳에 표시됩니다.</div>'; return; }
  holder.innerHTML = data.rows.map(row => {
    const [badge, note, kind] = rowBadge(row);
    const ok = row.decision.status === 'ok';
    const correct = row.final_correct;
    const policy = row.policy || {};
    const override = policy.override ? ` · 보조 규칙 ${esc(policy.override.rule)}: ${esc(policy.override.from)} → ${esc(policy.override.to)}` : '';
    const fired = !policy.override && policy.fired?.length ? ` · 보조 규칙 발동 (기록만): ${esc(policy.fired.join(', '))}` : '';
    const title = ok ? `${row.comparison ? '비교 기준 모델의 선택' : row.analysis_only ? '선택한 행동' : '최종 행동'} · ${esc(row.action)} ${correct == null ? '' : correct ? '<i class="good">✓</i>' : '<i class="bad">✗</i>'}` : '모델 호출 실패';
    return `<article class="decision-card ${ok ? 'primary' : 'error'} ${kind}"><div class="decision-head"><span class="decision-turn">판단 ${String(row.step).padStart(2, '0')}</span><span>${row.episode + 1}번째 게임 · ${badge}</span><span>${esc(Math.round(row.loop_ms))} ms</span></div><div class="decision-summary"><strong>${title}</strong><span>${esc(note)}${override}${fired}</span></div>${liveBlock(row, game)}${(row.shadow || []).map(s => shadowBlock(s, game, row.comparison)).join('')}</article>`;
  }).join('');
  applyWidths(holder);
}

function summaryText(grades) {
  return Object.entries(grades || {}).map(([qid, s]) => {
    if (!s.n) return `${esc(qid)} 채점 가능한 응답 없음`;
    if (s.type === 'choice') return `${esc(qid)} 기준 일치율 ${pct(s.accuracy)}`;
    if (s.type === 'score') return `${esc(qid)} 평균 점수 오차 ${num(s.mae)} · 단계 일치율 ${pct(s.exact)}`;
    return `${esc(qid)} 확률 오차 (Brier) ${num(s.brier, 3)} · 참/거짓 일치율 ${pct(s.accuracy)} (50% 기준)`;
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
  if (s?.live?.grades && Object.keys(s.live.grades).length) lines.push(`<div><b>제어 모델 전체</b>${summaryText(s.live.grades)}</div>`);
  Object.entries(s?.shadows || {}).forEach(([p, g]) => lines.push(`<div><b>비교 모델 · ${esc(p)}</b>${summaryText(g)}</div>`));
  if (s?.policy && Object.keys(s.policy.fired).length) lines.push(`<div><b>보조 규칙</b>${Object.entries(s.policy.fired).map(([k, v]) => `${esc(k)} ${v}회`).join(', ')} · 행동 교체 ${s.policy.overrides}회 (기준에 맞게 ${s.policy.helped}회, 기준과 다르게 ${s.policy.hurt}회)</div>`);
  if (s?.comparisons?.steps) {
    lines.push(`<div><b>같은 관찰 비교 · ${s.comparisons.steps}회</b>게임 입력 없이 비교한 결과입니다. 제어 모델 성적에 포함하지 않습니다.</div>`);
    Object.entries(s.comparisons.providers || {}).forEach(([p, entry]) => lines.push(`<div><b>${esc(providerName(p))} · ${entry.count}회 비교</b>${summaryText(entry.grades)}</div>`));
  }
  if (s?.outcomes?.length) lines.push(`<div><b>게임 결과</b>${s.outcomes.map(esc).join(', ')}</div>`);
  box.hidden = !lines.length;
  box.innerHTML = lines.join('');
  $('session-note').textContent = data.session ? `기록 저장 위치: ${data.session}` : '최근 판단이 위에 표시됩니다';
  updateMeasurements(data);
}

function updateMeta(data) {
  const c = data.connection;
  const connected = c.status === 'connected';
  $('key-status').textContent = data.key_set ? 'API 키 사용 가능 · 메모리에만 보관' : 'Jev를 사용하려면 API 키를 입력하세요';
  const focusOnRun = !!c.capabilities?.focus && !data.options?.dry_run;
  $('run').textContent = focusOnRun ? '▶ 게임 창으로 전환하여 1회 실행' : '▶ 1회 실행';
  $('run').title = focusOnRun ? '시작할 때 선택한 게임 창으로 전환합니다. 판단 중 다른 앱으로 이동하면 입력을 차단합니다.' : '';
  $('auto').textContent = data.auto ? '자동 플레이 중지' : focusOnRun ? '게임 창에서 자동 플레이' : '자동 플레이';
  const message = data.error || (c.status === 'connecting' ? '게임에 연결하는 중입니다…' :
    c.status === 'error' ? '연결 문제: ' + c.error :
    !connected ? '게임을 고르고 연결하세요.' :
    data.busy ? (data.auto ? '자동 플레이 중 · Esc로 멈춥니다' : '모델이 판단하는 중입니다…') : '연결됨. 모델을 실행하거나 직접 조작해 보세요.');
  showMessage(message, !!data.error || c.status === 'error' || !connected);
  ['run', 'analyze', 'reset', 'scenario'].forEach(id => { $(id).disabled = !connected || data.busy; });
  $('decision-delay').disabled = !!data.busy;
  $('stop').hidden = !data.busy;
  $('run-progress').textContent = data.busy ? `${data.activity === 'compare' ? '같은 관찰 비교' : data.phase?.name || '처리 중'} · ${duration(Math.max(0, serverNow() - (data.phase?.since || serverNow())))} 경과` : '';
  updateComparisonControls(data);
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
    updateLiveDecisions(data);
    updateInputTrace(data);
    updateCalls(data);
    updateDecisions(data);
    updateSummary(data);
    updateMeta(data);
    window.DecisionLearning?.update(data, connectedGame() || selectedGame());
    window.LayaTraining?.update(data, selectedGame());
  } catch (error) { showMessage('대시보드 서버 연결 실패: ' + error.message, true); }
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
