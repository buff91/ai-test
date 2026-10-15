"""Small turn-based arena used by the live Jev controller demo."""

from dataclasses import dataclass


ACTIONS = ("attack", "dodge", "reload", "retreat", "advance", "wait")
ACTION_KO = {
    "attack": "공격",
    "dodge": "회피",
    "reload": "재장전",
    "retreat": "후퇴",
    "advance": "전진",
    "wait": "대기",
}

SCENARIOS = {
    "교전 시작": {"player_x": 1, "hp": 100, "ammo": 3, "enemy_hp": 60, "windup": False},
    "공격 기회": {"player_x": 4, "hp": 100, "ammo": 3, "enemy_hp": 60, "windup": False},
    "공격 예고": {"player_x": 4, "hp": 100, "ammo": 3, "enemy_hp": 60, "windup": True},
    "빈 탄창": {"player_x": 3, "hp": 100, "ammo": 0, "enemy_hp": 60, "windup": False},
    "위기 상황": {"player_x": 4, "hp": 25, "ammo": 1, "enemy_hp": 60, "windup": True},
}


@dataclass
class Arena:
    scenario: str = "교전 시작"
    player_x: int = 1
    hp: int = 100
    ammo: int = 3
    enemy_x: int = 6
    enemy_hp: int = 60
    windup: bool = False
    turn: int = 1
    outcome: str = "playing"

    @classmethod
    def from_scenario(cls, name):
        if name not in SCENARIOS:
            raise ValueError("Unknown scenario: %s" % name)
        return cls(scenario=name, **SCENARIOS[name])

    def snapshot(self):
        return {
            "scenario": self.scenario,
            "turn": self.turn,
            "player_x": self.player_x,
            "hp": self.hp,
            "ammo": self.ammo,
            "enemy_x": self.enemy_x,
            "enemy_hp": self.enemy_hp,
            "enemy_windup": self.windup,
            "outcome": self.outcome,
        }

    def describe(self):
        distance = abs(self.enemy_x - self.player_x)
        enemy = (
            "The enemy turret has been destroyed."
            if self.enemy_hp <= 0
            else "The enemy turret has %d of 60 health and is %d tiles away."
            % (self.enemy_hp, distance)
        )
        threat = (
            "Its charged shot will fire this turn if it remains alive and within 4 tiles."
            if self.windup and self.enemy_hp > 0
            else "It is not firing this turn; if the player ends within 4 tiles, it may charge a shot for the next turn."
        )
        cover = (
            "The player is behind cover at tile 2, which blocks turret shots."
            if self.player_x == 2
            else "Cover that blocks turret shots is at tile 2."
        )
        return (
            "Turn %d. Player: %d of 100 health, %d of 3 ammo, at tile %d. "
            "%s %s %s "
            "Attack costs one ammo and hits for 30 damage within 3 tiles. "
            "Dodge avoids a shot this turn. Reload fills ammo to 3. "
            "Advance moves one tile toward the exit; retreat moves one tile away. "
            "The exit is at tile 8. To win, destroy the turret and reach the exit. "
            "Choose the single best next action."
            % (self.turn, self.hp, self.ammo, self.player_x, enemy, threat, cover)
        )

    def step(self, action):
        if self.outcome != "playing":
            return ["게임이 종료되었습니다. 초기화 후 다시 시작하세요."]
        if action not in ACTIONS:
            raise ValueError("Unknown action: %s" % action)

        events = []
        if action == "attack":
            if self.ammo <= 0:
                events.append("탄창이 비어 공격하지 못했습니다.")
            else:
                self.ammo -= 1
                if self.enemy_hp > 0 and abs(self.enemy_x - self.player_x) <= 3:
                    self.enemy_hp = max(0, self.enemy_hp - 30)
                    events.append("포탑에 30 피해를 입혔습니다.")
                else:
                    events.append("사거리 밖으로 발사했습니다.")
        elif action == "dodge":
            events.append("이번 턴의 사격을 회피할 준비를 했습니다.")
        elif action == "reload":
            self.ammo = 3
            events.append("탄창을 채웠습니다.")
        elif action == "retreat":
            self.player_x = max(0, self.player_x - 1)
            events.append("한 칸 후퇴했습니다.")
        elif action == "advance":
            self.player_x = min(8, self.player_x + 1)
            events.append("출구 쪽으로 한 칸 전진했습니다.")
        else:
            events.append("위치를 유지했습니다.")

        if self.enemy_hp > 0:
            distance = abs(self.enemy_x - self.player_x)
            if distance <= 4:
                if self.windup:
                    self.windup = False
                    if action == "dodge":
                        events.append("포탑의 사격을 회피했습니다.")
                    elif self.player_x == 2:
                        events.append("엄폐물이 포탑의 사격을 막았습니다.")
                    else:
                        self.hp = max(0, self.hp - 25)
                        events.append("포탑 사격으로 25 피해를 받았습니다.")
                else:
                    self.windup = True
                    events.append("포탑이 다음 사격을 충전합니다.")
            else:
                self.windup = False
        else:
            self.windup = False

        if self.hp <= 0:
            self.outcome = "lost"
            events.append("플레이어가 쓰러졌습니다.")
        elif self.enemy_hp <= 0 and self.player_x >= 8:
            self.outcome = "won"
            events.append("포탑을 파괴하고 출구에 도착했습니다!")
        elif self.turn >= 40:
            self.outcome = "draw"
            events.append("40턴 제한에 도달했습니다.")
        self.turn += 1
        return events
