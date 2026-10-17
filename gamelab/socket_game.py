"""socket connector: the game build has a test hook and serves its own state.

The build must answer JSON-line requests on a Unix socket, as the teaching games do:
  {"op": "state"}                                  -> {"ok", "snapshot", "description", "revision"}
  {"op": "step", "action", "expected_revision"}   -> {"ok", "error"?, "snapshot", "revision"}
Optional: "truth" (the right answer per question, never shown to the model), "done"
(the episode ended), {"op": "reset", "scenario"?} to start the next (or the named)
episode, and {"op": "info"} -> {"ok", "scenarios"} to list the scenarios reset accepts.

In launch.command, "{python}" is this Python, and the command runs in the project folder.
"""

import shutil
import json
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from gamelab.protocol import GameClient
from gamelab import build_info
from gamelab.game_case import check_build


ROOT = Path(__file__).resolve().parent.parent


def validate(game):
    launch, attach = game.config.get("launch"), game.config.get("socket")
    if bool(launch) == bool(attach):
        raise ValueError("%s: socket 게임은 launch(실행 파일을 띄움) 또는 socket(이미 떠 있는 게임) 중 하나가 필요합니다." % game.id)
    if launch and not (isinstance(launch.get("command"), list) and launch["command"]):
        raise ValueError("%s: launch.command는 실행 명령 목록이어야 합니다." % game.id)
    validate_actions(game)


def validate_actions(game):
    """Named choices map to a command name or a structured mod payload."""
    for name, command in game.actions.items():
        if not isinstance(command, (str, dict)) or not command:
            raise ValueError("%s.%s: 행동은 명령 문자열 또는 비어 있지 않은 JSON 객체여야 합니다." % (game.id, name))
        try:
            json.dumps(command, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise ValueError("%s.%s: 행동은 유효한 JSON이어야 합니다." % (game.id, name)) from exc


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
            return {"ok": True, "game": self.game.id, "connector": self.game.connector, "runtime": self.runtime,
                    "target": self.game.target, "version_check": check_build(self.game, info.get("build")),
                    "protocol": info.get("protocol"), "capabilities": info.get("capabilities", {}),
                    "configuration": self.game.configuration(),
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
                return {"ok": False, "applied": False, "command": None, "error": "알 수 없는 행동입니다: %s" % action}
            expected = kwargs.get("signature")
            if expected is None and self.game.connector == "mod":
                # Manual console commands have no model observation. Capture the
                # current revision immediately; the mod still atomically checks it.
                current = self.client.request("state")
                if not current.get("ok") or current.get("revision") is None:
                    return {"ok": False, "applied": False, "command": None,
                            "error": current.get("error") or "모드 상태 revision을 읽을 수 없습니다."}
                expected = current["revision"]
            # Turn simulations wait for input; continuous games keep moving during inference.
            if self.game.config.get("continuous") and kwargs.get("observed_at") is not None:
                age_ms = (time.time() - kwargs["observed_at"]) * 1000
                limit = kwargs.get("max_age_ms", self.game.config["max_age_ms"])
                if limit is not None and age_ms > limit:
                    return {"ok": False, "applied": False, "stale": True, "command": None,
                            "error": "판단이 %.0fms 걸려 허용 시간(%dms)을 넘었습니다." % (age_ms, limit)}
            freshness = {key: kwargs[key] for key in ("observed_at", "max_age_ms")
                         if self.game.config.get("continuous") and kwargs.get(key) is not None}
            command = {"op": "step", "action": self.game.actions[action], "expected_revision": expected, **freshness}
            response = self.client.request(**command)
            ok = bool(response.get("ok"))
            return {"ok": ok, "applied": ok and response.get("applied", True) is not False,
                    "action": action, "command": command, "error": response.get("error"),
                    # A changed revision means the game moved on while the model was thinking.
                    "stale": bool(response.get("stale")) or
                             (not ok and expected is not None and response.get("revision") != expected),
                    "state": response.get("snapshot"), "before": response.get("before"), "done": bool(response.get("done"))}
        if op in ("pause", "resume"):
            if self.game.config.get("continuous"):
                response = self.client.request(op)
                return {**self._observed(response), "ok": bool(response.get("ok")), "error": response.get("error")}
            return {"ok": False, "error": "socket 게임은 게임이 판단을 기다리므로 일시정지가 필요 없습니다."}
        if op == "halt":
            if self.game.config.get("continuous"):
                response = self.client.request("halt")
                return {**self._observed(response), "ok": bool(response.get("ok")), "error": response.get("error")}
            return {"ok": True}
        return {"ok": False, "error": "알 수 없는 명령입니다: %s" % op}

    def _observed(self, response):
        if response.get("ok") and not isinstance(response.get("snapshot"), dict):
            raise ValueError("게임 state 응답에는 snapshot 객체가 필요합니다.")
        state = response.get("snapshot") or {}
        return {"frame": response.get("revision"), "observed_at": time.time(), "window": None, "state": state,
                "description": self.game.describe(state, response.get("description") or ""),
                "signature": response.get("revision"), "truth": response.get("truth"),
                "reference_kind": response.get("reference_kind"),
                # The snapshot outcome also indicates completion when "done" is omitted.
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
        linked = SocketGame(game, GameClient(game.config["socket"]))
        _verify_target(linked)
        yield linked
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
        linked = SocketGame(game, client, process, runtime)
        _verify_target(linked)
        yield linked
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(workdir, ignore_errors=True)


def _verify_target(client):
    check = client.request("info")["version_check"]
    if check["status"] == "mismatch":
        raise ValueError(check["message"])


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
