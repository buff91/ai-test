/* Game-specific, reviewed Laya training. Server owns datasets, jobs and activation. */
(() => {
  'use strict';
  const $ = name => document.getElementById(`training-${name}`);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const json = value => JSON.stringify(value ?? null, null, 2);
  const object = value => value && typeof value === 'object' && !Array.isArray(value);
  const actionLabel = value => typeof value === 'string' ? value : value?.choice;
  const reviewNames = {pending: '검토 대기', approved: '승인됨', excluded: '제외됨'};
  const providerName = provider => ({laya: 'Laya', 'laya-mlx': 'Laya-MLX'}[provider] || provider || 'Laya');
  const referenceName = kind => ({turn_search: '턴제 행동 탐색 · 제한된 범위의 결과', sampled_next_second_search: '실시간 상태를 단순화한 짧은 탐색 기준', instructional_rules: '게임에 정한 교육용 규칙'}[kind] || '기준 설명 없음');
  const runtimeOf = checkpoint => checkpoint?.runtime || 'laya';
  const supportedRuntime = checkpoint => ['laya', 'laya-mlx'].includes(runtimeOf(checkpoint));
  const canConvert = checkpoint => runtimeOf(checkpoint) === 'laya' && checkpoint?.compatible !== false;
  const canApply = checkpoint => supportedRuntime(checkpoint) && checkpoint?.compatible !== false && checkpoint?.activation_allowed === true;
  const jobNames = {queued: '대기 중', running: '실행 중', succeeded: '완료', completed: '완료', failed: '실패'};
  let config, currentGame, currentVersion, status, snapshot = null, selected = '', editorKey = '';
  let loading = false, busy = false, lastFetch = 0, generation = 0, failed = false;
  let notice = '', noticeError = false, bookmarkSignature = '';

  function markup() {
    return `<div class="training-heading"><div><span class="section-index">04 · 게임별 모델 개선</span><h2>Laya 학습실</h2><p class="field-note">사례 수집 → 정답 검토 → PyTorch 학습 → 실행 방식별 평가 → 적용</p></div><span id="training-game" class="surface-badge">게임 선택 전</span></div>
      <p class="training-scope">PyTorch로 학습한 체크포인트를 Laya에 적용하거나, MLX로 변환해 Laya-MLX에서 사용합니다. 각 실행 방식의 체크포인트는 따로 평가하고 적용합니다. 데이터와 체크포인트는 이 컴퓨터에 저장됩니다.</p>
      <details class="training-learning-guide"><summary>정답은 어떻게 가르치나요?</summary><p>정답은 이 상태에서 사용자가 확인해 승인한 행동입니다. 게임 내 기준 규칙의 truth는 검토용 제안이며 절대적인 최적 행동을 뜻하지 않습니다.</p><ol><li>당시 상태와 모델의 답을 보고 정답 행동을 확인한 뒤 학습용 또는 평가용으로 승인합니다.</li><li>학습용으로 승인한 정답 행동의 예측 확률이 높아지도록 가중치를 업데이트합니다. 검토 대기·제외 사례는 학습에 쓰지 않습니다.</li><li>평가용 정답은 가중치 학습에 쓰지 않고, 따로 남겨 둔 상황에서 성능을 확인합니다. 비슷한 연속 장면에만 맞추지 않도록 서로 다른 판이나 시작 상황의 사례를 평가용으로 남기세요.</li></ol><p>승인 시에는 선택지와 값의 형식을 검사합니다. 실제로 좋은 행동인지는 검토자가 판단해야 하며, 잘못 승인한 답도 그대로 학습합니다. 평가 역시 따로 승인한 정답과의 일치를 측정합니다.</p><p>실패 사례만 모으면 특정 상황에 편향될 수 있습니다. 「추가로 남길 판단」으로 올바르게 행동한 상황과 경계 상황도 다양하게 저장하고 검토하세요.</p></details>
      <p id="training-status" class="field-note" role="status" aria-live="polite">게임을 선택하면 저장된 사례를 읽습니다.</p>
      <div id="training-counts" class="training-counts"></div>
      <div class="training-columns"><div class="training-workflow">
        <section class="training-step"><h3>1. 실패 사례 수집 · 정답 검토</h3><p class="field-note">실패한 판단은 자동으로 검토 대기에 모입니다. 기준 규칙의 제안은 직접 확인하고 승인하세요. 반복된 같은 상태는 하나로 합쳐집니다.</p>
          <div class="action-row training-actions"><button id="training-collect" type="button" disabled class="button-secondary">최근 실패 다시 수집</button><button id="training-refresh" type="button" disabled class="button-ghost">새로고침</button></div>
          <div class="training-bookmark"><label for="training-bookmark">추가로 남길 판단</label><select id="training-bookmark"><option value="">현재 게임의 판단 기록 없음</option></select><button id="training-bookmark-add" type="button" disabled class="button-ghost">이 판단을 검토 대기에 저장</button></div>
          <label for="training-case">저장된 사례</label><select id="training-case"><option value="">저장된 사례 없음</option></select>
          <div id="training-case-empty" class="training-empty">게임을 실행하면 실패 사례가 모입니다. 게임 연결 없이도 저장된 사례를 검토할 수 있습니다.</div>
          <div id="training-review" hidden><p id="training-case-note" class="field-note"></p><div class="training-case-grid"><details open><summary>당시 모델 입력</summary><pre id="training-state" class="training-pre"></pre></details><details open><summary>모델의 답 · 기준 제안</summary><pre id="training-answer" class="training-pre"></pre></details></div><details><summary>당시 질문 · 선택지</summary><pre id="training-questions" class="training-pre"></pre></details>
            <div class="training-label-fields"><div><label for="training-action">대표 정답 행동 하나</label><select id="training-action" aria-describedby="training-action-help"></select><p id="training-action-help" class="field-note">기준이 여러 행동을 허용해도 이번 사례에서 가르칠 대표 정답 하나를 고릅니다.</p></div><div><label for="training-split">사용할 데이터</label><select id="training-split"><option value="train">학습용 · 가중치 업데이트</option><option value="eval">평가용 · 학습에서 제외</option></select></div></div>
            <label for="training-expected">보조 질문 정답 (JSON)</label><textarea id="training-expected" rows="4" spellcheck="false" aria-describedby="training-expected-help">{}</textarea><p id="training-expected-help" class="field-note">확인한 질문만 남기세요. Choice는 선택지 이름, Score는 단계 번호, Noul은 true/false입니다. action은 위에서 선택합니다. 예: {"danger": 2, "crash_ahead": true}</p>
            <div class="action-row training-actions"><button id="training-approve" type="button" disabled class="button-primary">정답 확인 · 승인 저장</button><button id="training-exclude" type="button" disabled class="button-ghost">이 사례 제외</button></div>
          </div>
        </section>
        <section class="training-step"><h3>2. Laya 파인튜닝 · PyTorch</h3><p id="training-ready" class="field-note">승인된 학습용과 평가용 사례가 각각 1개 이상 필요합니다. 같은 상태는 두 집합에 나눠 넣을 수 없습니다.</p>
          <div class="training-options"><div><label for="training-base">기본 모델</label><select id="training-base"><option value="multilingual">Multilingual · 다국어</option><option value="english">English · 영어</option><option value="typed-decisions">Typed decisions</option></select></div><div><label for="training-epochs">학습 반복 횟수</label><input id="training-epochs" type="number" min="1" max="100" step="1" value="4"></div><div><label for="training-encoder-lr">인코더 학습률</label><input id="training-encoder-lr" type="number" min="0.00000001" max="0.01" step="any" value="0.000025"></div><div><label for="training-head-lr">헤드 학습률</label><input id="training-head-lr" type="number" min="0.00000001" max="0.01" step="any" value="0.0001"></div></div>
          <label class="check training-freeze"><input id="training-freeze" type="checkbox"><span>인코더 고정 · 판단 헤드만 학습</span></label><div class="action-row training-actions"><button id="training-train" type="button" disabled class="button-primary">새 체크포인트 학습 시작</button></div><p class="field-note">학습 작업은 백그라운드에서 진행합니다. 처음 사용할 기본 모델은 다운로드가 필요할 수 있습니다.</p>
        </section>
      </div><div class="training-results">
        <section class="training-step"><h3>3. MLX 변환 · 평가 · 게임에 적용</h3><p class="field-note">학습한 PyTorch 체크포인트에서 「MLX용으로 변환」을 선택할 수 있습니다. 변환한 체크포인트도 Laya-MLX에서 따로 평가해야 적용할 수 있습니다. 승인한 평가용 사례에서 같은 실행 방식의 기본 모델과 비교합니다.</p><div class="training-active-grid"><div><div id="training-active" class="training-active">Laya (PyTorch) · 기본 모델</div><div class="action-row training-actions"><button id="training-rollback" type="button" disabled class="button-ghost">Laya를 기본 모델로 복원</button></div></div><div><div id="training-active-mlx" class="training-active">Laya-MLX · 기본 모델</div><div class="action-row training-actions"><button id="training-rollback-mlx" type="button" disabled class="button-ghost">Laya-MLX를 기본 모델로 복원</button></div></div></div><div id="training-checkpoints"></div></section>
        <section class="training-step"><h3>작업과 실행 로그</h3><div id="training-jobs" class="training-jobs"><p class="field-note">아직 작업이 없습니다.</p></div></section>
      </div></div>`;
  }

  function setMessage(message, error = false) {
    notice = message;
    noticeError = error;
    renderMessage();
  }
  function renderMessage() {
    if (!$('status')) return;
    $('status').textContent = notice || (loading ? '저장된 학습 데이터를 읽는 중입니다.' : '검토 대기 사례는 학습에 쓰지 않습니다. 승인한 정답만 학습·평가에 사용합니다.');
    $('status').classList.toggle('bad', noticeError);
  }
  function cases() { return snapshot?.dataset?.cases || []; }
  function selectedCase() { return cases().find(item => item.id === selected); }
  function labels(item) { return Object.keys(item?.questions?.action?.criteria || currentGame?.questions?.action?.criteria || {}); }
  function connectedHere() { return status?.connection?.game === currentGame?.id && (!currentVersion || status?.connection?.target?.version === currentVersion); }
  function activeFor(provider) {
    return object(snapshot?.active_by_provider) ? snapshot.active_by_provider[provider] || null : provider === 'laya' ? snapshot?.active : null;
  }
  function activeJob() { return (snapshot?.jobs || []).some(job => ['queued', 'running'].includes(job.status)); }
  function applyButtons() {
    const unavailable = !currentGame?.id || loading || busy || failed;
    const counts = snapshot?.dataset?.counts || {};
    $('collect').disabled = unavailable || !connectedHere();
    $('refresh').disabled = !currentGame?.id || loading || busy;
    $('bookmark-add').disabled = unavailable || !$('bookmark').value || !connectedHere();
    $('train').disabled = unavailable || activeJob() || !(counts.train > 0 && counts.eval > 0);
    $('rollback').disabled = unavailable || activeJob() || !activeFor('laya');
    $('rollback-mlx').disabled = unavailable || activeJob() || !activeFor('laya-mlx');
    $('approve').disabled = unavailable || !selectedCase() || !labels(selectedCase()).length || selectedCase()?.compatible === false;
    $('exclude').disabled = unavailable || !selectedCase();
    document.querySelectorAll('#training-checkpoints button').forEach(button => {
      const checkpoint = (snapshot?.checkpoints || []).find(item => item.id === button.dataset.checkpoint);
      const action = button.dataset.trainingAction;
      button.disabled = unavailable || activeJob() || !checkpoint || checkpoint.compatible === false || !supportedRuntime(checkpoint) || (action === 'apply' && !canApply(checkpoint)) || (action === 'convert' && !canConvert(checkpoint));
    });
  }
  function renderCounts() {
    const counts = snapshot?.dataset?.counts || {};
    $('counts').innerHTML = [['total', '전체 사례'], ['pending', '검토 대기'], ['train', '승인 · 학습용'], ['eval', '승인 · 평가용'], ['excluded', '제외']].map(([key, title]) => `<div><small>${title}</small><strong>${esc(counts[key] ?? 0)}</strong></div>`).join('');
    $('ready').textContent = counts.train > 0 && counts.eval > 0 ? `승인된 학습용 ${counts.train}개 · 평가용 ${counts.eval}개. 같은 상태의 중복 여부와 정답 유효성은 시작할 때 다시 검사합니다.` : '승인된 학습용과 평가용 사례가 각각 1개 이상 필요합니다. 같은 상태는 두 집합에 나눠 넣을 수 없습니다.';
  }
  function renderCases(force = false) {
    const list = cases();
    if (!list.some(item => item.id === selected)) selected = list.find(item => item.review === 'pending')?.id || list[0]?.id || '';
    const options = list.map(item => `<option value="${esc(item.id)}">${esc(reviewNames[item.review] || item.review || '검토 대기')} · ${esc(item.split === 'eval' ? '평가용' : '학습용')} · ${esc(item.reason || item.id)}${item.occurrences > 1 ? ` · ${esc(item.occurrences)}회` : ''}</option>`).join('') || '<option value="">저장된 사례 없음</option>';
    if ($('case').innerHTML !== options) $('case').innerHTML = options;
    $('case').value = selected;
    const item = selectedCase();
    $('review').hidden = !item;
    $('case-empty').hidden = !!item;
    const key = item ? json([currentGame?.id, item.id, item.review, item.split, item.expected, item.compatible, item.incompatible_reason]) : '';
    if (!force && editorKey === key) return;
    editorKey = key;
    if (!item) return;
    const candidate = object(item.expected) && item.review === 'approved' ? item.expected : (item.suggested_expected || item.expected || {});
    const choices = labels(item);
    const suggested = actionLabel(candidate.action);
    const valid = choices.includes(suggested);
    const location = `${Number.isInteger(item.source?.episode) ? ` · ${item.source.episode + 1}번째 게임` : ''}${Number.isInteger(item.source?.step) ? ` · 판단 ${item.source.step}` : ''}`;
    $('case-note').textContent = `${reviewNames[item.review] || '검토 대기'} · ${item.reason || '직접 저장'} · ${item.occurrences || 1}회 수집${location} · ${item.id}${item.compatible === false ? ` · 승인 불가: ${item.incompatible_reason || '현재 질문 설정과 다릅니다.'}` : ''}`;
    $('state').textContent = typeof item.state === 'string' ? item.state : json(item.state);
    const answers = item.model_answer ?? item.decision ?? (item.source?.decision ? {제어_모델: item.source.decision, 비교_모델: item.source.shadow || []} : item.source?.answer ?? item.source);
    $('answer').textContent = json({모델의_답: answers, 기준_종류: `${referenceName(item.source?.reference_kind)}${item.source?.reference_kind ? ` (${item.source.reference_kind})` : ''}`, 기준이_허용한_행동: item.source?.truth?.action ?? [], 기준_정답_제안: item.suggested_expected || {}, 승인한_정답: item.review === 'approved' ? item.expected : '아직 승인하지 않음'});
    $('questions').textContent = json(item.questions);
    $('action').innerHTML = '<option value="">정답 행동을 선택하세요</option>' + choices.map(label => `<option value="${esc(label)}">${esc(currentGame?.learning?.actions?.[label] || label)} · ${esc(label)}</option>`).join('');
    $('action').value = valid ? suggested : '';
    $('split').value = item.split === 'eval' ? 'eval' : 'train';
    const auxiliary = object(candidate) ? {...candidate} : {};
    delete auxiliary.action;
    $('expected').value = json(auxiliary);
  }
  function fmt(value, percent = false) {
    if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
    return percent ? `${(value * 100).toFixed(1)}%` : value.toFixed(2);
  }
  function fmtDate(value) {
    if (!value) return '';
    const date = new Date(typeof value === 'number' ? value * 1000 : value);
    return Number.isFinite(date.getTime()) ? date.toLocaleString('ko-KR') : String(value);
  }
  function metricTable(evaluation, runtime) {
    if (!evaluation) return '<p class="field-note">아직 평가하지 않았습니다.</p>';
    const metrics = [['action_accuracy', '행동 정답률', true], ['accuracy', '전체 질문 정답률', true], ['brier', 'Brier · 낮을수록 좋음'], ['ece', '보정 오차 · 낮을수록 좋음'], ['mean_latency_ms', '평균 응답 시간 (ms)'], ['p95_latency_ms', 'P95 응답 시간 (ms)'], ['inference_failures', '추론 실패 수']];
    return `<div class="training-table-wrap"><table class="training-metrics"><thead><tr><th scope="col">평가용 ${esc(evaluation.eval_count ?? evaluation.after?.rows ?? '—')}개</th><th scope="col">기본 ${esc(providerName(runtime))}</th><th scope="col">새 ${esc(providerName(runtime))}</th></tr></thead><tbody>${metrics.map(([key, title, percent]) => `<tr><th scope="row">${title}</th><td>${esc(fmt(evaluation.before?.[key], percent))}</td><td>${esc(fmt(evaluation.after?.[key], percent))}</td></tr>`).join('')}</tbody></table></div><p class="field-note">${evaluation.held_out === true ? '학습에서 제외한 평가용 사례로 측정했습니다.' : '평가 데이터의 분리 여부를 확인하세요.'} 이 사례 집합의 결과이며 실제 게임 승률을 뜻하지 않습니다.</p><details class="training-evaluation-detail"><summary>질문별 평가 결과 · 상세</summary><pre class="training-pre">${esc(json({기본_모델: evaluation.before, 새_모델: evaluation.after}))}</pre></details>`;
  }
  function renderCheckpoints() {
    for (const [provider, id] of [['laya', 'active'], ['laya-mlx', 'active-mlx']]) {
      const active = activeFor(provider);
      const label = provider === 'laya' ? 'Laya (PyTorch)' : 'Laya-MLX';
      $(id).textContent = active ? `${label} · 적용 중: ${active.checkpoint_id}` : `${label} · 기본 모델 사용 중`;
    }
    $('checkpoints').innerHTML = (snapshot?.checkpoints || []).slice().reverse().map(item => {
      const runtime = runtimeOf(item), label = providerName(runtime), allowed = canApply(item);
      const reasons = item.reasons || item.evaluation?.gate?.reasons || [];
      const conversion = canConvert(item) ? `<button type="button" class="button-secondary" data-training-action="convert" data-checkpoint="${esc(item.id)}">MLX용으로 변환 · FP16</button>` : '';
      const source = item.source_checkpoint_id ? `<p class="field-note">변환 원본: ${esc(item.source_checkpoint_id)} · 원본의 평가·적용 승인은 승계하지 않습니다.</p>` : '';
      return `<article class="training-checkpoint"><div class="training-checkpoint-title"><b>${esc(item.id)}</b><span class="${allowed ? 'good' : 'warn'}">${allowed ? '적용 가능' : item.compatible === false ? '현재 게임·버전과 호환 안 됨' : '이 실행 방식에서 평가 필요'}</span></div><p class="field-note training-runtime">${esc(label)}${runtime === 'laya' ? ' · PyTorch' : ' · MLX'} · ${esc(item.base || '기본 모델')} · ${esc(fmtDate(item.created_at))}</p>${source}${metricTable(item.evaluation, runtime)}${reasons.length ? `<p class="training-warning">${esc(Array.isArray(reasons) ? reasons.join(' · ') : reasons)}</p>` : ''}<div class="action-row training-actions">${conversion}<button type="button" class="button-secondary" data-training-action="evaluate" data-checkpoint="${esc(item.id)}">${esc(label)} 기본 모델과 비교 평가</button><button type="button" class="button-primary" data-training-action="apply" data-checkpoint="${esc(item.id)}" ${allowed ? '' : 'disabled'}>${esc(label)}로 전환해 적용</button></div></article>`;
    }).join('') || '<p class="training-empty">아직 학습한 체크포인트가 없습니다.</p>';
  }
  function renderJobs() {
    $('jobs').innerHTML = (snapshot?.jobs || []).slice().reverse().map(job => {
      const progress = Number.isFinite(job.progress) ? Math.max(0, Math.min(1, job.progress)) : null;
      return `<article class="training-job"><div><b>${esc(job.kind === 'train' ? '파인튜닝' : job.kind === 'evaluate' ? '체크포인트 평가' : job.kind === 'convert' ? 'MLX 변환' : job.kind)}</b><span class="${job.status === 'failed' ? 'bad' : ['succeeded', 'completed'].includes(job.status) ? 'good' : 'warn'}">${esc(jobNames[job.status] || job.status)}</span></div><p class="field-note">${esc(job.checkpoint_id || job.id)} · ${esc(job.stage || '')}${progress !== null ? ` · ${Math.round(progress * 100)}%` : ''}</p>${job.error ? `<p class="training-warning">${esc(job.error)}</p>` : ''}${job.log_tail?.length ? `<details ${job.status === 'failed' ? 'open' : ''}><summary>실행 로그</summary><pre class="training-pre">${esc(Array.isArray(job.log_tail) ? job.log_tail.join('\n') : job.log_tail)}</pre></details>` : ''}</article>`;
    }).join('') || '<p class="field-note">아직 학습·평가 작업이 없습니다.</p>';
  }
  function render() {
    renderCounts(); renderCases(); renderCheckpoints(); renderJobs(); applyButtons(); renderMessage();
  }
  function renderBookmarks() {
    const matches = connectedHere();
    const rows = matches ? (status?.rows || []).filter(row => Number.isInteger(row.decision?.call_id)) : [];
    const signature = json([currentGame?.id, currentVersion, rows.map(row => [row.decision.call_id, row.step, row.decision.action])]);
    if (bookmarkSignature === signature) return;
    bookmarkSignature = signature;
    const previous = $('bookmark').value;
    $('bookmark').innerHTML = rows.map(row => `<option value="${row.decision.call_id}">판단 ${esc(row.step)} · ${esc(row.decision.provider || '')} · ${esc(row.decision.action || row.decision.status)}</option>`).join('') || '<option value="">현재 게임의 판단 기록 없음</option>';
    $('bookmark').value = rows.some(row => String(row.decision.call_id) === previous) ? previous : rows.length ? String(rows[0].decision.call_id) : '';
  }
  async function refresh(force = false) {
    const game = currentGame?.id;
    if (!game || loading || (!force && (busy || Date.now() - lastFetch < 3000))) return;
    const epoch = generation;
    loading = true; lastFetch = Date.now(); applyButtons(); renderMessage();
    try {
      const result = await config.api(`/api/training?game=${encodeURIComponent(game)}${currentVersion ? `&version=${encodeURIComponent(currentVersion)}` : ''}`);
      if (epoch !== generation) return;
      if (result.game !== game || (result.version && currentVersion && result.version !== currentVersion)) throw new Error('서버 응답의 게임·버전이 현재 선택과 다릅니다. 새로고침하세요.');
      snapshot = result; failed = false;
      if (noticeError) { notice = ''; noticeError = false; }
      render();
    } catch (error) {
      if (epoch === generation) { failed = true; setMessage(`학습 데이터를 읽지 못했습니다: ${error.message}`, true); }
    } finally {
      if (epoch === generation) { loading = false; applyButtons(); renderMessage(); }
      else { loading = false; refresh(true); }
    }
  }
  async function mutate(endpoint, payload, message) {
    if (!currentGame?.id || busy || failed) return;
    const game = currentGame.id, epoch = generation;
    const requestedProvider = runtimeOf(snapshot?.checkpoints?.find(item => item.id === payload.checkpoint_id));
    busy = true; applyButtons(); setMessage('요청을 처리하고 있습니다.');
    try {
      const result = await config.api(`/api/training/${endpoint}`, {game, ...(currentVersion ? {version: currentVersion} : {}), ...payload});
      if (epoch === generation) {
        if (endpoint === 'apply') config.onApplied?.({game, version: currentVersion, checkpoint_id: payload.checkpoint_id, provider: result?.provider || requestedProvider});
        setMessage(message); editorKey = ''; await refresh(true);
      }
    } catch (error) { if (epoch === generation) setMessage(error.message, true); }
    finally { busy = false; applyButtons(); }
  }
  function review(approval) {
    const item = selectedCase();
    if (!item) return;
    if (approval && item.compatible === false) return setMessage(`이 사례는 승인할 수 없습니다: ${item.incompatible_reason || '현재 질문 설정과 다릅니다.'}`, true);
    let expected = {};
    try {
      if (approval) {
        expected = JSON.parse($('expected').value || '{}');
        if (!object(expected)) throw new Error('보조 질문 정답은 JSON 객체로 작성하세요.');
        const action = $('action').value;
        if (!labels(item).includes(action)) throw new Error('확인한 정답 행동을 선택하세요.');
        expected.action = action;
      }
      return mutate('review', {case_id: item.id, expected, split: $('split').value, review: approval ? 'approved' : 'excluded'}, approval ? '확인한 정답을 승인해 저장했습니다.' : '이 사례를 학습·평가에서 제외했습니다.');
    } catch (error) { setMessage(`승인할 수 없습니다: ${error.message}`, true); }
  }
  function train() {
    const epochs = Number($('epochs').value), encoder = Number($('encoder-lr').value), head = Number($('head-lr').value);
    if (!Number.isInteger(epochs) || epochs < 1 || epochs > 100 || !Number.isFinite(encoder) || encoder < 1e-8 || encoder > .01 || !Number.isFinite(head) || head < 1e-8 || head > .01) return setMessage('반복 횟수는 1~100의 정수, 학습률은 0.00000001~0.01의 수로 입력하세요.', true);
    return mutate('train', {base: $('base').value, epochs, encoder_lr: encoder, head_lr: head, freeze_encoder: $('freeze').checked}, '파인튜닝 작업을 시작했습니다. 작업 로그에서 진행 상황을 확인하세요.');
  }
  function init(options) {
    if (config || !document.getElementById('laya-training')) return;
    config = options;
    document.getElementById('laya-training').innerHTML = markup();
    $('case').addEventListener('change', () => { selected = $('case').value; renderCases(true); applyButtons(); });
    $('bookmark').addEventListener('change', applyButtons);
    $('refresh').addEventListener('click', () => refresh(true));
    $('collect').addEventListener('click', () => mutate('collect', {}, '최근 실패 사례를 수집했습니다.'));
    $('bookmark-add').addEventListener('click', () => mutate('collect', {call_id: Number($('bookmark').value)}, '선택한 판단을 검토 대기에 저장했습니다.'));
    $('approve').addEventListener('click', () => review(true));
    $('exclude').addEventListener('click', () => review(false));
    $('train').addEventListener('click', train);
    $('rollback').addEventListener('click', () => mutate('rollback', {provider: 'laya'}, '이 게임의 Laya를 기본 모델로 복원했습니다.'));
    $('rollback-mlx').addEventListener('click', () => mutate('rollback', {provider: 'laya-mlx'}, '이 게임의 Laya-MLX를 기본 모델로 복원했습니다.'));
    $('checkpoints').addEventListener('click', event => {
      const button = event.target.closest('[data-training-action]');
      if (!button || button.disabled) return;
      const checkpoint = snapshot?.checkpoints?.find(item => item.id === button.dataset.checkpoint);
      if (!checkpoint || checkpoint.compatible === false || !supportedRuntime(checkpoint)) return;
      const endpoint = button.dataset.trainingAction;
      if (!['convert', 'evaluate', 'apply'].includes(endpoint) || (endpoint === 'apply' && !canApply(checkpoint)) || (endpoint === 'convert' && !canConvert(checkpoint))) return;
      const label = providerName(runtimeOf(checkpoint));
      const messages = {convert: 'MLX 변환 작업을 시작했습니다. 변환 후 Laya-MLX 비교 평가를 실행하세요.', evaluate: `${label} 기본 모델과 체크포인트의 비교 평가를 시작했습니다.`, apply: `체크포인트를 적용하고 제어 모델을 ${label}로 전환했습니다. 다음 ${label} 판단부터 새 가중치를 사용합니다.`};
      mutate(endpoint, {checkpoint_id: checkpoint.id, ...(endpoint === 'convert' ? {dtype: 'float16'} : {})}, messages[endpoint]);
    });
  }
  function update(data, game) {
    if (!config) return;
    status = data;
    game = game || config.getGame?.();
    const version = config.getVersion?.();
    if (game?.id !== currentGame?.id || version !== currentVersion) {
      generation++; currentGame = game; currentVersion = version; snapshot = null; selected = ''; editorKey = ''; lastFetch = 0; notice = ''; noticeError = false; failed = false;
      $('game').textContent = game?.id ? `${game.name || game.id}${version ? ` · ${version}` : ''}` : '게임 선택 전';
      render();
    } else currentGame = game;
    renderBookmarks(); applyButtons(); refresh();
  }
  window.LayaTraining = {init, update, refresh};
})();
