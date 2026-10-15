"""Standalone arena game. The test controller runs in a different process."""

import argparse
import threading
import tkinter as tk

from game_core import ACTIONS, ACTION_KO, SCENARIOS
from game_ipc import GameServer, GameService


BG = "#0d1420"
PANEL = "#172333"
TEXT = "#e9f4fa"
MUTED = "#9fb6c8"
CYAN = "#65d9e8"
ORANGE = "#ffb56a"
RED = "#fa6876"
GREEN = "#8fe3ab"


class ArenaWindow:
    def __init__(self, service, socket_path=None):
        self.service = service
        self.server = None
        if socket_path:
            self.server = GameServer(socket_path, service)
            threading.Thread(target=self.server.serve_forever, daemon=True).start()

        self.root = tk.Tk()
        self.root.title("Arena Game")
        self.root.geometry("800x590+20+80")
        self.root.minsize(720, 500)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.seen_revision = -1

        header = tk.Frame(self.root, bg=BG, padx=22, pady=18)
        header.pack(fill="x")
        tk.Label(header, text="ARENA GAME", font=("Helvetica Neue", 22, "bold"), fg=TEXT, bg=BG).pack(side="left")
        tk.Label(header, text="별도 실행 중인 게임", font=("Helvetica Neue", 11), fg=CYAN, bg=BG).pack(side="right")

        self.canvas = tk.Canvas(self.root, bg="#101c2a", bd=0, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=22)
        self.canvas.bind("<Configure>", lambda _: self.draw())

        hud = tk.Frame(self.root, bg=PANEL, padx=22, pady=12)
        hud.pack(fill="x", padx=22, pady=(12, 0))
        self.turn_label = self.label(hud, "TURN 01", CYAN)
        self.turn_label.pack(side="left", padx=(0, 22))
        self.hp_label = self.label(hud, "HP 100", GREEN)
        self.hp_label.pack(side="left", padx=(0, 22))
        self.ammo_label = self.label(hud, "AMMO 3", CYAN)
        self.ammo_label.pack(side="left", padx=(0, 22))
        self.enemy_label = self.label(hud, "TURRET 60", ORANGE)
        self.enemy_label.pack(side="left")

        controls = tk.Frame(self.root, bg=BG, padx=22, pady=12)
        controls.pack(fill="x")
        self.label(controls, "직접 조작", MUTED).pack(side="left", padx=(0, 9))
        for action in ACTIONS:
            tk.Button(
                controls, text=ACTION_KO[action], command=lambda a=action: self.manual_step(a),
                font=("Helvetica Neue", 11), fg=TEXT, bg=PANEL,
                activeforeground=TEXT, activebackground="#29425b", relief="flat", bd=0,
                padx=8, pady=7,
            ).pack(side="left", padx=2)

        bottom = tk.Frame(self.root, bg=BG, padx=22, pady=7)
        bottom.pack(fill="x")
        self.scenario_var = tk.StringVar(value="교전 시작")
        tk.OptionMenu(bottom, self.scenario_var, *SCENARIOS, command=self.reset).pack(side="left")
        self.event_label = tk.Label(bottom, text="포탑을 파괴하고 출구에 도착하세요.", font=("Helvetica Neue", 11), fg=MUTED, bg=BG)
        self.event_label.pack(side="left", padx=14)
        self.refresh()
        self.root.after(100, self.poll)

    @staticmethod
    def label(parent, text, color):
        return tk.Label(parent, text=text, font=("Helvetica Neue", 12, "bold"), fg=color, bg=parent.cget("bg"))

    def reset(self, name):
        self.service.handle({"op": "reset", "scenario": name})
        self.refresh()

    def manual_step(self, action):
        self.service.handle({"op": "step", "action": action})
        self.refresh()

    def poll(self):
        with self.service.lock:
            revision = self.service.revision
        if revision != self.seen_revision:
            self.refresh()
        self.root.after(100, self.poll)

    def refresh(self):
        with self.service.lock:
            state = self.service._state()
        snapshot = state["snapshot"]
        self.seen_revision = state["revision"]
        self.turn_label.configure(text="TURN %02d" % snapshot["turn"])
        self.hp_label.configure(text="HP %d / 100" % snapshot["hp"])
        self.ammo_label.configure(text="AMMO %d / 3" % snapshot["ammo"])
        self.enemy_label.configure(text="TURRET %d / 60" % snapshot["enemy_hp"])
        self.event_label.configure(text=" ".join(state["events"]) or "포탑을 파괴하고 출구에 도착하세요.")
        if self.scenario_var.get() != snapshot["scenario"]:
            self.scenario_var.set(snapshot["scenario"])
        self.draw()

    def draw(self):
        canvas = self.canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 720)
        height = max(canvas.winfo_height(), 310)
        floor = height - 80
        x_at = lambda tile: 58 + (width - 116) * tile / 8
        with self.service.lock:
            arena = self.service.arena
            player_x, enemy_x = arena.player_x, arena.enemy_x
            enemy_hp, windup, outcome = arena.enemy_hp, arena.windup, arena.outcome

        for x in range(0, width, 42):
            canvas.create_line(x, 0, x, height, fill="#172a3a")
        for y in range(0, height, 42):
            canvas.create_line(0, y, width, y, fill="#172a3a")
        canvas.create_text(20, 18, text="SECTOR 07  /  TURRET TEST", anchor="nw", fill=CYAN, font=("Menlo", 10, "bold"))
        canvas.create_rectangle(0, floor, width, height, fill="#203344", outline="")
        canvas.create_line(0, floor, width, floor, fill=CYAN, width=2)
        for tile in range(9):
            px = x_at(tile)
            canvas.create_text(px, floor + 28, text=str(tile), fill="#7592a8", font=("Menlo", 10))
        cover = x_at(2)
        canvas.create_rectangle(cover - 24, floor - 54, cover + 24, floor, fill="#496779", outline=CYAN)
        canvas.create_text(cover, floor - 67, text="COVER", fill=CYAN, font=("Menlo", 9, "bold"))
        exit_x = x_at(8)
        canvas.create_rectangle(exit_x - 24, floor - 125, exit_x + 24, floor, fill="#193b3e", outline=GREEN, width=2)
        canvas.create_text(exit_x, floor - 140, text="EXIT", fill=GREEN, font=("Menlo", 10, "bold"))
        if enemy_hp > 0:
            enemy = x_at(enemy_x)
            color = RED if windup else ORANGE
            canvas.create_rectangle(enemy - 30, floor - 90, enemy + 30, floor - 20, fill="#784756", outline=color, width=2)
            canvas.create_oval(enemy - 9, floor - 65, enemy + 9, floor - 47, fill=color, outline="")
            canvas.create_text(enemy, floor - 105, text="CHARGING" if windup else "TURRET", fill=color, font=("Menlo", 10, "bold"))
            if windup:
                canvas.create_line(enemy - 30, floor - 55, x_at(player_x), floor - 60, fill=RED, dash=(5, 7), width=2)
        player = x_at(player_x)
        canvas.create_polygon(
            player, floor - 85, player + 20, floor - 68, player + 17, floor - 28,
            player - 17, floor - 28, player - 20, floor - 68,
            fill="#3d93aa", outline=CYAN, width=2,
        )
        canvas.create_text(player, floor - 100, text="PLAYER", fill=CYAN, font=("Menlo", 10, "bold"))
        if outcome != "playing":
            canvas.create_rectangle(0, 0, width, height, fill="#07111a", stipple="gray50", outline="")
            canvas.create_text(width / 2, height / 2, text="MISSION COMPLETE" if outcome == "won" else "MISSION FAILED", fill=GREEN if outcome == "won" else RED, font=("Helvetica Neue", 25, "bold"))

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", help="Private local socket used by the test controller")
    args = parser.parse_args()
    ArenaWindow(GameService(), args.socket).run()


if __name__ == "__main__":
    main()
