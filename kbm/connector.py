"""kbm connector: start KbmAgent, which captures the game window and sends keyboard/mouse input."""

import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from game_ipc import GameClient
from kbm.keys import KEYCODES
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


@contextmanager
def connect(game, agent="app", dry_run=False, reflex=False, log_path=None, **_):
    """Start KbmAgent (built .app, or this Python for development) and yield a client."""
    workdir = Path(tempfile.mkdtemp(prefix="kbm-"))
    socket_path = workdir / "agent.sock"
    args = ["--game", game.id, "--interface", game.interface,
            "--games-dir", str(game.dir.parent.resolve()), "--socket", str(socket_path)]
    args += ["--log", str(log_path or workdir / "agent.log")]
    if dry_run:
        args.append("--dry-run")
    if reflex:
        args.append("--reflex")
    if agent == "app":
        if not APP.exists():
            raise FileNotFoundError("%s가 없습니다. 먼저 ./build_kbm.sh를 실행하세요." % APP)
        # 'open' makes KbmAgent.app the responsible process for macOS privacy permissions.
        process = subprocess.Popen(["open", "-n", "-W", str(APP), "--args", *args])
    else:
        process = subprocess.Popen([sys.executable, str(ROOT / "kbm_agent.py"), *args])
    client = GameClient(socket_path, timeout=10)
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
        shutil.rmtree(workdir, ignore_errors=True)


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
