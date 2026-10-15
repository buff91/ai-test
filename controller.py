"""Separate Jev/Laya test console that launches and controls the game binary."""

import json
import os
import queue
import subprocess
import tempfile
import threading
import time
import tkinter as tk
import urllib.error
from datetime import datetime
from pathlib import Path
from tkinter import filedialog

from compare import OPTIONS, QUESTIONS, PROVIDERS, call_jev, make_laya_mlx_router, make_laya_router, summarize_answer
from game_core import ACTIONS, ACTION_KO, SCENARIOS
from game_ipc import GameClient


ROOT = Path(__file__).resolve().parent
GAME_BINARY = ROOT / "dist" / "ArenaGame" / "ArenaGame"
BG = "#0d1420"
PANEL = "#172333"
PANEL_2 = "#1d2d40"
TEXT = "#e9f4fa"
MUTED = "#9fb6c8"
CYAN = "#65d9e8"
BLUE = "#62aef5"
RED = "#fa6876"


class TestConsole:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Jev Game Test Console")
        self.root.geometry("620x740+%d+60" % max(0, self.root.winfo_screenwidth() - 640))
        self.root.minsize(620, 620)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.model_var = tk.StringVar(value="jev")
        self.scenario_var = tk.StringVar(value="교전 시작")
        self.compare_var = tk.BooleanVar(value=False)
        self.key_var = tk.StringVar()
        self.auto = False
        self.busy = False
        self.generation = 0
        self.results_queue = queue.Queue()
        self.model_lock = threading.Lock()
        self.routers = {}
        self.history = []
        self.game_state = None
        self.client = None
        self.process = None
        self.temp_dir = None
        self.log_file = None
        self._build_ui()
        self._launch_game()
        self.root.after(75, self._poll_results)
        self.root.bind("<Escape>", lambda _: self._pause())

    def label(self, parent, text, size=11, color=TEXT, bold=False, **kwargs):
        return tk.Label(parent, text=text, fg=color, bg=parent.cget("bg"),
                        font=("Helvetica Neue", size, "bold" if bold else "normal"), **kwargs)

    def button(self, parent, text, command, primary=False):
        return tk.Button(parent, text=text, command=command, font=("Helvetica Neue", 11, "bold"),
                         fg=BG if primary else TEXT, bg=CYAN if primary else PANEL_2,
                         activeforeground=BG if primary else TEXT, activebackground="#a0eff5" if primary else "#29425b",
                         relief="flat", bd=0, padx=12, pady=8)

    def _build_ui(self):
        top = tk.Frame(self.root, bg=BG, padx=22, pady=18)
        top.pack(fill="x")
        self.label(top, "JEV GAME TEST CONSOLE", 21, TEXT, True).pack(anchor="w")
        self.label(top, "별도 게임 실행 파일의 상태를 읽고 모델 행동을 전달합니다.", 11, MUTED).pack(anchor="w", pady=(3, 0))

        setup = tk.Frame(self.root, bg=PANEL, padx=16, pady=14)
        setup.pack(fill="x", padx=22)
        row = tk.Frame(setup, bg=PANEL)
        row.pack(fill="x")
        self.label(row, "행동 모델", 11, MUTED).pack(side="left")
        menu = tk.OptionMenu(row, self.model_var, "jev", "laya-mlx", "laya")
        menu.configure(fg=TEXT, bg=PANEL_2, relief="flat", bd=0, width=11, highlightthickness=0)
        menu["menu"].configure(bg=PANEL_2, fg=TEXT)
        menu.pack(side="left", padx=10)
        self.label(row, "시작 상황", 11, MUTED).pack(side="left", padx=(12, 0))
        scenario = tk.OptionMenu(row, self.scenario_var, *SCENARIOS, command=self._reset)
        scenario.configure(fg=TEXT, bg=PANEL_2, relief="flat", bd=0, width=11, highlightthickness=0)
        scenario["menu"].configure(bg=PANEL_2, fg=TEXT)
        scenario.pack(side="left", padx=10)

        key_row = tk.Frame(setup, bg=PANEL)
        key_row.pack(fill="x", pady=(12, 0))
        self.label(key_row, "Jev API 키 · 메모리에만 보관", 10, MUTED).pack(side="left")
        self.key_entry = tk.Entry(key_row, textvariable=self.key_var, show="•", width=28,
                                  fg=TEXT, bg=PANEL_2, insertbackground=TEXT, relief="flat", bd=0,
                                  font=("Helvetica Neue", 11))
        self.key_entry.pack(side="right", ipady=6)

        controls = tk.Frame(self.root, bg=BG, padx=22, pady=15)
        controls.pack(fill="x")
        self.button(controls, "▶  모델 한 턴", self._one_turn, True).pack(side="left")
        self.button(controls, "자동 플레이", self._toggle_auto).pack(side="left", padx=7)
        self.button(controls, "분석만", self._analyze_only).pack(side="left", padx=2)
        self.button(controls, "게임 재시작", self._reset).pack(side="left", padx=7)
        tk.Checkbutton(self.root, text="Laya-MLX도 함께 분석", variable=self.compare_var,
                       fg=MUTED, bg=BG, selectcolor=PANEL_2, activebackground=BG,
                       activeforeground=TEXT, font=("Helvetica Neue", 11)).pack(anchor="w", padx=22)

        body = tk.Frame(self.root, bg=BG, padx=22, pady=10)
        body.pack(fill="both", expand=True)
        self.label(body, "게임 실행 파일에서 읽은 상태", 12, TEXT, True).pack(anchor="w")
        self.state_text = tk.Text(body, height=7, wrap="word", bg=PANEL, fg=MUTED, relief="flat", bd=0,
                                  padx=10, pady=9, font=("Helvetica Neue", 10))
        self.state_text.pack(fill="x", pady=(6, 12))
        self.state_text.configure(state="disabled")
        self.label(body, "모델의 판단", 12, TEXT, True).pack(anchor="w")
        self.decision_holder = tk.Frame(body, bg=BG)
        self.decision_holder.pack(fill="x", pady=(6, 8))
        self.label(self.decision_holder, "실행하면 선택과 확률이 표시됩니다.", 11, MUTED).pack(anchor="w")
        self.label(body, "실행 기록", 12, TEXT, True).pack(anchor="w")
        self.log_text = tk.Text(body, height=6, wrap="word", bg=PANEL, fg=MUTED, relief="flat", bd=0,
                                padx=10, pady=9, font=("Helvetica Neue", 10))
        self.log_text.pack(fill="both", expand=True, pady=(6, 0))
        self.log_text.configure(state="disabled")

        bottom = tk.Frame(self.root, bg=BG, padx=22, pady=14)
        bottom.pack(fill="x")
        self.status_label = self.label(bottom, "게임 실행 중…", 11, CYAN)
        self.status_label.pack(side="left")
        self.button(bottom, "기록 저장", self._save).pack(side="right")

    def _status(self, message, error=False):
        self.status_label.configure(text=message, fg=RED if error else CYAN)

    def _log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _launch_game(self):
        if not GAME_BINARY.is_file():
            self._status("게임 실행 파일이 없습니다. 먼저 ./build_game.sh 를 실행하세요.", True)
            return
        self._stop_game()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="jev-arena-")
        socket_path = str(Path(self.temp_dir.name) / "game.sock")
        self.client = GameClient(socket_path)
        self.log_file = open(Path(self.temp_dir.name) / "game.log", "w", encoding="utf-8")
        self.process = subprocess.Popen([str(GAME_BINARY), "--socket", socket_path],
                                        stdout=self.log_file, stderr=subprocess.STDOUT)
        self._status("게임 실행 파일을 여는 중…")
        self.root.after(150, self._wait_for_game)

    def _wait_for_game(self):
        if self.process and self.process.poll() is not None:
            self._status("게임 실행 파일이 종료되었습니다. 게임 로그를 확인하세요.", True)
            return
        try:
            state = self.client.request("state")
        except (OSError, ValueError):
            self.root.after(150, self._wait_for_game)
            return
        self._show_game_state(state)
        self._status("게임 연결됨 · Jev 키를 입력하고 모델 한 턴을 눌러보세요.")
        self._log("별도 게임 실행 파일에 연결했습니다.")
        self.root.after(250, self._poll_game)

    def _poll_game(self):
        if not self.client:
            return
        try:
            state = self.client.request("state")
        except (OSError, ValueError):
            self.auto = False
            self.game_state = None
            self._status("게임 연결이 끊어졌습니다. 게임 재시작을 눌러주세요.", True)
            return
        previous = self.game_state["revision"] if self.game_state else None
        if previous != state["revision"]:
            self._show_game_state(state)
            if previous is not None and state["events"]:
                self._log("게임 상태 변경 · " + " ".join(state["events"]))
        self.root.after(250, self._poll_game)

    def _show_game_state(self, state):
        self.game_state = state
        self.state_text.configure(state="normal")
        self.state_text.delete("1.0", "end")
        self.state_text.insert("1.0", state["description"])
        self.state_text.configure(state="disabled")

    def _reset(self, name=None):
        self.auto = False
        self.generation += 1
        self.busy = False
        self.history.clear()
        if not self.client or not self.game_state:
            self._launch_game()
            return
        try:
            state = self.client.request("reset", scenario=name or self.scenario_var.get())
        except (OSError, ValueError):
            self._launch_game()
            return
        self._show_game_state(state)
        self._status("게임을 다시 시작했습니다.")

    def _one_turn(self):
        self._request_decision(True)

    def _analyze_only(self):
        self._request_decision(False)

    def _pause(self):
        self.auto = False
        self._status("자동 플레이를 멈췄습니다.")

    def _toggle_auto(self):
        if self.auto:
            self._pause()
        else:
            self.auto = True
            self._request_decision(True)

    def _request_decision(self, apply):
        if self.busy:
            return
        if not self.game_state or not self.client:
            self.auto = False
            self._status("게임이 아직 연결되지 않았습니다.", True)
            return
        if self.game_state["snapshot"]["outcome"] != "playing":
            self.auto = False
            self._status("게임이 끝났습니다. 게임 재시작을 눌러주세요.", True)
            return
        provider = self.model_var.get()
        key = self.key_var.get().strip() or os.environ.get("JEV_API_KEY", "")
        if provider == "jev" and not key:
            self.auto = False
            self._status("Jev API 키를 위쪽 입력칸에 넣어주세요.", True)
            self.key_entry.focus_set()
            return
        providers = [provider]
        if self.compare_var.get() and provider != "laya-mlx":
            providers.append("laya-mlx")
        self.busy = True
        state = self.game_state
        generation = self.generation
        self._status(provider.upper() + " 판단 중…")
        threading.Thread(target=self._worker, args=(generation, state, providers, key, apply), daemon=True).start()

    def _worker(self, generation, state, providers, key, apply):
        results = []
        for provider in providers:
            started = time.perf_counter()
            try:
                with self.model_lock:
                    if provider == "jev":
                        raw = call_jev(state["description"], key, PROVIDERS["jev"]["model"], PROVIDERS["jev"]["timeout"])
                    else:
                        if provider not in self.routers:
                            factory = make_laya_mlx_router if provider == "laya-mlx" else make_laya_router
                            self.routers[provider] = factory()
                        raw = self.routers[provider].predict(state["description"], QUESTIONS, **({} if PROVIDERS[provider]["model"] == "auto" else {"model": PROVIDERS[provider]["model"]}))
                result = summarize_answer(raw)
                if result["action"] not in OPTIONS:
                    raise ValueError("모델이 알 수 없는 행동을 반환했습니다.")
                result["status"] = "ok"
            except urllib.error.HTTPError as exc:
                result = {"status": "error", "error": "Jev HTTP %s" % exc.code}
            except Exception as exc:
                result = {"status": "error", "error": "%s: %s" % (type(exc).__name__, exc)}
            result["provider"] = provider
            result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
            results.append(result)
        self.results_queue.put((generation, state, results, apply))

    def _poll_results(self):
        try:
            while True:
                generation, state, results, apply = self.results_queue.get_nowait()
                if generation != self.generation:
                    continue
                self.busy = False
                self._show_decisions(results)
                chosen = results[0]
                if chosen["status"] != "ok":
                    self.auto = False
                    self._status("모델 호출 실패: " + chosen["error"], True)
                    self._log("호출 실패: " + chosen["error"])
                    continue
                if apply:
                    try:
                        reply = self.client.request("step", action=chosen["action"], expected_revision=state["revision"])
                    except (OSError, ValueError) as exc:
                        reply = {"ok": False, "error": str(exc)}
                    if not reply["ok"]:
                        self.auto = False
                        self._status("행동 적용 실패: " + reply["error"], True)
                        if "snapshot" in reply:
                            self._show_game_state(reply)
                        continue
                    self._show_game_state(reply)
                    self.history.append({"questions": QUESTIONS, "state": state["description"], "before": reply["before"],
                                         "results": results, "action_applied": chosen["action"],
                                         "events": reply["events"], "after": reply["snapshot"]})
                    self._log("턴 %02d · %s → %s · %s" %
                              (reply["before"]["turn"], chosen["provider"], ACTION_KO[chosen["action"]], " ".join(reply["events"])))
                    self._status("%s가 %s을(를) 선택했습니다." % (chosen["provider"], ACTION_KO[chosen["action"]]))
                    if reply["snapshot"]["outcome"] == "playing" and self.auto:
                        self.root.after(650, self._one_turn)
                    else:
                        self.auto = False
                else:
                    self._status("분석 완료 · 게임에는 행동을 보내지 않았습니다.")
        except queue.Empty:
            pass
        self.root.after(75, self._poll_results)

    def _show_decisions(self, results):
        for child in self.decision_holder.winfo_children():
            child.destroy()
        for result in results:
            card = tk.Frame(self.decision_holder, bg=PANEL, padx=10, pady=8)
            card.pack(fill="x", pady=3)
            if result["status"] != "ok":
                self.label(card, result["provider"] + " · " + result["error"], 10, RED).pack(anchor="w")
                continue
            color = CYAN if result["provider"] == "jev" else BLUE
            title = "%s  →  %s    %.1f ms" % (result["provider"].upper(), ACTION_KO[result["action"]], result["elapsed_ms"])
            self.label(card, title, 12, color, True).pack(anchor="w")
            probabilities = result.get("probabilities") or {}
            summary = "   ".join("%s %.0f%%" % (ACTION_KO[action], 100 * float(probabilities.get(action) or 0)) for action in ACTIONS)
            self.label(card, summary, 9, MUTED).pack(anchor="w", pady=(4, 0))
            detail = "위험도 %.2f / %d · 피격 가능성 %.1f%%" % (result["danger"], len(QUESTIONS["danger"]["criteria"]) - 1, result["incoming_attack"] * 100)
            self.label(card, detail, 10, color).pack(anchor="w", pady=(4, 0))

    def _save(self):
        if not self.history:
            self._status("저장할 모델 실행 기록이 없습니다.", True)
            return
        path = filedialog.asksaveasfilename(title="게임 테스트 기록 저장", initialdir=str(ROOT / "results"),
                                            initialfile="jev-game-%s.json" % datetime.now().strftime("%Y%m%d-%H%M%S"),
                                            defaultextension=".json", filetypes=[("JSON", "*.json")])
        if path:
            Path(path).write_text(json.dumps(self.history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self._status("기록을 저장했습니다.")

    def _stop_game(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process = None
        if self.log_file:
            self.log_file.close()
            self.log_file = None
        if self.temp_dir:
            self.temp_dir.cleanup()
            self.temp_dir = None
        self.client = None
        self.game_state = None

    def close(self):
        self.auto = False
        self._stop_game()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    TestConsole().run()
