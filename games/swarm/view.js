// Swarm: groups closing in by distance, the skill's reach (3), and how much room is left behind.
GameViews.register('swarm', (s, h) => {
  const SKILL_RANGE = 3, GROUP_HP = 30;
  const far = Math.max(6, ...(s.groups || []).map(g => g.distance));
  const cells = [];
  for (let d = -s.room_to_back_off; d <= far; d++) {
    const here = (s.groups || []).filter(g => g.distance === d);
    cells.push({text: d === 0 ? '@' : here.length ? String(here.length) : d < 0 ? '·' : '',
                cls: [d === 0 && 'player', here.length && 'enemy', here.length && d <= 1 && 'danger', d < 0 && 'cover',
                      d > 0 && d <= SKILL_RANGE && !here.length && 'range'].filter(Boolean).join(' '),
                title: d < 0 ? '물러설 수 있는 공간' : `거리 ${d}${here.length ? ` · ${here.length}무리` : ''}`});
  }
  return h.track(cells, '거리') +
    `<div class="gv-note">왼쪽 노란 칸 = 물러설 공간 · 숫자 = 그 거리의 무리 수 · 파란 칸 = 스킬 사거리</div>` +
    (s.groups || []).map((g, i) => h.bar(`무리 ${i + 1} (거리 ${g.distance})`, g.hp, GROUP_HP, 'bad')).join('') +
    h.bar('체력', s.hp, 100) +
    h.chips([s.skill_cooldown ? {text: `스킬 쿨다운 ${s.skill_cooldown}`, cls: 'warn'} : {text: '스킬 사용 가능', cls: 'good'},
             s.potions ? {text: `물약 ${s.potions}`, cls: 'good'} : {text: '물약 없음', cls: 'warn'}]) +
    h.stats([h.stat('턴', s.turn), h.stat('시나리오', s.scenario), h.stat('결과', s.outcome)]);
});
