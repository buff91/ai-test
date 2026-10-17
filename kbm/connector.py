"""kbm connector: start KbmAgent, which captures the game window and sends keyboard/mouse input."""

import json
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from gamelab.protocol import GameClient
from gamelab import build_info
from kbm.keys import KEYCODES
from kbm.macos import MacPlatform
from kbm.steps import validate_step


ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "dist" / "KbmAgent.app"


def validate(game):
    window = game.config.get("window") or {}
    if not window.get("owner"):
        raise ValueError("%s: window.owner가 필요합니다." % game.id)
    for name, steps in game.actions.items():
        if not isinstance(steps, list):
            raise ValueError("%s.%s: kbm 행동은 입력 단계 목록이어야 합니다." % (game.id, name))
        total = sum(validate_step(step, "%s.%s" % (game.id, name)) for step in steps)
        if total > game.config["max_action_ms"]:
            raise ValueError("%s.%s: 행동 시간 %dms가 max_action_ms를 넘습니다." % (game.id, name, total))
    if game.config["pause_key"] is not None and game.config["pause_key"] not in KEYCODES:
        raise ValueError("%s: 알 수 없는 pause_key입니다." % game.id)
    if not callable(getattr(game.observer, "observe", None)):
        raise ValueError("%s: kbm 게임은 observer.py에 observe(frame) 함수가 필요합니다." % game.id)


def how_to_run(game):
    """kbm never starts the game: the window must already be open."""
    window = game.config.get("window") or {}
    return {"mode": "window", "owner": window.get("owner"), "title_contains": window.get("title_contains") or "",
            "agent_build": build_info.file_info(APP)}


@contextmanager
def connect(game, agent="app", dry_run=False, reflex=False, log_path=None, **_):
    """Start KbmAgent (built .app, or this Python for development) and yield a client."""
    with tempfile.TemporaryDirectory(prefix="kbm-") as folder:
        workdir = Path(folder)
        socket_path = workdir / "agent.sock"
        settings_path = workdir / "settings.json"
        settings_path.write_text(json.dumps(game.configuration()["values"], ensure_ascii=False), encoding="utf-8")
        args = ["--game", game.id, "--interface", game.interface,
                "--version", game.version,
                "--games-dir", str(game.dir.parent.resolve()), "--socket", str(socket_path),
                "--settings", str(settings_path)]
        args += ["--log", str(log_path or workdir / "agent.log")]
        if dry_run:
            args.append("--dry-run")
        if reflex:
            args.append("--reflex")
        if agent == "app":
            if not APP.exists():
                raise FileNotFoundError("%s가 없습니다. 먼저 ./build_kbm.sh를 실행하세요." % APP)
            # 'open' makes KbmAgent.app the responsible process for macOS privacy permissions.
            process = subprocess.Popen(["open", "-g", "-n", "-W", str(APP), "--args", *args])
        else:
            process = subprocess.Popen([sys.executable, str(ROOT / "kbm_agent.py"), *args])
        client = AgentClient(socket_path, process, agent, timeout=10, target=game.target,
                             window_config=game.config["window"], dry_run=dry_run)
        log_file = Path(log_path or workdir / "agent.log")
        try:
            _wait_ready(client, process, log_file)
            yield client
        finally:
            try:
                client.request("shutdown")
            except OSError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()


class AgentClient(GameClient):
    """KbmAgent's socket, plus whether the agent process is alive and which agent build it is."""

    def __init__(self, path, process, agent, timeout=10, target=None, window_config=None, dry_run=False):
        super().__init__(path, timeout=timeout)
        self.process = process
        self.started_at = time.time()
        self.target = target
        self.window_config = dict(window_config or {})
        self.dry_run = bool(dry_run)
        self.runtime = {"mode": "window", "agent": agent, "pid": process.pid,
                        "agent_build": build_info.file_info(APP) if agent == "app" else
                        {"path": "kbm_agent.py", "python": sys.executable}}

    def health(self):
        code = self.process.poll()
        return {"pid": self.process.pid, "alive": code is None, "exit_code": code, "started_at": self.started_at}

    def request(self, op, **kwargs):
        if op == "focus":
            # Focus belongs to an explicit console action. The already-running
            # capture agent supplies the target; callers cannot substitute a PID.
            result = {"ok": False, "focused": False, "input_sent": False,
                      "application_confirmed": False, "command": None}
            try:
                if kwargs:
                    raise ValueError("focus는 연결된 게임 창만 사용하며 추가 인자를 받지 않습니다.")
                if self.dry_run:
                    raise ValueError("DRY-RUN에서는 게임 창을 전환하지 않습니다.")
                observed = super().request("observe")
                if not observed.get("ok"):
                    raise ValueError(observed.get("error") or "게임 화면을 확인하지 못했습니다.")
                config = self.window_config
                if not config.get("owner"):
                    raise ValueError("연결된 게임 창 설정이 없습니다.")
                platform = MacPlatform(config["owner"], config.get("title_contains", ""), config.get("pid"))
                return {**result, **platform.focus(observed.get("window"))}
            except (LookupError, ValueError, OSError, RuntimeError) as exc:
                return {**result, "error": "%s: %s" % (type(exc).__name__, exc)}
        response = super().request(op, **kwargs)
        if op == "info" and response.get("ok"):
            response["runtime"] = self.runtime
            response["target"] = self.target
            response["version_check"] = {"status": "unverified", "message": "kbm은 화면으로 연결하므로 게임 버전을 직접 확인할 수 없습니다."}
            response["connector"] = "kbm"
            response["capabilities"] = {"observe": True, "act": True, "reset": False, "halt": True, "focus": True,
                                        "pause": bool((response.get("config") or {}).get("pause_key"))}
        return response


def _wait_ready(client, process, log_file, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            # The temporary log is deleted on exit, so show its tail here.
            tail = log_file.read_text(encoding="utf-8").strip().splitlines()[-3:] if log_file.exists() else []
            raise RuntimeError("KbmAgent가 바로 종료되었습니다.%s" % "".join("\n  " + line for line in tail))
        try:
            if client.request("info").get("ok"):
                return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError("KbmAgent가 응답하지 않습니다.")
