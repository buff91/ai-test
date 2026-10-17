// Play options select real interfaces and keep unfinished connection edits.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const nodes = new Map();
function node(id) {
  if (!nodes.has(id)) {
    let html = '';
    const classes = new Set();
    nodes.set(id, {
      value: '', textContent: '', hidden: false, disabled: false, checked: true, dataset: {},
      classList: {toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); }, contains(name) { return classes.has(name); }},
      setAttribute(name, value) { this[name] = value; },
      replaceChildren(...children) {
        this.children = children;
        // A select filled with fresh, unselected options selects its first option.
        this.value = children[0]?.value || '';
      },
      closest() { return node('closest-label'); },
      get innerHTML() { return html; },
      set innerHTML(value) {
        html = value;
        for (const match of value.matchAll(/<input id="([^"]+)"[^>]*value="([^"]*)"/g)) node(match[1]).value = match[2];
      },
    });
  }
  return nodes.get(id);
}
const routes = ['internal', 'kbm'].map(route => Object.assign(node(`route-${route}`), {dataset: {route}}));
const modes = ['play', 'socket', 'kbm'].map(lesson => Object.assign(node(`mode-${lesson}`), {dataset: {lesson}}));
const context = vm.createContext({window: {}, document: {
  getElementById: node, querySelector: selector => selector.startsWith('meta') ? {content: 'test'} : node(`selector:${selector}`),
  querySelectorAll: selector => selector === '[data-route]' ? routes : selector === '[data-lesson]' ? modes : [],
}, Option: function(text, value) { return {text, value}; }, console});
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
vm.runInContext(source.replace(/\nstart\(\);\s*$/, ''), context);
const run = script => vm.runInContext(script, context);
function item(version, name, connector, mode, display) {
  return {version, name, connector, run: {mode, command: ['python', '-m', 'demo'], owner: 'python',
    target: {mode, display, version_name: version}, socket: '/tmp/demo.sock'},
  configuration: {editable: ['interval_ms', 'actions', ...(mode === 'attach' ? ['socket'] : []), ...(connector === 'kbm' ? ['window'] : [])],
    values: {interval_ms: 1000, actions: {go: connector === 'kbm' ? [{tap: '1'}] : 'go'}, socket: '/tmp/demo.sock',
      ...(connector === 'kbm' ? {window: {owner: 'python', title_contains: 'Demo'}} : {})},
    fields: {interval_ms: {type: 'integer', min: 1, max: 60000}, actions: {type: 'json'}, socket: {type: 'string'}, window: {type: 'json'}}}};
}
const fixture = {
  providers: ['laya'],
  games: [{id: 'lesson-demo', name: 'Demo', learning: {observation: '숫자 계기 관찰 · 범용 시각 인식 아님'},
    providers: {live: 'laya', shadow: []},
    versions: [{id: 'sim'}, {id: 'desktop', window_interface: 'app'}], interfaces: [
      item('sim', 'socket', 'socket', 'launch', 'headless'),
      item('desktop', 'app', 'socket', 'launch', 'visible'),
      item('desktop', 'attach', 'socket', 'attach', 'visible'),
      item('desktop', 'kbm', 'kbm', 'window', 'visible'),
    ]}],
};
context.fixture = fixture;
run('catalog = fixture');
node('game').value = 'lesson-demo';
node('target').value = 'desktop/app';
node('agent').value = 'dev';
run('renderRunHow()');
assert.equal(routes[0]['aria-pressed'], 'true');
assert.equal(run('selectedInterface().connector'), 'socket');
assert.match(node('connect').textContent, /게임 창/);

const modelControls = ['model-settings', 'comparison-settings', 'run', 'auto', 'analyze', 'model-shortcut'];
function assertMode(expected) {
  assert.equal(run('lesson'), expected);
  for (const mode of modes) assert.equal(mode['aria-pressed'], String(mode.dataset.lesson === expected));
  for (const id of modelControls) assert.equal(node(id).hidden, expected === 'play', `${id} follows the selected play option`);
}

// The first choice can be screen control: no earlier exercise or data route is required.
run('renderLesson()');
assertMode('play');
run('selectPlayMode("kbm")');
assertMode('kbm');
assert.equal(node('target').value, 'desktop/kbm');
assert.equal(run('selectedInterface().connector'), 'kbm');
assert.equal(node('dry-run').checked, true, 'direct screen selection retains input preview');
run('selectPlayMode("play")');
assertMode('play');
assert.equal(node('target').value, 'desktop/kbm', 'direct play does not replace the selected game target');
assert.equal(node('surface-badge').textContent, '직접 플레이');
assert.match(node('foreground-title').textContent, /직접 조작/);
assert.match(node('foreground-copy').textContent, /모델은 실행하지 않습니다/);
run('selectPlayMode("socket")');
assertMode('socket');
assert.equal(node('target').value, 'desktop/app');

// The detailed route selector and top-level options always describe the same choice.
run('selectPlayMode("play"); selectControlRoute("internal")');
assertMode('socket');
run('selectControlRoute("kbm")');
assertMode('kbm');
node('target').value = 'desktop/app';
run('renderRunHow()');
assertMode('socket');

// New game selection must not confuse the select's first option with a user preference.
run('rememberTargetPreference()');
fixture.games.push({...JSON.parse(JSON.stringify(fixture.games[0])), id: 'second-game'});
node('game').value = 'second-game';
run('onGameChange()');
assertMode('socket');
assert.equal(node('target').value, 'desktop/app', 'data control starts with a visible game, not the first headless option');
run('preparedWindows.set("second-game/desktop", {game: "second-game", version: "desktop", socket: "/tmp/second-prepared.sock", pid: 51, alive: true}); onGameChange()');
assertMode('socket');
assert.equal(node('target').value, 'desktop/attach', 'data control reuses a prepared visible game after a game change');
assert.equal(run('connectionSettingsPayload().socket'), '/tmp/second-prepared.sock');
node('game').value = 'lesson-demo';
run('onGameChange(); selectPlayMode("kbm")');
node('game').value = 'second-game';
run('onGameChange()');
assertMode('kbm');
assert.equal(node('target').value, 'desktop/kbm', 'screen control stays selected when the new game also has a prepared socket interface');
node('game').value = 'lesson-demo';
run('onGameChange(); selectPlayMode("socket")');

// Switching changes the actual connector, and retains the desktop version.
node('connection-json').value = '{unfinished json';
node('setting-interval_ms').value = '375';
run('selectControlRoute("kbm")');
assert.equal(node('target').value, 'desktop/kbm');
assert.equal(run('selectedInterface().connector'), 'kbm');
assert.equal(routes[1]['aria-pressed'], 'true');
assert.match(node('control-route-note').textContent, /범용 시각 인식 아님/);
assert.match(node('route-flow').innerHTML, /화면 관찰 코드/);
assert.equal(node('dry-run').checked, true, 'a new KBM target starts in preview mode');
node('connection-json').value = JSON.stringify({actions: {go: [{tap: '2'}]}});
node('setting-interval_ms').value = '140';
node('dry-run').checked = false;
run('selectControlRoute("internal")');
assert.equal(node('target').value, 'desktop/app', 'the internal route keeps the same visible game version');
assert.equal(node('connection-json').value, '{unfinished json', 'unfinished JSON must survive a route round trip');
assert.equal(node('setting-interval_ms').value, '375');
assert.throws(() => run('connectionSettingsPayload()'), /JSON 문법/);
run('selectControlRoute("kbm")');
assert.equal(run('connectionSettingsPayload().actions.go[0].tap'), '2');
assert.equal(run('connectionSettingsPayload().interval_ms'), 140);
assert.equal(node('dry-run').checked, false, 'explicit input preview choice is retained for this target');

// A current attach or headless target is not silently changed by reselecting its route.
node('target').value = 'desktop/attach';
run('renderRunHow()');
node('socket-path').value = '/tmp/class-custom.sock';
run('selectControlRoute("internal")');
assert.equal(node('target').value, 'desktop/attach');
run('selectControlRoute("kbm")');
node('target').value = 'desktop/attach';
run('renderRunHow()');
assert.equal(run('connectionSettingsPayload().socket'), '/tmp/class-custom.sock');
node('target').value = 'sim/socket';
run('renderRunHow(); selectControlRoute("internal")');
assert.equal(node('target').value, 'sim/socket');
run('selectControlRoute("kbm")');
assert.equal(node('target').value, 'desktop/kbm', 'route falls back only when the version lacks that route');

// Unavailable routes and connection in progress cannot switch a running target.
run('latest = {connection: {status: "connecting"}}; renderControlRoutes(); selectControlRoute("internal")');
assert.equal(node('target').value, 'desktop/kbm');
assert.ok(routes.every(button => button.disabled));
run('latest = null; catalog.games[0].interfaces[3].error = "unsupported"; renderControlRoutes()');
assert.equal(routes[1].disabled, true);
assert.equal(routes[0].disabled, false);

// Unsupported choices and active operations cannot make the UI claim a different mode.
run('selectPlayMode("socket"); renderLesson()');
assertMode('socket');
assert.equal(modes[2].disabled, true);
run('selectPlayMode("kbm")');
assertMode('socket');
assert.equal(node('target').value, 'desktop/app');
run('delete catalog.games[0].interfaces[3].error');
for (const state of [
  'latest = {busy: true, connection: {status: "disconnected"}}',
  'latest = {connection: {status: "connecting"}}',
  'latest = null; windowPreparing = true',
]) {
  run(`${state}; renderLesson(); selectPlayMode("kbm"); selectPlayMode("play")`);
  assertMode('socket');
  assert.equal(node('target').value, 'desktop/app');
  assert.ok(modes.every(mode => mode.disabled), 'all play options are locked while work is in progress');
  run('latest = null; windowPreparing = false');
}

// An idle connection allows direct play or its current model route, but not a transport change.
run('selectPlayMode("kbm"); latest = {busy: false, auto: false, options: {dry_run: true}, connection: {status: "connected", connector: "kbm", game: "lesson-demo", target: {version: "desktop", display: "visible"}}}; renderLesson()');
assert.equal(modes[0].disabled, false);
assert.equal(modes[1].disabled, true);
assert.equal(modes[2].disabled, false);
run('selectPlayMode("play")');
assertMode('play');
assert.equal(node('surface-badge').textContent, '직접 플레이', 'an idle KBM connection does not override direct-play presentation');
assert.match(node('foreground-title').textContent, /직접 조작/);
run('selectPlayMode("socket")');
assertMode('play');
assert.equal(node('target').value, 'desktop/kbm');
run('selectPlayMode("kbm")');
assertMode('kbm');
run('latest = null');

// Preparing a persistent window uses the declared version and attaches that exact socket.
async function verifyPreparation() {
  run('delete catalog.games[0].interfaces[3].error');
  node('target').value = 'desktop/app';
  run('renderRunHow()');
  context.testWindow = {game: 'lesson-demo', version: 'desktop', interface: 'app', socket: '/tmp/prepared-window.sock', pid: 42, alive: true, reused: false};
  context.apiCalls = [];
  run('api = async (url, body) => { apiCalls.push({url, body}); return {game_window:testWindow}; }; poll = async () => {}');
  await run('prepareGameWindow()');
  assert.equal(context.apiCalls[0].url, '/api/game-window');
  assert.deepEqual(JSON.parse(JSON.stringify(context.apiCalls[0].body)), {game: 'lesson-demo', version: 'desktop'});
  assert.equal(node('target').value, 'desktop/attach');
  assert.equal(run('connectionSettingsPayload().socket'), '/tmp/prepared-window.sock');
  assert.equal(run('connectionPayload().prepared_window_pid'), undefined, 'the socket interface never requests an OS window binding');
  assert.match(node('window-preparation-note').textContent, /연결을 끊어도 이 창은 유지/);
  run('selectControlRoute("kbm"); selectControlRoute("internal")');
  assert.equal(node('target').value, 'desktop/attach', 'switching back to the interface reuses the prepared game');
  assert.equal(run('connectionSettingsPayload().socket'), '/tmp/prepared-window.sock');

  run('selectControlRoute("kbm"); api = () => new Promise(resolve => { window.resolvePreparation = resolve; })');
  assert.equal(run('connectionPayload().prepared_window_pid'), 42, 'default KBM window selection binds the exact prepared process');
  node('connection-json').value = JSON.stringify({actions: {go: [{tap: '2'}]}, window: {owner: 'custom', title_contains: 'External'}});
  assert.equal(run('connectionPayload().prepared_window_pid'), undefined, 'custom external window selection is never overridden by a prepared process');
  node('connection-json').value = JSON.stringify({actions: {go: [{tap: '2'}]}, window: {owner: 'python', title_contains: 'Demo', pid: 999}});
  assert.equal(run('connectionPayload().prepared_window_pid'), undefined, 'a custom PID is not silently replaced');
  node('connection-json').value = JSON.stringify({actions: {go: [{tap: '2'}]}, window: {owner: 'python', title_contains: 'Demo', pid: 42}});
  const pending = run('prepareGameWindow()');
  assert.equal(node('prepare-window').disabled, true);
  assert.equal(node('game').disabled, true);
  run('selectControlRoute("internal")');
  assert.equal(node('target').value, 'desktop/kbm', 'an in-flight preparation cannot attach a changed selection');
  context.testWindow = {...context.testWindow, socket: '/tmp/recreated-window.sock', pid: 43};
  run('window.resolvePreparation({game_window: testWindow})');
  await pending;
  assert.equal(node('target').value, 'desktop/kbm', 'preparation leaves the requested KBM route selected');
  assert.equal(node('game').disabled, false);
  assert.equal(run('connectionPayload().prepared_window_pid'), 43, 'a previously managed PID follows the recreated window');
  assert.equal(run('connectionPayload().settings.window.pid'), 43);
  run('selectControlRoute("internal")');
  assert.equal(run('connectionSettingsPayload().socket'), '/tmp/recreated-window.sock', 'recreating a window on the KBM route refreshes the earlier managed socket');
  node('socket-path').value = '/tmp/explicit-custom.sock';
  run('selectControlRoute("kbm"); preparedWindows.set("lesson-demo/desktop", {...testWindow, socket: "/tmp/newer-window.sock"}); selectControlRoute("internal")');
  assert.equal(run('connectionSettingsPayload().socket'), '/tmp/explicit-custom.sock', 'an explicit custom socket remains a user choice');
  run('latest = {busy: true, connection: {status: "disconnected"}}; renderControlRoutes()');
  assert.equal(node('prepare-window').disabled, true);

  // Visiting a game again, or receiving the first status after reload, must not launch a second instance.
  run('latest = null; targetPreferences.clear(); onGameChange()');
  assert.equal(node('target').value, 'desktop/attach', 'the game default reuses an already prepared visible instance');
  node('target').value = 'desktop/app';
  run('rememberTargetPreference(); onGameChange()');
  assert.equal(node('target').value, 'desktop/app', 'an explicit advanced launch preference is retained');
  run('targetPreferences.clear(); onGameChange("sim/socket")');
  assert.equal(node('target').value, 'sim/socket', 'a supplied connected target takes precedence');
  node('target').value = 'desktop/app';
  run('renderRunHow(); syncPreparedTarget({connection: {status: "disconnected"}, busy: false})');
  assert.equal(node('target').value, 'desktop/attach', 'the initial status switches the untouched launch default to the prepared instance');
  node('target').value = 'desktop/app';
  run('rememberTargetPreference(); renderRunHow(); syncPreparedTarget({connection: {status: "disconnected"}})');
  assert.equal(node('target').value, 'desktop/app', 'status polling does not override an explicit target');
  node('target').value = 'sim/socket';
  run('rememberTargetPreference(); onGameChange()');
  assert.equal(node('target').value, 'sim/socket', 'explicit headless preference remains available');
}
verifyPreparation().then(() => console.log('play options: direct selection, truthful modes, operation locks, connector coherence, draft preservation and prepared-window reuse passed'))
  .catch(error => { console.error(error); process.exitCode = 1; });
