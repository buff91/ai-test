"""Game-independent JSON-lines transport for local game and mod adapters.

One UTF-8 JSON object request and response per connection. No model or demo-game
imports belong here: a mod written in Java/C#/Lua implements the same wire format.
"""

import json
import os
import socket
import socketserver
from urllib.parse import urlparse


MAX_MESSAGE_BYTES = 1024 * 1024
PROTOCOL = {"name": "gamelab", "version": 1}


def endpoint_spec(value, *, allow_ephemeral=False):
    """Normalize a loopback endpoint from a config object or CLI URL."""
    if isinstance(value, str):
        parsed = urlparse(value)
        if parsed.scheme != "tcp" or parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username:
            raise ValueError("endpoint는 tcp://127.0.0.1:8765 형식이어야 합니다.")
        try:
            value = {"host": parsed.hostname, "port": parsed.port}
        except ValueError as exc:
            raise ValueError("endpoint 포트가 올바르지 않습니다.") from exc
    if not isinstance(value, dict) or set(value) - {"host", "port"}:
        raise ValueError("endpoint는 host와 port를 가진 객체여야 합니다.")
    host, port = value.get("host", "127.0.0.1"), value.get("port")
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("모드 브리지는 이 컴퓨터의 127.0.0.1 또는 localhost에 연결하세요.")
    if type(port) is not int or not (0 if allow_ephemeral else 1) <= port <= 65535:
        raise ValueError("endpoint.port는 1~65535의 정수여야 합니다.")
    return {"host": host, "port": port}


def _encode(value):
    payload = (json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ValueError("JSON 메시지가 1 MiB 제한을 넘습니다.")
    return payload


def _decode(line):
    if not line:
        raise ConnectionError("게임 프로세스가 응답하지 않습니다.")
    if len(line) > MAX_MESSAGE_BYTES or not line.endswith(b"\n"):
        raise ValueError("JSON 메시지는 1 MiB 이하이며 줄바꿈으로 끝나야 합니다.")
    result = json.loads(line)
    if not isinstance(result, dict):
        raise ValueError("JSON object required")
    return result


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(5)
        try:
            request = _decode(self.rfile.readline(MAX_MESSAGE_BYTES + 1))
            response = self.server.service.handle(request)
            payload = _encode(response)
        except (ValueError, OSError, RuntimeError) as exc:
            payload = _encode({"ok": False, "error": str(exc)})
        self.wfile.write(payload)


class GameServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, path, service):
        self.service = service
        super().__init__(str(path), _Handler)
        os.chmod(path, 0o600)


class ModServer(socketserver.ThreadingTCPServer):
    """Local mod SDK server. Production engines dispatch callbacks on their game thread."""
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, endpoint, service):
        spec = endpoint_spec(endpoint, allow_ephemeral=True)
        self.service = service
        super().__init__((spec["host"], spec["port"]), _Handler)


class GameClient:
    def __init__(self, path, timeout=3):
        self.path = str(path)
        self.timeout = timeout

    def _connect(self):
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(self.timeout)
        try:
            conn.connect(self.path)
        except BaseException:
            conn.close()
            raise
        return conn

    def request(self, op, **kwargs):
        payload = _encode({"op": op, **kwargs})
        with self._connect() as conn:
            conn.sendall(payload)
            with conn.makefile("rb") as stream:
                return _decode(stream.readline(MAX_MESSAGE_BYTES + 1))


class ModClient(GameClient):
    def __init__(self, endpoint, timeout=3):
        self.endpoint = endpoint_spec(endpoint)
        self.timeout = timeout

    def _connect(self):
        return socket.create_connection((self.endpoint["host"], self.endpoint["port"]), timeout=self.timeout)
