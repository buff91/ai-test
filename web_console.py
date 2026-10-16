"""Local WebView console for every game under games/: pick a game and interface, connect, and play.

It drives the same pieces as play.py (GameCase, the connectors, gamelab.runner.tick),
so how a game is started stays in games/<id>/interfaces/<name>/interface.json:
  socket + launch   the console starts the game ("launch.command") and stops it on disconnect
  socket + socket   the game is already running; a person starts it ("manual") and presses connect
  kbm               the game window must be open; KbmAgent captures it and sends input

Besides playing, it shows that the game is alive (process, last response), which build
runs (gamelab/build_info.py), and every model call as it happens (request, answer, time).
A game may draw its own state panel with games/<id>/view.js; otherwise a table is shown.

  uv run python web_console.py
  uv run python web_console.py --game arena --interface app     # connect right away
"""

import argparse
import json
import os
import secrets
import tempfile
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import count
from pathlib import Path
from urllib.parse import urlparse

from gamelab import build_info
from gamelab.game_case import GAMES, GameCase, list_games
from gamelab.game_types import PROVIDER_NAMES
from gamelab.policy import Policy
from gamelab.runner import format_row, new_session, record_row, summarize_rows, tick, write_summary
from settings import PROVIDERS


ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
ROWS_SHOWN = 30
CALLS_KEPT = 40
PHASES = {"observe": "게임 관찰", "act": "행동 전송", "pause": "게임 일시정지", "resume": "게임 재개", "reset": "새 판 시작"}
RUNTIMES = {"jev": "TypeSafe API (HTTPS)", "laya": "로컬 PyTorch", "laya-mlx": "로컬 MLX (Apple GPU)"}


def default_decider(provider, game, key):
    from gamelab.decide import for_game  # loads the model runtimes only when a model is first asked

    return for_game(provider, game, key)


class Probe:
    """The connected client, timing every request so the console can show that the game answers."""

    def __init__(self, client, lab, generation):
        self.client = client
        self.lab = lab
        self.generation = generation

    def request(self, op, **kwargs):
        if op in PHASES:
            self.lab._phase(self.generation, PHASES[op])
        started = time.perf_counter()
        try:
            response = self.client.request(op, **kwargs)
        except Exception as exc:
            self.lab._heartbeat(self.generation, op, None, "%s: %s" % (type(exc).__name__, exc))
            raise
        self.lab._heartbeat(self.generation, op, (time.perf_counter() - started) * 1000,
                            None if response.get("ok") else response.get("error"))
        return response

    def health(self):
        health = getattr(self.client, "health", None)
        return health() if health else None


class Traced:
    """A decider whose calls show up in the console while they run and after."""

    def __init__(self, decider, lab, generation, role):
        self.decider = decider
        self.provider = decider.provider
        self.lab = lab
        self.generation = generation
        self.role = role

    def decide(self, state):
        call = self.lab._call_started(self.generation, self.role, self.decider, state)
        row = self.decider.decide(state)
        self.lab._call_finished(self.generation, call, row)
        return row


class Lab:
    def __init__(self, games_dir=GAMES, make_decider=default_decider, record=True):
        self.games_dir = Path(games_dir)
        self.make_decider = make_decider
        self.record = record
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=6)
        self.token = secrets.token_urlsafe(32)
        self.key = os.environ.get("JEV_API_KEY", "")
        self.generation = 0
        self.call_ids = count(1)
        self.events = []
        self.stack = None
        self.frame_dir = tempfile.TemporaryDirectory(prefix="console-frames-")
        self._clear()

    def _clear(self):
        self.status_name = "disconnected"
        self.game = None
        self.client = None
        self.info = {}
        self.observed = None
        self.observed_at = 0.0
        self.conn_error = None
        self.rows = []
        self.outcomes = []
        self.episode = 0
        self.session = None
        self.busy = False
        self.auto = False
        self.error = None
        self.options = {}
        self.link = {"last_ok_at": None, "rtt_ms": None, "failures": 0, "last_error": None, "last_op": None}
        self.phase = {"name": "대기", "since": time.time()}
        self.calls = []
        self.window_build = None
        self.frame_at = None

    def _event(self, text):
        self.events.insert(0, {"at": datetime.now().strftime("%H:%M:%S"), "text": text})
        del self.events[80:]

    # Catalog -------------------------------------------------------------

    def catalog(self):
        games = []
        for game_id in list_games(self.games_dir):
            try:
                game = GameCase(game_id, self.games_dir)
            except Exception as exc:
                games.append({"id": game_id, "error": str(exc), "interfaces": []})
                continue
            interfaces = []
            for name in game.interfaces:
                try:
                    one = GameCase(game_id, self.games_dir, interface=name)
                    interfaces.append({"name": name, "connector": one.connector, "run": one.how_to_run(),
                                       "interval_ms": one.config["interval_ms"], "pause_key": one.config.get("pause_key")})
                except Exception as exc:
                    interfaces.append({"name": name, "error": str(exc)})
            games.append({
                "id": game.id, "name": game.name, "type": game.type, "type_label": game.type_spec["label"],
                "timing": game.type_spec["timing"], "latency_budget_ms": game.type_spec["latency_budget_ms"],
                "providers": game.providers, "has_policy": bool(game.policy), "questions": game.questions,
                "options": list(game.options), "interfaces": interfaces, "default_interface": game.interface,
                "view": (game.dir / "view.js").is_file(),
            })
        return {"games": games, "providers": list(PROVIDER_NAMES)}

    def view_script(self, game_id):
        if game_id not in list_games(self.games_dir):
            raise LookupError(game_id)
        path = self.games_dir / game_id / "view.js"
        if not path.is_file():
            raise LookupError(game_id)
        return path.read_bytes()

    # Connection ------------------------------------------------------------

    def connect(self, game_id, interface=None, agent="app", dry_run=False):
        game = GameCase(game_id, self.games_dir, interface=interface or None)
        game.require_interface()
        if agent not in ("app", "dev"):
            raise ValueError("agent는 app 또는 dev입니다.")
        self.disconnect()
        with self.lock:
            self.generation += 1
            self.status_name = "connecting"
            self.game = game
            self.options = {"provider": game.providers["live"], "shadows": list(game.providers["shadow"]),
                            "policy": False, "dry_run": bool(dry_run), "agent": agent}
            generation = self.generation
        threading.Thread(target=self._open, args=(generation, game, agent, bool(dry_run)), daemon=True).start()
        return {"connecting": True}

    def _open(self, generation, game, agent, dry_run):
        stack = ExitStack()
        try:
            client = Probe(stack.enter_context(game.connect(agent=agent, dry_run=dry_run)), self, generation)
            info = client.request("info")
            observed = client.request("observe", **self._frame_args(game))
        except Exception as exc:
            stack.close()
            with self.lock:
                if generation == self.generation:
                    self.status_name = "error"
                    self.conn_error = "%s: %s" % (type(exc).__name__, exc)
                    self._event("연결 실패: %s" % self.conn_error)
            return
        with self.lock:
            stale = generation != self.generation
            if not stale:
                self.stack, self.client, self.info = stack, client, info
                self._observed_locked(observed)
                self.status_name = "connected"
                self._phase_locked("대기")
                self._event("%s · %s 인터페이스에 연결했습니다." % (game.id, game.interface))
        if stale:
            stack.close()

    def disconnect(self):
        with self.lock:
            self.generation += 1
            stack, session, rows, outcomes, game = self.stack, self.session, self.rows, self.outcomes, self.game
            was = self.status_name
            self.stack = None
            self._clear()
            if was in ("connected", "connecting", "error"):
                self._event("연결을 끊었습니다.")
        if session and rows:
            write_summary(session, summarize_rows(list(reversed(rows)), game.questions, outcomes))
        if stack:
            stack.close()
        return {"disconnected": True}

    def close(self):
        self.disconnect()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.frame_dir.cleanup()

    # Liveness and calls (called from Probe and Traced) -----------------------

    def _heartbeat(self, generation, op, rtt_ms, error):
        with self.lock:
            if generation != self.generation:
                return
            self.link["last_op"] = op
            if rtt_ms is None:
                self.link["failures"] += 1
                self.link["last_error"] = error
                return
            # A response of any kind means the game is there; error is what it said, if it refused.
            self.link.update(last_ok_at=time.time(), rtt_ms=round(rtt_ms, 1), failures=0, last_error=error)

    def _phase(self, generation, name):
        with self.lock:
            if generation == self.generation:
                self._phase_locked(name)

    def _phase_locked(self, name):
        self.phase = {"name": name, "since": time.time()}

    def _call_started(self, generation, role, decider, state):
        provider = decider.provider
        model = getattr(decider, "jev_model" if provider == "jev" else "laya_model", None)
        questions = getattr(decider, "questions", None) or {}
        request = {"model": model, "state": state,
                   "questions": {qid: q.get("type") for qid, q in questions.items()}}
        call = {"id": next(self.call_ids), "role": role, "provider": provider, "model": model,
                "runtime": RUNTIMES.get(provider), "started_at": time.time(), "finished_at": None,
                "endpoint": PROVIDERS["jev"]["endpoint"] if provider == "jev" else None,
                "timeout_s": getattr(decider, "timeout", None) if provider == "jev" else None,
                "request": request, "input_chars": len(state if isinstance(state, str) else json.dumps(state))}
        with self.lock:
            if generation == self.generation:
                self.calls.insert(0, call)
                del self.calls[CALLS_KEPT:]
                if role == "live":
                    self._phase_locked("모델 판단")
        return call

    def _call_finished(self, generation, call, row):
        finished = time.time()
        with self.lock:
            # ms is measured here around the whole call, not taken from what the decider reports.
            call.update(finished_at=finished, status=row.get("status"), error=row.get("error"),
                        action=row.get("action"), ms=round((finished - call["started_at"]) * 1000, 1), usage=row.get("usage"),
                        routed_model=row.get("model"), confidence=row.get("confidence"),
                        response=row.get("answers"))

    def _observed_locked(self, observed):
        self.observed, self.observed_at = observed, time.time()
        if observed and observed.get("frame_saved"):
            self.frame_at = time.time()
        window = (observed or {}).get("window") or {}
        if window.get("pid") and (self.window_build or {}).get("pid") != window["pid"]:
            exe = build_info.executable_of(window["pid"])
            self.window_build = {"pid": window["pid"], "executable": exe,
                                 **(build_info.file_info(exe) if exe else {})}

    def _frame_args(self, game):
        return {"save_frame": str(Path(self.frame_dir.name) / "frame.png")} if game.connector == "kbm" else {}

    def frame(self):
        path = Path(self.frame_dir.name) / "frame.png"
        if not path.is_file():
            raise LookupError("frame")
        return path.read_bytes()

    # Status ----------------------------------------------------------------

    def status(self):
        self._refresh()
        with self.lock:
            game = self.game
            rows = self.rows
            health = self.client.health() if self.client else None
            if health and health.get("alive") is False and self.status_name == "connected":
                self.status_name = "error"
                self.conn_error = "게임 프로세스가 종료되었습니다 (exit code %s)." % health.get("exit_code")
                self.auto = False
                self._event(self.conn_error)
            now = time.time()
            return {
                "now": now,
                "connection": {"status": self.status_name, "error": self.conn_error,
                               "game": game.id if game else None, "interface": game.interface if game else None,
                               "connector": game.connector if game else None,
                               "permissions": self.info.get("permissions"), "halted": self.info.get("halted"),
                               "scenarios": self.info.get("scenarios")},
                "health": {**(health or {}), **self.link,
                           "last_ok_ago_ms": round((now - self.link["last_ok_at"]) * 1000) if self.link["last_ok_at"] else None},
                "runtime": {**(self.info.get("runtime") or {}), "game_build": self.info.get("game_build"),
                            "window_build": self.window_build},
                "phase": {**self.phase, "busy": self.busy},
                "calls": self.calls[:12],
                "frame_at": self.frame_at,
                "observed": _observed_view(self.observed),
                "rows": rows[:ROWS_SHOWN],
                "summary": summarize_rows(list(reversed(rows)), game.questions, self.outcomes) if game and rows else None,
                "steps": len(rows),
                "session": str(self.session.relative_to(ROOT)) if self.session else None,
                "busy": self.busy, "auto": self.auto, "error": self.error,
                "key_set": bool(self.key), "options": self.options, "events": self.events[:30],
            }

    def _refresh(self):
        """Re-read the game when idle: a heartbeat, and manual play in the game window shows up here too."""
        with self.lock:
            if self.status_name != "connected" or self.busy or not self.game:
                return
            period = max(0.5, self.game.config["interval_ms"] / 1000)
            if time.time() - self.observed_at < period:
                return
            client, generation, game = self.client, self.generation, self.game
            self.observed_at = time.time()
        try:
            observed = client.request("observe", **self._frame_args(game))
        except (OSError, ValueError) as exc:
            observed = {"ok": False, "error": "게임 연결이 끊어졌습니다: %s" % exc}
        with self.lock:
            if generation == self.generation and not self.busy:
                self._observed_locked(observed)
                self._phase_locked("대기")

    # Model -----------------------------------------------------------------

    def run(self, apply=True, auto=False, provider=None, shadows=None, policy=None, key=None):
        with self.lock:
            if self.status_name != "connected":
                raise ValueError("먼저 게임에 연결하세요.")
            if self.busy:
                raise ValueError("모델이 이미 판단 중입니다.")
            if key:
                self.key = key.strip()
            if provider is not None:
                if provider not in PROVIDER_NAMES:
                    raise ValueError("알 수 없는 모델입니다: %s" % provider)
                self.options["provider"] = provider
            if shadows is not None:
                if not isinstance(shadows, list) or any(p not in PROVIDER_NAMES for p in shadows):
                    raise ValueError("shadow 모델 목록이 올바르지 않습니다.")
                self.options["shadows"] = shadows
            if policy is not None:
                self.options["policy"] = bool(policy)
            if self.options["provider"] == "jev" and not self.key:
                raise ValueError("Jev API 키를 입력하세요.")
            self.busy = True
            self.auto = bool(auto and apply)
            self.error = None
            generation = self.generation
            args = (generation, self.game, self.client, dict(self.options), self.key, bool(apply))
        threading.Thread(target=self._worker, args=args, daemon=True).start()
        return {"started": True}

    def _deciders(self, generation, game, options, key):
        self._phase(generation, "모델 준비")
        live = options["provider"]
        decider = Traced(self.make_decider(live, game, key), self, generation, "live")
        shadows = []
        for provider in options["shadows"]:
            if provider == live:
                continue
            if provider == "jev" and not key:
                self._note("Jev shadow 건너뜀: API 키가 없습니다.")
                continue
            shadows.append(Traced(self.make_decider(provider, game, key), self, generation, "shadow"))
        escalate_to = (game.policy.get("escalate") or {}).get("to")
        escalate = None
        if options["policy"] and escalate_to and escalate_to != live:
            if escalate_to == "jev" and not key:
                self._note("escalate 건너뜀: Jev API 키가 없습니다.")
            else:
                escalate = Traced(self.make_decider(escalate_to, game, key), self, generation, "escalate")
        policy = Policy(game.policy, game.questions, enabled=options["policy"], escalate_decider=escalate)
        return decider, shadows, policy

    def _note(self, text):
        with self.lock:
            self._event(text)

    def _worker(self, generation, game, client, options, key, apply):
        try:
            decider, shadows, policy = self._deciders(generation, game, options, key)
        except Exception as exc:
            self._stop(generation, "모델 준비 실패: %s: %s" % (type(exc).__name__, exc))
            return
        kbm = game.connector == "kbm"
        realtime = game.type_spec["timing"] == "realtime"
        interval = game.config["interval_ms"] / 1000
        while True:
            started = time.time()
            with self.lock:
                if generation != self.generation:
                    return
                step, episode = len(self.rows) + 1, self.episode
            try:
                result = tick(client, decider, game.config, pause=kbm and bool(game.config["pause_key"]),
                              shadows=shadows, shadow_wait_ms=game.config["interval_ms"] if realtime else None,
                              questions=game.questions, policy=policy, pool=self.pool,
                              step=step, episode=episode, apply=apply)
            except Exception as exc:
                self._stop(generation, "게임과 통신하지 못했습니다: %s: %s" % (type(exc).__name__, exc))
                return
            with self.lock:
                if generation != self.generation:
                    return
                self._phase_locked("대기")
                if result["kind"] == "error":
                    self._stop_locked("관찰 실패: %s" % result["error"])
                    return
                if result["kind"] == "done":
                    self._observed_locked(result["observed"])
                    if not self._finish_episode_locked(result["outcome"], client):
                        return
                    continue
                row = result["row"]
                self.rows.insert(0, row)
                if self.record and apply:
                    self.session = self.session or new_session(game.id)
                    record_row(self.session, row)
                for warning in row["warnings"]:
                    self._event("! " + warning)
                self._event(format_row(row))
                self.observed_at = 0.0
                if row["decision"].get("status") != "ok":
                    self._stop_locked("모델 호출 실패: %s" % row["decision"].get("error"))
                    return
                if result["halted"]:
                    self._stop_locked(row["error"] or "입력이 멈췄습니다.")
                    return
                if row["error"] and not row["stale"]:
                    self._stop_locked("행동 적용 실패: %s" % row["error"])
                    return
                if not self.auto:
                    self.busy = False
                    return
            time.sleep(max(0, interval - (time.time() - started)))

    def _finish_episode_locked(self, outcome, client):
        """In auto play record a finished episode and start the next one. True to keep playing."""
        if not self.auto:
            # A single turn on a finished game: nothing was played, so nothing is counted.
            self._stop_locked("판이 끝났습니다(%s). 새 판을 시작하세요." % outcome)
            return False
        self.outcomes.append(outcome)
        self.episode += 1
        self._event("판 %d 끝: %s" % (self.episode, outcome))
        reset = client.request("reset")
        if not reset.get("ok"):
            self._stop_locked("판이 끝났지만 다음 판을 시작하지 못했습니다: %s" % reset.get("error"))
            return False
        return True

    def _stop(self, generation, message):
        with self.lock:
            if generation == self.generation:
                self._stop_locked(message)

    def _stop_locked(self, message):
        self.busy = False
        self.auto = False
        self.error = message
        self._phase_locked("대기")
        self._event(message)

    def stop_auto(self):
        with self.lock:
            self.auto = False
        return {"auto": False}

    # Direct control ----------------------------------------------------------

    def reset(self, scenario=None):
        with self.lock:
            client = self._connected_client()
            self.auto = False
            if self.busy:
                raise ValueError("모델 판단이 끝난 뒤 다시 시도하세요.")
        reply = client.request("reset", **({"scenario": scenario} if scenario else {}))
        with self.lock:
            self._phase_locked("대기")
            if not reply.get("ok"):
                raise ValueError("새 판을 시작하지 못했습니다: %s" % reply.get("error"))
            self._observed_locked(reply)
            self.error = None
            self._event("새 판: %s" % (scenario or "다음 시작 상황"))
        return {"ok": True}

    def manual(self, action):
        with self.lock:
            client = self._connected_client()
            if action not in self.game.actions:
                raise ValueError("알 수 없는 행동입니다: %s" % action)
            if self.busy:
                raise ValueError("모델 판단이 끝난 뒤 다시 시도하세요.")
            self.auto = False
        reply = client.request("act", action=action)
        with self.lock:
            self._phase_locked("대기")
            self.observed_at = 0.0
            self._event("직접 조작: %s%s" % (action, "" if reply.get("ok") else " · " + str(reply.get("error"))))
        return {"ok": bool(reply.get("ok")), "error": reply.get("error")}

    def _connected_client(self):
        if self.status_name != "connected":
            raise ValueError("먼저 게임에 연결하세요.")
        return self.client

    def set_key(self, key):
        with self.lock:
            self.key = (key or "").strip()
        return {"key_set": bool(self.key)}

    def export(self):
        with self.lock:
            game = self.game
            data = {"game": game.id if game else None, "interface": game.interface if game else None,
                    "runtime": self.info.get("runtime"), "questions": game.questions if game else None,
                    "options": self.options,
                    "summary": summarize_rows(list(reversed(self.rows)), game.questions, self.outcomes) if game else None,
                    "rows": list(reversed(self.rows)), "calls": list(reversed(self.calls))}
        return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def _observed_view(observed):
    if not observed:
        return None
    if not observed.get("ok", True):
        return {"ok": False, "error": observed.get("error")}
    state = observed.get("state")
    if state is None:
        state = observed.get("snapshot")
    return {"ok": True, "state": state, "description": observed.get("description"), "truth": observed.get("truth"),
            "window": observed.get("window"), "done": bool(observed.get("done"))}


def make_handler(lab):
    static = {"/app.css": "text/css", "/app.js": "application/javascript"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def _send(self, status, data, content_type="application/json; charset=utf-8", extra=None):
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data: blob:; base-uri 'none'")
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self):
            return secrets.compare_digest(self.headers.get("X-Session-Token", ""), lab.token)

        def do_GET(self):
            path = urlparse(self.path).path
            parts = path.strip("/").split("/")
            if path == "/":
                html = (WEB / "index.html").read_text(encoding="utf-8").replace("__SESSION_TOKEN__", lab.token)
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            elif path in static:
                self._send(200, (WEB / path[1:]).read_bytes(), static[path] + "; charset=utf-8")
            elif len(parts) == 3 and parts[0] == "games" and parts[2] == "view.js":
                # A game's own state panel: code from this repo's games/ folder, loaded as a script.
                try:
                    self._send(200, lab.view_script(parts[1]), "application/javascript; charset=utf-8")
                except LookupError:
                    self._send(404, {"error": "Not found"})
            elif not self._authorized():
                self._send(403 if path.startswith("/api/") else 404, {"error": "접근 권한이 없습니다."})
            elif path == "/api/games":
                self._send(200, lab.catalog())
            elif path == "/api/status":
                self._send(200, lab.status())
            elif path == "/api/frame":
                try:
                    self._send(200, lab.frame(), "image/png")
                except LookupError:
                    self._send(404, {"error": "캡처한 화면이 없습니다."})
            elif path == "/api/export":
                self._send(200, lab.export(), "application/json; charset=utf-8",
                           {"Content-Disposition": 'attachment; filename="game-decisions.json"'})
            else:
                self._send(404, {"error": "Not found"})

        def do_POST(self):
            if not self._authorized():
                self._send(403, {"error": "접근 권한이 없습니다."})
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length > 16384:
                self._send(413, {"error": "요청이 너무 큽니다."})
                return
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
                path = urlparse(self.path).path
                model = {k: data.get(k) for k in ("provider", "shadows", "policy", "key")}
                routes = {
                    "/api/connect": lambda: lab.connect(data.get("game"), data.get("interface"),
                                                        data.get("agent", "app"), data.get("dry_run", False)),
                    "/api/disconnect": lab.disconnect,
                    "/api/run": lambda: lab.run(apply=data.get("apply", True), **model),
                    "/api/auto": lambda: lab.run(apply=True, auto=True, **model) if data.get("enabled") else lab.stop_auto(),
                    "/api/reset": lambda: lab.reset(data.get("scenario")),
                    "/api/manual": lambda: lab.manual(data.get("action")),
                    "/api/key": lambda: lab.set_key(data.get("key")),
                }
                if path not in routes:
                    self._send(404, {"error": "Not found"})
                    return
                self._send(200, routes[path]())
            except (OSError, LookupError, ValueError, TypeError, RuntimeError) as exc:
                self._send(400, {"error": str(exc)})

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--games-dir", type=Path, default=GAMES)
    parser.add_argument("--game", help="시작하자마자 연결할 게임")
    parser.add_argument("--interface", help="--game과 함께: 연결할 인터페이스 (기본: game.json의 interface)")
    args = parser.parse_args()
    lab = Lab(args.games_dir)
    if args.game:
        lab.connect(args.game, args.interface)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(lab))
    server.daemon_threads = True
    url = "http://127.0.0.1:%d/" % server.server_port
    print("WebView: " + url, flush=True)
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        lab.close()


if __name__ == "__main__":
    main()
