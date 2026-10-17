"""One visible teaching runtime for RPG, racing and MOBA.

Run: python -m gamelab.desktop_game --game party --socket /tmp/teaching-party.sock
Racing/MOBA use an independent clock; RPG advances only on player/model input.
Keyboard, mouse buttons and the socket hook all invoke the same action handler.
"""
from __future__ import annotations
import argparse
import math
import os
import sys
import threading
import time
from pathlib import Path

from gamelab.protocol import GameServer
from sims import SIMS
from sims.base import truth
from sims.service import SimService
from gamelab import screens, presentation
from kbm.keys import KEYCODES

WIDTH, HEIGHT = screens.WIDTH, screens.HEIGHT
BG, PANEL, TEXT, MUTED = "#0b1120", "#142137", "#eef4ff", "#9fb0c8"
CYAN, GREEN, RED, AMBER, OFF = "#67e8f9", "#7be5ad", "#ff6b81", "#ffb357", "#182c3c"
TITLES = {"party": "Party RPG", "racer": "Lane Racer", "moba": "Three Lane MOBA"}
DISPLAY_TITLES = {"party": "빛의 원정대", "racer": "코스트 레이서", "moba": "삼중 전선"}
GENRES = {"party": "턴제 RPG", "racer": "실시간 레이싱", "moba": "실시간 MOBA"}
KEYS = {
    "party": {"1": "attack", "2": "aoe_skill", "3": "heal", "4": "guard", "5": "use_item"},
    "racer": {"up": "accelerate", "down": "brake", "left": "steer_left", "right": "steer_right", "space": "boost", "w": "keep"},
    "moba": {"w": "advance", "s": "retreat", "space": "attack", "q": "skill", "b": "recall", "a": "lane_left", "d": "lane_right", "e": "wait"},
}
_CONTROL_KEYS = {key for mapping in KEYS.values() for key in mapping} | {"n", "p"}
_MAC_CONTROL_KEYS = {code: key for key, code in KEYCODES.items() if key in _CONTROL_KEYS}


def input_key(event):
    """Keep symbolic shortcuts, with an Aqua physical-key fallback for IMEs.

    Tk 8.6 Aqua packs its native virtual key in bits 24..31 of keycode:
    https://github.com/tcltk/tk/blob/core-8-6-branch/macosx/tkMacOSXKeyboard.c
    Korean input can supply keysym='??' while retaining that native key.
    """
    key = event.keysym.lower()
    if key in _CONTROL_KEYS or sys.platform != "darwin":
        return key
    code, state = getattr(event, "keycode", None), getattr(event, "state", 0)
    if type(code) is not int or not 0 <= code <= 0xffffffff or type(state) is not int:
        return key
    # Aqua Control/Command/Option chords are not plain game input. Shift and
    # CapsLock retain the existing case-insensitive behavior.
    if state & (0x04 | 0x08 | 0x10):
        return key
    physical = code >> 24
    # 128..255, including NO_VIRTUAL, represent no physical keyboard key.
    if physical > 127:
        return key
    return _MAC_CONTROL_KEYS.get(physical, key)


class DesktopService(SimService):
    """Small game host shared by real manual input and mod-style socket clients."""
    def __init__(self, game, scenario=None, tick_ms=3000):
        if not isinstance(tick_ms, (int, float)) or not math.isfinite(tick_ms) or not 100 <= tick_ms <= 10000:
            raise ValueError("tick_ms must be between 100 and 10000.")
        super().__init__(SIMS[game], scenario)
        self.game_id = game
        self.continuous = game != "party"
        self.tick_seconds = tick_ms / 1000
        self.paused = self.continuous
        self.ready = True
        self.elapsed = 0.0
        self.accumulator = 0.0
        self.input_used = False
        self.last_action = "none"

    def render_state(self):
        with self.lock:
            return {"snapshot": {**self.sim.snapshot(), "paused": self.paused,
                                  "elapsed": round(self.elapsed, 2), "last_action": self.last_action,
                                  "input_ready": not self.input_used, "tick_ms": round(self.tick_seconds * 1000)},
                    "events": list(self.events), "revision": self.revision, "episode": self.episode,
                    "done": self.sim.outcome != "playing"}

    def _state(self):
        state = self.render_state()
        if self._truth is None or self._truth[0] != self.revision:
            self._truth = (self.revision, truth(self.sim))
        # Full discrete search is a teaching reference; realtime inputs can arrive at any phase.
        return {**state, "truth": self._truth[1], "reference_kind": "next_beat_search",
                "description": self.sim.describe() + (" The world runs independently every %.0f ms; at most one input per beat. " % (self.tick_seconds * 1000) if self.continuous else " The battle waits for each input. ") +
                "Paused: %s. Input ready: %s." % (self.paused, not self.input_used)}

    def advance(self, dt):
        with self.lock:
            if not self.continuous or self.paused or self.sim.outcome != "playing": return []
            if not isinstance(dt, (int, float)) or not math.isfinite(dt) or dt < 0:
                raise ValueError("dt must be finite and nonnegative")
            self.elapsed += dt
            self.accumulator += dt
            events = []
            while self.accumulator >= self.tick_seconds and self.sim.outcome == "playing":
                self.accumulator -= self.tick_seconds
                events += self.sim.world_step()
                self.input_used = False
                self.revision += 1
            if events: self.events = events[-5:]
            return events

    def handle(self, request):
        with self.lock:
            op = request.get("op")
            if op == "info":
                return {"ok": True, "actions": list(self.sim.ACTIONS), "scenarios": list(self.sim.SCENARIOS),
                        "timing": "realtime" if self.continuous else "turn", "continuous": self.continuous,
                        "supports_pause": self.continuous, "tick_ms": self.tick_seconds * 1000,
                        "build": {"game": self.game_id, "version": "desktop-v1"}}
            if op in ("pause", "resume", "halt"):
                self.paused = self.continuous and op != "resume"
                self.ready = False
                self.revision += 1
                return {"ok": True, **self._state()}
            if op == "reset":
                names = list(self.sim.SCENARIOS)
                scenario = request.get("scenario") or names[(names.index(self.sim.scenario) + 1) % len(names)]
                if scenario not in self.sim.SCENARIOS:
                    return {"ok": False, "error": "Unknown scenario: %s" % scenario, **self._state()}
                self.sim = self.sim_cls.start(scenario)
                self.paused, self.ready = self.continuous, True
                self.input_used = False
                self.elapsed = self.accumulator = 0.0
                self.episode += 1; self.revision += 1
                self.events = ["New scenario: " + scenario]; self.last_action = "none"
                return {"ok": True, **self._state()}
            if op == "step":
                if request.get("action") not in self.sim.ACTIONS:
                    return {"ok": False, "error": "Unknown action.", **self._state()}
                expected = request.get("expected_revision")
                if expected is not None and expected != self.revision:
                    return {"ok": False, "stale": True, "error": "Game state changed while the model decided.", **self._state()}
                observed_at, limit = request.get("observed_at"), request.get("max_age_ms")
                if observed_at is not None and limit is not None:
                    if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in (observed_at, limit)) or limit < 0:
                        return {"ok": False, "error": "Invalid observation age budget.", **self._state()}
                    if (time.time() - observed_at) * 1000 > limit:
                        return {"ok": False, "stale": True, "error": "Decision exceeded its age budget.", **self._state()}
                if self.sim.outcome != "playing":
                    return {"ok": False, "error": "Game ended. Reset for another scenario.", **self._state()}
                if self.continuous and self.paused and not self.ready:
                    return {"ok": False, "error": "Game paused. Resume before applying input.", **self._state()}
                if self.continuous and self.input_used:
                    return {"ok": False, "busy": True, "error": "One input per beat; waiting for the next world tick.", **self._state()}
                before = self.sim.snapshot()
                action = request["action"]
                self.events = self.sim.apply(action) if self.continuous else self.sim.step(action)
                self.last_action = action
                if self.continuous:
                    self.paused, self.ready, self.input_used = False, False, True
                self.revision += 1
                return {"ok": True, "before": before, **self._state()}
            if op == "state": return {"ok": True, **self._state()}
            return {"ok": False, "error": "Unknown operation: %s" % op, **self._state()}


class GameWindow:
    def __init__(self, service, socket_path=None):
        import sys
        import tkinter as tk
        # Relocatable uv Python needs its own Tcl resource directories.
        if not getattr(sys, "frozen", False):
            for variable, directory, entry in (("TCL_LIBRARY", "tcl8.6", "init.tcl"), ("TK_LIBRARY", "tk8.6", "tk.tcl")):
                resource = Path(sys.base_prefix) / "lib" / directory
                if (resource / entry).is_file(): os.environ.setdefault(variable, str(resource))
        self.service, self.socket_path = service, socket_path
        self.server, self.socket_inode, self.closed = None, None, False
        self.buttons = []; self.last_frame = time.monotonic(); self.visual_time = 0
        self.root = tk.Tk()
        self.root.title("Teaching Lab - " + TITLES[service.game_id])
        self.root.geometry(f"{WIDTH}x{HEIGHT}+90+30")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.canvas = tk.Canvas(self.root, width=WIDTH, height=HEIGHT, bg=BG, bd=0, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.root.bind("<KeyPress>", self.keypress)
        self.canvas.bind("<Motion>", self.hover)
        self.canvas.bind("<Button-1>", self.click)
        if socket_path:
            self.server = GameServer(socket_path, service)
            self.socket_inode = os.stat(socket_path).st_ino
            threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.draw()
        self.root.after(100, self.show)
        self.root.after(33, self.tick)

    def show(self): self.root.lift(); self.root.focus_force()

    def text(self, x, y, text, size=12, color=TEXT, anchor="w", bold=False):
        return self.canvas.create_text(x, y, text=text, fill=color, anchor=anchor,
                                       font=("Arial", size, "bold" if bold else "normal"))

    def box(self, x, y, w, h, fill=PANEL, outline=""):
        return self.canvas.create_rectangle(x, y, x+w, y+h, fill=fill, outline=outline)

    def bar(self, x, y, w, label, value, maximum, color=GREEN):
        self.text(x, y, f"{label}  {value}/{maximum}", 13)
        self.box(x, y+14, w, 13, OFF)
        self.box(x, y+14, w * max(0, min(1, value/maximum)), 13, color)

    def submit(self, request):
        response = self.service.handle(request)
        if not response.get("ok"):
            if response.get("busy"):
                message = "이번 박자에 이미 입력했습니다. 다음 박자에 다시 조작하세요."
            elif self.service.sim.outcome != "playing":
                message = "게임이 끝났습니다. N 키로 다음 시나리오를 시작하세요."
            elif self.service.paused:
                message = "일시정지 상태입니다. P 키로 재개하세요."
            else:
                message = response.get("error", "입력을 적용하지 못했습니다.")
            self.input_notice = message
            self.notice_until = time.monotonic() + 2.5
        else:
            self.input_notice = ""
        return response

    def keypress(self, event):
        key = input_key(event)
        if key == "n": self.submit({"op": "reset"})
        elif key == "p": self.toggle_pause()
        elif key in KEYS[self.service.game_id]: self.submit({"op": "step", "action": KEYS[self.service.game_id][key]})
        self.draw()

    def toggle_pause(self):
        return self.submit({"op": "resume" if self.service.paused else "pause"})

    def hover(self, event):
        action = next((a for x, y, width, height, a in self.buttons
                       if x <= event.x <= x+width and y <= event.y <= y+height), None)
        self.hover_action = action
        self.canvas.configure(cursor="pointinghand" if action else "")

    def click(self, event):
        for x, y, width, height, action in self.buttons:
            if x <= event.x <= x+width and y <= event.y <= y+height:
                if action == "__reset": self.submit({"op": "reset"})
                elif action == "__pause": self.toggle_pause()
                else: self.submit({"op": "step", "action": action})
                self.draw(); return

    def tick(self):
        if self.closed: return
        now = time.monotonic(); dt = now - self.last_frame; self.last_frame = now
        self.visual_time += dt
        self.service.advance(dt)
        self.draw(); self.root.after(33, self.tick)

    def draw(self):
        self.canvas.delete("all"); self.buttons = []
        data = self.service.render_state(); s = data["snapshot"]; game = self.service.game_id
        now = time.monotonic()
        previous = getattr(self, "previous_snapshot", None)
        # Both local input and socket input receive the same visible feedback.
        if previous and data["revision"] != getattr(self, "previous_revision", None):
            applied = s["last_action"] != "none" and (
                not self.service.continuous or s["last_action"] != previous["last_action"] or
                (previous["input_ready"] and not s["input_ready"]))
            if applied: self.effect_started = now
        self.previous_snapshot, self.previous_revision = dict(s), data["revision"]
        self.effect_age = max(0, now-getattr(self, "effect_started", 0))
        self.effect_active = self.effect_age < .7
        self.text(24, 30, DISPLAY_TITLES[game], 25, TEXT, bold=True)
        self.text(245, 31, GENRES[game], 11, MUTED)
        if s["outcome"] != "playing":
            status = {"won": "목표 달성", "lost": "도전 실패", "draw": "시간 종료"}[s["outcome"]]
        elif game == "party": status = "행동 선택 중 · 시간 정지"
        elif s["paused"]: status = "일시정지 · P 키로 시작"
        else: status = "진행 중 · 독립 게임 시계"
        self.text(1075, 28, status, 13, AMBER if s["paused"] else GREEN, anchor="e", bold=True)
        scenario = presentation.SCENARIOS.get(s["scenario"], s["scenario"])
        self.text(1075, 62, f"{scenario}  ·  {'턴' if game == 'party' else '박자'} {s['turn']}", 12, MUTED, anchor="e")
        self.text(24, 63, "키보드 또는 아래 버튼으로 플레이하세요", 12, MUTED)
        getattr(presentation, "draw_" + game)(self, s)
        if self.service.continuous:
            progress = min(1, self.service.accumulator/self.service.tick_seconds)
            self.box(20, 425, 1060*progress, 3, CYAN)
        actions = list(KEYS[game].items())
        actions += [("N", "__reset")] + ([("P", "__pause")] if self.service.continuous else [])
        width = 1050/len(actions)
        for i, (key, action) in enumerate(actions):
            x, y = 25+i*width, 443
            selected = action == s["last_action"] and self.effect_active
            hover = action == getattr(self, "hover_action", None)
            control = action.startswith("__")
            waiting = self.service.continuous and not s["input_ready"] and not control
            fill = "#285363" if selected else "#2b4058" if hover else "#1c2c40" if control else "#23354e"
            self.box(x, y, width-6, 48, fill, CYAN if selected or hover else "#354b66")
            label = presentation.ACTION_LABELS[action]
            if action == "__pause" and s["paused"]: label = "시작 / 재개"
            self.text(x+(width-6)/2, y+13, presentation.KEY_LABELS.get(key, key.upper()), 10, CYAN, "center", True)
            self.text(x+(width-6)/2, y+33, label, 10, MUTED if waiting else TEXT, "center")
            self.buttons.append((x, y, width-6, 48, action))
        notice = getattr(self, "input_notice", "") if now < getattr(self, "notice_until", 0) else ""
        if not notice:
            notice = "1회 입력 = 1턴" if game == "party" else (
                "첫 행동을 선택하면 시작 · 박자당 1회 입력" if s["paused"] and self.service.ready else
                "일시정지 · P 키로 재개" if s["paused"] else
                "입력 가능 · 박자당 1회 입력" if s["input_ready"] else "입력 적용 완료 · 다음 박자를 기다리는 중")
        self.text(26, 503, notice, 10, AMBER if getattr(self, "input_notice", "") and now < getattr(self, "notice_until", 0) else MUTED)
        for x in (24, 1076): self.box(x-3, 517, 6, 6, AMBER)
        self.text(40, 520, "화면 관측 계기 · 키마 제어는 아래 화면 픽셀에서 상태를 읽습니다", 10, MUTED)
        for i, (name, value) in enumerate(zip(screens.fields(game), screens.encode(game, s))):
            x, y, centers = screens.instrument(i)
            self.text(x, y, name, 9, MUTED)
            self.text(x+150, y, str(value), 10, TEXT, "e")
            for bit, (bx, by) in enumerate(centers):
                self.box(bx-2, by-3, 4, 6, CYAN if value & (1<<bit) else OFF)
        self.box(20, 750, 1060, 34, "#142137")
        last_action = presentation.ACTION_LABELS.get(s["last_action"], s["last_action"])
        self.text(34, 767, "최근 행동 · " + last_action, 11, CYAN)
        event = self.event_label(data["events"][-1]) if data["events"] else "목표와 상황을 살피고 행동을 선택하세요."
        self.text(270, 767, event, 11, MUTED)
        if s["outcome"] != "playing":
            self.box(448, 194, 395, 107, "#101e30", GREEN if s["outcome"] == "won" else RED)
            self.text(645, 229, status, 25, GREEN if s["outcome"] == "won" else RED, "center", True)
            self.text(645, 274, "N 키 · 다음 시나리오 도전", 12, TEXT, "center")

    @staticmethod
    def event_label(event):
        import re
        fixed = {"Recalled to base: +30 HP.": "기지로 귀환했습니다. 체력 +30",
                 "Skill is cooling down.": "스킬을 다시 사용할 수 있을 때까지 기다리세요.",
                 "Hit the enemy core.": "적 코어를 공격했습니다.",
                 "No target in range.": "공격 범위에 대상이 없습니다.",
                 "New minion waves spawned in all three lanes.": "모든 전선에 새 미니언이 등장했습니다."}
        if event in fixed: return fixed[event]
        if event.startswith("New scenario: "):
            scenario = event.removeprefix("New scenario: ")
            return "새 시나리오 · " + presentation.SCENARIOS.get(scenario, scenario)
        match = re.fullmatch(r"Cleared (\d+) enemy minion\(s\)\.", event)
        if match: return f"적 미니언 {match[1]}마리를 처치했습니다."
        match = re.fullmatch(r"Hero took (\d+) damage\.", event)
        if match: return f"영웅이 {match[1]} 피해를 받았습니다."
        match = re.fullmatch(r"Hit the (top|mid|bot) tower\.", event)
        if match: return {"top":"상단", "mid":"중단", "bot":"하단"}[match[1]] + " 적 포탑을 공격했습니다."
        return event.replace("paladin", "성기사").replace("knight", "기사")

    def draw_party(self, state): presentation.draw_party(self, state)
    def draw_racer(self, state): presentation.draw_racer(self, state)
    def draw_moba(self, state): presentation.draw_moba(self, state)

    def close(self):
        self.closed = True
        if self.server:
            self.server.shutdown(); self.server.server_close()
        if self.socket_path:
            path = Path(self.socket_path)
            if path.exists() and path.stat().st_ino == self.socket_inode: path.unlink()
        self.root.destroy()

    def run(self): self.root.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", choices=tuple(SIMS), required=True)
    parser.add_argument("--socket")
    parser.add_argument("--scenario")
    parser.add_argument("--tick-ms", type=int, default=3000, help="Realtime world beat, independent of model latency (100-10000).")
    args = parser.parse_args()
    GameWindow(DesktopService(args.game, args.scenario, args.tick_ms), args.socket).run()


if __name__ == "__main__": main()
