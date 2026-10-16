"""socket connector: the game build has a test hook and serves its own state.

The build must answer JSON-line requests on a Unix socket, as ArenaGame and sims/ do:
  {"op": "state"}                                  -> {"ok", "snapshot", "description", "revision"}
  {"op": "step", "action", "expected_revision"}   -> {"ok", "error"?, "snapshot", "revision"}
Optional: "truth" (the right answer per question, never shown to the model), "done"
(the episode ended) and {"op": "reset"} to start the next episode.

In launch.command, "{python}" is this Python, and the command runs in the project folder.
"""

import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from game_ipc import GameClient


ROOT = Path(__file__).resolve().parent.parent


def validate(game):
    launch, attach = game.config.get("launch"), game.config.get("socket")
    if bool(launch) == bool(attach):
        raise ValueError("%s: socket 게임은 launch(실행 파일을 띄움) 또는 socket(이미 떠 있는 게임) 중 하나가 필요합니다." % game.id)
    if launch and not (isinstance(launch.get("command"), list) and launch["command"]):
        raise ValueError("%s: launch.command는 실행 명령 목록이어야 합니다." % game.id)
    for name, command in game.actions.items():
        if not isinstance(command, str) or not command:
            raise ValueError("%s.%s: socket 행동은 게임에 보낼 명령 이름(문자열)이어야 합니다." % (game.id, name))


class SocketGame:
    """Speaks the runner's ops (info, observe, act, ...) on top of the game's state/step hook."""

    def __init__(self, game, client):
        self.game = game
        self.client = client

    def request(self, op, **kwargs):
        if op == "info":
            return {"ok": True, "game": self.game.id, "connector": "socket"}
        if op == "observe":
            response = self.client.request("state")
            return {**self._observed(response), "ok": bool(response.get("ok")), "error": response.get("error")}
        if op == "reset":
            response = self.client.request("reset")
            return {**self._observed(response), "ok": bool(response.get("ok")), "error": response.get("error")}
        if op == "act":
            action = kwargs.get("action")
            if action not in self.game.actions:
                return {"ok": False, "applied": False, "error": "알 수 없는 행동입니다: %s" % action}
            expected = kwargs.get("signature")
            response = self.client.request("step", action=self.game.actions[action], expected_revision=expected)
            ok = bool(response.get("ok"))
            return {"ok": ok, "applied": ok, "action": action, "error": response.get("error"),
                    # A changed revision means the game moved on while the model was thinking.
                    "stale": not ok and expected is not None and response.get("revision") != expected,
                    "state": response.get("snapshot"), "before": response.get("before"), "done": bool(response.get("done"))}
        if op in ("pause", "resume"):
            return {"ok": False, "error": "socket 게임은 게임이 판단을 기다리므로 일시정지가 필요 없습니다."}
        if op == "halt":
            return {"ok": True}
        return {"ok": False, "error": "알 수 없는 명령입니다: %s" % op}

    def _observed(self, response):
        state = response.get("snapshot") or {}
        return {"frame": response.get("revision"), "observed_at": time.time(), "window": None, "state": state,
                "description": self.game.describe(state, response.get("description") or ""),
                "signature": response.get("revision"), "truth": response.get("truth"), "done": bool(response.get("done"))}


@contextmanager
def connect(game, **_):
    launch = game.config.get("launch")
    if not launch:
        yield SocketGame(game, GameClient(game.config["socket"]))
        return
    workdir = Path(tempfile.mkdtemp(prefix="game-"))
    socket_path = workdir / "game.sock"
    command = [sys.executable if part == "{python}" else part for part in launch["command"]]
    if command[0] != sys.executable:
        command[0] = str(ROOT / command[0]) if not Path(command[0]).is_absolute() else command[0]
        if not Path(command[0]).exists():
            raise FileNotFoundError("%s가 없습니다. %s" % (command[0], launch.get("build_hint", "게임을 먼저 빌드하세요.")))
    process = subprocess.Popen([*command, launch.get("socket_arg", "--socket"), str(socket_path)], cwd=ROOT)
    client = GameClient(socket_path)
    try:
        _wait_ready(client, process, launch.get("ready_timeout", 15))
        yield SocketGame(game, client)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(workdir, ignore_errors=True)


def _wait_ready(client, process, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("게임이 바로 종료되었습니다.")
        try:
            if client.request("state").get("ok"):
                return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError("게임이 소켓에 응답하지 않습니다.")
