// Behavioral checks for the read-only learning panel; no network or inference.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const nodes = new Map();
for (const name of ['record', 'status', 'input', 'questions', 'output', 'effect', 'timing']) {
  nodes.set(`learning-${name}`, {
    innerHTML: '', textContent: '', value: 'latest', listeners: {},
    addEventListener(event, fn) { this.listeners[event] = fn; },
    querySelectorAll() { return []; },
  });
}
const context = {window: {}, document: {getElementById: id => nodes.get(id)}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../web/learning.js'), 'utf8'), context);
const panel = context.window.DecisionLearning;
const node = name => nodes.get(`learning-${name}`);
const questions = {
  action: {type: 'choice', instructions: 'Pick <attack>', criteria: {attack: 'Attack <target>', wait: 'Wait'}},
  danger: {type: 'score', instructions: 'Danger?', criteria: ['safe', 'critical']},
  incoming: {type: 'noul', instructions: 'Will a hit occur?'},
};
const game = {id: 'party', questions, latency_budget_ms: 100};
function row(id, state = `state ${id}`) {
  return {step: id, episode: 0, request: {state, questions}, state: {hp: 3, t: id},
    decision: {call_id: id, status: 'ok', provider: 'laya', action: 'attack', model_ms: 200,
      probabilities: {attack: 0.8}, answers: {action: {choice: 'attack', probabilities: {attack: 0.8}}, danger: {score: 0.25, probabilities: {'0': 0.75, '1': 0.25}}, incoming: {noul: 0.9}}},
    action: 'attack', applied: true, after_state: {hp: 2, t: id}, loop_ms: 250, preparation_ms: 12,
    preparations: [{provider: 'laya', preparation_ms: 12, router_cached: false, status: 'ok'}]};
}
function status(rows, more = {}) {
  return {session: 'a', now: 20, connection: {status: 'connected', game: 'party'}, rows, calls: [], ...more};
}
panel.init();
const first = row(1, '<script>alert(1)</script>');
panel.update(status([first]), game);
assert.match(node('input').innerHTML, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
assert.doesNotMatch(node('input').innerHTML, /<script>/);
assert.match(node('questions').innerHTML, /Pick &lt;attack&gt;/);
assert.match(node('output').innerHTML, /80\.0%/);
assert.match(node('output').innerHTML, /wait<\/span><span class="learning-muted">—<\/span><b>응답에 없음/);
assert.match(node('output').innerHTML, /0 × 0\.75 \+ 1 × 0\.25 = 0\.25/);
assert.match(node('output').innerHTML, /90\.0%/);
assert.match(node('effect').innerHTML, /hp/);
assert.doesNotMatch(node('effect').innerHTML, /<b>t<\/b>/);
assert.match(node('timing').innerHTML, /목표 100 ms를 초과/);

// Pinning an old row survives polling and even eviction from the server window.
node('record').value = 'call:1';
node('record').listeners.change();
panel.update(status([row(2)]), game);
assert.equal(node('record').value, 'call:1');
assert.match(node('status').textContent, /선택한 기록 고정/);
assert.match(node('input').innerHTML, /alert\(1\)/);
node('record').value = 'latest';
node('record').listeners.change();
assert.match(node('input').innerHTML, /state 2/);

// A new live call shows its own actual request, with no invented answer values.
const active = {id: 3, step: 3, role: 'live', provider: 'laya', started_at: 18, finished_at: null, request: {state: 'new pending', questions}};
panel.update(status([row(2)], {calls: [active], busy: true}), game);
assert.match(node('input').innerHTML, /new pending/);
assert.match(node('status').textContent, /호출 중/);
assert.doesNotMatch(node('output').innerHTML, /80\.0%|value="0"/);
assert.match(node('timing').innerHTML, /2,000 ms/);

// Incomplete score distributions remain incomplete, and usage truncation warns.
const partial = row(4);
partial.decision.answers.danger.probabilities = {'0': 0.75};
partial.decision.usage = {truncated: true, truncated_questions: ['danger']};
partial.cancelled = true;
partial.analysis_only = true;
partial.applied = false;
partial.after_state = null;
panel.update(status([partial]), game);
assert.match(node('output').innerHTML, /전체 가중 합을 계산하지 않습니다/);
assert.doesNotMatch(node('output').innerHTML, /= 0\.75/);
assert.match(node('output').innerHTML, /입력 일부 잘림/);
assert.match(node('effect').innerHTML, /입력 취소/);
assert.match(node('effect').innerHTML, /아직 사후 관찰이 없습니다/);

// A failed analysis is a failed call, never a successful analysis-only result.
const failed = row(5);
failed.analysis_only = true;
failed.applied = false;
failed.decision.status = 'error';
failed.decision.error = '<network error>';
panel.update(status([failed]), game);
assert.match(node('status').textContent, /호출 실패/);
assert.match(node('output').innerHTML, /&lt;network error&gt;/);
assert.match(node('effect').innerHTML, /호출 실패/);

// Missing request data must not imply that the current questions were sent.
const missingRequest = row(6);
delete missingRequest.request;
panel.update(status([missingRequest]), game);
assert.match(node('input').innerHTML, /요청 스냅샷 없음/);
assert.match(node('questions').innerHTML, /현재 게임 설정/);

// Session changes and disconnects clear pinned state and response fragments.
node('record').value = 'call:6';
node('record').listeners.change();
panel.update(status([row(1)], {session: 'b'}), game);
assert.equal(node('record').value, 'latest');
panel.update({connection: {status: 'disconnected'}, rows: []}, game);
assert.match(node('status').textContent, /연결 전/);
assert.match(node('output').innerHTML, /아직 모델 응답이 없습니다/);
assert.doesNotMatch(node('input').innerHTML, /state 1/);

// Initialization failure is visible even when no model call/row could be created.
panel.update(status([], {preparations: [{provider: 'laya', status: 'error', error: 'load <failure>', preparation_ms: 5}]}), game);
assert.match(node('status').textContent, /모델 준비 실패/);
assert.match(node('output').innerHTML, /load &lt;failure&gt;/);
// Preparing the next run labels retained previous data rather than claiming it is new.
panel.update(status([row(7)], {busy: true, phase: {name: '모델 준비'}}), game);
assert.match(node('status').textContent, /모델 준비 중 · 아래는 직전 완료 기록/);

// A saved session path appears on first applied run without changing connection generation.
panel.update(status([row(8)], {generation: 10, session: null}), game);
node('record').value = 'call:8';
node('record').listeners.change();
panel.update(status([row(9)], {generation: 10, session: 'newly-saved'}), game);
assert.equal(node('record').value, 'call:8');
assert.match(node('input').innerHTML, /state 8/);
node('record').value = 'latest';
node('record').listeners.change();
const dropped = row(10);
dropped.decision.usage = {state_tokens_dropped: 12};
panel.update(status([dropped], {generation: 10, session: 'newly-saved'}), game);
assert.match(node('output').innerHTML, /입력 일부 잘림/);
// A connector accepting a dry-run mapping does not mean it emitted input.
const preview = row(11);
preview.input_trace = {connector: 'kbm', command: null, result: {ok: true, applied: true, dry_run: true}};
panel.update(status([preview]), game);
assert.match(node('effect').innerHTML, /입력 미리보기/);
assert.match(node('effect').innerHTML, /전송한 명령 없음/);
assert.doesNotMatch(node('effect').innerHTML, /이 행동의 적용을 보고/);
const sent = row(12);
sent.input_trace = {connector: 'kbm', command: [{tap: '1'}], result: {ok: true, applied: true, input_sent: true, application_confirmed: false}};
panel.update(status([sent]), game);
assert.match(node('effect').innerHTML, /키마 입력 전송 · 게임 적용 미확인/);
assert.doesNotMatch(node('effect').innerHTML, /이 행동의 적용을 보고/);
const sampled = row(13);
sampled.reference_kind = 'sampled_next_second_search';
panel.update(status([sampled]), game);
assert.match(node('output').innerHTML, /관찰 순간의 상태를 칸·박자 단위로 바꿔 탐색한 교육용 기준/);
assert.match(node('output').innerHTML, /연속 이동·재사용 대기시간·입력 시점에 따른 최적 행동을 보장하지 않습니다/);
console.log('learning panel: request escaping, record pinning, live calls, probabilities, effects, session reset, preparation and truncation passed');
