"""realtime.topdown: hold out against enemy groups closing in, with a skill, a potion, and room to back off."""

from dataclasses import dataclass

from sims.base import Sim


GROUP_HP, HIT, SKILL, SKILL_RANGE, SKILL_COOLDOWN, BITE, POTION = 30, 15, 20, 3, 4, 9, 40


@dataclass
class SwarmSim(Sim):
    ACTIONS = ("attack_target", "skill_on_target", "kite_back", "drink_potion", "hold")
    HOLD = "hold"
    HORIZON = 8
    SCENARIOS = {
        "low_hp": {"hp": 12, "groups": ((2, 30), (3, 30))},
        "grouped": {"hp": 90, "groups": ((1, 30), (2, 30), (3, 30))},
        "single_in_range": {"hp": 80, "groups": ((1, 30),), "cooldown": 3},
        "swarmed": {"hp": 35, "potions": 0, "cooldown": 2, "groups": ((1, 30), (1, 30), (2, 30), (2, 30))},
        "approaching": {"groups": ((5, 30), (6, 30))},
    }

    scenario: str = "low_hp"
    hp: int = 100
    potions: int = 1
    cooldown: int = 0
    room: int = 3
    groups: tuple = ()   # (distance, hp)
    turn: int = 1
    outcome: str = "playing"

    def snapshot(self):
        return {"scenario": self.scenario, "turn": self.turn, "hp": self.hp, "potions": self.potions,
                "skill_cooldown": self.cooldown, "room_to_back_off": self.room,
                "groups": [{"distance": d, "hp": hp} for d, hp in self.groups], "outcome": self.outcome}

    def describe(self):
        groups = ", ".join("%d HP at distance %d" % (hp, d) for d, hp in sorted(self.groups)) or "none"
        skill = "ready" if self.cooldown == 0 else "on cooldown for %d more turns" % self.cooldown
        return ("Turn %d of a top-down action RPG. Player health %d of 100, %d potions (each heals %d), skill %s, "
                "room to back off %d times. Enemy groups: %s. Each group at distance 1 or less bites for %d at the end "
                "of the turn; farther groups move one step closer. attack_target hits the nearest group within "
                "distance 1 for %d. skill_on_target hits every group within distance %d for %d, then needs %d turns "
                "to recharge. kite_back pushes every group 2 steps away. Win by clearing all groups. "
                "Choose the single best next action."
                % (self.turn, self.hp, self.potions, POTION, skill, self.room, groups, BITE, HIT, SKILL_RANGE,
                   SKILL, SKILL_COOLDOWN))

    def step(self, action):
        if self.outcome != "playing":
            return ["게임이 끝났습니다."]
        if action not in self.ACTIONS:
            raise ValueError("Unknown action: %s" % action)
        events = []
        groups = sorted(self.groups)
        if action == "attack_target":
            if groups and groups[0][0] <= 1:
                groups[0] = (groups[0][0], groups[0][1] - HIT)
                events.append("가장 가까운 무리를 공격했습니다.")
            else:
                events.append("사거리 안에 적이 없습니다.")
        elif action == "skill_on_target":
            if self.cooldown == 0 and groups and groups[0][0] <= SKILL_RANGE:
                groups = [(d, hp - SKILL if d <= SKILL_RANGE else hp) for d, hp in groups]
                self.cooldown = SKILL_COOLDOWN + 1
                events.append("스킬을 썼습니다.")
            else:
                events.append("스킬을 쓸 수 없습니다.")
        elif action == "kite_back":
            if self.room > 0:
                self.room -= 1
                groups = [(d + 2, hp) for d, hp in groups]
                events.append("뒤로 빠졌습니다.")
            else:
                events.append("더 물러설 곳이 없습니다.")
        elif action == "drink_potion":
            if self.potions > 0:
                self.potions -= 1
                self.hp = min(100, self.hp + POTION)
                events.append("물약을 마셨습니다.")
            else:
                events.append("물약이 없습니다.")
        groups = [(d, hp) for d, hp in groups if hp > 0]
        bites = sum(1 for d, _ in groups if d <= 1)
        if bites:
            self.hp = max(0, self.hp - BITE * bites)
            events.append("%d 무리에게 물렸습니다." % bites)
        self.groups = tuple(sorted((d if d <= 1 else d - 1, hp) for d, hp in groups))
        self.cooldown = max(0, self.cooldown - 1)
        self.turn += 1
        if self.hp == 0:
            self.outcome = "lost"
        elif not self.groups:
            self.outcome = "won"
        elif self.turn > 30:
            self.outcome = "draw"
        return events

    def legal(self):
        nearest = min((d for d, _ in self.groups), default=99)
        usable = {"attack_target": nearest <= 1, "skill_on_target": self.cooldown == 0 and nearest <= SKILL_RANGE,
                  "kite_back": self.room > 0, "drink_potion": self.potions > 0 and self.hp < 100}
        return tuple(a for a in self.ACTIONS if usable.get(a, True))

    def vital(self):
        return self.hp

    def heuristic(self):
        return (self.hp * 0.8 - sum(hp for _, hp in self.groups) + self.potions * 15 + self.room * 3 - self.cooldown * 2
                + sum(min(d, 4) for d, _ in self.groups))
