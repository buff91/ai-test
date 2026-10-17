// Real-time summaries preserve track cells while displaying fractional progress and time.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const views = new Map();
const context = {GameViews: {register: (id, draw) => views.set(id, draw)}};
for (const game of ['racer', 'moba']) {
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, `../games/${game}/view.js`), 'utf8'), context);
}
const tracks = [], bars = [];
const helpers = {
  track(cells, label) { tracks.push({cells, label}); return ''; },
  chips() { return ''; },
  bar(label, value) { bars.push({label, value}); return ''; },
  stat(label, value) { return `${label}: ${value}`; },
  stats(items) { return items.join(' | '); },
};
const state = {position: 2, realtime_position: 2.4, elapsed: 1.25, lane: 'center', speed: 2,
  turn: 1, integrity: 3, boost: 1, outcome: 'playing',
  obstacles: [{cell: 3, lane: 'center'}], corners: [{from: 4, to: 6, max_speed: 1}]};
const rendered = views.get('racer')(state, helpers);
assert.match(rendered, /경과 시간: 1.3초/);
assert.match(tracks[1].cells[0].cls, /player/);
assert.match(tracks[1].cells[1].cls, /enemy/);
assert.match(tracks[1].cells[2].cls, /hazard/);
assert.equal(bars.find(bar => bar.label === '진행').value, 2.4);
assert.match(views.get('racer')({...state, paused: true}, helpers), /결과: 일시정지/);
assert.match(views.get('moba')({elapsed: 5.4, lane: 'mid', outcome: 'playing'}, helpers), /경과 시간: 5.4초/);
assert.match(views.get('moba')({elapsed: 5.4, lane: 'mid', paused: true}, helpers), /결과: 일시정지/);
console.log('real-time views: elapsed time, pause state, fractional progress and integer track cells passed');
