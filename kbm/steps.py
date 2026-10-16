"""game.json input steps (tap, hold, click, move, look, wait) for the kbm connector."""

from kbm.keys import KEYCODES
from kbm.macos import sleep_ms


STEP_KINDS = ("tap", "hold", "click", "move", "look", "wait")


def validate_step(step, where):
    """Check one input step and return how long it blocks, in ms."""
    if not isinstance(step, dict) or len(set(step) & set(STEP_KINDS)) != 1:
        raise ValueError("%s: 단계마다 %s 중 하나만 지정하세요: %r" % (where, "/".join(STEP_KINDS), step))
    kind = next(k for k in STEP_KINDS if k in step)
    value = step[kind]
    if kind in ("tap", "hold"):
        for key in [value] if isinstance(value, str) else value:
            if key not in KEYCODES:
                raise ValueError("%s: 알 수 없는 키 %r" % (where, key))
        return int(step.get("ms", 40 if kind == "tap" else 0)) or _missing(where, "hold에는 ms가 필요합니다.")
    if kind == "click":
        if value not in ("left", "right"):
            raise ValueError("%s: click은 left 또는 right입니다." % where)
        _check_point(step.get("at", "cursor"), where)
        return int(step.get("ms", 40))
    if kind == "move":
        _check_point(value, where)
        return 0
    if kind == "look":
        if not (isinstance(value, list) and len(value) == 2):
            raise ValueError("%s: look은 [dx, dy] 픽셀입니다." % where)
        return 0
    return int(value)


def run_steps(steps, backend, window, state):
    """Translate steps into input events. Points are window-relative 0..1 or a state key."""
    def point(at):
        if at == "cursor":
            return None
        if isinstance(at, str):
            key, at = at, state.get(at)
            if not at:
                raise LookupError("화면에서 대상 위치를 찾지 못했습니다: %s" % key)
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
    if at == "cursor" or isinstance(at, str):
        return
    if not (isinstance(at, list) and len(at) == 2 and all(0 <= v <= 1 for v in at)):
        raise ValueError("%s: 좌표는 창 기준 [0..1, 0..1] 또는 state 키 이름입니다." % where)


def _missing(where, message):
    raise ValueError("%s: %s" % (where, message))
