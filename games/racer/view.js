// Racer: the next 12 cells of a 3-lane track, corners (speed limit) and obstacles.
GameViews.register('racer', (s, h) => {
  const LENGTH = 40, LOOKAHEAD = 12, LANES = ['left', 'center', 'right'];
  const names = {left: '왼쪽', center: '중앙', right: '오른쪽'};
  const corner = cell => (s.corners || []).find(c => cell >= c.from && cell <= c.to);
  const rows = LANES.map(lane => {
    const cells = [];
    for (let cell = s.position; cell <= Math.min(LENGTH, s.position + LOOKAHEAD); cell++) {
      const car = cell === s.position && lane === s.lane;
      const rock = (s.obstacles || []).some(o => o.cell === cell && o.lane === lane);
      const c = corner(cell);
      const tooFast = c && s.speed > c.max_speed;
      const reach = cell > s.position && cell <= s.position + s.speed && lane === s.lane;
      cells.push({text: car ? '▲' : rock ? '■' : cell === LENGTH ? '⚑' : c ? String(c.max_speed) : '',
                  cls: [car && 'player', rock && 'enemy', cell === LENGTH && 'goal', c && !rock && !car && (tooFast ? 'hazard' : 'cover'),
                        reach && !rock && 'range', reach && (rock || tooFast) && 'danger'].filter(Boolean).join(' '),
                  title: `${cell}칸${c ? ` · 코너 제한 ${c.max_speed}` : ''}`});
    }
    return h.track(cells, names[lane]);
  }).join('');
  const next = (s.corners || []).find(c => c.to >= s.position);
  return rows +
    h.chips([next ? {text: `코너 ${next.from}–${next.to} 제한 ${next.max_speed}`, cls: s.speed > next.max_speed ? 'bad' : 'good'} : {text: '앞에 코너 없음', cls: 'good'},
             s.boost ? `부스트 ${s.boost}` : null]) +
    `<div class="gv-note">숫자 칸 = 코너 제한 속도 · 주황 = 지금 속도로는 충돌 · 파란 칸 = 다음 박자에 지나갈 칸</div>` +
    h.bar('진행', s.position, LENGTH, '') + h.bar('속도', s.speed, 5, '') + h.bar('내구도', s.integrity, 3) +
    h.stats([h.stat('박자', s.turn), h.stat('차선', names[s.lane] || s.lane), h.stat('결과', ({playing: '진행 중', won: '완주', lost: '도전 실패', draw: '시간 종료'})[s.outcome] || s.outcome)]);
});
