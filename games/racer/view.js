// Racer: the next 12 cells of a 3-lane track, corners (speed limit) and obstacles.
GameViews.register('racer', (s, h) => {
  const LENGTH = 40, LOOKAHEAD = 12, LANES = ['left', 'center', 'right'];
  const position = Number.isFinite(s.realtime_position) ? s.realtime_position : s.position;
  const cellPosition = Math.floor(position);
  const names = {left: '왼쪽', center: '중앙', right: '오른쪽'};
  const corner = cell => (s.corners || []).find(c => cell >= c.from && cell <= c.to);
  const rows = LANES.map(lane => {
    const cells = [];
    for (let cell = cellPosition; cell <= Math.min(LENGTH, cellPosition + LOOKAHEAD); cell++) {
      const car = cell === cellPosition && lane === s.lane;
      const rock = (s.obstacles || []).some(o => o.cell === cell && o.lane === lane);
      const c = corner(cell);
      const tooFast = c && s.speed > c.max_speed;
      const reach = cell > position && cell <= position + s.speed && lane === s.lane;
      cells.push({text: car ? '▲' : rock ? '■' : cell === LENGTH ? '⚑' : c ? String(c.max_speed) : '',
                  cls: [car && 'player', rock && 'enemy', cell === LENGTH && 'goal', c && !rock && !car && (tooFast ? 'hazard' : 'cover'),
                        reach && !rock && 'range', reach && (rock || tooFast) && 'danger'].filter(Boolean).join(' '),
                  title: `${cell}칸${c ? ` · 코너 제한 ${c.max_speed}` : ''}`});
    }
    return h.track(cells, names[lane]);
  }).join('');
  const next = (s.corners || []).find(c => c.to >= position);
  return rows +
    h.chips([next ? {text: `코너 ${next.from}–${next.to} 제한 ${next.max_speed}`, cls: s.speed > next.max_speed ? 'bad' : 'good'} : {text: '앞에 코너 없음', cls: 'good'},
             s.boost ? `부스트 ${s.boost}` : null]) +
    `<div class="gv-note">숫자 칸 = 코너 제한 속도 · 주황 = 지금 속도로는 충돌 · 파란 칸 = 현재 속도로 지나갈 구간</div>` +
    h.bar('진행', Math.round(position * 10) / 10, LENGTH, '') + h.bar('속도', s.speed, 5, '') + h.bar('내구도', s.integrity, 3) +
    h.stats([h.stat(Number.isFinite(s.elapsed) ? '경과 시간' : '경과 박자', Number.isFinite(s.elapsed) ? `${s.elapsed.toFixed(1)}초` : s.turn),
      h.stat('차선', names[s.lane] || s.lane), h.stat('결과', s.paused ? '일시정지' : ({playing: '진행 중', won: '완주', lost: '도전 실패', draw: '시간 종료'})[s.outcome] || s.outcome)]);
});
