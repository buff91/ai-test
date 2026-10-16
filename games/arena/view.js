// Arena: a 1-D lane from the start to the exit at tile 8, cover at tile 2, a turret in between.
GameViews.register('arena', (s, h) => {
  const EXIT = 8, COVER = 2, ATTACK_RANGE = 3, TURRET_RANGE = 4;
  const gap = Math.abs(s.enemy_x - s.player_x);
  const cells = [];
  for (let x = 0; x <= EXIT; x++) {
    const here = x === s.player_x, turret = x === s.enemy_x && s.enemy_hp > 0;
    const inTurretRange = s.enemy_hp > 0 && Math.abs(s.enemy_x - x) <= TURRET_RANGE;
    cells.push({
      text: here ? '@' : turret ? 'T' : x === EXIT ? '⇥' : x === COVER ? '▤' : '',
      cls: [here && 'player', turret && 'enemy', x === EXIT && !here && 'exit', x === COVER && !here && 'cover',
            inTurretRange && !turret && 'range', here && s.enemy_windup && inTurretRange && 'danger'].filter(Boolean).join(' '),
      title: `tile ${x}`,
    });
  }
  return h.track(cells, 'lane') +
    h.chips([
      s.enemy_windup ? {text: '포탑 조준 중 — 다음 턴 발사', cls: 'bad'} : {text: '포탑 조준 안 함', cls: 'good'},
      gap <= ATTACK_RANGE && s.enemy_hp > 0 ? {text: `사거리 안 (${gap}칸)`, cls: 'good'} : `포탑까지 ${gap}칸`,
      s.player_x === COVER ? {text: '엄폐 중', cls: 'good'} : null,
      s.ammo === 0 ? {text: '탄약 없음', cls: 'warn'} : null,
    ]) +
    h.bar('체력', s.hp, 100) + h.bar('탄약', s.ammo, 3, s.ammo ? '' : 'bad') + h.bar('포탑 체력', s.enemy_hp, 60, 'bad') +
    h.stats([h.stat('턴', s.turn), h.stat('시나리오', s.scenario), h.stat('결과', s.outcome, s.outcome === 'playing' ? '' : s.outcome === 'won' ? 'good' : 'bad')]);
});
