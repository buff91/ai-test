"""Local browser API and static assets for the teaching console."""

import json
import secrets
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from gamelab.game_case import ROOT

WEB = ROOT / "web"


def make_handler(lab):
    static = {"/app.css": "text/css", "/app.js": "application/javascript", "/learning.js": "application/javascript",
              "/training.css": "text/css", "/training.js": "application/javascript"}

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
            elif path == "/api/training":
                try:
                    query = parse_qs(urlparse(self.path).query)
                    self._send(200, lab.training_status(query.get("game", [None])[0], query.get("version", [None])[0]))
                except (OSError, LookupError, ValueError, TypeError, RuntimeError) as exc:
                    self._send(400, {"error": str(exc)})
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
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0:
                    raise ValueError("Content-Length는 0 이상이어야 합니다.")
                if length > 16384:
                    self._send(413, {"error": "요청이 너무 큽니다."})
                    return
                data = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("요청은 JSON 객체여야 합니다.")
                path = urlparse(self.path).path
                model = {k: data.get(k) for k in ("provider", "shadows", "policy", "key", "decision_delay_ms")}
                model["demonstration"] = data.get("demonstration", False)
                routes = {
                    "/api/game-window": lambda: lab.prepare_game_window(data.get("game"), data.get("version")),
                    "/api/connect": lambda: lab.connect(data.get("game"), data.get("interface"),
                                                        data.get("agent", "app"), data.get("dry_run", False),
                                                        version=data.get("version"), socket_path=data.get("socket_path"),
                                                        mode=data.get("mode"), settings=data.get("settings"),
                                                        **({"prepared_window_pid": data["prepared_window_pid"]}
                                                           if "prepared_window_pid" in data else {})),
                    "/api/disconnect": lab.disconnect,
                    "/api/run": lambda: lab.run(apply=data.get("apply", True), **model),
                    "/api/compare": lambda: lab.compare(providers=data.get("providers"), key=data.get("key")),
                    "/api/auto": lambda: lab.run(apply=True, auto=True, **model) if data.get("enabled") else lab.stop_auto(),
                    "/api/reset": lambda: lab.reset(data.get("scenario")),
                    "/api/manual": lambda: lab.manual(data.get("action")),
                    "/api/key": lambda: lab.set_key(data.get("key")),
                    "/api/training/collect": lambda: lab.training_collect(data.get("game"), data.get("call_id"), data.get("version")),
                    "/api/training/review": lambda: lab.training_review(
                        data.get("game"), data.get("case_id"), expected=data.get("expected"),
                        split=data.get("split", "train"), review=data.get("review", "approved"), version=data.get("version")),
                    "/api/training/train": lambda: lab.training_train(
                        data.get("game"), {k: data[k] for k in ("base", "epochs", "encoder_lr", "head_lr",
                                                               "freeze_encoder", "micro_batch", "grad_accum") if k in data},
                        data.get("version")),
                    "/api/training/evaluate": lambda: lab.training_evaluate(data.get("game"), data.get("checkpoint_id"), data.get("version")),
                    "/api/training/convert": lambda: lab.training_convert(data.get("game"), data.get("checkpoint_id"),
                                                                         dtype=data.get("dtype", "float16"), version=data.get("version")),
                    "/api/training/apply": lambda: lab.training_apply(data.get("game"), data.get("checkpoint_id"), data.get("version")),
                    "/api/training/rollback": lambda: lab.training_rollback(data.get("game"), data.get("version"), provider=data.get("provider", "laya")),
                }
                if path not in routes:
                    self._send(404, {"error": "Not found"})
                    return
                self._send(200, routes[path]())
            except (OSError, LookupError, ValueError, TypeError, RuntimeError) as exc:
                self._send(400, {"error": str(exc)})

    return Handler


