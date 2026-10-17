// The live demonstration shows real calls and keeps selection, latency and input evidence distinct.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const nodes = new Map();
function node(id) {
  if (!nodes.has(id)) {
    const classes = new Set();
    nodes.set(id, {value: '', innerHTML: '', textContent: '', hidden: false, disabled: false,
      checked: false, dataset: {}, listeners: {}, children: [],
      addEventListener(name, listener) { this.listeners[name] = listener; },
      setAttribute(name, value) { this[name] = value; },
      replaceChildren(...children) { this.children = children; this.value = children[0]?.value || ''; },
      closest() { return node('label'); },
      classList: {toggle(name, active) { active ? classes.add(name) : classes.delete(name); }, contains(name) { return classes.has(name); }}});
  }
  return nodes.get(id);
}
const context = vm.createContext({console, window: {}, document: {
  getElementById: node,
  querySelector: selector => selector.startsWith('meta') ? {content: 'test'} : node(selector),
  querySelectorAll: () => [], addEventListener() {},
}, Option: function(text, value) { return {text, value}; }});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8').replace(/\nstart\(\);\s*$/, ''), context);
const run = script => vm.runInContext(script, context);
const game = {id: 'racer', timing: 'realtime', interfaces: [], versions: [], providers: {live: 'laya', shadow: []},
  learning: {actions: {brake: '감속', accelerate: '가속'}}, options: ['brake', 'accelerate']};
context.fixture = {games: [game], providers: ['laya', 'jev', 'laya-mlx']};
node('game').value = 'racer';
node('decision-delay').value = '0';
run('catalog = fixture; choice = {provider:"laya", shadows:["jev"], policy:false}; lesson = "socket"; clockOffset = Date.now()/1000 - 20');
const config = {provider: 'laya', shadows: ['jev']};
context.config = config;
const running = {connection: {status: 'connected', game: 'racer', connector: 'socket', scenarios: ['hairpin_fast']},
  busy: true, auto: true, key_set: true, options: config, now: 20, demonstration: {enabled:false,status:'idle'},
  observed: {ok: true, state: {elapsed: 8.3, outcome: 'playing', paused: false}},
  calls: [{id: 3, provider: 'laya', role: 'live', step: 2, started_at: 18, finished_at: null},
    {id: 2, provider: 'jev', role: 'shadow', step: 1, started_at: 16, finished_at: null},
    {id: 1, provider: 'laya', role: 'live', step: 1, started_at: 15, finished_at: 16, ms: 950, status: 'ok', action: 'brake'}],
  rows: [{step: 1, decision: {provider: 'laya', call_id: 1, status: 'ok', action: 'brake'}, applied: true}]};
context.data = running;
run('latest = data; updateLiveDecisions(data)');
assert.equal(node('live-decisions').hidden, false);
assert.equal(node('top').classList.contains('realtime-model-view'), true);
assert.equal(node('top').classList.contains('demonstration-running'), false);
assert.equal(node('live-world-status').textContent, '게임 진행 중 · 8.3초');
assert.match(node('live-provider-cards').innerHTML, /판단 중 · 2\.0초/);
assert.match(node('live-provider-cards').innerHTML, /직전 선택<\/dt><dd>감속/);
assert.match(node('live-provider-cards').innerHTML, /950 ms/);
assert.match(node('live-provider-cards').innerHTML, /게임 적용 확인/);
assert.match(node('live-provider-cards').innerHTML, /비교 모델 · 기록만/);
assert.match(node('live-provider-cards').innerHTML, /판단 중인 관찰<\/dt><dd>2번째 관찰/);
assert.match(node('live-provider-cards').innerHTML, /응답한 관찰<\/dt><dd>1번째 관찰/);
assert.equal(node('realtime-demo').disabled, true);

// A shadow reply completed for an earlier observation is visible without claiming game input.
const comparison = {...running, calls: [{id: 2, provider: 'jev', role: 'shadow', step: 1,
  started_at: 16, finished_at: 20, ms: 4020, status: 'ok', action: 'accelerate'}], rows: []};
context.data = comparison;
const shadow = run('liveProviderState(data,"jev",config)');
assert.equal(shadow.action, 'accelerate');
assert.equal(shadow.latency, 4020);
assert.equal(shadow.effect, '판단만 기록 · 게임 입력 없음');
assert.equal(shadow.completedSourceStep, 1);
assert.equal(shadow.pendingSourceStep, null);
assert.equal(shadow.running, false);

// Warmup is measured preparation, never a control decision, and a missing API key is explicit.
context.data = {...running, key_set: false, calls: [{id: 4, role: 'warmup', provider: 'laya', started_at: 19, finished_at: null}], rows: []};
const warmup = run('liveProviderState(data,"laya",config)');
assert.match(warmup.status, /첫 추론 준비 중/);
assert.equal(warmup.action, null);
assert.match(warmup.effect, /게임에 입력하지 않습니다/);
assert.equal(run('liveProviderState(data,"jev",config).status'), 'API 키 필요');
assert.equal(run('liveProviderState(data,"jev",{provider:"laya",shadows:[]}).status'), '선택 안 됨');

// Historical success never makes a missing key look ready to issue a new API request.
context.data = {...comparison, key_set: false};
const missingKey = run('liveProviderState(data,"jev",config)');
assert.equal(missingKey.status, 'API 키 필요');
assert.match(missingKey.effect, /이전 응답/);
assert.equal(missingKey.action, 'accelerate');
assert.equal(missingKey.requiresKey, true);

// A historical running row carries its original source, without an empty error label.
assert.match(run('shadowBlock({provider:"jev",status:"running",source_step:3,current_observation:false}, {})'), /기록 시점에 판단 중 · 3번째 관찰/);

// An expired model answer remains visibly cancelled, even if the API itself succeeded.
context.data = {...running, calls: [running.calls[2]], rows: [{...running.rows[0], applied: false, stale: true}]};
assert.equal(run('liveProviderState(data,"laya",config).effect'), '입력 취소');

// The dedicated CTA opts into prepared live runs; ordinary auto stays unchanged.
run('setupControls(); command = (url, body) => { window.sent = {url, body}; }');
node('realtime-demo').listeners.click();
assert.equal(context.window.sent.url, '/api/auto');
assert.equal(context.window.sent.body.enabled, true);
assert.equal(context.window.sent.body.demonstration, true);
assert.equal(context.window.sent.body.provider, 'laya');
node('auto').listeners.click();
assert.equal(context.window.sent.body.demonstration, undefined);

// A ready socket game can demonstrate; preview and unsupported reset paths explain their limit.
context.data = {...running, busy: false, auto: false, calls: [], rows: []};
run('latest = data; updateLiveDecisions(data)');
assert.equal(node('realtime-demo').disabled, false);
context.data = {...context.data, key_set: false};
run('latest = data; updateLiveDecisions(data)');
assert.equal(node('realtime-demo').disabled, false, 'a missing comparison key does not block the local control model');
assert.match(node('live-demo-note').textContent, /API 키/);
context.data = {...context.data, connection: {...running.connection, connector: 'kbm'}, options: {...config, dry_run: true}};
run('latest = data; updateLiveDecisions(data)');
assert.equal(node('realtime-demo').disabled, true);
assert.match(node('live-demo-note').textContent, /실제 게임을 제어하지 않습니다/);
run('lesson = "play"; updateLiveDecisions(data)');
assert.equal(node('live-decisions').hidden, true);
assert.equal(node('top').classList.contains('realtime-model-view'), false);
assert.equal(node('top').classList.contains('demonstration-running'), false);

// Static assets may have updated while an old console process still lacks the demo API.
context.data = {...running, busy:false, auto:false};
delete context.data.demonstration;
run('latest = data; lesson = "socket"; updateLiveDecisions(data)');
assert.equal(node('realtime-demo').disabled, true);
assert.match(node('live-demo-note').textContent, /콘솔 재시작 필요/);

// Comparison activity shows the requested pair instead of an unrelated control selection.
context.data = {...running, active_options:{provider:"jev",shadows:["laya"]}, options:{provider:"laya-mlx",shadows:[]}};
run('latest = data; updateLiveDecisions(data)');
assert.equal(node('live-jev-choice').checked, true);
assert.doesNotMatch(node('live-provider-cards').innerHTML, /Laya-MLX/);
context.data = {...running, demonstration:{enabled:true,status:"error",started_at:12}};
run('latest = data; updateLiveDecisions(data)');
assert.match(node('live-demo-status').textContent, /시연 중 오류/);
assert.doesNotMatch(node('live-demo-status').textContent, /시연 준비에 실패/);

// Only an active opt-in demonstration compacts the surrounding page chrome.
context.data = {...running, demonstration:{enabled:true,status:"warming"}};
run('latest = data; lesson = "socket"; updateLiveDecisions(data)');
assert.equal(node('top').classList.contains('demonstration-running'), true);
context.data = {...running, busy:false, auto:false, demonstration:{enabled:true,status:"stopped"}};
run('latest = data; updateLiveDecisions(data)');
assert.equal(node('top').classList.contains('demonstration-running'), false);
// Realtime first visits reveal model controls, while an explicit manual choice persists.
run('latest = null; playModeChosen = false; onGameChange()');
assert.equal(run('lesson'), 'socket');
run('selectPlayMode("play"); onGameChange()');
assert.equal(run('lesson'), 'play');
console.log('live demonstration: real calls, concurrent providers, observed world time, warmup, input evidence, explicit CTA and manual choice passed');
