"""Run a standalone RPG bridge: python -m examples.external_mod.demo --port 8765.

This small state machine demonstrates the mod boundary. It is not a Slay the Spire
mod and does not interact with an installed game. Replace DemoGame callbacks with
the host game's official mod API, dispatching mutations to its game thread.
"""

import argparse
import threading

from gamelab.mod_bridge import ModBridge, ModServer


class DemoGame:
    def __init__(self):
        self.lock = threading.RLock()
        self.revision = 0
        self.hp, self.enemy_hp, self.turn = 30, 30, 1

    def observe(self):
        with self.lock:
            outcome = "victory" if self.enemy_hp <= 0 else "defeat" if self.hp <= 0 else "playing"
            return {"snapshot": {"hp": self.hp, "enemy_hp": self.enemy_hp, "turn": self.turn,
                                 "enemy_intent": "attack 6", "outcome": outcome},
                    "description": "HP %d/30. Enemy HP %d/30, attacks for 6 next turn." % (self.hp, self.enemy_hp),
                    "revision": self.revision, "done": outcome != "playing"}

    def act(self, payload):
        with self.lock:
            if self.hp <= 0 or self.enemy_hp <= 0:
                raise ValueError("전투가 끝났습니다. reset으로 다시 시작하세요.")
            if not isinstance(payload, dict):
                raise ValueError("예제 행동은 command를 가진 객체여야 합니다.")
            command = payload.get("command")
            if command == "attack" and payload.get("target") == 0:
                self.enemy_hp = max(0, self.enemy_hp - 10)
            elif command == "heal":
                self.hp = min(30, self.hp + 10)
            elif command != "guard":
                raise ValueError("지원하지 않는 예제 행동입니다.")
            if self.enemy_hp > 0:
                self.hp = max(0, self.hp - (1 if command == "guard" else 6))
            self.turn += 1
            self.revision += 1
            return self.observe()

    def reset(self, scenario=None):
        with self.lock:
            self.hp, self.enemy_hp, self.turn = 30, 30, 1
            self.revision += 1
            return self.observe()


def make_bridge(game=None):
    game = game or DemoGame()
    return ModBridge(game="mod_demo", version="demo-v1", observe=game.observe, act=game.act, reset=game.reset)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with ModServer({"host": "127.0.0.1", "port": args.port}, make_bridge()) as server:
        print("GameLab mod demo: tcp://127.0.0.1:%d (Ctrl-C로 종료)" % args.port, flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
