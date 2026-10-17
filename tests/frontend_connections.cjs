// Connection edits and transport evidence must remain faithful to the backend contract.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const nodes = new Map();
const node = id => {
  if (!nodes.has(id)) nodes.set(id, {value: '', innerHTML: '', textContent: '', hidden: false, disabled: false,
    removeAttribute(name) { delete this[name]; }, querySelectorAll() { return []; }});
  return nodes.get(id);
};
const revoked = [];
const context = vm.createContext({window: {}, document: {
  getElementById: node,
  querySelector: () => ({content: 'test'}),
  querySelectorAll: () => [],
}, URL: {revokeObjectURL: value => revoked.push(value)}, console});
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
vm.runInContext(source.replace(/\nstart\(\);\s*$/, ''), context);
const run = script => vm.runInContext(script, context);
const plain = value => JSON.parse(JSON.stringify(value));
const schema = {
  connector: 'mod', editable: ['interval_ms', 'max_age_ms', 'max_action_ms', 'endpoint', 'actions'],
  values: {interval_ms: 300, max_age_ms: 3000, max_action_ms: 500, endpoint: {host: '127.0.0.1', port: 9876}, actions: {play: {command: 'play', card_index: 0}}},
  fields: {interval_ms: {type: 'integer', min: 1, max: 60000, label: '판단 간격'},
    max_age_ms: {type: 'integer', min: 1, max: 600000}, max_action_ms: {type: 'integer', min: 1, max: 60000},
    endpoint: {type: 'json'}, actions: {type: 'json'}},
};
context.testSchema = schema;
run(`catalog = {games: [{id: 'external-demo', interfaces: [{version: 'custom', name: 'plugin', configuration: testSchema}]}]};`);
node('game').value = 'external-demo';
node('target').value = 'custom/plugin';
run('renderConnectionSettings()');
assert.deepEqual(JSON.parse(node('connection-json').value), {endpoint: schema.values.endpoint, actions: schema.values.actions});
for (const key of ['interval_ms', 'max_age_ms', 'max_action_ms']) node(`setting-${key}`).value = String(schema.values[key]);
node('setting-interval_ms').value = '125';
node('connection-json').value = JSON.stringify({endpoint: {host: '127.0.0.1', port: 9877}, actions: {play: {command: 'play', card_index: 2}}});
const payload = plain(run('connectionSettingsPayload()'));
assert.equal(payload.interval_ms, 125);
assert.equal(payload.endpoint.port, 9877);
assert.equal(payload.actions.play.card_index, 2);
assert.equal(schema.values.endpoint.port, 9876, 'editing the form must not mutate catalog defaults');

node('connection-json').value = '{bad json';
assert.throws(() => run('connectionSettingsPayload()'), /JSON 문법/);
node('connection-json').value = '{"launch":{"command":["unexpected"]}}';
assert.throws(() => run('connectionSettingsPayload()'), /수정할 수 없는 설정/);
node('connection-json').value = '[]';
assert.throws(() => run('connectionSettingsPayload()'), /JSON 객체/);
node('connection-json').value = '{}';
for (const invalid of ['', '0', '1.5', '60001']) {
  node('setting-interval_ms').value = invalid;
  assert.throws(() => run('connectionSettingsPayload()'), /정수를 입력/);
}
node('setting-interval_ms').value = '125';

// The separate socket field is authoritative and does not compete with JSON.
context.socketSchema = {editable: ['socket'], values: {socket: '/tmp/default.sock'}, fields: {socket: {type: 'string'}}};
run('catalog.games[0].interfaces[0].configuration = socketSchema; renderConnectionSettings()');
assert.equal(node('socket-path').value, '/tmp/default.sock');
assert.equal(node('connection-json').value, '{}');
node('socket-path').value = ' /tmp/lesson.sock ';
assert.equal(run('connectionSettingsPayload().socket'), '/tmp/lesson.sock');

// Localized teaching labels never replace the action IDs sent to the game.
const manualButtons = run('manualActionButtons({options:["go", "wait"], learning:{actions:{go:"<전진>"}}})');
assert.match(manualButtons, /data-action="go"/);
assert.match(manualButtons, />&lt;전진&gt;<\/button>/);
assert.match(manualButtons, /data-action="wait"[^>]*>wait<\/button>/);
assert.doesNotMatch(manualButtons, /<전진>/);

// Only actual transport evidence gets described as sent. Null stays unsent.
context.traceStatus = {input_trace: {connector: 'kbm', action: '<click>', command: [{tap: '<key>'}], result: {applied: true, ok: true, input_sent: true, application_confirmed: false}}};
run('updateInputTrace(traceStatus)');
assert.match(node('input-trace').innerHTML, /&lt;click&gt;|&lt;key&gt;/);
assert.doesNotMatch(node('input-trace').innerHTML, /<click>|<key>/);
assert.match(node('input-trace-note').textContent, /키마 입력 전송 · 게임 적용 미확인/);
assert.doesNotMatch(node('input-trace-note').textContent, /게임 적용 확인/);
run('updateInputTrace({input_trace:{connector:"socket",action:"play",command:"play",result:{applied:true,ok:true}}})');
assert.match(node('input-trace-note').textContent, /게임 적용 확인/);
run('updateInputTrace({rows:[{input_trace:{connector:"mod",action:"play",command:null,result:{stale:true,applied:false}}}]})');
assert.match(node('input-trace').innerHTML, /전송한 명령 없음/);
assert.match(node('input-trace-note').textContent, /입력 취소/);
run('updateInputTrace({input_trace:{connector:"kbm",action:"play",mapped_command:[{tap:"space"}],command:null,result:{dry_run:true,applied:true}}})');
assert.match(node('input-trace-note').textContent, /실제 입력 없음/);
assert.match(node('input-trace').innerHTML, /전송한 명령 없음/);
assert.doesNotMatch(node('input-trace-note').textContent, /적용 보고/);
run('updateInputTrace({rows:[{action:"play",applied:true}]})');
assert.match(node('input-trace-note').textContent, /입력 실행을 추정하지 않습니다/);

// Disconnect / reconnect cannot leave a previous game's captured screen visible.
run('frameUrl = "blob:old-screen"; lastFrameAt = 7');
node('frame').src = 'blob:old-screen';
run('updateFrame({connection:{status:"disconnected",connector:"kbm"}})');
assert.equal(node('frame-box').hidden, true);
assert.equal(node('frame').src, undefined);
assert.deepEqual(revoked, ['blob:old-screen']);
run('updateFrame({connection:{status:"connected",connector:"kbm"},frame_at:null})');
assert.equal(node('frame').hidden, true);
assert.match(node('frame-note').textContent, /아직 캡처가 없습니다/);
console.log('connection UI: schema-driven edits, validation, faithful input evidence, captured frame reset passed');
