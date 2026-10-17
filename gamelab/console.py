"""Shared teaching console state, connections and model runs (no HTTP/UI dependency)."""

import os
import json
from copy import deepcopy
import secrets
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime
from itertools import count
from pathlib import Path

from gamelab import build_info
from gamelab.game_case import GAMES, ROOT, GameCase, list_games
from gamelab.game_windows import GameWindows
from gamelab.game_types import PROVIDER_NAMES
from gamelab.policy import Policy
from gamelab.runner import format_row, new_session, record_row, summarize_rows, tick, write_summary
from settings import PROVIDERS

ROWS_SHOWN = 30
CALLS_KEPT = 40
PHASES = {"observe": "게임 관찰", "act": "행동 전송", "pause": "게임 일시정지", "resume": "게임 재개", "reset": "새 판 시작", "focus": "게임 창으로 전환"}
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
        return self._request(op, True, kwargs)

    def observe_background(self, **kwargs):
        """Heartbeat reads must not replace the active model-call phase."""
        return self._request("observe", False, kwargs)

    def _request(self, op, update_phase, kwargs):
        if update_phase and op in PHASES:
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

    def __init__(self, decider, lab, generation, role, preparation=None):
        self.decider = decider
        self.provider = decider.provider
        self.lab = lab
        self.generation = generation
        self.role = role
        self.preparation = preparation or {}
        self.first_call = True

    def decide(self, state):
        return self.decide_with_context(state)

    def decide_with_context(self, state, step=None, episode=None):
        preparation_ms = self.preparation.get("preparation_ms", 0) if self.first_call else 0
        self.first_call = False
        call = self.lab._call_started(self.generation, self.role, self.decider, state, step, episode,
                                      preparation_ms, self.preparation.get("router_cached"))
        started = time.perf_counter()
        try:
            row = self.decider.decide(state)
        except Exception as exc:
            row = {"provider": self.provider, "status": "error", "error": _safe_error(exc, getattr(self.decider, "key", None) or self.lab.key),
                   "model_ms": round((time.perf_counter() - started) * 1000, 1)}
        row.update(call_id=call["id"], step=call["step"], episode=call["episode"],
                   preparation_ms=preparation_ms, router_cached=call["router_cached"])
        self.lab._call_finished(self.generation, call, row)
        return row


class UnavailableDecider:
    """Keep the other comparison result visible when one runtime cannot initialize."""

    def __init__(self, provider, game, error):
        self.provider, self.questions, self.error = provider, game.questions, error
        self.jev_model = self.laya_model = game.providers["models"].get(provider)

    def decide(self, state):
        return {"provider": self.provider, "status": "error", "error": self.error, "model_ms": 0}


def _safe_error(exc, key=None):
    message = "%s: %s" % (type(exc).__name__, exc)
    return message.replace(key, "[redacted]") if key else message


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
        self.game_windows = GameWindows(self.games_dir)
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
        self.cancel_run = threading.Event()
        self.link = {"last_ok_at": None, "rtt_ms": None, "failures": 0, "last_error": None, "last_op": None}
        self.phase = {"name": "대기", "since": time.time()}
        self.calls = []
        self.preparations = []
        self.activity = None
        self.run_focus = None
        self.active_step = self.active_episode = None
        self.window_build = None
        self.frame_at = None
        self.input_trace = None

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
            versions = []
            for version, spec in game.versions.items():
                version_entry = {"id": version, "name": spec.get("name", version),
                                 "description": spec.get("description", ""), "default_interface": spec.get("interface"),
                                 "window_interface": None}
                versions.append(version_entry)
                for name in spec["interfaces"]:
                    try:
                        one = GameCase(game_id, self.games_dir, interface=name, version=version)
                        if one.connector == "socket" and one.config.get("launch") and one.target["display"] == "visible":
                            version_entry["window_interface"] = version_entry["window_interface"] or name
                        interfaces.append({"name": name, "version": version, "connector": one.connector, "run": one.how_to_run(),
                                           "interval_ms": one.config["interval_ms"], "pause_key": one.config.get("pause_key"),
                                           "configuration": one.configuration()})
                    except Exception as exc:
                        interfaces.append({"name": name, "version": version, "error": str(exc)})
            games.append({
                "id": game.id, "name": game.name, "type": game.type, "type_label": game.type_spec["label"],
                "featured": bool(game.game_config.get("featured")), "learning": game.game_config.get("learning", {}),
                "timing": game.type_spec["timing"], "latency_budget_ms": game.type_spec["latency_budget_ms"],
                "providers": game.providers, "has_policy": bool(game.policy), "questions": game.questions,
                "options": list(game.options), "interfaces": interfaces, "default_interface": game.interface,
                "versions": versions, "default_version": game.version,
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

    def prepare_game_window(self, game_id, version=None):
        with self.lock:
            if self.status_name in ("connected", "connecting") or self.busy:
                raise ValueError("연결을 해제한 뒤 게임 창을 준비하세요.")
            state = self.game_windows.prepare(game_id, version)
            self._event("%s 게임 창을 %s. 연결 방식을 바꿔도 이 창은 유지됩니다." %
                        (game_id, "다시 사용합니다" if state["reused"] else "준비했습니다"))
            return {"game_window": state}

    def connect(self, game_id, interface=None, agent="app", dry_run=False, version=None, socket_path=None, mode=None, settings=None,
                prepared_window_pid=None):
        game = GameCase(game_id, self.games_dir, interface=interface or None, version=version or None,
                        overrides=settings)
        game.require_interface()
        actual_mode = "attach" if game.target["mode"] == "attach" else game.target["display"]
        if mode and mode != actual_mode:
            raise ValueError("선택한 실행 방식과 인터페이스가 다릅니다.")
        if socket_path is not None:
            game.set_socket_path(socket_path)
        if agent not in ("app", "dev"):
            raise ValueError("agent는 app 또는 dev입니다.")
        if prepared_window_pid is not None:
            matching = next((window for window in self.game_windows.status()
                             if window["game"] == game.id and window["version"] == game.version
                             and window["alive"] and window["pid"] == prepared_window_pid), None)
            if game.connector != "kbm" or type(prepared_window_pid) is not int or not matching:
                raise ValueError("준비한 게임 창을 확인할 수 없습니다. 게임 창 준비를 다시 실행하세요.")
            game.config["window"] = {**game.config["window"], "pid": prepared_window_pid}
        self.disconnect()
        with self.lock:
            self.generation += 1
            self.status_name = "connecting"
            self.game = game
            self.options = {"provider": game.providers["live"], "shadows": list(game.providers["shadow"]),
                            "policy": False, "dry_run": bool(dry_run), "agent": agent, "decision_delay_ms": 0}
            generation = self.generation
        threading.Thread(target=self._open, args=(generation, game, agent, bool(dry_run)), daemon=True).start()
        return {"connecting": True}

    def _open(self, generation, game, agent, dry_run):
        stack = ExitStack()
        try:
            client = Probe(stack.enter_context(game.connect(agent=agent, dry_run=dry_run)), self, generation)
            info = client.request("info")
            with self.lock:
                if generation == self.generation:
                    self.info = info
            permissions = info.get("permissions") or {}
            if game.connector == "kbm" and permissions.get("screen_capture") is False:
                raise ValueError("KbmAgent의 화면 기록 권한이 없습니다. 시스템 설정 → 개인정보 보호 및 보안 → 화면 기록에서 허용한 뒤 다시 연결하세요.")
            if game.connector == "kbm" and not dry_run and permissions.get("post_events") is False:
                raise ValueError("KbmAgent의 손쉬운 사용 권한이 없습니다. 실제 키마 입력을 사용하려면 시스템 설정에서 허용하세요. DRY-RUN은 화면 기록 권한만으로 사용할 수 있습니다.")
            observed = client.request("observe", **self._frame_args(game))
            if not info.get("ok") or not observed.get("ok"):
                raise ValueError(observed.get("error") or info.get("error") or "게임 상태를 읽지 못했습니다.")
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
                self._event("%s / %s · %s 인터페이스에 연결했습니다." % (game.id, game.version, game.interface))
        if stale:
            stack.close()

    def disconnect(self):
        with self.lock:
            self.cancel_run.set()
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
        self.game_windows.close()
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

    def _call_started(self, generation, role, decider, state, step=None, episode=None,
                      preparation_ms=0, router_cached=None):
        provider = decider.provider
        model = getattr(decider, "jev_model" if provider == "jev" else "laya_model", None)
        questions = getattr(decider, "questions", None) or {}
        request = {"model": model, "state": state,
                   "questions": questions}
        call = {"id": next(self.call_ids), "role": role, "provider": provider, "model": model,
                "runtime": RUNTIMES.get(provider), "started_at": time.time(), "finished_at": None,
                "endpoint": PROVIDERS["jev"]["endpoint"] if provider == "jev" else None,
                "timeout_s": getattr(decider, "timeout", None) if provider == "jev" else None,
                "request": request, "input_chars": len(state if isinstance(state, str) else json.dumps(state)),
                "step": step, "episode": episode, "preparation_ms": preparation_ms,
                "router_cached": router_cached}
        with self.lock:
            if generation == self.generation:
                call["comparison"] = self.activity == "compare"
                if step is None:
                    call.update(step=self.active_step, episode=self.active_episode)
                self.calls.insert(0, call)
                del self.calls[CALLS_KEPT:]
                if role == "live":
                    self._phase_locked("모델 판단")
        return call

    def _call_finished(self, generation, call, row):
        finished = time.time()
        with self.lock:
            if generation != self.generation:
                return
            # ms is measured here around the whole call, not taken from what the decider reports.
            call.update(finished_at=finished, status=row.get("status"), error=row.get("error"),
                        action=row.get("action"), ms=round((finished - call["started_at"]) * 1000, 1), usage=row.get("usage"),
                        routed_model=row.get("checkpoint") or row.get("model"), reported_model=row.get("model"),
                        routing=row.get("routing"), checkpoint=row.get("checkpoint"),
                        checkpoint_cached=row.get("checkpoint_cached"), model_ms_scope=row.get("model_ms_scope"),
                        confidence=row.get("confidence"),
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
                "generation": self.generation,
                "game_windows": self.game_windows.status(),
                "connection": {"status": self.status_name, "error": self.conn_error,
                               "game": game.id if game else None, "interface": game.interface if game else None,
                               "target": game.target if game else None, "version_check": self.info.get("version_check"),
                               "connector": game.connector if game else None,
                               "settings": game.configuration()["values"] if game else None,
                               "permissions": self.info.get("permissions"), "halted": self.info.get("halted"),
                               "capabilities": self.info.get("capabilities", {}),
                               "scenarios": self.info.get("scenarios")},
                "health": {**(health or {}), **self.link,
                           "last_ok_ago_ms": round((now - self.link["last_ok_at"]) * 1000) if self.link["last_ok_at"] else None},
                "runtime": {**(self.info.get("runtime") or {}), "game_build": self.info.get("game_build"),
                            "window_build": self.window_build},
                "phase": {**self.phase, "busy": self.busy},
                "calls": self.calls[:12],
                "preparations": self.preparations, "activity": self.activity,
                "frame_at": self.frame_at,
                "input_trace": self.input_trace,
                "run_focus": self.run_focus,
                "observed": _observed_view(self.observed),
                "rows": rows[:ROWS_SHOWN],
                "summary": summarize_rows(list(reversed(rows)), game.questions, self.outcomes) if game and rows else None,
                "steps": len(rows),
                "session": str(self.session.relative_to(ROOT)) if self.session else None,
                "busy": self.busy, "auto": self.auto, "error": self.error,
                "key_set": bool(self.key), "options": self.options, "events": self.events[:30],
            }

    def _refresh(self):
        """Refresh idle games and keep socket telemetry live during inference and auto play."""
        with self.lock:
            background_safe = bool(self.game and self.game.connector in ("socket", "mod"))
            if self.status_name != "connected" or not self.game or (self.busy and not background_safe):
                return
            period = max(0.5, self.game.config["interval_ms"] / 1000)
            if time.time() - self.observed_at < period:
                return
            client, generation, game = self.client, self.generation, self.game
            self.observed_at = time.time()
        try:
            observed = client.observe_background(**self._frame_args(game))
        except (OSError, ValueError) as exc:
            observed = {"ok": False, "error": "게임 연결이 끊어졌습니다: %s" % exc}
        with self.lock:
            if generation == self.generation and (not self.busy or background_safe):
                self._observed_locked(observed)
                if not observed.get("ok"):
                    self.status_name = "error"
                    self.conn_error = observed.get("error") or "게임 상태를 읽지 못했습니다."
                    self.auto = False
                    self._event(self.conn_error)
                if not self.busy:
                    self._phase_locked("대기")

    # Model -----------------------------------------------------------------

    def run(self, apply=True, auto=False, provider=None, shadows=None, policy=None, key=None, decision_delay_ms=None):
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
            if decision_delay_ms is not None:
                if isinstance(decision_delay_ms, bool) or not isinstance(decision_delay_ms, int) or not 0 <= decision_delay_ms <= 3000:
                    raise ValueError("추가 입력 지연은 0~3000ms의 정수여야 합니다.")
                self.options["decision_delay_ms"] = decision_delay_ms
            if self.options["provider"] == "jev" and not self.key:
                raise ValueError("Jev API 키를 입력하세요.")
            self.busy = True
            self.activity = "run"
            self.preparations = []
            self.run_focus = None
            self.auto = bool(auto and apply)
            self.error = None
            self.cancel_run = threading.Event()
            generation = self.generation
            focus = bool(apply and not self.options.get("dry_run") and self.info.get("capabilities", {}).get("focus"))
            args = (generation, self.game, self.client, dict(self.options), self.key, bool(apply), self.cancel_run, focus)
        threading.Thread(target=self._worker, args=args, daemon=True).start()
        return {"started": True}

    def compare(self, providers=None, key=None):
        """Ask separate runtimes about one observation without changing run settings or sending input."""
        providers = ["laya", "jev"] if providers is None else providers
        if (not isinstance(providers, list) or len(providers) < 2 or len(providers) > len(PROVIDER_NAMES)
                or any(p not in PROVIDER_NAMES for p in providers) or len(set(providers)) != len(providers)):
            raise ValueError("서로 다른 비교 모델을 2개 이상 선택하세요.")
        with self.lock:
            if self.status_name != "connected":
                raise ValueError("먼저 게임에 연결하세요.")
            if self.busy:
                raise ValueError("모델이 이미 판단 중입니다.")
            if key:
                self.key = key.strip()
            if "jev" in providers and not self.key:
                raise ValueError("Jev 비교에는 API 키가 필요합니다. Jev API 키를 입력하세요.")
            options = {**self.options, "provider": providers[0], "shadows": providers[1:],
                       "policy": False, "decision_delay_ms": 0, "comparison": True}
            self.busy, self.auto, self.error = True, False, None
            self.activity, self.preparations = "compare", []
            self.run_focus = None
            self.cancel_run = threading.Event()
            args = (self.generation, self.game, self.client, options, self.key, False, self.cancel_run)
        threading.Thread(target=self._worker, args=args, daemon=True).start()
        return {"started": True, "comparison": True, "providers": providers}

    def _prepare_decider(self, generation, provider, game, key, role, tolerate_failure=False):
        with self.lock:
            if generation != self.generation or self.cancel_run.is_set():
                raise InterruptedError("모델 준비를 중지했습니다.")
        preparation = {"provider": provider, "role": role, "status": "preparing", "started_at": time.time(),
                       "finished_at": None, "preparation_ms": None, "router_cached": None,
                       "model": game.providers["models"].get(provider), "timing_scope": "decider_initialization",
                       "model_loading": "inside_predict" if provider != "jev" else "remote"}
        with self.lock:
            if generation == self.generation:
                self.preparations.append(preparation)
        started = time.perf_counter()
        try:
            decider = self.make_decider(provider, game, key)
        except Exception as exc:
            preparation.update(status="error", error=_safe_error(exc, key))
            if not tolerate_failure:
                raise
            decider = UnavailableDecider(provider, game, preparation["error"])
        else:
            preparation.update(status="ready", router_cached=getattr(decider, "router_cached", None))
        finally:
            preparation.update(finished_at=time.time(), preparation_ms=round((time.perf_counter() - started) * 1000, 1))
        return Traced(decider, self, generation, role, preparation)

    def _deciders(self, generation, game, options, key):
        self._phase(generation, "모델 준비")
        live = options["provider"]
        compare = options.get("comparison", False)
        decider = self._prepare_decider(generation, live, game, key, "live", compare)
        shadows = []
        for provider in options["shadows"]:
            if provider == live:
                continue
            if provider == "jev" and not key:
                self._note("Jev shadow 건너뜀: API 키가 없습니다.")
                continue
            shadows.append(self._prepare_decider(generation, provider, game, key, "shadow", compare))
        escalate_to = (game.policy.get("escalate") or {}).get("to")
        escalate = None
        if options["policy"] and escalate_to and escalate_to != live:
            if escalate_to == "jev" and not key:
                self._note("escalate 건너뜀: Jev API 키가 없습니다.")
            else:
                escalate = self._prepare_decider(generation, escalate_to, game, key, "escalate")
        policy = Policy(None if compare else game.policy, game.questions, enabled=options["policy"], escalate_decider=escalate)
        return decider, shadows, policy

    def _note(self, text):
        with self.lock:
            self._event(text)

    def _worker(self, generation, game, client, options, key, apply, cancel_event, focus=False):
        focus_result = None
        if focus:
            # Only an explicit real run may activate the selected window. Never
            # reactivate during the loop: switching apps must still stop input.
            with self.lock:
                if generation != self.generation:
                    return
                if cancel_event.is_set():
                    self.busy, self.activity = False, None
                    self._phase_locked("대기")
                    return
            try:
                focus_result = client.request("focus")
            except Exception as exc:
                focus_result = {"ok": False, "error": _safe_error(exc, key)}
            with self.lock:
                if generation != self.generation:
                    return
                if cancel_event.is_set():
                    self.busy, self.activity = False, None
                    self._phase_locked("대기")
                    return
                self.run_focus = focus_result
                if not focus_result.get("ok"):
                    self._stop_locked("게임 창 전환 실패: %s" % focus_result.get("error", "전면 상태를 확인하지 못했습니다."))
                    return
        try:
            decider, shadows, policy = self._deciders(generation, game, options, key)
        except InterruptedError:
            with self.lock:
                if generation == self.generation:
                    self.busy, self.activity = False, None
                    self._phase_locked("대기")
            return
        except Exception as exc:
            self._stop(generation, "모델 준비 실패: %s" % _safe_error(exc, key))
            return
        kbm = game.connector == "kbm"
        realtime = game.type_spec["timing"] == "realtime"
        interval = game.config["interval_ms"] / 1000
        with self.lock:
            preparation_rows = [dict(p) for p in self.preparations] if generation == self.generation else []
        while True:
            started = time.time()
            with self.lock:
                if generation != self.generation:
                    return
                if cancel_event.is_set():
                    self.busy = False
                    self.activity = None
                    self._phase_locked("대기")
                    return
                step, episode = len(self.rows) + 1, self.episode
                self.active_step, self.active_episode = step, episode
            try:
                result = tick(client, decider, game.config, pause=kbm and bool(game.config["pause_key"]),
                              shadows=shadows, shadow_wait_ms=game.config["interval_ms"] if realtime else None,
                              questions=game.questions, policy=policy, pool=self.pool,
                              step=step, episode=episode, apply=apply,
                              decision_delay_ms=options.get("decision_delay_ms", 0), cancel_event=cancel_event)
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
                if focus_result is not None:
                    row["focus"] = focus_result
                    focus_result = None
                row["comparison"] = bool(options.get("comparison"))
                row["preparations"] = preparation_rows
                preparation_rows = []
                row["preparation_ms"] = round(sum(p["preparation_ms"] or 0 for p in row["preparations"]), 1)
                self.rows.insert(0, row)
                self.input_trace = row.get("input_trace")
                if self.record and apply:
                    self.session = self.session or new_session(game.id, self._session_metadata())
                    record_row(self.session, row)
                for warning in row["warnings"]:
                    self._event("! " + warning)
                self._event(format_row(row))
                self.observed_at = 0.0
                if row["decision"].get("status") != "ok":
                    self._stop_locked("모델 호출 실패: %s" % row["decision"].get("error"))
                    return
                if row["comparison"] and any(d.get("status") != "ok" for d in row["shadow"]):
                    self._stop_locked("일부 비교 모델 호출이 실패했습니다. 각 모델의 호출 기록을 확인하세요.")
                    return
                if result["halted"]:
                    self._stop_locked(row["error"] or "입력이 멈췄습니다.")
                    return
                if row["error"] and not row["stale"]:
                    self._stop_locked("행동 적용 실패: %s" % row["error"])
                    return
                if not self.auto:
                    self.busy = False
                    self.activity = None
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
        self.activity = None
        self.error = message
        self._phase_locked("대기")
        self._event(message)

    def stop_auto(self):
        with self.lock:
            self.auto = False
            self.cancel_run.set()
        return {"auto": False}

    # Direct control ----------------------------------------------------------

    def reset(self, scenario=None):
        with self.lock:
            client = self._connected_client()
            generation = self.generation
            self.auto = False
            if self.busy:
                raise ValueError("모델 판단이 끝난 뒤 다시 시도하세요.")
        reply = client.request("reset", **({"scenario": scenario} if scenario else {}))
        with self.lock:
            if generation != self.generation:
                return {"ok": False, "cancelled": True, "error": "연결이 변경되어 이전 응답을 버렸습니다."}
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
            generation, game = self.generation, self.game
            if action not in self.game.actions:
                raise ValueError("알 수 없는 행동입니다: %s" % action)
            if self.busy:
                raise ValueError("모델 판단이 끝난 뒤 다시 시도하세요.")
            self.auto = False
        reply = client.request("act", action=action)
        with self.lock:
            if generation != self.generation:
                return {"ok": False, "cancelled": True, "error": "연결이 변경되어 이전 응답을 버렸습니다."}
            self.input_trace = {"connector": game.connector, "action": action,
                                "command": deepcopy(reply.get("command")),
                                "mapped_command": deepcopy(game.actions[action]), "attempted": True,
                                "result": deepcopy(reply)}
            self._phase_locked("대기")
            self.observed_at = 0.0
            self._event("직접 조작: %s%s" % (action, "" if reply.get("ok") else " · " + str(reply.get("error"))))
        return {"ok": bool(reply.get("ok")), "error": reply.get("error"), "input_trace": self.input_trace}

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
                    **self._session_metadata(),
                    "questions": game.questions if game else None,
                    "options": self.options,
                    "summary": summarize_rows(list(reversed(self.rows)), game.questions, self.outcomes) if game else None,
                    "rows": list(reversed(self.rows)), "calls": list(reversed(self.calls))}
            data["preparations"] = self.preparations
            data["run_focus"] = self.run_focus
        return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")

    def _session_metadata(self):
        return {"target": self.game.target if self.game else None, "runtime": self.info.get("runtime"),
                "settings": self.game.configuration()["values"] if self.game else None,
                "game_build": self.info.get("game_build"), "version_check": self.info.get("version_check")}


def _observed_view(observed):
    if not observed:
        return None
    if not observed.get("ok", True):
        return {"ok": False, "error": observed.get("error")}
    state = observed.get("state")
    if state is None:
        state = observed.get("snapshot")
    return {"ok": True, "state": state, "description": observed.get("description"), "truth": observed.get("truth"),
            "reference_kind": observed.get("reference_kind"),
            "window": observed.get("window"), "done": bool(observed.get("done"))}


