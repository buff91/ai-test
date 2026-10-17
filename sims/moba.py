"""Small three-lane MOBA: minion waves, tower protection, recall and a core objective.

A step is one world beat. Desktop input calls apply() immediately; its independent
clock calls world_step(). Search uses both, so references describe the next beat.
"""
from dataclasses import dataclass
from sims.base import Sim

LANES = ("top", "mid", "bot")


@dataclass
class MobaSim(Sim):
    ACTIONS = ("advance", "retreat", "attack", "skill", "recall", "lane_left", "lane_right", "wait")
    HOLD = "wait"
    HORIZON = 4
    MAX_VITAL = 100
    SCENARIOS = {
        "wave_clear": {"lane": 1, "position": 3, "enemy_minions": (2, 4, 2)},
        "tower_push": {"lane": 1, "position": 5, "allied_minions": (2, 4, 2), "enemy_minions": (2, 0, 2), "enemy_towers": (60, 28, 60)},
        "unsafe_dive": {"lane": 1, "position": 5, "hp": 24, "allied_minions": (2, 0, 2), "enemy_minions": (2, 0, 2)},
        "defend_bot": {"lane": 1, "position": 2, "allied_minions": (2, 2, 0), "enemy_minions": (2, 2, 4), "fronts": (3, 3, 1), "allied_towers": (60, 60, 14)},
        "core_finish": {"lane": 1, "position": 6, "allied_minions": (2, 3, 2), "enemy_minions": (2, 0, 2), "enemy_towers": (60, 0, 60), "enemy_core": 20},
    }
    scenario: str = "wave_clear"
    hp: int = 100
    lane: int = 1
    position: int = 3
    allied_minions: tuple = (2, 2, 2)
    enemy_minions: tuple = (2, 2, 2)
    fronts: tuple = (3, 3, 3)
    allied_towers: tuple = (60, 60, 60)
    enemy_towers: tuple = (60, 60, 60)
    allied_core: int = 100
    enemy_core: int = 100
    cooldown: int = 0
    gold: int = 0
    turn: int = 1
    outcome: str = "playing"

    def snapshot(self):
        return {"scenario": self.scenario, "turn": self.turn, "hero_hp": self.hp,
                "lane": LANES[self.lane], "position": self.position,
                "allied_minions": list(self.allied_minions), "enemy_minions": list(self.enemy_minions),
                "fronts": list(self.fronts), "allied_towers": list(self.allied_towers),
                "enemy_towers": list(self.enemy_towers), "allied_core": self.allied_core,
                "enemy_core": self.enemy_core, "skill_cooldown": self.cooldown,
                "gold": self.gold, "outcome": self.outcome}

    def describe(self):
        lanes = "; ".join("%s: front %d, allied/enemy minions %d/%d, allied/enemy tower HP %d/%d" %
                         (LANES[i], self.fronts[i], self.allied_minions[i], self.enemy_minions[i],
                          self.allied_towers[i], self.enemy_towers[i]) for i in range(3))
        return ("Three-lane MOBA beat %d. Hero HP %d/100, lane %s, position %d (home=0, enemy core=6), "
                "skill cooldown %d, gold %d. Allied/enemy core HP %d/%d. %s. "
                "advance/retreat move one cell; lane_left/right rotate one lane. attack at the minion front clears one enemy minion; "
                "skill clears three if ready, then cools for three beats. At position 5+ and with no enemy minions, attack/skill "
                "deal 14/24 to the lane tower; at position 6 after that tower falls they hit the enemy core. "
                "An enemy tower hits for 18 when you enter position 5+ without allied minions. Enemy minions within one cell "
                "hit for 3 each. Minions clash, survivors push their lane and damage towers/cores; two new minions per side spawn "
                "every four beats. recall moves home and heals 30. Destroy the enemy core; hero death or allied core destruction loses. "
                "Choose one action." % (self.turn, self.hp, LANES[self.lane], self.position, self.cooldown,
                                            self.gold, self.allied_core, self.enemy_core, lanes))

    def _set_lane(self, field, value):
        seq = list(getattr(self, field)); seq[self.lane] = max(0, value); setattr(self, field, tuple(seq))

    def apply(self, action):
        if action not in self.ACTIONS:
            raise ValueError("Unknown action: %s" % action)
        if self.outcome != "playing":
            return []
        events = []
        if action == "advance": self.position = min(6, self.position + 1)
        elif action == "retreat": self.position = max(0, self.position - 1)
        elif action == "lane_left": self.lane = max(0, self.lane - 1)
        elif action == "lane_right": self.lane = min(2, self.lane + 1)
        elif action == "recall":
            self.position = 0; self.hp = min(100, self.hp + 30)
            events.append("Recalled to base: +30 HP.")
        elif action in ("attack", "skill"):
            if action == "skill" and self.cooldown:
                return ["Skill is cooling down."]
            skill = action == "skill"
            if skill: self.cooldown = 3
            count = self.enemy_minions[self.lane]
            if count and abs(self.position - self.fronts[self.lane]) <= 1:
                kills = min(count, 3 if skill else 1)
                self._set_lane("enemy_minions", count - kills)
                self.gold += kills * 10
                events.append("Cleared %d enemy minion(s)." % kills)
            elif not count and self.position >= 5 and self.enemy_towers[self.lane]:
                self._set_lane("enemy_towers", self.enemy_towers[self.lane] - (24 if skill else 14))
                events.append("Hit the %s tower." % LANES[self.lane])
            elif not count and self.position == 6 and self.enemy_towers[self.lane] == 0:
                self.enemy_core = max(0, self.enemy_core - (24 if skill else 14))
                events.append("Hit the enemy core.")
            else: events.append("No target in range.")
        self._finish()
        return events

    def world_step(self):
        if self.outcome != "playing": return []
        events = []
        damage = 0
        if self.position >= 5 and self.enemy_towers[self.lane] and self.allied_minions[self.lane] == 0:
            damage += 18
        if abs(self.position - self.fronts[self.lane]) <= 1:
            damage += self.enemy_minions[self.lane] * 3
        self.hp = max(0, self.hp - damage)
        if damage: events.append("Hero took %d damage." % damage)
        allied, enemy, fronts = list(self.allied_minions), list(self.enemy_minions), list(self.fronts)
        own_towers, towers = list(self.allied_towers), list(self.enemy_towers)
        for i in range(3):
            if allied[i] and enemy[i]: allied[i] -= 1; enemy[i] -= 1
            elif allied[i]:
                fronts[i] = min(6, fronts[i] + 1)
                if fronts[i] >= 5:
                    if towers[i]: towers[i] = max(0, towers[i] - allied[i] * 3)
                    else: self.enemy_core = max(0, self.enemy_core - allied[i] * 2)
            elif enemy[i]:
                fronts[i] = max(0, fronts[i] - 1)
                if fronts[i] <= 1:
                    if own_towers[i]: own_towers[i] = max(0, own_towers[i] - enemy[i] * 3)
                    else: self.allied_core = max(0, self.allied_core - enemy[i] * 2)
        self.turn += 1
        if self.turn % 4 == 0:
            allied = [min(6, n + 2) for n in allied]
            enemy = [min(6, n + 2) for n in enemy]
            events.append("New minion waves spawned in all three lanes.")
        self.allied_minions, self.enemy_minions, self.fronts = tuple(allied), tuple(enemy), tuple(fronts)
        self.allied_towers, self.enemy_towers = tuple(own_towers), tuple(towers)
        self.cooldown = max(0, self.cooldown - 1)
        self._finish()
        return events

    def _finish(self):
        if self.hp <= 0 or self.allied_core <= 0: self.outcome = "lost"
        elif self.enemy_core <= 0: self.outcome = "won"
        elif self.turn > 90: self.outcome = "draw"

    def step(self, action):
        return self.apply(action) + self.world_step()

    def legal(self):
        usable = {"advance": self.position < 6, "retreat": self.position > 0,
                  "lane_left": self.lane > 0, "lane_right": self.lane < 2,
                  "skill": self.cooldown == 0, "recall": self.hp < 100 or self.position > 0}
        return tuple(a for a in self.ACTIONS if usable.get(a, True))

    def vital(self): return self.hp

    def heuristic(self):
        return (self.hp + self.allied_core * 2 - self.enemy_core * 3 +
                sum(self.allied_towers) - sum(self.enemy_towers) * 1.5 +
                sum(self.allied_minions) * 5 - sum(self.enemy_minions) * 5 +
                self.gold * .15 + self.position * 2 - self.cooldown)
