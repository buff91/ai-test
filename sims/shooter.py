"""realtime.action: a corridor run past gunners (telegraphed shots) and rushers (melee)."""

from dataclasses import dataclass

from sims.base import Sim


EXIT, RANGE, SHOT, MELEE, AGGRO = 10, 5, 25, 20, 6


@dataclass
class ShooterSim(Sim):
    ACTIONS = ("attack", "dodge", "reload", "retreat", "advance", "wait")
    HOLD = "wait"
    HORIZON = 9
    SCENARIOS = {
        "corridor": {"enemies": (("gunner", 5, 2, False), ("rusher", 8, 2, False))},
        "gunner_aiming": {"x": 2, "enemies": (("gunner", 5, 2, True),)},
        "empty_mag": {"x": 3, "ammo": 0, "enemies": (("gunner", 7, 2, False),)},
        "rusher_close": {"x": 4, "enemies": (("rusher", 6, 2, False),)},
        "clear_path": {"x": 6},
        "last_stand": {"x": 4, "hp": 25, "ammo": 1, "enemies": (("gunner", 7, 1, True),)},
    }

    scenario: str = "corridor"
    x: int = 0
    hp: int = 100
    ammo: int = 3
    enemies: tuple = ()   # (kind, tile, hits left, aiming)
    turn: int = 1
    outcome: str = "playing"

    def snapshot(self):
        return {"scenario": self.scenario, "turn": self.turn, "player_x": self.x, "hp": self.hp, "ammo": self.ammo,
                "enemies": [dict(zip(("kind", "tile", "hits_left", "aiming"), e)) for e in self.enemies],
                "outcome": self.outcome}

    def describe(self):
        lines = []
        for kind, tile, hits, aiming in self.enemies:
            where = "at tile %d (%d tiles ahead, %d hit%s left)" % (tile, tile - self.x, hits, "" if hits == 1 else "s")
            if kind == "gunner":
                state = ("aiming: it fires for %d this turn unless the player dodges or ends beyond %d tiles" % (SHOT, RANGE)
                         if aiming else "not aiming; within %d tiles it aims this turn and fires next turn" % RANGE)
            else:
                state = ("it strikes for %d at the end of any turn the player is adjacent (dodge does not help), "
                         "and otherwise charges one tile closer when within %d tiles" % (MELEE, AGGRO))
            lines.append("A %s %s, %s." % (kind, where, state))
        return ("Turn %d. The player is at tile %d of a corridor; the exit is at tile %d. Health %d of 100, ammo %d of 3. "
                "%s Attack spends one ammo to hit the nearest enemy ahead within %d tiles. Dodge avoids gunner shots "
                "this turn. Reload refills ammo. Advance moves one tile toward the exit unless a living enemy stands "
                "in the next tile; retreat moves one tile back. Choose the single best next action."
                % (self.turn, self.x, EXIT, self.hp, self.ammo, " ".join(lines) or "No enemies remain.", RANGE))

    def step(self, action):
        if self.outcome != "playing":
            return ["게임이 끝났습니다."]
        if action not in self.ACTIONS:
            raise ValueError("Unknown action: %s" % action)
        events = []
        enemies = list(self.enemies)
        if action == "attack":
            ahead = [i for i, e in enumerate(enemies) if 0 < e[1] - self.x <= RANGE]
            if self.ammo < 1:
                events.append("탄약이 없습니다.")
            else:
                self.ammo -= 1
                if ahead:
                    i = min(ahead, key=lambda i: enemies[i][1])
                    kind, tile, hits, aiming = enemies[i]
                    enemies[i] = (kind, tile, hits - 1, aiming)
                    events.append("%s 명중." % kind)
                else:
                    events.append("사거리 안에 적이 없습니다.")
        elif action == "reload":
            self.ammo = 3
        elif action == "retreat":
            self.x = max(0, self.x - 1)
        elif action == "advance":
            if any(e[1] == self.x + 1 and e[2] > 0 for e in enemies):
                events.append("앞이 막혔습니다.")
            else:
                self.x += 1
        enemies = [e for e in enemies if e[2] > 0]
        moved = []
        for kind, tile, hits, aiming in enemies:
            distance = tile - self.x
            if kind == "gunner":
                if 0 < distance <= RANGE:
                    if aiming:
                        aiming = False
                        if action == "dodge":
                            events.append("사격을 피했습니다.")
                        else:
                            self.hp = max(0, self.hp - SHOT)
                            events.append("사격에 맞았습니다.")
                    else:
                        aiming = True
                else:
                    aiming = False
            elif distance <= 1:
                self.hp = max(0, self.hp - MELEE)
                events.append("돌격병에게 맞았습니다.")
            elif distance <= AGGRO and not any(e[1] == tile - 1 for e in enemies):
                tile -= 1
            moved.append((kind, tile, hits, aiming))
        self.enemies = tuple(moved)
        self.turn += 1
        if self.hp == 0:
            self.outcome = "lost"
        elif self.x >= EXIT:
            self.outcome = "won"
        elif self.turn > 40:
            self.outcome = "draw"
        return events

    def legal(self):
        blocked = any(e[1] == self.x + 1 for e in self.enemies)
        return tuple(a for a in self.ACTIONS if not (a == "attack" and not self.ammo or a == "reload" and self.ammo == 3
                                                     or a == "retreat" and self.x == 0 or a == "advance" and blocked))

    def vital(self):
        return self.hp

    def heuristic(self):
        return self.x * 8 + self.hp * 0.6 - sum(e[2] for e in self.enemies) * 12 + self.ammo * 2
