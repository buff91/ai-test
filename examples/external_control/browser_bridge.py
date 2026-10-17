"""DOM observation + real Playwright clicks, exposed as the existing socket protocol.

The bundled page is the default. --url accepts only pages implementing the same
visible #state text and button contract; it is not an arbitrary-site controller.
Playwright is optional and imported only when this executable starts.
"""

import argparse
import json
import os
from pathlib import Path
import re
import signal
import socketserver
import stat
import sys


DEMO = Path(__file__).with_name("demo.html")
BUTTONS = {"increase": "1 늘리기", "decrease": "1 줄이기", "confirm": "완료", "wait": "기다리기"}
SCENARIO = "기본"
READ_TIMEOUT_MS = 500
CLICK_TIMEOUT_MS = 750


class BrowserService:
    """Must be created and called on the same thread as sync_playwright()."""

    def __init__(self, page, bundled=True, screenshot=None):
        self.page = page
        self.bundled = bundled
        self.screenshot = Path(screenshot) if screenshot else None
        self.revision = -1
        self._last_dom = None
        self._last_state = None

    def _state(self):
        region = self.page.locator("#state")
        if not region.is_visible():
            raise ValueError("페이지에 보이는 #state 영역이 필요합니다.")
        text = region.inner_text(timeout=READ_TIMEOUT_MS)
        pairs = re.findall(r"^(목표|현재|완료|리비전):\s*([^\n]+?)\s*$", text, re.MULTILINE)
        fields = dict(pairs)
        if len(pairs) != 4 or set(fields) != {"목표", "현재", "완료", "리비전"}:
            raise ValueError("#state에는 목표, 현재, 완료, 리비전 표시가 각각 한 줄씩 필요합니다.")
        if fields["완료"] not in ("예", "아니오"):
            raise ValueError("완료 표시는 예 또는 아니오여야 합니다.")
        snapshot = {"target": int(fields["목표"]), "count": int(fields["현재"]),
                    "done": fields["완료"] == "예", "page_revision": int(fields["리비전"])}
        if snapshot["page_revision"] < 0:
            raise ValueError("페이지 리비전은 0 이상이어야 합니다.")
        if snapshot != self._last_dom:
            self.revision += 1
            self._last_dom = dict(snapshot)
        snapshot["outcome"] = "success" if snapshot["done"] and snapshot["count"] == snapshot["target"] else (
            "failed" if snapshot["done"] else "playing")
        description = "목표: {target}\n현재: {count}\n완료: {done}\n페이지 리비전: {page_revision}".format(
            **{**snapshot, "done": "예" if snapshot["done"] else "아니오"})
        self._last_state = {"snapshot": snapshot, "description": description, "revision": self.revision,
                            "done": snapshot["done"]}
        return self._last_state

    def error_response(self, exc):
        """Keep a selector failure from masquerading as a changed revision."""
        try:
            state = self._state()
        except Exception:
            state = {**(self._last_state or {}), "state_cached": True}
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc), **state}

    def handle(self, request):
        op = request.get("op")
        if op == "info":
            return {"ok": True, "actions": list(BUTTONS), "scenarios": [SCENARIO],
                    "build": {"game": "web_counter", "version": "demo-v1"} if self.bundled else {}}
        before = self._state()
        if op == "state":
            return {"ok": True, **before}
        if op not in ("step", "reset"):
            return {"ok": False, "error": "알 수 없는 명령입니다.", **before}
        if op == "reset":
            if request.get("scenario", SCENARIO) != SCENARIO:
                return {"ok": False, "error": "알 수 없는 시작 상황입니다.", **before}
            action, button = "reset", "새 판"
        else:
            action = request.get("action")
            if not isinstance(action, str) or action not in BUTTONS:
                return {"ok": False, "error": "알 수 없는 행동입니다.", **before}
            expected = request.get("expected_revision")
            if expected is not None and (type(expected) is not int or expected != before["revision"]):
                return {"ok": False, "stale": True, "error": "판단 이후 페이지 상태가 바뀌었습니다.", **before}
            if before["done"]:
                return {"ok": False, "error": "완료된 판입니다. 새 판을 시작하세요.", **before}
            if action == "confirm" and before["snapshot"]["count"] != before["snapshot"]["target"]:
                return {"ok": False, "error": "현재 값이 목표와 같아야 완료할 수 있습니다.", **before}
            button = BUTTONS[action]
        self.page.get_by_role("button", name=button, exact=True).click(timeout=CLICK_TIMEOUT_MS)
        after = self._state()
        old, new = before["snapshot"], after["snapshot"]
        expected_count = 0 if action == "reset" else old["count"] + {"increase": 1, "decrease": -1}.get(action, 0)
        expected_done = action == "confirm"
        # A second user click during Playwright's actionability wait is detectable
        # because each accepted page action advances the visible revision once.
        # The read/click pair cannot be atomic without a hook in the target page.
        verified = (new["count"] == expected_count and new["done"] == expected_done
                    and new["target"] == old["target"] and new["page_revision"] == old["page_revision"] + 1)
        if not verified:
            return {"ok": False, "error": "클릭 후 화면의 값·완료·리비전이 예상과 다릅니다.",
                    "before": old, **after}
        response = {"ok": True, "before": old, **after}
        if action == "confirm" and self.screenshot:
            try:
                self.page.screenshot(path=str(self.screenshot), timeout=750)
            except Exception as exc:
                # Recording failure does not undo a verified button click.
                response["screenshot_error"] = str(exc)
                print("완료 클릭은 확인했지만 화면 저장에 실패했습니다: %s" % exc, file=sys.stderr)
        return response


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(2)
        try:
            line = self.rfile.readline(65537)
            if len(line) > 65536 or not line.endswith(b"\n"):
                raise ValueError("요청은 64 KiB 이하 JSON 한 줄이어야 합니다.")
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("요청은 JSON 객체여야 합니다.")
            response = self.server.service.handle(request)
        except Exception as exc:
            # Selector failures and timeouts must never look like successful input.
            response = self.server.service.error_response(exc)
        try:
            self.wfile.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError):
            pass


class BrowserServer(socketserver.UnixStreamServer):
    """Intentionally single-threaded: Playwright objects stay on their own thread."""

    allow_reuse_address = False

    def __init__(self, path, service):
        self.service = service
        if os.path.lexists(path):
            raise FileExistsError("기존 소켓/파일은 덮어쓰지 않습니다: %s" % path)
        super().__init__(str(path), _Handler)
        self._socket_identity = os.lstat(path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            self.server_close()
            raise

    def server_close(self):
        super().server_close()
        owned = getattr(self, "_socket_identity", None)
        if owned is None:
            return
        try:
            current = os.lstat(self.server_address)
            if stat.S_ISSOCK(current.st_mode) and (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                os.unlink(self.server_address)
        except FileNotFoundError:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True, help="새로 만들 Unix socket 경로")
    parser.add_argument("--headless", action="store_true", help="브라우저 창 없이 실행")
    parser.add_argument("--url", help="동일한 DOM 계약을 구현한 페이지 URL (빌드 신원 미검증)")
    parser.add_argument("--screenshot", help="성공적으로 완료할 때 저장할 PNG 경로")
    args = parser.parse_args(argv)
    if os.path.lexists(args.socket):
        parser.error("기존 소켓/파일은 덮어쓰지 않습니다: %s" % args.socket)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        parser.error("Playwright가 필요합니다: uv sync --group browser && uv run --group browser playwright install chromium")
    if args.screenshot:
        Path(args.screenshot).parent.mkdir(parents=True, exist_ok=True)

    def stop(_signum, _frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, stop)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=args.headless)
            try:
                page = browser.new_page(viewport={"width": 1080, "height": 760})
                page.goto(args.url or DEMO.as_uri(), wait_until="domcontentloaded", timeout=15000)
                page.locator("#state").wait_for(state="visible", timeout=1000)
                service = BrowserService(page, bundled=args.url is None, screenshot=args.screenshot)
                service.handle({"op": "state"})
                with BrowserServer(args.socket, service) as server:
                    print("웹 제어 브리지 준비: %s (%s)" % (args.socket, "headless" if args.headless else "브라우저 창"), flush=True)
                    server.serve_forever(poll_interval=0.1)
            finally:
                browser.close()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print("웹 제어 브리지 오류: %s" % exc, file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
