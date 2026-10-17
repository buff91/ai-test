/* Read-only explanation of the request, returned answers, and game-side effect.
 * Public contract: DecisionLearning.init(); DecisionLearning.update(status, game).
 */
(() => {
  'use strict';
  const $ = name => document.getElementById(`learning-${name}`);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const own = (object, key) => object != null && Object.prototype.hasOwnProperty.call(object, key);
  const number = value => typeof value === 'number' && Number.isFinite(value);
  const probability = value => number(value) && value >= 0 && value <= 1;
  const pct = value => probability(value) ? `${(value * 100).toFixed(1)}%` : '응답에 없음';
  const num = value => number(value) ? value.toFixed(3).replace(/\.?0+$/, '') : '응답에 없음';
  const ms = value => number(value) ? `${Math.round(value).toLocaleString()} ms` : '측정값 없음';
  const json = value => typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  const note = (text, kind = '') => `<p class="learning-note ${kind}">${esc(text)}</p>`;
  const detail = (key, title, content) => `<details class="learning-details" data-learning-detail="${esc(key)}"><summary>${esc(title)}</summary>${content}</details>`;
  const raw = (key, title, value) => detail(key, title, `<pre class="learning-pre">${esc(json(value))}</pre>`);
  const fact = (label, value) => `<div class="learning-metric"><span>${esc(label)}</span><b>${esc(value)}</b></div>`;
  let current = null;
  let selected = 'latest';
  let identity = '';
  let boundSelect = null;
  let kept = new Map();

  function rowKey(row) {
    if (row.decision?.call_id != null) return `call:${row.decision.call_id}`;
    // Old recordings have no call id. Keep the observation in the identity so a
    // reset to step 1 cannot silently turn a selected old record into a new one.
    return `row:${JSON.stringify([row.episode, row.step, row.request?.state ?? row.description ?? row.state, row.decision?.provider, row.decision?.model_ms])}`;
  }

  function put(name, html) {
    const node = $(name);
    if (!node || node.innerHTML === html) return;
    const opened = new Set(Array.from(node.querySelectorAll('details[open]')).map(el => el.dataset.learningDetail));
    const scroll = Array.from(node.querySelectorAll('pre')).map(el => [el.scrollTop, el.scrollLeft]);
    node.innerHTML = html;
    node.querySelectorAll('details').forEach(el => { if (opened.has(el.dataset.learningDetail)) el.open = true; });
    node.querySelectorAll('pre').forEach((el, i) => { if (scroll[i]) [el.scrollTop, el.scrollLeft] = scroll[i]; });
  }

  function init() {
    const select = $('record');
    if (!select || select === boundSelect) return;
    boundSelect = select;
    select.addEventListener('change', () => {
      selected = select.value;
      if (current) render(current.data, current.game);
    });
  }

  function update(data, game) {
    init();
    data = data || {};
    const generation = data.generation ?? data.connection?.generation;
    const nextIdentity = JSON.stringify([game?.id || data.connection?.game || '', generation ?? data.session ?? '', data.connection?.interface || '', data.connection?.target || null]);
    if (identity !== nextIdentity) {
      identity = nextIdentity;
      selected = 'latest';
      kept = new Map();
    }
    current = {data, game: game || {}};
    if (data.connection?.status !== 'connected' && !(data.rows || []).length) {
      selected = 'latest';
      kept.clear();
    }
    for (const row of data.rows || []) kept.set(rowKey(row), row);
    // Retain only visible rows and the user's pinned record.
    const visible = new Set((data.rows || []).map(rowKey));
    for (const key of kept.keys()) if (key !== selected && !visible.has(key)) kept.delete(key);
    render(data, game || {});
  }

  function selectRecord(data) {
    const rows = data.rows || [];
    const select = $('record');
    if (!select) return selected === 'latest' ? rows[0] : kept.get(selected);
    let options = '<option value="latest">최신 판단 따라가기</option>';
    const entries = rows.map(row => [rowKey(row), row]);
    if (selected !== 'latest' && kept.has(selected) && !entries.some(([key]) => key === selected)) entries.push([selected, kept.get(selected)]);
    for (const [key, row] of entries) {
      const status = effectStatus(row)[0];
      options += `<option value="${esc(key)}">${esc(`판단 ${row.step} · ${(row.decision?.provider || '모델').toUpperCase()} · ${status}`)}</option>`;
    }
    if (select.innerHTML !== options) select.innerHTML = options;
    if (selected !== 'latest' && !kept.has(selected)) selected = 'latest';
    select.value = selected;
    return selected === 'latest' ? rows[0] : kept.get(selected);
  }

  function callFor(decision, data) {
    if (decision?.call_id == null) return null;
    return (data.calls || []).find(call => call.id === decision.call_id) || null;
  }

  function newCall(data, row) {
    if (selected !== 'latest' || data.connection?.status !== 'connected') return null;
    const call = (data.calls || []).find(item => item.role === 'live' || item.role === 'comparison');
    if (!call) return null;
    if (row?.decision?.call_id === call.id) return null;
    if (row?.decision?.call_id != null && call.id < row.decision.call_id) return null;
    if (row && call.step != null && call.step <= row.step) return null;
    if (row && call.step == null && call.finished_at) return null;
    return call;
  }

  function render(data, game) {
    const row = selectRecord(data);
    const call = newCall(data, row);
    const linkedCall = call || callFor(row?.decision, data);
    const request = call?.request || row?.request || linkedCall?.request;
    const recorded = !!row && !call;
    const questions = request?.questions || game.questions || {};
    const actual = request && own(request, 'state');
    const input = actual ? request.state : recorded ? (row.description || row.state) : (data.observed?.description || data.observed?.state);
    const busy = !!call && !call.finished_at;
    const preparing = selected === 'latest' && !!data.busy && !call && (data.phase?.name === '모델 준비' || !recorded);
    const preparationError = selected === 'latest' && !data.busy && (data.preparations || []).find(p => p.status === 'error');
    let status = call ? (busy ? `실제 요청 · ${(call.provider || '').toUpperCase()} 호출 중` : `호출 ${call.status === 'ok' ? '완료 · 게임 처리 대기' : '실패'}`) :
      recorded ? `판단 ${row.step} · ${selected === 'latest' ? '최신 기록' : '선택한 기록 고정'} · ${effectStatus(row)[0]}` :
      preparing ? '모델 준비 중 · 첫 호출에는 로딩 시간이 걸릴 수 있습니다.' : data.connection?.status === 'connected' ? '입력 미리보기 · 판단을 실행하면 실제 요청과 응답이 표시됩니다.' : '연결 전 · 질문 설정 미리보기';
    if (preparing && recorded) status = `모델 준비 중 · 아래는 직전 완료 기록 (판단 ${row.step})`;
    if (preparationError && !call) status = `모델 준비 실패 · ${preparationError.error || data.error || '오류 정보 없음'}${recorded ? ' · 아래는 직전 완료 기록' : ''}`;
    if (!recorded && !call && data.options?.provider === 'jev' && !data.key_set) status += ' · Jev API 키 필요';
    if ($('status')) $('status').textContent = status;
    put('input', `${note(actual ? '실제 요청의 state' : recorded ? '기록된 관찰 · 요청 스냅샷 없음' : '관찰 입력 미리보기', 'learning-label')}
      ${input == null ? note('게임을 연결하면 상태 설명을 여기에서 읽을 수 있습니다.', 'learning-muted') : `<pre class="learning-pre">${esc(json(input))}</pre>`}
      ${note('모델에 보내는 것은 state와 질문(instructions·criteria)입니다. 이 프로젝트는 상태 설명이 있으면 설명을, 없으면 상태 데이터를 보냅니다.')}
      ${note('채점용 정답(truth), 이전 대화·판단 기록, 화면 PNG 자체는 이 요청에 포함하지 않습니다.', 'learning-muted')}`);
    put('questions', renderQuestions(questions, !!request?.questions));
    put('output', call ? renderCallOutput(call, data, questions) : row ? renderOutputs(row, data, questions) : preparationError ? note(`모델 준비 실패: ${preparationError.error || data.error || '오류 정보 없음'}`, 'learning-warning') : note(preparing ? '초기화를 마치고 모델이 응답하면 선택 확률과 평가값이 표시됩니다.' : '아직 모델 응답이 없습니다. 미리보기는 실제 판단 결과가 아닙니다.', 'learning-muted'));
    put('effect', call ? note(busy ? '모델 응답 대기 중입니다. 아직 이 호출의 최종 후보와 입력 적용 결과가 없습니다.' : '응답을 받았습니다. 게임 입력 결과가 기록될 때까지 기다리는 중입니다.') : row ? renderEffect(row) : note('Choice 결과 → 보조 규칙 → 최종 후보 → 입력 결과를 차례로 확인합니다.', 'learning-muted'));
    put('timing', renderTiming(call ? null : row, call, data, game));
  }

  function renderQuestions(questions, snapshot) {
    const roles = {
      choice: ['행동 선택 · Choice', '이 질문에 제시된 선택지 중 하나를 고릅니다. action 질문의 선택은 게임 입력 후보가 됩니다.'],
      score: ['단계 평가 · Score', '0부터 시작하는 각 단계에 확률을 부여하고, 단계 번호의 가중 평균을 반환합니다.'],
      noul: ['참 확률 · Noul', '질문의 문장이 참일 확률 P(true)를 0~1로 반환합니다.'],
    };
    const blocks = Object.entries(questions).map(([id, q]) => {
      const [title, meaning] = roles[q.type] || [q.type || '질문', '질문 원문과 실제 응답을 확인하세요.'];
      let criteria = '';
      if (q.type === 'choice') criteria = `<dl class="learning-options">${Object.entries(q.criteria || {}).map(([key, value]) => `<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join('')}</dl>`;
      else if (q.type === 'score') criteria = `<dl class="learning-options">${(q.criteria || []).map((value, i) => `<dt>단계 ${i}</dt><dd>${esc(value)}</dd>`).join('')}</dl>`;
      return `<article class="learning-question"><b>${esc(title)} <span class="learning-muted">${esc(id)}</span></b>${note(meaning)}${detail(`question:${id}`, '질문·선택 기준 원문 보기', `<p>${esc(q.instructions || 'instructions 없음')}</p>${criteria}`)}</article>`;
    }).join('');
    return note(snapshot ? '이 기록에 실제로 전달한 질문' : '현재 게임 설정의 질문 · 실제 요청 스냅샷은 아직 없음', 'learning-label') +
      (blocks || note('질문 설정이 없습니다.', 'learning-muted')) +
      note('각 질문은 같은 상태를 따로 평가합니다. danger → action 순서의 내부 추론 기록이 아닙니다. 보조 규칙을 켠 경우 프로그램이 평가값을 읽어 행동을 바꿀 수 있습니다.', 'learning-muted');
  }

  function bar(label, value, chosen = false) {
    const known = probability(value);
    return `<div class="learning-probability${chosen ? ' selected' : ''}"><span>${esc(label)}${chosen ? ' · 선택' : ''}</span>${known ? `<progress max="1" value="${value}" aria-label="${esc(label)} 확률"></progress>` : '<span class="learning-muted">—</span>'}<b>${esc(pct(value))}</b></div>`;
  }

  function renderAnswer(id, q, answer, decision) {
    answer = answer || {};
    const title = `<b>${esc(id)} · ${esc({choice: 'Choice', score: 'Score', noul: 'Noul'}[q.type] || q.type)}</b>`;
    if (q.type === 'choice') {
      const picked = answer.choice ?? (id === 'action' ? decision.action : null);
      const probs = answer.probabilities || (id === 'action' ? decision.probabilities : null) || {};
      const keys = Array.from(new Set([...Object.keys(q.criteria || {}), ...Object.keys(probs)]));
      const selectedProbability = own(probs, picked) ? probs[picked] : (id === 'action' ? decision.probability : null);
      return `<div class="learning-result">${title}${fact('선택', picked ?? '응답에 없음')}${fact('선택 확률', pct(selectedProbability))}${keys.map(key => bar(key, probs[key], key === picked)).join('')}${fact('분포 집중도 · confidence', num(answer.confidence ?? (id === 'action' ? decision.confidence : null)))}</div>`;
    }
    if (q.type === 'score') {
      const probs = answer.probabilities;
      const levels = (q.criteria || []).map((_, i) => String(i));
      const complete = levels.length > 0 && probs && levels.every(key => probability(probs[key]));
      const weighted = complete ? levels.reduce((sum, key) => sum + Number(key) * probs[key], 0) : null;
      let explain = note('단계별 확률이 응답에 없어 가중 합을 재구성할 수 없습니다.', 'learning-muted');
      if (complete) explain = note(`${levels.map(key => `${key} × ${num(probs[key])}`).join(' + ')} = ${num(weighted)} (반환된 확률로 재계산; 반올림 차이 가능)`);
      else if (probs && Object.keys(probs).length) explain = note('일부 단계 확률이 없거나 유효하지 않아 전체 가중 합을 계산하지 않습니다.', 'learning-muted');
      return `<div class="learning-result">${title}${fact('반환 점수', `${num(answer.score)}${levels.length ? ` / ${levels.length - 1}` : ''}`)}${probs ? levels.map(key => bar(`단계 ${key}`, probs[key])).join('') : ''}${explain}${fact('분포 집중도 · confidence', num(answer.confidence))}</div>`;
    }
    if (q.type === 'noul') return `<div class="learning-result">${title}${bar('참일 확률 · P(true)', answer.noul)}${note('이 실습은 50%를 기준으로 참/거짓 일치 여부를 채점합니다. 행동을 바꾸는 임계값은 별도의 보조 규칙에서 정합니다.', 'learning-muted')}</div>`;
    return raw(`answer:${id}`, `${id} 응답`, answer);
  }

  function truncation(usage) {
    if (!usage || typeof usage !== 'object') return false;
    return Object.entries(usage).some(([key, value]) => /truncat|tokens.*dropped|dropped.*tokens/.test(key) && (value === true || (number(value) && value > 0) || (Array.isArray(value) && value.length > 0)) || (value && typeof value === 'object' && truncation(value)));
  }

  function renderProvider(decision, role, questions, call) {
    const d = decision || {};
    const provider = (d.provider || call?.provider || '모델').toUpperCase();
    let body = `<div class="learning-label">${esc(role)} · ${esc(provider)}</div>`;
    if (d.status !== 'ok') {
      const text = d.status === 'timeout' ? '대기 시간 초과 · 이 기록에는 응답이 없습니다.' : d.status === 'running' ? '호출 중 · 아직 반환된 확률이나 평가값이 없습니다.' : `호출 실패 · ${d.error || d.status || '오류 정보 없음'}`;
      body += note(text, 'learning-warning');
    } else {
      body += Object.entries(questions).map(([id, q]) => renderAnswer(id, q, d.answers?.[id], d)).join('');
    }
    const model = d.model || call?.routed_model;
    body += fact('응답 모델 식별자', model ? json(model) : '응답 정보 없음');
    const routing = d.routing || call?.routing;
    const checkpoint = d.checkpoint || call?.checkpoint;
    if (checkpoint != null) body += fact('체크포인트', typeof checkpoint === 'string' ? checkpoint : json(checkpoint));
    const usage = d.usage || call?.usage;
    if (truncation(usage)) body += note('입력 일부 잘림: 응답의 usage에 잘린 입력이 보고되었습니다. 모델이 원문 전체를 처리한 것으로 해석하지 마세요.', 'learning-warning');
    if (routing != null) body += raw(`routing:${provider}:${role}`, '모델 경로 선택 · routing', routing);
    if (checkpoint != null) body += raw(`checkpoint:${provider}:${role}`, '실제 체크포인트', checkpoint);
    if (usage != null) body += raw(`usage:${provider}:${role}`, '입력 처리량 · usage', usage);
    if (d.answers) body += raw(`response:${provider}:${role}`, '응답 원문', d.answers);
    return `<article class="learning-card">${body}</article>`;
  }

  function renderOutputs(row, data, questions) {
    const blocks = [renderProvider(row.decision, row.comparison ? '비교 실험 · 기준 모델' : '제어 모델', questions, callFor(row.decision, data))];
    for (const d of row.shadow || []) blocks.push(renderProvider(d, '비교 모델 · 기록만', questions, callFor(d, data)));
    const graded = row.grade?.action;
    let gradeText = '이 기록의 기준 행동 일치 여부: 채점 정보 없음';
    if (graded && !graded.status && typeof graded.correct === 'boolean') gradeText = `이 기록의 기준 행동 일치 여부: ${graded.correct ? '일치' : '불일치'} · 기준 ${Array.isArray(graded.truth) ? graded.truth.join(', ') : graded.truth}`;
    return blocks.join('') + note('선택 확률은 선택지에 배분한 값, confidence는 분포 집중도, 기준 행동 일치율은 여러 기록을 채점한 결과입니다. 서로 다른 수치이며 높은 확률이 정답을 보장하지 않습니다. confidence 계산식은 런타임마다 다를 수 있습니다.', 'learning-muted') + note(gradeText) +
      (row.reference_kind === 'instructional_rules' ? note('이 기준은 게임의 교육용 규칙입니다. 모델의 정답 보장이나 일반 성능 평가가 아닙니다.', 'learning-muted') : '') +
      (row.reference_kind === 'sampled_next_second_search' ? note('관찰 순간의 상태를 칸·박자 단위로 바꿔 탐색한 교육용 기준입니다. 실제 게임의 연속 이동·재사용 대기시간·입력 시점에 따른 최적 행동을 보장하지 않습니다.', 'learning-muted') : '');
  }

  function renderCallOutput(call, data, questions) {
    const calls = (data.calls || []).filter(c => c.id === call.id || (call.step != null && c.step === call.step));
    return calls.map(c => renderProvider({provider: c.provider, status: c.finished_at ? c.status : 'running', action: c.action, answers: c.response, confidence: c.confidence, error: c.error}, c.role === 'warmup' ? '모델 준비 · 게임 입력 없음' : c.role === 'live' ? (data.activity === 'compare' ? '비교 실험 · 기준 모델' : '제어 모델') : '비교 모델 · 기록만', questions, c)).join('');
  }

  function effectStatus(row) {
    if (row.cancelled) return ['입력 취소', '중지 요청으로 입력을 보내지 않았습니다.'];
    if (row.stale) return ['입력 취소', row.error || '관찰이 오래되었거나 게임 상황이 바뀌어 입력을 취소했습니다.'];
    if (row.decision?.status !== 'ok') return ['호출 실패', row.decision?.error || '모델 응답이 실패하여 입력을 보내지 않았습니다.'];
    if (row.error) return ['입력 실패', row.error];
    if (row.input_trace?.result?.dry_run) return ['입력 미리보기', '연결부가 미리보기를 처리했습니다. 실제 키보드·마우스 입력은 보내지 않았습니다.'];
    const trace = row.input_trace;
    if (trace?.connector === 'kbm' && trace.result?.application_confirmed !== true) {
      if (trace.result?.input_sent) return ['키마 입력 전송 · 게임 적용 미확인', '운영체제에 키보드·마우스 입력을 보냈습니다. 게임이 이 행동을 받아 적용했는지는 별도 확인이 필요합니다.'];
      if (row.applied) return ['키마 처리 완료 · 게임 적용 미확인', '키마 연결부가 입력 계획을 처리했습니다. 이 기록만으로 실제 게임의 행동 적용을 확인하지 않습니다.'];
    }
    if (row.applied) return ['게임 적용 확인', '게임 연결부가 이 행동의 적용을 보고했습니다.'];
    if (row.analysis_only) return [row.comparison ? '비교 판단만 기록' : '판단만 기록', '게임에 입력을 보내지 않았습니다.'];
    return ['입력 미적용', '게임 연결부가 적용을 보고하지 않았습니다.'];
  }

  function renderEffect(row) {
    const policy = row.policy || {};
    const override = policy.override;
    const [label, reason] = effectStatus(row);
    let policyText = override ? `${override.rule}: ${override.from} → ${override.to}` : policy.escalation ? `추가 모델 ${policy.escalation.provider}: ${policy.escalation.action || policy.escalation.status}` : policy.enabled ? '행동 교체 없음' : '꺼짐';
    if (!policy.enabled && policy.fired?.length) policyText += ` · 조건 충족만 기록 (${policy.fired.join(', ')})`;
    let body = `<div class="learning-flow">${fact('1. 모델의 Choice', row.decision?.action ?? '응답 없음')}${fact('2. 보조 규칙', policyText)}${fact('3. 최종 입력 후보', row.action ?? '후보 없음')}${fact('4. 입력 결과', label)}</div>${note(reason)}`;
    const trace = row.input_trace;
    body += trace ? raw('input-command', '인터페이스가 기록한 전송 명령', trace.command ?? '전송한 명령 없음') + raw('input-result', '인터페이스 처리 응답', trace.result) :
      note('전송 명령 기록이 없어 실제 키·클릭이나 Mod 명령을 재구성하지 않습니다.', 'learning-muted');
    if (row.after_state == null) return body + note('아직 사후 관찰이 없습니다. 현재 게임 화면을 이 판단의 결과로 연결하지 않습니다.', 'learning-muted');
    const before = row.state;
    const after = row.after_state;
    if (!before || typeof before !== 'object' || !after || typeof after !== 'object') return body + raw('state-change', '관찰 전후 원문', {before, after});
    const changes = [...new Set([...Object.keys(before), ...Object.keys(after)])].filter(key => JSON.stringify(before[key]) !== JSON.stringify(after[key]));
    body += note(changes.length ? `관찰 전후 바뀐 항목 ${changes.length}개` : '기록된 관찰 전후 상태에 바뀐 항목이 없습니다.', 'learning-label');
    body += changes.map(key => `<div class="learning-change"><b>${esc(key)}</b><span>${esc(own(before, key) ? json(before[key]) : '(없음)')} → ${esc(own(after, key) ? json(after[key]) : '(없음)')}</span></div>`).join('');
    body += note('이 차이는 관찰 전후의 변화입니다. 실시간 진행이나 외부 입력도 섞일 수 있어 모두 모델 행동의 효과라고 단정할 수 없습니다.', 'learning-muted');
    return body;
  }

  function renderTiming(row, call, data, game) {
    let body = '';
    if (call) {
      const elapsed = number(data.now) && number(call.started_at) ? Math.max(0, (data.now - call.started_at) * 1000) : null;
      body += fact(call.finished_at ? '호출 완료 시간' : '현재 호출 경과', ms(call.finished_at ? call.ms : elapsed));
    }
    const preparations = row?.preparations || data.preparations || [];
    if (row) {
      body += fact('준비 시간 · Router/Decider', ms(row.preparation_ms));
      body += fact(row.comparison ? '비교 기준 모델 호출' : '제어 모델 호출', ms(row.decision?.model_ms));
      for (const shadow of row.shadow || []) body += fact(`비교 모델 ${(shadow.provider || '').toUpperCase()} 호출`, ms(shadow.model_ms));
      body += fact('관찰 → 입력 시도', ms(row.observation_to_input_ms));
      body += fact('전체 처리 · loop', ms(row.loop_ms));
      body += fact('실험용 추가 대기', ms(row.decision_delay_ms));
      const over = number(row.decision?.model_ms) && number(game.latency_budget_ms) && row.decision.model_ms > game.latency_budget_ms;
      if (over) body += note(`모델은 응답했지만 목표 ${ms(game.latency_budget_ms)}를 초과했습니다.`, 'learning-warning');
    }
    if (Array.isArray(preparations) && preparations.length) body += detail('preparations', '모델별 준비·캐시 상태', preparations.map(p => `<div class="learning-facts">${fact((p.provider || '모델').toUpperCase(), ms(p.preparation_ms))}${fact('Router 재사용', typeof p.router_cached === 'boolean' ? (p.router_cached ? '예' : '아니요') : '정보 없음')}${p.status ? fact('준비 상태', ({preparing: '준비 중', ready: '준비 완료', error: '준비 실패'})[p.status] || p.status) : ''}${p.error ? note(p.error, 'learning-warning') : ''}</div>`).join(''));
    if (!row && !call) body += note(data.busy ? '모델 준비 중입니다. 호출이 시작되면 경과 시간을 표시합니다.' : '실제 실행 후 측정 시간이 표시됩니다.', 'learning-muted');
    return body + note('모델 호출 시간에는 첫 predict의 모델 로딩이 포함될 수 있습니다. Router/Decider 준비는 별도로 측정합니다. Router 재사용만으로 모델이 이미 로딩되었다고 볼 수 없습니다.', 'learning-muted') +
      note('전체 처리 시간에는 관찰·모델 응답·추가 대기·게임 입력·비교 모델 대기가 포함될 수 있습니다. 겹쳐 실행한 시간은 단순 합계와 다릅니다.', 'learning-muted');
  }

  window.DecisionLearning = {init, update};
})();
