// User-visible training flows without torch, downloads, a browser or server.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const nodes = new Map();
const decode = text => text.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
class Node {
  constructor(id = '') { this.id = id; this.listeners = {}; this.value = ''; this.disabled = false; this.hidden = false; this.checked = false; this.textContent = ''; this._html = ''; this.buttons = []; this.classes = new Set(); this.classList = {toggle: (key, on) => on ? this.classes.add(key) : this.classes.delete(key)}; }
  set innerHTML(html) {
    this._html = html;
    if (this.id === 'laya-training') {
      for (const match of html.matchAll(/id="([^"]+)"/g)) if (!nodes.has(match[1])) nodes.set(match[1], new Node(match[1]));
      for (const match of html.matchAll(/<input\b[^>]*id="([^"]+)"[^>]*>/g)) nodes.get(match[1]).value = match[0].match(/\bvalue="([^"]*)"/)?.[1] || '';
      for (const match of html.matchAll(/<(select|textarea)\b[^>]*id="([^"]+)"[^>]*>([\s\S]*?)<\/\1>/g)) {
        const node = nodes.get(match[2]); node.innerHTML = match[3];
        node.value = match[1] === 'textarea' ? match[3] : decode(match[3].match(/<option value="([^"]*)"/)?.[1] || '');
      }
    }
    this.buttons = [...html.matchAll(/<button\b([^>]*)>/g)].map(match => {
      const button = new Node();
      button.dataset = {trainingAction: match[1].match(/data-training-action="([^"]*)"/)?.[1], checkpoint: decode(match[1].match(/data-checkpoint="([^"]*)"/)?.[1] || '')};
      button.disabled = /\bdisabled\b/.test(match[1]);
      button.closest = () => button;
      return button;
    });
  }
  get innerHTML() { return this._html; }
  addEventListener(event, fn) { this.listeners[event] = fn; }
}
nodes.set('laya-training', new Node('laya-training'));
const context = {window: {}, document: {getElementById: id => nodes.get(id), querySelectorAll: selector => selector === '#training-checkpoints button' ? nodes.get('training-checkpoints')?.buttons || [] : []}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../web/training.js'), 'utf8'), context);
const panel = context.window.LayaTraining;
const node = id => nodes.get(`training-${id}`);
const requests = [];
const applications = [];
let rejectApply = false;
const game = {id: 'racer', name: '레이싱', questions: {action: {type: 'choice', criteria: {brake: 'Brake', keep: 'Keep'}}, danger: {type: 'score', criteria: ['safe', 'danger']}}};
let version = 'desktop-v1';
let backend = {game: 'racer', version, dataset: {cases: [], counts: {total: 0, pending: 0, train: 0, eval: 0, excluded: 0}}, jobs: [], checkpoints: [], active: null};
let getHandler = null;
const clone = value => JSON.parse(JSON.stringify(value));
const connected = {connection: {game: 'racer', status: 'connected', target: {version: 'desktop-v1'}}, rows: [{step: 2, decision: {call_id: 8, provider: 'laya', action: 'keep'}}, {step: 1, decision: {call_id: 7, provider: 'laya', action: 'brake'}}]};
const disconnected = {connection: {status: 'disconnected'}, rows: []};
panel.init({api: async (url, payload) => {
  requests.push({url, payload: payload && clone(payload)});
  if (payload) {
    if (rejectApply && url.endsWith('/apply')) throw new Error('평가 결과가 변경되었습니다.');
    return {ok: true};
  }
  return getHandler ? getHandler(url) : clone(backend);
}, getGame: () => game, getVersion: () => version, onApplied: applied => applications.push(clone(applied))});
const settle = () => new Promise(resolve => setImmediate(resolve));
const trigger = async (id, event = 'click', target) => { node(id).listeners[event]({target: target || node(id)}); await settle(); };
const checkpointButton = (id, action) => node('checkpoints').buttons.find(button => button.dataset.checkpoint === id && button.dataset.trainingAction === action);
const posts = endpoint => requests.filter(request => request.url === `/api/training/${endpoint}`);

(async () => {
  // Saved data stays usable without any game process, and is explicitly scoped by version.
  panel.update(disconnected, game); await settle();
  assert.equal(requests[0].url, '/api/training?game=racer&version=desktop-v1');
  assert.equal(node('collect').disabled, true);
  assert.equal(node('train').disabled, true);
  assert.match(node('game').textContent, /레이싱 · desktop-v1/);
  assert.match(nodes.get('laya-training').innerHTML, /Laya-MLX는 기본 모델/);
  const count = requests.length;
  panel.update(disconnected, game); await settle();
  assert.equal(requests.length, count, '500 ms app polling must not produce another training GET');

  // Pending oracle labels are a proposal, never automatically approved or trained.
  backend.dataset = {cases: [{id: 'case-one', review: 'pending', split: 'train', reason: '<img src=x onerror=alert(1)>', state: '<script>bad()</script>', questions: game.questions, suggested_expected: {action: 'brake', danger: 1}, expected: {}, source: {decision: {action: 'keep'}}, occurrences: 3}], counts: {total: 1, pending: 1, approved: 0, train: 0, eval: 0, excluded: 0}};
  await panel.refresh(true);
  assert.equal(node('action').value, 'brake');
  assert.equal(node('state').textContent, '<script>bad()</script>', 'untrusted state is assigned via textContent');
  assert.match(node('answer').textContent, /아직 승인하지 않음/);
  assert.match(node('case').innerHTML, /&lt;img/);
  assert.doesNotMatch(node('case').innerHTML, /<img/);
  assert.equal(node('train').disabled, true);
  node('expected').value = '{"danger":0}';
  await panel.refresh(true);
  assert.equal(node('expected').value, '{"danger":0}', 'poll refresh preserves the unsaved review draft');
  node('action').value = '';
  await trigger('approve');
  assert.equal(posts('review').length, 0);
  assert.match(node('status').textContent, /정답 행동을 선택/);
  node('action').value = 'brake';
  node('expected').value = '[]';
  await trigger('approve');
  assert.equal(posts('review').length, 0);
  node('expected').value = '{"danger":0}';
  node('split').value = 'eval';
  await trigger('approve');
  assert.deepEqual(posts('review').at(-1).payload, {game: 'racer', version: 'desktop-v1', case_id: 'case-one', expected: {danger: 0, action: 'brake'}, split: 'eval', review: 'approved'});
  await trigger('exclude');
  assert.deepEqual(posts('review').at(-1).payload.expected, {});
  assert.equal(posts('review').at(-1).payload.review, 'excluded');

  // Old schema records can be excluded, but cannot be approved.
  backend.dataset.cases[0].compatible = false;
  backend.dataset.cases[0].incompatible_reason = '질문이 변경됨';
  backend.dataset.cases[0].expected = {action: 'brake'};
  await panel.refresh(true);
  assert.equal(node('approve').disabled, true);
  assert.equal(node('exclude').disabled, false);
  assert.match(node('case-note').textContent, /승인 불가/);
  delete backend.dataset.cases[0].compatible;

  // Explicit bookmarking uses the selected decision id and correct game/version.
  panel.update(connected, game);
  assert.equal(node('bookmark').value, '8');
  assert.equal(node('collect').disabled, false);
  await trigger('bookmark-add');
  assert.deepEqual(posts('collect').at(-1).payload, {game: 'racer', version: 'desktop-v1', call_id: 8});
  await trigger('collect');
  assert.deepEqual(posts('collect').at(-1).payload, {game: 'racer', version: 'desktop-v1'});

  // Reviewed splits unlock training. Invalid tuning values never start a job.
  backend.dataset.counts = {...backend.dataset.counts, train: 2, eval: 1, approved: 3};
  await panel.refresh(true);
  assert.equal(node('train').disabled, false);
  node('epochs').value = '2.5';
  await trigger('train');
  assert.equal(posts('train').length, 0);
  node('epochs').value = '4';
  node('encoder-lr').value = '.1';
  await trigger('train');
  assert.equal(posts('train').length, 0);
  node('encoder-lr').value = '.000025';
  node('freeze').checked = true;
  await trigger('train');
  assert.deepEqual(posts('train').at(-1).payload, {game: 'racer', version: 'desktop-v1', base: 'multilingual', epochs: 4, encoder_lr: .000025, head_lr: .0001, freeze_encoder: true});

  // Failed jobs/logs are visible and escaped; only an explicit passed gate allows apply.
  backend.jobs = [{id: 'train-1', kind: 'train', status: 'failed', error: '<download failed>', log_tail: ['<script>log()</script>']}];
  backend.checkpoints = [{id: 'candidate-1', activation_allowed: false, reasons: ['<eval needed>'], created_at: 1792170000}];
  await panel.refresh(true);
  assert.match(node('jobs').innerHTML, /실패/);
  assert.match(node('jobs').innerHTML, /&lt;download failed&gt;/);
  assert.doesNotMatch(node('jobs').innerHTML, /<script>/);
  assert.equal(checkpointButton('candidate-1', 'apply').disabled, true);
  const forgedClick = checkpointButton('candidate-1', 'apply'); forgedClick.disabled = false;
  await trigger('checkpoints', 'click', forgedClick);
  assert.equal(posts('apply').length, 0, 'apply handler rechecks the server-provided gate');
  await trigger('checkpoints', 'click', checkpointButton('candidate-1', 'evaluate'));
  assert.equal(posts('evaluate').at(-1).payload.checkpoint_id, 'candidate-1');
  backend.checkpoints[0].activation_allowed = true;
  backend.checkpoints[0].evaluation = {held_out: true, eval_count: 1, before: {action_accuracy: .5, accuracy: .4, mean_latency_ms: 90}, after: {action_accuracy: .8, accuracy: .7, mean_latency_ms: 92}};
  await panel.refresh(true);
  assert.match(node('checkpoints').innerHTML, /50\.0%/);
  assert.match(node('checkpoints').innerHTML, /80\.0%/);
  assert.match(node('checkpoints').innerHTML, /학습에서 제외한 평가용/);
  assert.equal(checkpointButton('candidate-1', 'apply').disabled, false);
  await trigger('checkpoints', 'click', checkpointButton('candidate-1', 'apply'));
  assert.deepEqual(posts('apply').at(-1).payload, {game: 'racer', version: 'desktop-v1', checkpoint_id: 'candidate-1'});
  assert.deepEqual(applications, [{game: 'racer', version: 'desktop-v1', checkpoint_id: 'candidate-1'}]);
  rejectApply = true;
  await trigger('checkpoints', 'click', checkpointButton('candidate-1', 'apply'));
  assert.equal(applications.length, 1, 'a rejected apply must not switch the selected provider');
  rejectApply = false;
  backend.active = {checkpoint_id: 'candidate-1'};
  await panel.refresh(true);
  await trigger('rollback');
  assert.equal(posts('rollback').at(-1).payload.game, 'racer');

  // A refresh failure visibly blocks promotion using potentially stale metrics.
  getHandler = async () => { throw new Error('server <offline>'); };
  await panel.refresh(true);
  assert.match(node('status').textContent, /학습 데이터를 읽지 못했습니다: server <offline>/);
  assert.equal(checkpointButton('candidate-1', 'apply').disabled, true);
  getHandler = null;
  await panel.refresh(true);
  assert.equal(checkpointButton('candidate-1', 'apply').disabled, false);

  // Switching versions while a GET is in flight clears prior counts and discards its response.
  let finishOld;
  getHandler = () => new Promise(resolve => { finishOld = resolve; });
  const pending = panel.refresh(true);
  version = 'sim-v1';
  panel.update(connected, game);
  assert.equal(node('collect').disabled, true, 'desktop rows cannot be collected for sim-v1');
  assert.match(node('counts').innerHTML, /<strong>0<\/strong>/);
  backend = {game: 'racer', version: 'sim-v1', dataset: {cases: [], counts: {}}, jobs: [], checkpoints: [], active: null};
  getHandler = null;
  finishOld({game: 'racer', version: 'desktop-v1', dataset: {cases: [], counts: {total: 99, train: 99, eval: 99}}, jobs: [], checkpoints: [], active: {checkpoint_id: 'foreign'}});
  await pending; await settle();
  assert.match(requests.at(-1).url, /version=sim-v1/);
  assert.doesNotMatch(node('active').textContent, /foreign/);
  assert.doesNotMatch(node('counts').innerHTML, />99</);
  assert.equal(node('train').disabled, true);

  // Changing games immediately replaces the durable dataset and checkpoint namespace.
  const other = {...game, id: 'party', name: '파티 RPG'};
  backend = {...backend, game: 'party'};
  panel.update(disconnected, other); await settle();
  assert.match(requests.at(-1).url, /game=party/);
  assert.match(node('game').textContent, /파티 RPG/);
  assert.equal(node('rollback').disabled, true);
  assert.match(node('case').innerHTML, /저장된 사례 없음/);
  console.log('training panel: reviewed labels, tuning validation, async jobs, escaped data, evaluation/apply gates, offline errors and game/version identity passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
