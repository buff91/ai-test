"""turn.tactics: the original turret arena (game_core.Arena) with its own truth."""

from dataclasses import dataclass

from game_core import ACTIONS, Arena
from sims.base import Sim


@dataclass
class ArenaSim(Arena, Sim):
    ACTIONS = ACTIONS
    HOLD = "wait"
    HORIZON = 10
    SCENARIOS = {
        "opening": {"player_x": 1, "hp": 100, "ammo": 3, "enemy_hp": 60, "windup": False},
        "opportunity": {"player_x": 4, "hp": 100, "ammo": 3, "enemy_hp": 60, "windup": False},
        "telegraph": {"player_x": 4, "hp": 100, "ammo": 3, "enemy_hp": 60, "windup": True},
        "empty_mag": {"player_x": 3, "hp": 100, "ammo": 0, "enemy_hp": 60, "windup": False},
        "crisis": {"player_x": 4, "hp": 25, "ammo": 1, "enemy_hp": 60, "windup": True},
    }

    def vital(self):
        return self.hp

    def heuristic(self):
        # Kill the turret first (in range from tile 3), then walk to the exit at tile 8.
        progress = self.player_x * 8 if self.enemy_hp <= 0 else min(self.player_x, 3) * 4
        return self.hp + (60 - self.enemy_hp) * 2 + self.ammo * 2 + progress
