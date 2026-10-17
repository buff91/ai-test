"""game.json input steps (tap, hold, click, move, look, wait) for the kbm connector."""

import math

from kbm.keys import KEYCODES
from kbm.macos import sleep_ms


STEP_KINDS = ("tap", "hold", "click", "move", "look", "wait")


def validate_step(step, where):
    """Check one input step and return how long it blocks, in ms."""
    if not isinstance(step, dict) or len(set(step) & set(STEP_KINDS)) != 1:
        raise ValueError("%s: 단계마다 %s 중 하나만 지정하세요: %r" % (where, "/".join(STEP_KINDS), step))
    kind = next(k for k in STEP_KINDS if k in step)
    value = step[kind]
    allowed = {kind} | ({"ms"} if kind in ("tap", "hold", "click") else set()) | ({"at"} if kind == "click" else set())
    if set(step) - allowed:
        raise ValueError("%s: 지원하지 않는 입력 설정입니다: %s" % (where, ", ".join(sorted(set(step) - allowed))))
    if kind in ("tap", "hold"):
        keys = [value] if isinstance(value, str) else value
        if not isinstance(keys, list) or not keys or not all(isinstance(k, str) for k in keys):
            raise ValueError("%s: tap/hold에는 키 이름 또는 비어 있지 않은 키 목록이 필요합니다." % where)
        for key in keys:
            if key not in KEYCODES:
                raise ValueError("%s: 알 수 없는 키 %r" % (where, key))
        return _duration(step.get("ms", 40 if kind == "tap" else None), where, minimum=1 if kind == "hold" else 0)
    if kind == "click":
        if value not in ("left", "right"):
            raise ValueError("%s: click은 left 또는 right입니다." % where)
        _check_point(step.get("at", "cursor"), where)
        return _duration(step.get("ms", 40), where)
    if kind == "move":
        _check_point(value, where)
        if value == "cursor":
            raise ValueError("%s: move에는 좌표 또는 대상 state 키가 필요합니다." % where)
        return 0
    if kind == "look":
        if not (isinstance(value, list) and len(value) == 2 and all(_finite(v) and abs(v) <= 100000 for v in value)):
            raise ValueError("%s: look은 [dx, dy] 픽셀입니다." % where)
        return 0
    return _duration(value, where)


def run_steps(steps, backend, window, state):
    """Translate steps into input events. Points are window-relative 0..1 or a state key."""
    def point(at):
        if at == "cursor":
            return None
        if isinstance(at, str):
            key, at = at, state.get(at)
            if not at:
                raise LookupError("화면에서 대상 위치를 찾지 못했습니다: %s" % key)
        if not (isinstance(at, (list, tuple)) and len(at) == 2 and all(_finite(v) and 0 <= v <= 1 for v in at)):
            raise ValueError("화면에서 읽은 대상 좌표가 창 범위를 벗어났습니다: %r" % (at,))
        return window["x"] + window["width"] * at[0], window["y"] + window["height"] * at[1]

    for step in steps:
        if "tap" in step or "hold" in step:
            keys = step.get("tap", step.get("hold"))
            keys = [keys] if isinstance(keys, str) else keys
            for key in keys:
                backend.key(key, True)
            sleep_ms(int(step.get("ms", 40 if "tap" in step else 0)))
            for key in reversed(keys):
                backend.key(key, False)
        elif "click" in step:
            target = point(step.get("at", "cursor"))
            if target is None:
                target = backend.cursor()
            else:
                backend.move(*target)
            backend.button(step["click"], True, *target)
            sleep_ms(int(step.get("ms", 40)))
            backend.button(step["click"], False, *target)
        elif "move" in step:
            backend.move(*point(step["move"]))
        elif "look" in step:
            backend.look(*step["look"])
        else:
            sleep_ms(int(step["wait"]))


def _check_point(at, where):
    if isinstance(at, str) and at:
        return
    if not (isinstance(at, list) and len(at) == 2 and all(_finite(v) and 0 <= v <= 1 for v in at)):
        raise ValueError("%s: 좌표는 창 기준 [0..1, 0..1] 또는 state 키 이름입니다." % where)


def _duration(value, where, minimum=0):
    if type(value) is not int or not minimum <= value <= 60000:
        raise ValueError("%s: 입력 시간은 %d~60000ms의 정수여야 합니다." % (where, minimum))
    return value


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)
