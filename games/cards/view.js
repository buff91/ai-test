// Cards: the hand, energy, and what the enemy intends to do next.
GameViews.register('cards', (s, h) => {
  const hand = s.hand || {};
  const intent = s.enemy_intent === 'attack' ? {text: `적 의도: 공격 ${s.enemy_amount}`, cls: s.enemy_amount > s.block ? 'bad' : 'good'}
    : {text: `적 의도: ${s.enemy_intent} ${s.enemy_amount}`, cls: 'warn'};
  const incoming = s.enemy_intent === 'attack' ? Math.max(0, s.enemy_amount - s.block) : 0;
  const cards = [];
  for (const [kind, n] of Object.entries(hand)) for (let i = 0; i < n; i++) cards.push({text: kind[0].toUpperCase(), cls: kind === 'attack' ? 'enemy' : kind === 'block' ? 'cover' : 'exit', title: kind});
  return h.chips([intent, incoming ? {text: `막지 않으면 ${incoming} 피해`, cls: incoming >= s.hp ? 'bad' : 'warn'} : {text: '이번 턴 피해 없음', cls: 'good'}]) +
    h.track(cards.length ? cards : [{text: '·', cls: 'dim'}], '손패') +
    h.chips(Object.entries(hand).map(([k, n]) => `${k} ×${n}`)) +
    h.bar('체력', s.hp, 40) + h.bar('방어', s.block, 20, 'good') + h.bar('에너지', s.energy, 3, '') + h.bar('적 체력', s.enemy_hp, 50, 'bad') +
    h.stats([h.stat('턴', s.turn), h.stat('힘', s.strength), h.stat('물약', s.potions, s.potions ? '' : 'warn'), h.stat('결과', s.outcome)]);
});
