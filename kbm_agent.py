"""KbmAgent entry point: the only process that captures the screen and sends input.

Build it with ./build_kbm.sh so macOS Screen Recording and Accessibility
permissions belong to KbmAgent.app instead of the terminal.
"""

import argparse
import json
import os
import signal
import sys
import threading
from datetime import datetime
from pathlib import Path

from gamelab.protocol import GameServer
from gamelab.game_case import GameCase
from kbm.agent import AgentService
from kbm.macos import DryRunInput, MacPlatform, QuartzInput


class _Service:
    def __init__(self, agent, server_ref, log):
        self.agent = agent
        self.server_ref = server_ref
        self.log = log

    def handle(self, request):
        if request.get("op") == "shutdown":
            self.agent.halt("에이전트를 종료합니다.")
            threading.Thread(target=self.server_ref[0].shutdown, daemon=True).start()
            return {"ok": True}
        response = self.agent.handle(request)
        if request.get("op") == "act":
            self.log("act %s -> %s" % (request.get("action"), "applied" if response.get("ok") else response.get("error")))
        return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", required=True)
    parser.add_argument("--interface", required=True, help="games/<game>/interfaces/<name>")
    parser.add_argument("--version")
    parser.add_argument("--settings", type=Path, help="이번 연결에 적용할 검증된 JSON 설정")
    parser.add_argument("--games-dir", type=Path, required=True)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--dry-run", action="store_true", help="Record input events instead of sending them")
    parser.add_argument("--reflex", action="store_true", help="Run observer.reflex() at reflex_hz")
    parser.add_argument("--log", type=Path)
    args = parser.parse_args()

    log_file = open(args.log, "a", encoding="utf-8") if args.log else sys.stderr

    def log(message):
        print("%s %s" % (datetime.now().strftime("%H:%M:%S.%f")[:-3], message), file=log_file, flush=True)

    try:
        overrides = json.loads(args.settings.read_text(encoding="utf-8")) if args.settings else None
        game = GameCase(args.game, args.games_dir, interface=args.interface, version=args.version, overrides=overrides)
    except Exception as exc:
        log("KbmAgent 시작 실패: %s: %s" % (type(exc).__name__, exc))
        raise SystemExit(1)
    window = game.config["window"]
    agent = AgentService(game, MacPlatform(window["owner"], window.get("title_contains", ""), window.get("pid")),
                         DryRunInput() if args.dry_run else QuartzInput())
    server_ref = []
    if os.path.exists(args.socket):
        os.unlink(args.socket)
    server = GameServer(args.socket, _Service(agent, server_ref, log))
    server_ref.append(server)

    def stop(*_):
        agent.halt("종료 신호를 받았습니다.")
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if args.reflex and game.config["reflex_hz"] > 0:
        threading.Thread(target=agent.run_reflex, args=(log,), daemon=True).start()
    log("KbmAgent ready: game=%s dry_run=%s socket=%s" % (game.id, args.dry_run, args.socket))
    try:
        server.serve_forever()
    finally:
        agent.backend.release_all()
        server.server_close()
        if os.path.exists(args.socket):
            os.unlink(args.socket)
        log("KbmAgent stopped")


if __name__ == "__main__":
    main()
