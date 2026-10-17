"""The KbmAgent service: observe the game window and turn named actions into input.

It uses the JSON-line transport in gamelab.protocol, so the model side never
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
            "done": observed["state"].get("outcome", "playing") not in (None, "playing"),
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
            return {"ok": False, "applied": False, "command": None, "error": "알 수 없는 행동입니다: %s" % action}
        latest = self._observe()
        error = self._guard(latest["window"])
        if error:
            return {"ok": False, "error": error, "applied": False, "command": None, "halted": bool(self.halted), **latest}
        if observed_at is not None and max_age_ms is not None:
            age_ms = (time.time() - observed_at) * 1000
            if age_ms > max_age_ms:
                return {"ok": False, "applied": False, "stale": True, "command": None,
                        "error": "판단이 %.0fms 걸려 허용 시간(%dms)을 넘었습니다." % (age_ms, max_age_ms), **latest}
        if signature is not None and signature != latest["signature"]:
            return {"ok": False, "applied": False, "stale": True, "command": None,
                    "error": "판단 중 화면 상황이 바뀌었습니다.", **latest}
        if latest["state"].get("input_ready") is False:
            return {"ok": False, "applied": False, "busy": True, "command": None,
                    "error": "게임 화면이 다음 입력을 기다리라고 표시해 입력하지 않았습니다.", **latest}
        started = time.perf_counter()
        trace = _InputTrace(self.backend)
        steps, error = [], None
        try:
            for step in self.game.actions[action]:
                run_steps([step], trace, latest["window"], latest["state"])
                steps.append(step)
        except (LookupError, ValueError, OSError, RuntimeError) as exc:
            error = "%s: %s" % (type(exc).__name__, exc)
        finally:
            self.backend.release_all()
        dry_run = bool(getattr(self.backend, "dry_run", False))
        input_sent = bool(trace.events) and not dry_run
        # A successful OS call proves dispatch, not that the game accepted the
        # event. Keep applied's existing input-plan contract for callers, while
        # making this distinction explicit in recordings and the lecture UI.
        return {"ok": error is None, "applied": error is None, "error": error, "action": action,
                "dry_run": dry_run, "input_sent": input_sent,
                "application_confirmed": False,
                "feedback": "입력 계획만 실행했습니다. 실제 입력은 전송하지 않았습니다." if dry_run else
                            "운영체제 입력 전송 기록입니다. 게임의 행동 수신은 확인하지 않았습니다." if input_sent else
                            "실제 입력이 전송되지 않았습니다.",
                "command": None if dry_run else {"op": "kbm", "steps": steps, "events": trace.events},
                "planned_command": {"op": "kbm", "steps": self.game.actions[action]} if dry_run else None,
                "input_ms": round((time.perf_counter() - started) * 1000, 1), "before": latest}

    def set_paused(self, paused):
        """Make toggle-key pause idempotent using the visible pause indicator."""
        key = self.game.config["pause_key"]
        if not key:
            return {"ok": False, "error": "이 게임 케이스에는 pause_key가 없습니다."}
        latest = self._observe()
        current = latest["state"].get("paused")
        if type(current) is not bool:
            return {"ok": False, "input_sent": False, "application_confirmed": False,
                    "error": "화면 관측에 paused 값이 없어 일시정지 상태를 확인할 수 없습니다."}
        if current == paused:
            return {"ok": True, "paused": current, "input_sent": False, "application_confirmed": True,
                    "state": latest["state"]}
        error = self._guard(latest["window"])
        if error:
            return {"ok": False, "input_sent": False, "application_confirmed": False, "error": error}
        trace = _InputTrace(self.backend)
        error = None
        try:
            run_steps([{"tap": key}], trace, latest["window"], latest["state"])
        except (LookupError, ValueError, OSError, RuntimeError) as exc:
            error = "%s: %s" % (type(exc).__name__, exc)
        finally:
            self.backend.release_all()
        dry_run = bool(getattr(self.backend, "dry_run", False))
        result = {"dry_run": dry_run, "input_sent": bool(trace.events) and not dry_run,
                  "application_confirmed": False,
                  "command": None if dry_run else {"op": "kbm", "steps": [{"tap": key}], "events": trace.events}}
        if error:
            return {**result, "ok": False, "error": error}
        if dry_run:
            return {**result, "ok": False, "paused": current,
                    "error": "입력 미리보기에서는 게임 일시정지 상태를 바꿀 수 없습니다."}
        # Key dispatch and the next render are asynchronous. Wait briefly for
        # visible confirmation, without issuing a second toggle.
        deadline = time.monotonic() + .5
        while True:
            try:
                latest = self._observe()
            except (LookupError, ValueError, OSError, RuntimeError) as exc:
                return {**result, "ok": False, "error": "입력 후 화면 확인 실패: %s" % exc}
            if latest["state"].get("paused") is paused:
                return {**result, "ok": True, "paused": paused, "application_confirmed": True,
                        "state": latest["state"]}
            if time.monotonic() >= deadline:
                return {**result, "ok": False, "paused": latest["state"].get("paused"),
                        "error": "일시정지 키를 보냈지만 화면에서 요청한 상태를 확인하지 못했습니다."}
            time.sleep(.03)

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
                    return {"input_sent": False, "application_confirmed": False,
                            **self.act(request.get("action"), request.get("signature"),
                                       request.get("observed_at"), request.get("max_age_ms"))}
                if op in ("pause", "resume"):
                    return self.set_paused(op == "pause")
                if op == "halt":
                    self.halt(request.get("reason") or "러너가 입력을 멈췄습니다.")
                    return {"ok": True, "halted": self.halted}
            except (LookupError, ValueError, OSError, RuntimeError) as exc:
                return {"ok": False, "applied": False, "command": None,
                        "input_sent": False, "application_confirmed": False,
                        "error": "%s: %s" % (type(exc).__name__, exc)}
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


class _InputTrace:
    """Retain the backend calls that returned successfully, including partial actions."""
    def __init__(self, backend):
        self.backend = backend
        self.events = []

    def cursor(self):
        return self.backend.cursor()

    def __getattr__(self, name):
        if name not in ("key", "move", "button", "look"):
            raise AttributeError(name)
        def send(*args):
            result = getattr(self.backend, name)(*args)
            self.events.append({"event": name, "args": list(args)})
            return result
        return send
