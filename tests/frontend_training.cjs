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
let postHandler = null;
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
    if (postHandler) return postHandler(url, payload);
    if (url.endsWith('/apply')) return {ok: true, provider: backend.checkpoints.find(item => item.id === payload.checkpoint_id)?.runtime || 'laya'};
    if (url.endsWith('/rollback')) {
      if (backend.active_by_provider) backend.active_by_provider[payload.provider] = null;
      if (payload.provider === 'laya') backend.active = null;
    }
    return {ok: true};
  }
  return getHandler ? getHandler(url) : clone(backend);
}, getGame: () => game, getVersion: () => version, onApplied: applied => applications.push(clone(applied))});
const settle = () => new Promise(resolve => setImmediate(resolve));
const trigger = async (id, event = 'click', target) => { node(id).listeners[event]({target: target || node(id)}); await settle(); };
const checkpointButton = (id, action) => node('checkpoints').buttons.find(button => button.dataset.checkpoint === id && button.dataset.trainingAction === action);
const checkpointCard = id => node('checkpoints').innerHTML.split('<article class="training-checkpoint">').find(card => card.startsWith(`<div class="training-checkpoint-title"><b>${id}</b>`))?.split('</article>')[0] || '';
const posts = endpoint => requests.filter(request => request.url === `/api/training/${endpoint}`);

(async () => {
  // Saved data stays usable without any game process, and is explicitly scoped by version.
  panel.update(disconnected, game); await settle();
  assert.equal(requests[0].url, '/api/training?game=racer&version=desktop-v1');
  assert.equal(node('collect').disabled, true);
  assert.equal(node('train').disabled, true);
  assert.match(node('game').textContent, /레이싱 · desktop-v1/);
  assert.match(nodes.get('laya-training').innerHTML, /각 실행 방식의 체크포인트는 따로 평가/);
  assert.match(nodes.get('laya-training').innerHTML, /정답은 어떻게 가르치나요/);
  assert.match(nodes.get('laya-training').innerHTML, /예측 확률이 높아지도록 가중치를 업데이트/);
  assert.match(nodes.get('laya-training').innerHTML, /검토 대기·제외 사례는 학습에 쓰지 않습니다/);
  assert.match(nodes.get('laya-training').innerHTML, /평가용 정답은 가중치 학습에 쓰지 않고/);
  assert.match(nodes.get('laya-training').innerHTML, /잘못 승인한 답도 그대로 학습/);
  assert.match(nodes.get('laya-training').innerHTML, /올바르게 행동한 상황과 경계 상황/);
  assert.match(nodes.get('laya-training').innerHTML, /서로 다른 판이나 시작 상황/);
  assert.equal(node('rollback-mlx').disabled, true);
  const count = requests.length;
  panel.update(disconnected, game); await settle();
  assert.equal(requests.length, count, '500 ms app polling must not produce another training GET');

  // Pending oracle labels are a proposal, never automatically approved or trained.
  backend.dataset = {cases: [{id: 'case-one', review: 'pending', split: 'train', reason: '<img src=x onerror=alert(1)>', state: '<script>bad()</script>', questions: game.questions, suggested_expected: {action: 'brake', danger: 1}, expected: {}, source: {episode: 2, step: 14, decision: {action: 'keep'}, reference_kind: 'turn_search', truth: {action: ['brake', 'keep']}}, occurrences: 3}], counts: {total: 1, pending: 1, approved: 0, train: 0, eval: 0, excluded: 0}};
  await panel.refresh(true);
  assert.equal(node('action').value, 'brake');
  assert.equal(node('state').textContent, '<script>bad()</script>', 'untrusted state is assigned via textContent');
  assert.match(node('answer').textContent, /아직 승인하지 않음/);
  assert.match(node('answer').textContent, /턴제 행동 탐색/);
  assert.match(node('case-note').textContent, /3번째 게임 · 판단 14/);
  assert.deepEqual(JSON.parse(node('answer').textContent).기준이_허용한_행동, ['brake', 'keep']);
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
  const previousReviews = posts('review').length;
  await trigger('approve');
  assert.equal(posts('review').length, previousReviews);
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
  assert.equal(checkpointButton('candidate-1', 'convert').disabled, false, 'unevaluated compatible PyTorch checkpoints can be converted');
  await trigger('checkpoints', 'click', checkpointButton('candidate-1', 'convert'));
  assert.deepEqual(posts('convert').at(-1).payload, {game: 'racer', version: 'desktop-v1', checkpoint_id: 'candidate-1', dtype: 'float16'});
  backend.checkpoints[0].compatible = false;
  await panel.refresh(true);
  assert.equal(checkpointButton('candidate-1', 'convert'), undefined, 'incompatible checkpoints cannot be converted');
  assert.equal(checkpointButton('candidate-1', 'evaluate').disabled, true);
  backend.checkpoints[0].compatible = true;
  await panel.refresh(true);
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
  assert.deepEqual(applications, [{game: 'racer', version: 'desktop-v1', checkpoint_id: 'candidate-1', provider: 'laya'}]);
  rejectApply = true;
  await trigger('checkpoints', 'click', checkpointButton('candidate-1', 'apply'));
  assert.equal(applications.length, 1, 'a rejected apply must not switch the selected provider');
  rejectApply = false;
  backend.active = {checkpoint_id: 'candidate-1'};
  await panel.refresh(true);
  await trigger('rollback');
  assert.deepEqual(posts('rollback').at(-1).payload, {game: 'racer', version: 'desktop-v1', provider: 'laya'});

  // Converted MLX artifacts receive an independent evaluation gate, even when the source passed.
  backend.jobs.push({id: 'convert-1', kind: 'convert', status: 'completed', progress: 1, checkpoint_id: 'candidate-mlx'});
  backend.checkpoints.push({id: 'candidate-mlx', runtime: 'laya-mlx', source_checkpoint_id: 'candidate-1', compatible: true, activation_allowed: false, evaluation: null, reasons: ['MLX 평가 필요']});
  await panel.refresh(true);
  assert.match(node('jobs').innerHTML, /MLX 변환/);
  assert.equal(checkpointButton('candidate-mlx', 'convert'), undefined);
  assert.equal(checkpointButton('candidate-mlx', 'apply').disabled, true, 'source approval must never activate its MLX conversion');
  assert.equal(checkpointButton('candidate-1', 'apply').disabled, false);
  assert.match(checkpointCard('candidate-mlx'), /아직 평가하지 않았습니다/);
  assert.doesNotMatch(checkpointCard('candidate-mlx'), /80\.0%/);
  assert.match(checkpointCard('candidate-mlx'), /원본의 평가·적용 승인은 승계하지 않습니다/);
  assert.match(checkpointCard('candidate-mlx'), /Laya-MLX로 전환해 적용/);
  await trigger('checkpoints', 'click', checkpointButton('candidate-mlx', 'evaluate'));
  assert.deepEqual(posts('evaluate').at(-1).payload, {game: 'racer', version: 'desktop-v1', checkpoint_id: 'candidate-mlx'});
  backend.checkpoints[1].evaluation = {held_out: true, eval_count: 1, before: {action_accuracy: .5}, after: {action_accuracy: .8}};
  backend.checkpoints[1].activation_allowed = true;
  await panel.refresh(true);
  await trigger('checkpoints', 'click', checkpointButton('candidate-mlx', 'apply'));
  assert.equal(applications.at(-1).provider, 'laya-mlx', 'provider switch follows the successful API result');
  assert.equal(applications.at(-1).checkpoint_id, 'candidate-mlx');
  assert.match(checkpointCard('candidate-mlx'), /기본 Laya-MLX/);

  // Each runtime has its own active indicator and rollback request.
  backend.active_by_provider = {laya: {checkpoint_id: 'candidate-1'}, 'laya-mlx': {checkpoint_id: 'candidate-mlx'}};
  backend.active = {checkpoint_id: 'candidate-1'};
  await panel.refresh(true);
  assert.equal(node('rollback').disabled, false);
  assert.equal(node('rollback-mlx').disabled, false);
  assert.match(node('active').textContent, /candidate-1/);
  assert.match(node('active-mlx').textContent, /candidate-mlx/);
  await trigger('rollback-mlx');
  assert.deepEqual(posts('rollback').at(-1).payload, {game: 'racer', version: 'desktop-v1', provider: 'laya-mlx'});
  assert.equal(node('rollback-mlx').disabled, true);
  assert.equal(node('rollback').disabled, false);
  assert.match(node('active').textContent, /candidate-1/, 'MLX rollback preserves the PyTorch activation');
  assert.match(node('active-mlx').textContent, /기본 모델 사용 중/);
  await trigger('rollback');
  assert.equal(node('rollback').disabled, true);
  assert.equal(node('rollback-mlx').disabled, true);

  // A refresh failure visibly blocks promotion using potentially stale metrics.
  getHandler = async () => { throw new Error('server <offline>'); };
  await panel.refresh(true);
  assert.match(node('status').textContent, /학습 데이터를 읽지 못했습니다: server <offline>/);
  assert.equal(checkpointButton('candidate-1', 'apply').disabled, true);
  getHandler = null;
  await panel.refresh(true);
  assert.equal(checkpointButton('candidate-1', 'apply').disabled, false);

  // An unexpected server identity is an error, never a zero-count success.
  getHandler = async () => ({...clone(backend), game: 'foreign-game'});
  await panel.refresh(true);
  assert.match(node('status').textContent, /서버 응답의 게임·버전/);
  assert.equal(checkpointButton('candidate-1', 'apply').disabled, true);
  getHandler = null;
  await panel.refresh(true);

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

  // A successful old-version apply must not switch the current game's newly selected runtime.
  backend.checkpoints = [{id: 'sim-mlx', runtime: 'laya-mlx', activation_allowed: true, compatible: true}];
  await panel.refresh(true);
  let finishApply;
  postHandler = (url, payload) => new Promise(resolve => { finishApply = resolve; });
  const previousApplications = applications.length;
  await trigger('checkpoints', 'click', checkpointButton('sim-mlx', 'apply'));
  version = 'desktop-v1';
  backend = {game: 'racer', version, dataset: {cases: [], counts: {}}, jobs: [], checkpoints: [], active: null, active_by_provider: {laya: null, 'laya-mlx': null}};
  panel.update(disconnected, game);
  finishApply({ok: true, provider: 'laya-mlx'});
  postHandler = null;
  await settle();
  await panel.refresh(true);
  assert.equal(applications.length, previousApplications, 'old version apply response cannot change the new selection');
  assert.equal(node('rollback-mlx').disabled, true);
  assert.doesNotMatch(node('active-mlx').textContent, /sim-mlx/);

  // Switching games during apply also suppresses an old result's provider callback.
  backend.checkpoints = [{id: 'desktop-mlx', runtime: 'laya-mlx', activation_allowed: true, compatible: true}];
  await panel.refresh(true);
  let finishOtherApply;
  postHandler = () => new Promise(resolve => { finishOtherApply = resolve; });
  await trigger('checkpoints', 'click', checkpointButton('desktop-mlx', 'apply'));
  const other = {...game, id: 'party', name: '파티 RPG'};
  backend = {...backend, game: 'party', checkpoints: []};
  panel.update(disconnected, other);
  finishOtherApply({ok: true, provider: 'laya-mlx'});
  postHandler = null;
  await settle(); await panel.refresh(true);
  assert.equal(applications.length, previousApplications, 'old game apply response cannot change another game');
  assert.match(requests.at(-1).url, /game=party/);
  assert.match(node('game').textContent, /파티 RPG/);
  assert.equal(node('rollback').disabled, true);
  assert.match(node('case').innerHTML, /저장된 사례 없음/);
  console.log('training panel: reviewed labels, label guidance, PyTorch/MLX conversion and independent gates, provider activation/rollback, async failures, escaped data and game/version races passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
