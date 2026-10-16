"""Run a simulation game: serve it to play.py, write its cases.json, or play it by hand.

  uv run python -m sims serve arena --socket /tmp/arena.sock     (play.py launches this itself)
  uv run python -m sims cases arena --output games/arena/cases.json
  uv run python -m sims play swarm --show-truth
"""

import argparse
import json
import random
import sys
from pathlib import Path

from sims import SIMS
from sims.base import truth


def cmd_serve(args):
    from game_ipc import GameServer
    from sims.service import SimService

    server = GameServer(args.socket, SimService(SIMS[args.game], args.scenario))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        Path(args.socket).unlink(missing_ok=True)


def generate_cases(sim_cls, per_scenario=6, explore=0.3, seed=0):
    """Walk every scenario mostly along the truth, sometimes off it, and keep graded positions."""
    rng = random.Random(seed)
    cases = []
    for scenario in sim_cls.SCENARIOS:
        sim, seen, count = sim_cls.start(scenario), set(), 0
        for _ in range(60):
            if sim.outcome != "playing" or count >= per_scenario:
                break
            answer = truth(sim)
            if answer.get("action") and sim.key() not in seen:
                seen.add(sim.key())
                cases.append({"id": "%s-%02d" % (scenario, count), "state": sim.describe(), "truth": answer})
                count += 1
            best = answer.get("action") or list(sim.legal())
            sim.step(rng.choice(list(sim.legal())) if rng.random() < explore else best[0])
    return cases


def cmd_cases(args):
    cases = generate_cases(SIMS[args.game], args.per_scenario, args.explore, args.seed)
    text = "[\n" + ",\n".join("  " + json.dumps(c, ensure_ascii=False) for c in cases) + "\n]\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print("%d개 케이스를 %s에 썼습니다." % (len(cases), args.output))
    else:
        sys.stdout.write(text)


def cmd_play(args):
    sim = SIMS[args.game].start(args.scenario)
    while sim.outcome == "playing":
        print("\n" + sim.describe())
        if args.show_truth:
            print("truth: %s" % json.dumps(truth(sim), ensure_ascii=False))
        action = input("행동 (%s, q=끝): " % ", ".join(sim.ACTIONS)).strip()
        if action == "q":
            return
        if action not in sim.ACTIONS:
            print("알 수 없는 행동입니다.")
            continue
        print(" ".join(sim.step(action)))
    print("\n결과: %s" % sim.outcome)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("serve", help="socket으로 게임 제공 (play.py run이 직접 띄움)")
    p.add_argument("game", choices=sorted(SIMS))
    p.add_argument("--socket", required=True)
    p.add_argument("--scenario")
    p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("cases", help="시나리오를 진행하며 정답이 붙은 비교 케이스 생성")
    p.add_argument("game", choices=sorted(SIMS))
    p.add_argument("--per-scenario", type=int, default=6)
    p.add_argument("--explore", type=float, default=0.3, help="정답 대신 무작위 행동을 고를 비율")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--output", type=Path)
    p.set_defaults(fn=cmd_cases)
    p = sub.add_parser("play", help="터미널에서 직접 플레이")
    p.add_argument("game", choices=sorted(SIMS))
    p.add_argument("--scenario")
    p.add_argument("--show-truth", action="store_true")
    p.set_defaults(fn=cmd_play)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
