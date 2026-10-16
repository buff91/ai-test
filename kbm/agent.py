"""The KbmAgent service: observe the game window and turn named actions into input.

It speaks the same JSON-line ops protocol as game_ipc, so the model side never
touches screen capture or input permissions.
"""

import threading
import time

from kbm.steps import run_steps
from kbm.vision import Frame


# Moving the mouse into the top-left corner stops all input until the agent restarts.
FAILSAFE_POINTS = 2


class AgentService:
    def __init__(self, game, platform, backend):
        self.game = game
        self.platform = platform
        self.backend = backend
        self.lock = threading.RLock()
        self.frame = 0
        self.halted = None
        self.stop_reflex = threading.Event()

    def _observe(self, save_frame=None):
        window = self.platform.find_window()
        image = self.platform.capture(window)
        if save_frame:
            image.save(save_frame)
        observed = self.game.observe(Frame(image))
        self.frame += 1
        return {
            "frame": self.frame,
            "frame_saved": bool(save_frame),
            "observed_at": time.time(),
            "window": window,
            "state": observed["state"],
            "description": observed["description"],
            "truth": observed.get("truth"),
            "signature": self.game.signature(observed["state"]),
        }

    def _guard(self, window):
        x, y = self.platform.cursor()
        if x <= FAILSAFE_POINTS and y <= FAILSAFE_POINTS:
            self.halt("마우스가 화면 왼쪽 위 모서리에 있어 입력을 멈췄습니다.")
        if self.halted:
            return self.halted
        if not self.platform.is_frontmost(window):
            return "게임 창이 맨 앞에 있지 않아 입력하지 않았습니다."
        return None

    def halt(self, reason):
        self.halted = reason
        self.stop_reflex.set()
        self.backend.release_all()

    def act(self, action, signature=None, observed_at=None, max_age_ms=None):
        if action not in self.game.actions:
            return {"ok": False, "error": "알 수 없는 행동입니다: %s" % action}
        latest = self._observe()
        error = self._guard(latest["window"])
        if error:
            return {"ok": False, "error": error, "applied": False, "halted": bool(self.halted), **latest}
        if observed_at is not None and max_age_ms is not None:
            age_ms = (time.time() - observed_at) * 1000
            if age_ms > max_age_ms:
                return {"ok": False, "applied": False, "stale": True,
                        "error": "판단이 %.0fms 걸려 허용 시간(%dms)을 넘었습니다." % (age_ms, max_age_ms), **latest}
        if signature is not None and signature != latest["signature"]:
            return {"ok": False, "applied": False, "stale": True,
                    "error": "판단 중 화면 상황이 바뀌었습니다.", **latest}
        started = time.perf_counter()
        try:
            run_steps(self.game.actions[action], self.backend, latest["window"], latest["state"])
        finally:
            self.backend.release_all()
        return {"ok": True, "applied": True, "action": action,
                "input_ms": round((time.perf_counter() - started) * 1000, 1), "before": latest}

    def handle(self, request):
        op = request.get("op")
        with self.lock:
            try:
                if op == "info":
                    return {"ok": True, "game": self.game.id, "name": self.game.name, "interface": self.game.interface,
                            "actions": list(self.game.actions), "questions": self.game.questions,
                            "config": {k: v for k, v in self.game.config.items() if k != "actions"},
                            "halted": self.halted, "permissions": _safe(self.platform.permissions)}
                if op == "observe":
                    return {"ok": True, **self._observe(request.get("save_frame"))}
                if op == "act":
                    return self.act(request.get("action"), request.get("signature"),
                                    request.get("observed_at"), request.get("max_age_ms"))
                if op in ("pause", "resume"):
                    key = self.game.config["pause_key"]
                    if not key:
                        return {"ok": False, "error": "이 게임 케이스에는 pause_key가 없습니다."}
                    window = self.platform.find_window()
                    error = self._guard(window)
                    if error:
                        return {"ok": False, "error": error}
                    run_steps([{"tap": key}], self.backend, window, {})
                    return {"ok": True}
                if op == "halt":
                    self.halt(request.get("reason") or "러너가 입력을 멈췄습니다.")
                    return {"ok": True, "halted": self.halted}
            except (LookupError, ValueError, OSError, RuntimeError) as exc:
                return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
        return {"ok": False, "error": "알 수 없는 명령입니다: %s" % op}

    def run_reflex(self, log=print):
        """Fast local rules (observer.reflex) at reflex_hz, without waiting for a model."""
        period = 1 / self.game.config["reflex_hz"]
        while not self.stop_reflex.wait(period):
            with self.lock:
                try:
                    observed = self._observe()
                    action = self.game.reflex(observed["state"])
                    if action:
                        result = self.act(action)
                        log("reflex %s -> %s" % (action, "applied" if result["ok"] else result["error"]))
                except (LookupError, ValueError, OSError, RuntimeError) as exc:
                    log("reflex error: %s" % exc)


def _safe(fn):
    try:
        return fn()
    except RuntimeError as exc:
        return {"error": str(exc)}
