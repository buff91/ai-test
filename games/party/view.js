// Party: the paladin (you), the AI knight, and either a goblin pack or the dragon.
GameViews.register('party', (s, h) => {
  const goblins = (s.goblins || []).map((hp, i) => ({text: hp > 0 ? '고' : '×', cls: hp > 0 ? 'enemy' : 'dim', title: `고블린 ${i + 1}: 체력 ${hp}`}));
  const dragon = s.dragon_hp > 0;
  return h.bar('성기사 · 직접 조작', s.paladin_hp, 60) + h.bar('기사 · 자동 공격', s.knight_hp, 80) + h.bar('마나', s.mana, 30, '') +
    (goblins.length ? h.track(goblins, '고블린') + h.chips((s.goblins || []).map((hp, i) => hp > 0 ? `${i + 1}번 ${hp} HP` : null)) : '') +
    (dragon ? h.bar('드래곤', s.dragon_hp, 90, 'bad') +
      h.chips([{text: `다음 행동: ${({claw: '발톱 공격', charge: '힘 모으기', strike: '강타'})[s.dragon_next] || s.dragon_next}`, cls: s.dragon_next === 'strike' ? 'bad' : s.dragon_next === 'charge' ? 'warn' : ''}]) : '') +
    h.chips([s.knight_hp <= 0 ? {text: '기사 쓰러짐', cls: 'bad'} : null, s.phoenix_feathers ? {text: `부활 깃털 ${s.phoenix_feathers}`, cls: 'good'} : {text: '깃털 없음', cls: 'warn'}]) +
    h.stats([h.stat('턴', s.turn), h.stat('시나리오', s.scenario), h.stat('결과', ({playing: '진행 중', won: '목표 달성', lost: '도전 실패', draw: '시간 종료'})[s.outcome] || s.outcome)]);
});
