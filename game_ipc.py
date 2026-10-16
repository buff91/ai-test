"""Local, private JSON-line socket protocol for the separate arena process."""

import json
import os
import socket
import socketserver
import threading

from game_core import ACTIONS, SCENARIOS, Arena


class GameService:
    def __init__(self):
        self.arena = Arena.from_scenario("교전 시작")
        self.revision = 0
        self.events = []
        self.lock = threading.RLock()

    def _state(self):
        return {
            "snapshot": self.arena.snapshot(),
            "description": self.arena.describe(),
            "revision": self.revision,
            "events": list(self.events),
        }

    def handle(self, request):
        with self.lock:
            op = request.get("op")
            if op == "state":
                return {"ok": True, **self._state()}
            if op == "info":
                return {"ok": True, "actions": list(ACTIONS), "scenarios": list(SCENARIOS)}
            if op == "reset":
                scenario = request.get("scenario", "교전 시작")
                if scenario not in SCENARIOS:
                    return {"ok": False, "error": "알 수 없는 시작 상황입니다.", **self._state()}
                self.arena = Arena.from_scenario(scenario)
                self.events = ["새 게임을 시작했습니다."]
                self.revision += 1
                return {"ok": True, **self._state()}
            if op == "step":
                action = request.get("action")
                expected = request.get("expected_revision")
                if action not in ACTIONS:
                    return {"ok": False, "error": "알 수 없는 행동입니다.", **self._state()}
                if expected is not None and expected != self.revision:
                    return {"ok": False, "error": "모델 판단 중 게임 상태가 바뀌었습니다.", **self._state()}
                if self.arena.outcome != "playing":
                    return {"ok": False, "error": "게임이 끝났습니다.", **self._state()}
                before = self.arena.snapshot()
                self.events = self.arena.step(action)
                self.revision += 1
                return {"ok": True, "before": before, **self._state()}
            return {"ok": False, "error": "알 수 없는 명령입니다.", **self._state()}


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            line = self.rfile.readline(1024 * 1024)
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("JSON object required")
            response = self.server.service.handle(request)
        except (ValueError, json.JSONDecodeError) as exc:
            response = {"ok": False, "error": str(exc)}
        self.wfile.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))


class GameServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, path, service):
        self.service = service
        super().__init__(path, _Handler)
        os.chmod(path, 0o600)


class GameClient:
    def __init__(self, path, timeout=3):
        self.path = str(path)
        self.timeout = timeout

    def request(self, op, **kwargs):
        payload = (json.dumps({"op": op, **kwargs}, ensure_ascii=False) + "\n").encode("utf-8")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(self.timeout)
            conn.connect(self.path)
            conn.sendall(payload)
            chunks = []
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
        if not chunks:
            raise ConnectionError("게임 프로세스가 응답하지 않습니다.")
        return json.loads(b"".join(chunks).split(b"\n", 1)[0])
