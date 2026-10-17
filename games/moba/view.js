// Three lanes have distinct minion pressure, towers and a shared core objective.
GameViews.register('moba', (s, h) => {
  const names = {top: '상단', mid: '중단', bot: '하단'};
  const outcomes = {playing: '진행 중', won: '목표 달성', lost: '도전 실패', draw: '시간 종료'};
  const position = Number.isFinite(s.realtime_position) ? s.realtime_position.toFixed(1) : s.position;
  const cooldown = Number.isFinite(s.skill_cooldown_seconds) ? `${s.skill_cooldown_seconds.toFixed(1)}초` : s.skill_cooldown;
  let lanes = ['top', 'mid', 'bot'].map((lane, i) =>
    h.chips([{text: `${names[lane]} ${s.lane === lane ? '◆ 영웅' : ''}`, cls: s.lane === lane ? 'good' : ''},
      `전선 ${s.fronts?.[i] ?? 0}`, `아군 ${s.allied_minions?.[i] ?? 0} · 적 ${s.enemy_minions?.[i] ?? 0}`]) +
    h.bar(`${names[lane]} 아군 포탑`, s.allied_towers?.[i] ?? 0, 60) +
    h.bar(`${names[lane]} 적 포탑`, s.enemy_towers?.[i] ?? 0, 60, 'bad')).join('');
  return h.bar('영웅 체력', s.hero_hp, 100) +
    h.chips([`전선 ${names[s.lane] || s.lane}`, `위치 ${position}/6`, `스킬 대기 ${cooldown}`, `골드 ${s.gold}`]) + lanes +
    h.bar('아군 코어', s.allied_core, 100) + h.bar('적 코어', s.enemy_core, 100, 'bad') +
    h.stats([h.stat(Number.isFinite(s.elapsed) ? '경과 시간' : '경과 박자', Number.isFinite(s.elapsed) ? `${s.elapsed.toFixed(1)}초` : s.turn),
      h.stat('시나리오', s.scenario), h.stat('결과', s.paused ? '일시정지' : outcomes[s.outcome] || s.outcome)]);
});
