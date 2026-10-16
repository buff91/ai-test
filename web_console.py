"""Local WebView dashboard for a separately running arena game binary."""

import argparse
import json
import os
import secrets
import subprocess
import tempfile
import threading
import time
import urllib.error
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from compare import OPTIONS, QUESTIONS, PROVIDERS, call_jev, make_laya_mlx_router, make_laya_router, summarize_answer
from game_core import ACTIONS, SCENARIOS
from game_ipc import GameClient


ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
GAME_BINARY = ROOT / "dist" / "ArenaGame" / "ArenaGame"


class Lab:
    def __init__(self):
        self.lock = threading.RLock()
        self.model_lock = threading.Lock()
        self.routers = {}
        self.temp_dir = None
        self.game_log = None
        self.game_process = None
        self.client = None
        self.key = os.environ.get("JEV_API_KEY", "")
        self.provider = "jev"
        self.compare = False
        self.auto = False
        self.busy = False
        self.generation = 0
        self.decisions = []
        self.events = []
        self.error = None
        self.game_state = None
        self.token = secrets.token_urlsafe(32)
        self._launch_game()

    def _launch_game(self):
        if not GAME_BINARY.is_file():
            raise FileNotFoundError("게임 실행 파일이 없습니다. ./build_game.sh 를 먼저 실행하세요.")
        self.temp_dir = tempfile.TemporaryDirectory(prefix="jev-arena-web-")
        socket_path = str(Path(self.temp_dir.name) / "game.sock")
        self.client = GameClient(socket_path)
        self.game_log = open(Path(self.temp_dir.name) / "game.log", "w", encoding="utf-8")
        self.game_process = subprocess.Popen(
            [str(GAME_BINARY), "--socket", socket_path],
            stdout=self.game_log, stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if self.game_process.poll() is not None:
                raise RuntimeError("게임 실행 파일이 종료되었습니다. 게임 로그를 확인하세요.")
            try:
                self.game_state = self.client.request("state")
                self._event("별도 게임 실행 파일에 연결했습니다.")
                return
            except (OSError, ValueError):
                time.sleep(0.1)
        raise TimeoutError("게임 실행 파일에 연결하지 못했습니다.")

    def _event(self, message):
        self.events.insert(0, {"at": datetime.now().strftime("%H:%M:%S"), "text": message})
        del self.events[80:]

    def status(self):
        with self.lock:
            try:
                self.game_state = self.client.request("state")
            except (OSError, ValueError) as exc:
                self.error = "게임 연결이 끊어졌습니다: %s" % exc
            return {
                "game": self.game_state,
                "provider": self.provider,
                "compare": self.compare,
                "auto": self.auto,
                "busy": self.busy,
                "key_set": bool(self.key),
                "decisions": self.decisions[:40],
                "events": self.events[:30],
                "error": self.error,
                "scenarios": list(SCENARIOS),
                "actions": list(ACTIONS),
                "questions": QUESTIONS,
            }

    def run_model(self, provider=None, compare=None, apply=True, key=None):
        with self.lock:
            if self.busy:
                raise ValueError("모델이 이미 판단 중입니다.")
            if provider is not None:
                if provider not in PROVIDERS:
                    raise ValueError("알 수 없는 모델입니다.")
                self.provider = provider
            if compare is not None:
                self.compare = bool(compare)
            if key:
                self.key = key.strip()
            if self.provider == "jev" and not self.key:
                self.auto = False
                raise ValueError("Jev API 키를 입력하세요.")
            self.game_state = self.client.request("state")
            if self.game_state["snapshot"]["outcome"] != "playing":
                self.auto = False
                raise ValueError("게임이 끝났습니다. 새 게임을 시작하세요.")
            self.busy = True
            self.error = None
            generation = self.generation
            state = self.game_state
            providers = [self.provider]
            if self.compare and self.provider != "laya-mlx":
                providers.append("laya-mlx")
            thread = threading.Thread(
                target=self._worker,
                args=(generation, state, providers, self.key, bool(apply)),
                daemon=True,
            )
            thread.start()
            return {"started": True, "revision": state["revision"]}

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
                        raw = self.routers[provider].predict(
                            state["description"], QUESTIONS, **({} if PROVIDERS[provider]["model"] == "auto" else {"model": PROVIDERS[provider]["model"]})
                        )
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

        with self.lock:
            if generation != self.generation:
                return
            primary = results[0]
            record = {
                "at": datetime.now().strftime("%H:%M:%S"),
                "turn": state["snapshot"]["turn"],
                "state": state["description"],
                "before": state["snapshot"],
                "questions": QUESTIONS,
                "results": results,
                "applied": False,
                "events": [],
            }
            if primary["status"] != "ok":
                self.error = "모델 호출 실패: " + primary["error"]
                self.auto = False
                self._event(self.error)
            elif apply:
                try:
                    reply = self.client.request(
                        "step", action=primary["action"], expected_revision=state["revision"]
                    )
                    self.game_state = reply
                    if not reply["ok"]:
                        raise ValueError(reply["error"])
                    record["applied"] = True
                    record["after"] = reply["snapshot"]
                    record["events"] = reply["events"]
                    self._event("%s → %s · %s" % (
                        primary["provider"], primary["action"], " ".join(reply["events"])
                    ))
                    if reply["snapshot"]["outcome"] != "playing":
                        self.auto = False
                except (OSError, ValueError) as exc:
                    self.error = "행동 적용 실패: %s" % exc
                    self.auto = False
                    self._event(self.error)
            self.decisions.insert(0, record)
            del self.decisions[100:]
            self.busy = False
            if self.auto and record["applied"]:
                timer = threading.Timer(0.65, self._next_auto, args=(generation,))
                timer.daemon = True
                timer.start()

    def _next_auto(self, generation):
        with self.lock:
            if not self.auto or generation != self.generation:
                return
        try:
            self.run_model()
        except (OSError, ValueError) as exc:
            with self.lock:
                self.auto = False
                self.error = str(exc)

    def set_auto(self, enabled, provider=None, compare=None, key=None):
        with self.lock:
            self.auto = bool(enabled)
        if enabled:
            try:
                return self.run_model(provider, compare, True, key)
            except Exception:
                with self.lock:
                    self.auto = False
                raise
        return {"auto": False}

    def reset(self, scenario):
        if scenario not in SCENARIOS:
            raise ValueError("알 수 없는 시작 상황입니다.")
        with self.lock:
            self.auto = False
            self.generation += 1
            self.busy = False
            self.decisions.clear()
            self.events.clear()
            self.error = None
            self.game_state = self.client.request("reset", scenario=scenario)
            self._event("새 게임: " + scenario)
            return self.game_state

    def manual(self, action):
        if action not in ACTIONS:
            raise ValueError("알 수 없는 행동입니다.")
        with self.lock:
            self.auto = False
            self.game_state = self.client.request("step", action=action)
            if self.game_state["ok"]:
                self._event("직접 조작: " + action + " · " + " ".join(self.game_state["events"]))
            return self.game_state

    def export(self):
        with self.lock:
            return json.dumps(self.decisions, ensure_ascii=False, indent=2).encode("utf-8")

    def close(self):
        with self.lock:
            self.auto = False
            self.generation += 1
        if self.game_process and self.game_process.poll() is None:
            self.game_process.terminate()
            try:
                self.game_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.game_process.kill()
                self.game_process.wait()
        if self.game_log:
            self.game_log.close()
        if self.temp_dir:
            self.temp_dir.cleanup()


def make_handler(lab):
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
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'")
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self):
            return secrets.compare_digest(self.headers.get("X-Session-Token", ""), lab.token)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                html = (WEB / "index.html").read_text(encoding="utf-8").replace("__SESSION_TOKEN__", lab.token)
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            elif path in ("/app.css", "/app.js"):
                mime = "text/css" if path.endswith("css") else "application/javascript"
                self._send(200, (WEB / path[1:]).read_bytes(), mime + "; charset=utf-8")
            elif path == "/api/status" and self._authorized():
                self._send(200, lab.status())
            elif path == "/api/export" and self._authorized():
                self._send(200, lab.export(), "application/json; charset=utf-8",
                           {"Content-Disposition": 'attachment; filename="game-decisions.json"'})
            else:
                self._send(404, {"error": "Not found"})

        def do_POST(self):
            if not self._authorized():
                self._send(403, {"error": "접근 권한이 없습니다."})
                return
            if int(self.headers.get("Content-Length", "0")) > 16384:
                self._send(413, {"error": "요청이 너무 큽니다."})
                return
            try:
                data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                path = urlparse(self.path).path
                if path == "/api/run":
                    result = lab.run_model(data.get("provider"), data.get("compare"), data.get("apply", True), data.get("key"))
                elif path == "/api/auto":
                    result = lab.set_auto(data.get("enabled"), data.get("provider"), data.get("compare"), data.get("key"))
                elif path == "/api/reset":
                    result = lab.reset(data.get("scenario", "교전 시작"))
                elif path == "/api/manual":
                    result = lab.manual(data.get("action"))
                else:
                    self._send(404, {"error": "Not found"})
                    return
                self._send(200, result)
            except (OSError, ValueError, TypeError) as exc:
                self._send(400, {"error": str(exc)})

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    lab = Lab()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(lab))
    server.daemon_threads = True
    url = "http://127.0.0.1:%d/" % server.server_port
    print("WebView: " + url, flush=True)
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
        lab.close()


if __name__ == "__main__":
    main()
