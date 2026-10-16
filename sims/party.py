"""turn.rpg: a paladin and an auto-fighting knight against goblins or a dragon with a telegraphed strike."""

from dataclasses import dataclass

from sims.base import Sim


HERO_MAX, ALLY_MAX = 60, 80
ATTACK, AOE, AOE_COST, HEAL, HEAL_COST, REVIVE = 18, 9, 10, 30, 8, 40
ALLY_HIT, GOBLIN_HIT, CLAW, STRIKE = 10, 6, 10, 45
# The dragon repeats: claw the knight, charge (no damage), strike the paladin.
BOSS_PHASES = ("claw", "charge", "strike")


@dataclass
class PartySim(Sim):
    ACTIONS = ("attack", "aoe_skill", "heal", "guard", "use_item")
    HOLD = "guard"
    HORIZON = 8
    MAX_VITAL = HERO_MAX + ALLY_MAX
    QUESTION_IDS = {"action": "action", "danger": "danger", "incoming": None}
    SCENARIOS = {
        "goblin_pack": {"goblins": (20, 20, 20, 20)},
        "dragon_strike": {"hero_hp": 40, "dragon": 90, "boss_phase": 2},
        "knight_low": {"ally_hp": 9, "items": 0, "goblins": (20, 20, 20)},
        "knight_down": {"ally_hp": 0, "dragon": 90, "boss_phase": 0},
        "last_goblin": {"goblins": (15,)},
    }

    scenario: str = "goblin_pack"
    hero_hp: int = HERO_MAX
    ally_hp: int = ALLY_MAX
    mana: int = 30
    items: int = 1
    goblins: tuple = ()
    dragon: int = 0
    boss_phase: int = 0
    turn: int = 1
    outcome: str = "playing"

    def _enemies(self):
        return [hp for hp in self.goblins if hp > 0] + ([self.dragon] if self.dragon > 0 else [])

    def snapshot(self):
        return {"scenario": self.scenario, "turn": self.turn, "paladin_hp": self.hero_hp, "knight_hp": self.ally_hp,
                "mana": self.mana, "phoenix_feathers": self.items, "goblins": list(self.goblins),
                "dragon_hp": self.dragon, "dragon_next": BOSS_PHASES[self.boss_phase] if self.dragon > 0 else None,
                "outcome": self.outcome}

    def describe(self):
        enemies = []
        if self.goblins:
            enemies.append("%d goblins with %s HP; each hits the knight for %d (the paladin if the knight is down)"
                           % (len(self.goblins), "/".join(map(str, self.goblins)), GOBLIN_HIT))
        if self.dragon > 0:
            next_move = {"claw": "claw the knight for %d (the paladin if the knight is down)" % CLAW,
                         "charge": "charge up, dealing no damage, and strike next round",
                         "strike": "unleash its charged strike on the paladin for %d" % STRIKE}[BOSS_PHASES[self.boss_phase]]
            enemies.append("a dragon with %d HP that will %s this round" % (self.dragon, next_move))
        knight = ("the knight has %d of %d HP and attacks the weakest enemy for %d each round"
                  % (self.ally_hp, ALLY_MAX, ALLY_HIT) if self.ally_hp > 0 else "the knight has fallen (0 HP)")
        return ("Round %d of a party RPG battle. You control the paladin: %d of %d HP, %d mana, %d phoenix feathers; %s. "
                "Enemies: %s. Paladin actions: attack deals %d to the weakest enemy; aoe_skill deals %d to every enemy "
                "for %d mana; heal restores %d HP to the most hurt living ally for %d mana; guard halves the damage the "
                "paladin takes this round; use_item revives a fallen knight with %d HP. The party loses if the paladin "
                "falls. Mana regenerates 2 per round. Choose the single best next action."
                % (self.turn, self.hero_hp, HERO_MAX, self.mana, self.items, knight, "; ".join(enemies) or "none",
                   ATTACK, AOE, AOE_COST, HEAL, HEAL_COST, REVIVE))

    def _hit_weakest(self, damage):
        living = [(hp, i) for i, hp in enumerate(self.goblins) if hp > 0]
        if self.dragon > 0:
            living.append((self.dragon, -1))
        if not living:
            return
        _, index = min(living)
        if index < 0:
            self.dragon = max(0, self.dragon - damage)
        else:
            self.goblins = tuple(max(0, hp - damage) if i == index else hp for i, hp in enumerate(self.goblins))

    def _hurt(self, target, damage, guarding):
        if target == "knight" and self.ally_hp > 0:
            self.ally_hp = max(0, self.ally_hp - damage)
        else:
            self.hero_hp = max(0, self.hero_hp - (damage // 2 if guarding else damage))

    def step(self, action):
        if self.outcome != "playing":
            return ["게임이 끝났습니다."]
        if action not in self.ACTIONS:
            raise ValueError("Unknown action: %s" % action)
        events = []
        if action == "attack":
            self._hit_weakest(ATTACK)
            events.append("가장 약한 적을 공격했습니다.")
        elif action == "aoe_skill":
            if self.mana >= AOE_COST:
                self.mana -= AOE_COST
                self.goblins = tuple(max(0, hp - AOE) for hp in self.goblins)
                self.dragon = max(0, self.dragon - AOE)
                events.append("모든 적에게 %d 피해를 주었습니다." % AOE)
            else:
                events.append("마나가 부족합니다.")
        elif action == "heal":
            living = [(hp / cap, name) for name, hp, cap in (("paladin", self.hero_hp, HERO_MAX), ("knight", self.ally_hp, ALLY_MAX)) if hp > 0]
            if self.mana >= HEAL_COST:
                self.mana -= HEAL_COST
                _, name = min(living)
                if name == "knight":
                    self.ally_hp = min(ALLY_MAX, self.ally_hp + HEAL)
                else:
                    self.hero_hp = min(HERO_MAX, self.hero_hp + HEAL)
                events.append("%s을 치유했습니다." % name)
            else:
                events.append("마나가 부족합니다.")
        elif action == "use_item":
            if self.items > 0 and self.ally_hp == 0:
                self.items -= 1
                self.ally_hp = REVIVE
                events.append("기사를 부활시켰습니다.")
            else:
                events.append("아이템을 쓸 대상이 없습니다.")
        else:
            events.append("방어 자세를 취했습니다.")
        if self.ally_hp > 0:
            self._hit_weakest(ALLY_HIT)
        guarding = action == "guard"
        for hp in self.goblins:
            if hp > 0:
                self._hurt("knight", GOBLIN_HIT, guarding)
        if self.dragon > 0:
            phase = BOSS_PHASES[self.boss_phase]
            if phase == "claw":
                self._hurt("knight", CLAW, guarding)
            elif phase == "strike":
                self._hurt("paladin", STRIKE, guarding)
                events.append("드래곤의 강타!")
            self.boss_phase = (self.boss_phase + 1) % len(BOSS_PHASES)
        self.goblins = tuple(hp for hp in self.goblins if hp > 0)
        self.mana = min(30, self.mana + 2)
        self.turn += 1
        if self.hero_hp == 0:
            self.outcome = "lost"
        elif not self._enemies():
            self.outcome = "won"
        elif self.turn > 20:
            self.outcome = "draw"
        return events

    def legal(self):
        usable = {"aoe_skill": self.mana >= AOE_COST, "heal": self.mana >= HEAL_COST,
                  "use_item": self.items > 0 and self.ally_hp == 0}
        return tuple(a for a in self.ACTIONS if usable.get(a, True))

    def vital(self):
        return self.hero_hp + self.ally_hp

    def _standing(self):
        return (self.hero_hp > 0) + (self.ally_hp > 0)

    def danger_truth(self):
        """Will a party member fall if the paladin just attacks: 2 this round, 1 next round, else 0."""
        standing = self._standing()
        first = self.after("attack")
        if first._standing() < standing:
            return 2
        if first.outcome == "playing" and first.after("attack")._standing() < standing:
            return 1
        return 0

    def heuristic(self):
        return (self.hero_hp * 1.5 + self.ally_hp + (40 if self.ally_hp > 0 else 0)
                - sum(self._enemies()) * 1.2 + self.mana * 0.3 + self.items * 10)
