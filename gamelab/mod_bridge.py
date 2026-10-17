"""Small callback SDK for external game mods implementing GameLab JSONL v1.

observe() returns {snapshot: object, revision: integer/string, description?: str}.
act(payload) mutates the game and returns its new observation. The host game must
advance revision for every decision-relevant change, including player input.
Callbacks must execute on the host's game thread when required by its engine.
"""

from copy import deepcopy
import math
import threading
import time

from gamelab.protocol import PROTOCOL, ModServer


class ModBridge:
    def __init__(self, *, game, version, observe, act, reset=None, scenarios=None):
        if not isinstance(game, str) or not game or not isinstance(version, str) or not version:
            raise ValueError("모드 브리지는 game과 version 식별자가 필요합니다.")
        self.build = {"game": game, "version": version}
        self.observe = observe
        self.act = act
        self.reset = reset
        self.scenarios = list(scenarios or [])
        self.lock = threading.RLock()

    def _state(self, result=None):
        result = self.observe() if result is None else result
        if not isinstance(result, dict) or not isinstance(result.get("snapshot"), dict):
            raise ValueError("observe/act callback은 snapshot 객체를 반환해야 합니다.")
        if type(result.get("revision")) not in (int, str):
            raise ValueError("observe/act callback은 revision을 반환해야 합니다.")
        return deepcopy(result)

    def handle(self, request):
        with self.lock:
            try:
                op = request.get("op")
                if op == "info":
                    return {"ok": True, "protocol": dict(PROTOCOL), "build": dict(self.build),
                            "capabilities": {"observe": True, "act": True, "reset": self.reset is not None,
                                             "pause": False, "halt": False}, "scenarios": self.scenarios}
                if op == "state":
                    return {**self._state(), "ok": True}
                if op == "step":
                    current = self._state()
                    expected = request.get("expected_revision")
                    if expected is None or expected != current["revision"]:
                        return {**current, "ok": False, "applied": False, "stale": True,
                                "error": "상태 revision이 변경되었습니다. 다시 관찰하세요."}
                    observed_at, max_age_ms = request.get("observed_at"), request.get("max_age_ms")
                    if observed_at is not None and max_age_ms is not None:
                        if (type(observed_at) not in (int, float) or type(max_age_ms) not in (int, float)
                                or not math.isfinite(observed_at) or not math.isfinite(max_age_ms) or max_age_ms <= 0):
                            raise ValueError("observed_at/max_age_ms는 유효한 시간이어야 합니다.")
                        if (time.time() - observed_at) * 1000 > max_age_ms:
                            return {**current, "ok": False, "applied": False, "stale": True,
                                    "error": "판단 유효 시간이 지났습니다. 다시 관찰하세요."}
                    action = request.get("action")
                    if not isinstance(action, (str, dict)) or not action:
                        return {"ok": False, "error": "action은 명령 문자열 또는 JSON 객체여야 합니다."}
                    result = self.act(deepcopy(action))
                    if isinstance(result, dict) and (result.get("ok") is False or result.get("applied") is False):
                        return {**self._state(), **result, "ok": False, "applied": False}
                    return {**self._state(result), "ok": True, "applied": True, "before": current["snapshot"]}
                if op == "reset" and self.reset is not None:
                    return {**self._state(self.reset(request.get("scenario"))), "ok": True}
                return {"ok": False, "error": "지원하지 않는 모드 명령입니다: %s" % op}
            except (LookupError, ValueError, RuntimeError) as exc:
                return {"ok": False, "error": str(exc)}


__all__ = ["ModBridge", "ModServer"]
