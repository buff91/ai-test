"""Start the local teaching console. Choose RPG, racing, MOBA or an external game catalog.

  uv run python web_console.py
  uv run python web_console.py --game party --version desktop-v1
  uv run python web_console.py --games-dir /path/to/external/games
"""

import argparse
import threading
import webbrowser
from pathlib import Path
from http.server import ThreadingHTTPServer

from gamelab.game_case import GAMES
from gamelab.console import Lab, Probe, Traced
from gamelab.console_http import make_handler


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--games-dir", type=Path, default=GAMES)
    parser.add_argument("--game", help="시작하자마자 연결할 게임")
    parser.add_argument("--interface", help="--game과 함께: 연결할 인터페이스 (기본: 선택 버전의 기본 인터페이스)")
    parser.add_argument("--version", help="--game과 함께: game.json에 매핑된 버전")
    parser.add_argument("--socket", help="기존 게임에 연결할 소켓 절대 경로")
    args = parser.parse_args()
    lab = Lab(args.games_dir)
    if args.game:
        lab.connect(args.game, args.interface, version=args.version, socket_path=args.socket)
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
