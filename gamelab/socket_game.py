"""socket connector: the game build has a test hook and serves its own state.

The build must answer JSON-line requests on a Unix socket, as ArenaGame and sims/ do:
  {"op": "state"}                                  -> {"ok", "snapshot", "description", "revision"}
  {"op": "step", "action", "expected_revision"}   -> {"ok", "error"?, "snapshot", "revision"}
Optional: "truth" (the right answer per question, never shown to the model), "done"
(the episode ended), {"op": "reset", "scenario"?} to start the next (or the named)
episode, and {"op": "info"} -> {"ok", "scenarios"} to list the scenarios reset accepts.

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
from gamelab import build_info


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

    def __init__(self, game, client, process=None, runtime=None):
        self.game = game
        self.client = client
        self.process = process
        self.runtime = runtime or {"mode": "attach", "socket": str(getattr(client, "path", ""))}
        self.started_at = time.time()

    def health(self):
        """Is the game process still there (None when this console did not start it)."""
        if not self.process:
            return {"pid": None, "alive": None, "exit_code": None, "started_at": self.started_at}
        code = self.process.poll()
        return {"pid": self.process.pid, "alive": code is None, "exit_code": code, "started_at": self.started_at}

    def request(self, op, **kwargs):
        if op == "info":
            try:
                info = self.client.request("info")
            except (OSError, ValueError):
                info = {}
            return {"ok": True, "game": self.game.id, "connector": "socket", "runtime": self.runtime,
                    "scenarios": info.get("scenarios") if info.get("ok") else None,
                    # What the build says about itself, when it answers "info" with a "build".
                    "game_build": info.get("build") if info.get("ok") else None}
        if op == "observe":
            response = self.client.request("state")
            return {**self._observed(response), "ok": bool(response.get("ok")), "error": response.get("error")}
        if op == "reset":
            scenario = kwargs.get("scenario")
            response = self.client.request("reset", **({"scenario": scenario} if scenario else {}))
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
                "signature": response.get("revision"), "truth": response.get("truth"),
                # Builds without "done" (ArenaGame) still report the outcome in their snapshot.
                "done": bool(response.get("done", state.get("outcome", "playing") != "playing"))}


def how_to_run(game):
    """What starts the game, for the web console's connect panel."""
    launch = game.config.get("launch")
    if launch:
        return {"mode": "launch", "command": launch["command"], "socket_arg": launch.get("socket_arg", "--socket"),
                "hint": launch.get("build_hint"), "build": build_info.describe(launch["command"], launch.get("sources"))}
    return {"mode": "attach", "socket": game.config.get("socket")}


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
    full = [*command, launch.get("socket_arg", "--socket"), str(socket_path)]
    runtime = {"mode": "launch", "command": full, "cwd": str(ROOT),
               "build": build_info.describe(launch["command"], launch.get("sources"))}
    process = subprocess.Popen(full, cwd=ROOT)
    runtime["pid"] = process.pid
    client = GameClient(socket_path)
    try:
        _wait_ready(client, process, launch.get("ready_timeout", 15))
        yield SocketGame(game, client, process, runtime)
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
