// Shooter: a corridor to the exit at tile 10 with gunners (aim, then shoot) and rushers (charge).
GameViews.register('shooter', (s, h) => {
  const EXIT = 10, RANGE = 5;
  const cells = [];
  for (let x = 0; x <= EXIT; x++) {
    const enemy = (s.enemies || []).find(e => e.tile === x);
    const here = x === s.player_x;
    cells.push({text: here ? '@' : enemy ? (enemy.kind === 'gunner' ? 'G' : 'R') : x === EXIT ? '⇥' : '',
                cls: [here && 'player', enemy && 'enemy', enemy?.aiming && 'danger', x === EXIT && !here && 'exit',
                      !here && !enemy && Math.abs(x - s.player_x) <= RANGE && 'range'].filter(Boolean).join(' '),
                title: enemy ? `${enemy.kind} · ${enemy.hits_left} hits left${enemy.aiming ? ' · aiming' : ''}` : `tile ${x}`});
  }
  return h.track(cells, '복도') +
    h.chips((s.enemies || []).map(e => ({text: `${e.kind === 'gunner' ? '사수' : '돌진병'} ${e.tile}칸 · ${e.hits_left}발${e.aiming ? ' · 조준 중' : ''}`,
                                         cls: e.aiming ? 'bad' : Math.abs(e.tile - s.player_x) <= 1 ? 'warn' : ''}))) +
    `<div class="gv-note">파란 칸 = 사거리 ${RANGE}칸 · 주황 테두리 = 조준 중인 사수</div>` +
    h.bar('체력', s.hp, 100) + h.bar('탄약', s.ammo, 3, s.ammo ? '' : 'bad') +
    h.stats([h.stat('턴', s.turn), h.stat('시나리오', s.scenario), h.stat('결과', s.outcome)]);
});
