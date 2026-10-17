"""Console-owned visible games that survive changing their control connection."""

import threading
from contextlib import ExitStack

from gamelab.game_case import GameCase, list_games


def window_case(game_id, games_dir, version):
    # Only commands already declared by the local catalog can be launched.
    if not isinstance(game_id, str) or game_id not in list_games(games_dir):
        raise ValueError("등록된 게임을 선택하세요.")
    base = GameCase(game_id, games_dir, version=version)
    for name in base.interfaces:
        case = GameCase(game_id, games_dir, version=base.version, interface=name)
        if case.connector == "socket" and case.config.get("launch") and case.target["display"] == "visible":
            return case
    raise ValueError("이 버전에는 준비할 수 있는 게임 창이 없습니다.")


class GameWindows:
    def __init__(self, games_dir):
        self.games_dir = games_dir
        self.lock = threading.RLock()
        self.entries = {}
        self.closed = False

    def _status(self, key, entry):
        _, client, interface = entry
        health = client.health()
        return {"game": key[0], "version": key[1], "interface": interface,
                "socket": str(client.client.path), "pid": health.get("pid"),
                "alive": health.get("alive")}

    def prepare(self, game_id, version=None):
        case = window_case(game_id, self.games_dir, version)
        key = (case.id, case.version)
        with self.lock:
            if self.closed:
                raise RuntimeError("콘솔이 종료되었습니다.")
            previous = self.entries.get(key)
            if previous:
                state = self._status(key, previous)
                if state["alive"]:
                    try:
                        response = previous[1].request("observe")
                        if not response.get("ok"):
                            raise RuntimeError(response.get("error") or "상태 응답이 없습니다.")
                    except (OSError, ValueError, RuntimeError) as exc:
                        raise RuntimeError("준비한 게임 프로세스는 실행 중이지만 연결할 수 없습니다. 창을 닫고 다시 준비하세요: %s" % exc) from exc
                    return {**state, "reused": True}
                previous[0].close()
                del self.entries[key]
            stack = ExitStack()
            try:
                client = stack.enter_context(case.connect())
                entry = (stack, client, case.interface)
                state = self._status(key, entry)
            except BaseException:
                stack.close()
                raise
            self.entries[key] = entry
            return {**state, "reused": False}

    def status(self):
        with self.lock:
            return [self._status(key, entry) for key, entry in self.entries.items()]

    def close(self):
        with self.lock:
            self.closed = True
            entries, self.entries = self.entries, {}
            for stack, _, _ in entries.values():
                stack.close()
