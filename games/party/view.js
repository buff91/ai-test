// Party: the paladin (you), the AI knight, and either a goblin pack or the dragon.
GameViews.register('party', (s, h) => {
  const goblins = (s.goblins || []).map((hp, i) => ({text: hp > 0 ? 'g' : '×', cls: hp > 0 ? 'enemy' : 'dim', title: `goblin ${i + 1}: ${hp} HP`}));
  const dragon = s.dragon_hp > 0;
  return h.bar('성기사 (나)', s.paladin_hp, 60) + h.bar('기사 (AI)', s.knight_hp, 80) + h.bar('마나', s.mana, 30, '') +
    (goblins.length ? h.track(goblins, '고블린') + h.chips((s.goblins || []).map((hp, i) => hp > 0 ? `${i + 1}번 ${hp} HP` : null)) : '') +
    (dragon ? h.bar('드래곤', s.dragon_hp, 90, 'bad') +
      h.chips([{text: `다음 행동: ${s.dragon_next}`, cls: s.dragon_next === 'strike' ? 'bad' : s.dragon_next === 'charge' ? 'warn' : ''}]) : '') +
    h.chips([s.knight_hp <= 0 ? {text: '기사 쓰러짐', cls: 'bad'} : null, s.phoenix_feathers ? {text: `불사조 깃털 ${s.phoenix_feathers}`, cls: 'good'} : {text: '깃털 없음', cls: 'warn'}]) +
    h.stats([h.stat('라운드', s.turn), h.stat('시나리오', s.scenario), h.stat('결과', s.outcome)]);
});
