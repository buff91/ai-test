"""macOS window lookup, screen capture, and CGEvent input. Quartz is imported lazily."""

import os
import subprocess
import tempfile
import time

from kbm.keys import KEYCODES, MODIFIER_FLAGS


def _quartz():
    try:
        import Quartz
    except ImportError as exc:
        raise RuntimeError("PyObjC Quartz가 없습니다. 'uv sync'로 kbm 의존성을 설치하세요.") from exc
    return Quartz


class MacPlatform:
    """Finds the game window, captures it, and reports focus and cursor position."""

    def __init__(self, owner, title_contains=""):
        self.owner = owner
        self.title_contains = title_contains

    def _windows(self):
        Q = _quartz()
        options = Q.kCGWindowListOptionOnScreenOnly | Q.kCGWindowListExcludeDesktopElements
        return [w for w in Q.CGWindowListCopyWindowInfo(options, Q.kCGNullWindowID) or []
                if w.get("kCGWindowLayer", 0) == 0]

    def find_window(self):
        """Largest on-screen window of the owner as {id, pid, x, y, width, height} in points."""
        matches = []
        for w in self._windows():
            if w.get("kCGWindowOwnerName") != self.owner:
                continue
            if self.title_contains and self.title_contains not in (w.get("kCGWindowName") or ""):
                continue
            b = w["kCGWindowBounds"]
            matches.append({"id": int(w["kCGWindowNumber"]), "pid": int(w["kCGWindowOwnerPID"]),
                            "x": float(b["X"]), "y": float(b["Y"]),
                            "width": float(b["Width"]), "height": float(b["Height"])})
        if not matches:
            Q = _quartz()
            hidden = [w for w in Q.CGWindowListCopyWindowInfo(Q.kCGWindowListOptionAll, Q.kCGNullWindowID) or []
                      if w.get("kCGWindowOwnerName") == self.owner and w.get("kCGWindowLayer", 0) == 0
                      and w["kCGWindowBounds"]["Height"] > 100]
            if hidden:
                raise LookupError("%s 창이 화면에 없습니다. 최소화했거나 다른 데스크톱(Space)에 있으면 캡처할 수 없으니 "
                                  "화면에 보이게 해 주세요 (다른 창에 가려지는 것은 괜찮습니다)." % self.owner)
            raise LookupError("게임 창을 찾지 못했습니다: owner=%r (게임이 실행 중인지 확인하세요)" % self.owner)
        return max(matches, key=lambda m: m["width"] * m["height"])

    def is_frontmost(self, window):
        # The window list is ordered front to back, so the first normal window has focus.
        front = next(iter(self._windows()), None)
        return bool(front) and int(front["kCGWindowOwnerPID"]) == window["pid"]

    def capture(self, window):
        """Capture only the game window (even if partly covered) as a PIL image."""
        from PIL import Image

        fd, path = tempfile.mkstemp(suffix=".png", prefix="kbm-frame-")
        os.close(fd)
        try:
            subprocess.run(["screencapture", "-x", "-o", "-l", str(window["id"]), "-t", "png", path],
                           check=True, timeout=5, capture_output=True)
            with Image.open(path) as image:
                image.load()
                return image.copy()
        finally:
            os.unlink(path)

    def cursor(self):
        Q = _quartz()
        point = Q.CGEventGetLocation(Q.CGEventCreate(None))
        return point.x, point.y

    def permissions(self):
        Q = _quartz()
        check = lambda name: getattr(Q, name)() if hasattr(Q, name) else None
        return {"screen_capture": check("CGPreflightScreenCaptureAccess"),
                "post_events": check("CGPreflightPostEventAccess")}


class QuartzInput:
    """Posts real keyboard and mouse events to the HID event tap."""

    def __init__(self):
        self.Q = _quartz()
        self.held = set()

    def _flags(self):
        flags = 0
        for key in self.held:
            flags |= MODIFIER_FLAGS.get(key, 0)
        return flags

    def key(self, name, down):
        event = self.Q.CGEventCreateKeyboardEvent(None, KEYCODES[name], down)
        (self.held.add if down else self.held.discard)(name)
        self.Q.CGEventSetFlags(event, self._flags())
        self.Q.CGEventPost(self.Q.kCGHIDEventTap, event)

    def move(self, x, y):
        Q = self.Q
        Q.CGEventPost(Q.kCGHIDEventTap, Q.CGEventCreateMouseEvent(None, Q.kCGEventMouseMoved, (x, y), Q.kCGMouseButtonLeft))

    def button(self, button, down, x, y):
        Q = self.Q
        kinds = {"left": (Q.kCGEventLeftMouseDown, Q.kCGEventLeftMouseUp, Q.kCGMouseButtonLeft),
                 "right": (Q.kCGEventRightMouseDown, Q.kCGEventRightMouseUp, Q.kCGMouseButtonRight)}
        press, release, code = kinds[button]
        event = Q.CGEventCreateMouseEvent(None, press if down else release, (x, y), code)
        Q.CGEventSetFlags(event, self._flags())
        Q.CGEventPost(Q.kCGHIDEventTap, event)

    def cursor(self):
        point = self.Q.CGEventGetLocation(self.Q.CGEventCreate(None))
        return point.x, point.y

    def look(self, dx, dy):
        """Relative mouse motion for games that capture the cursor (FPS camera)."""
        Q = self.Q
        point = Q.CGEventGetLocation(Q.CGEventCreate(None))
        event = Q.CGEventCreateMouseEvent(None, Q.kCGEventMouseMoved, point, Q.kCGMouseButtonLeft)
        Q.CGEventSetIntegerValueField(event, Q.kCGMouseEventDeltaX, int(dx))
        Q.CGEventSetIntegerValueField(event, Q.kCGMouseEventDeltaY, int(dy))
        Q.CGEventPost(Q.kCGHIDEventTap, event)

    def release_all(self):
        for name in list(self.held):
            self.key(name, False)


class DryRunInput:
    """Records the events it would send; used by --dry-run and tests."""

    def __init__(self):
        self.events = []
        self.held = set()

    def key(self, name, down):
        (self.held.add if down else self.held.discard)(name)
        self.events.append(("key", name, down))

    def move(self, x, y):
        self.events.append(("move", round(x, 1), round(y, 1)))

    def button(self, button, down, x, y):
        self.events.append(("button", button, down, round(x, 1), round(y, 1)))

    def cursor(self):
        return (0.0, 0.0)

    def look(self, dx, dy):
        self.events.append(("look", int(dx), int(dy)))

    def release_all(self):
        for name in list(self.held):
            self.key(name, False)


def sleep_ms(ms):
    if ms > 0:
        time.sleep(ms / 1000)
