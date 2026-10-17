"""Exercise the real browser -> socket -> SocketGame -> runner path without a model key."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys

from gamelab.game_case import GameCase, ROOT
from gamelab.runner import tick


class ScriptedDecider:
    provider = "scripted-smoke"

    def decide(self, description):
        target = int(re.search(r"^목표: (-?\d+)$", description, re.MULTILINE).group(1))
        count = int(re.search(r"^현재: (-?\d+)$", description, re.MULTILINE).group(1))
        action = "increase" if count < target else "decrease" if count > target else "confirm"
        return {"status": "ok", "provider": self.provider, "action": action, "model_ms": 0}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true", help="브라우저 창 없이 검증")
    parser.add_argument("--output", type=Path, help="결과 폴더 (기본: results/external-control/<실행 시각>)")
    args = parser.parse_args(argv)
    output = (args.output or ROOT / "results" / "external-control" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")).resolve()
    output.mkdir(parents=True, exist_ok=True)
    screenshot = output / "complete.png"
    report = {"ok": False, "provider": ScriptedDecider.provider, "headless": args.headless, "steps": []}
    try:
        game = GameCase("web_counter", games_dir=Path(__file__).with_name("games"), version="demo-v1",
                        interface="headless" if args.headless else "browser")
        launch = game.config["launch"]
        game.config["launch"] = {**launch, "command": [*launch["command"], "--screenshot", str(screenshot)]}
        with game.connect() as client:
            info = client.request("info")
            require(info["version_check"]["status"] == "verified", "번들 페이지 빌드 식별에 실패했습니다.")
            report["target"] = info["target"]
            initial = client.request("observe")
            require(initial["ok"] and initial["state"]["count"] == 0 and not initial["done"], "초기 화면이 예상과 다릅니다.")
            invalid = client.request("act", action="confirm", signature=initial["signature"])
            require(not invalid["ok"] and not invalid["applied"], "목표 전 완료가 거절되지 않았습니다.")
            waiting = client.request("act", action="wait", signature=initial["signature"])
            require(waiting["ok"] and waiting["state"]["count"] == 0, "기다리기 동작 검증에 실패했습니다.")
            stale = client.request("act", action="increase", signature=initial["signature"])
            require(not stale["ok"] and stale["stale"], "이전 관찰로 내린 판단이 거절되지 않았습니다.")
            observed = client.request("observe")
            decreased = client.request("act", action="decrease", signature=observed["signature"])
            require(decreased["ok"] and decreased["state"]["count"] == -1, "줄이기 동작 검증에 실패했습니다.")
            reset = client.request("reset")
            require(reset["ok"] and reset["state"]["count"] == 0 and not reset["done"], "새 판 동작 검증에 실패했습니다.")
            decider = ScriptedDecider()
            for step in range(1, 11):
                result = tick(client, decider, game.config, step=step)
                require(result["kind"] == "step", "runner.tick이 step을 반환하지 않았습니다: %s" % result)
                row = result["row"]
                report["steps"].append(row)
                require(row["applied"] and not row["error"], "실제 클릭 적용에 실패했습니다: %s" % row["error"])
                if row["done"]:
                    break
            else:
                raise RuntimeError("10번의 판단 안에 완료되지 않았습니다.")
            ended = tick(client, decider, game.config, step=len(report["steps"]) + 1)
            require(ended["kind"] == "done" and ended["outcome"] == "success", "완료 화면 검증에 실패했습니다.")
            require(screenshot.is_file() and screenshot.stat().st_size > 0, "완료 화면 PNG가 생성되지 않았습니다.")
            finished = client.request("observe")
            blocked = client.request("act", action="increase", signature=finished["signature"])
            require(not blocked["ok"] and not blocked["applied"], "완료 후 입력이 거절되지 않았습니다.")
            report.update(ok=True, outcome=ended["outcome"], screenshot=str(screenshot),
                          checks=["build", "initial", "premature_confirm", "wait", "stale", "decrease", "reset", "runner_completion", "done_rejection"])
    except Exception as exc:
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
        print("외부 웹 제어 검증 실패: %s" % report["error"], file=sys.stderr)
    finally:
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if report["ok"]:
        print("외부 웹 제어 검증 성공: %d회 판단 → 목표 일치 → 완료" % len(report["steps"]))
        print("결과: %s" % output)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
