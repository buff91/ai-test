"""Games under games/: check, play live with a model, and compare models offline.

A game folder holds what the game is (game.json with its type and policy,
questions.json with its Choice/Score/Noul questions, cases.json with states and their
truth). How to reach it lives in games/<id>/interfaces/<name>/: "kbm" (screen +
keyboard/mouse) or "socket" (the game serves its own state, and sims/ games also the
truth). The type in config/game_types.json decides which model plays (live) and which
only shadow. Every answer that has a truth is graded per question.

  uv run python play.py list
  uv run python play.py types
  uv run python play.py new mygame --type realtime.action [--interface kbm|socket]
  uv run python play.py add-interface mygame socket
  uv run python play.py doctor arena
  uv run python play.py observe arena
  uv run python play.py run arena --provider laya-mlx --steps 20
  uv run python play.py run shooter --policy --episodes 3
  uv run python play.py report results/games/arena/<session>
  uv run python play.py export-cases arena results/games/arena/<session>
  uv run python play.py compare cards --provider all
  uv run python play.py matrix --provider local
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from gamelab.game_case import CONNECTORS, GAMES, GameCase, list_games
from gamelab import grade
from gamelab.game_types import load_types
from gamelab.policy import Policy
from gamelab.runner import RESULTS, export_cases, format_session, play, report


PROVIDER_GROUPS = {"all": ["jev", "laya", "laya-mlx"], "local": ["laya", "laya-mlx"]}


def connect(game, args, dry_run=False, reflex=False):
    return game.connect(agent=args.agent, dry_run=dry_run, reflex=reflex)


def cmd_list(args):
    games, broken = _load_all(args.games_dir)
    types = load_types()
    for type_id, spec in types.items():
        mine = [g for g in games if g.type == type_id]
        if not mine:
            continue
        print("\n%s  (%s)" % (spec["label"], type_id))
        for game in mine:
            mapping = game.providers["live"] + "".join(" +" + p for p in game.providers["shadow"])
            interfaces = ", ".join(name + ("*" if name == game.interface and len(game.interfaces) > 1 else "")
                                   for name in game.interfaces) or "비교 전용"
            print("  %-15s %-14s %-18s %s" % (game.id, interfaces, mapping, game.name))
    print("\n인터페이스가 여러 개면 *가 기본값입니다. 다른 것은 --interface로 고릅니다.")
    for game in games:
        for name in game.interfaces:
            try:
                GameCase(game.id, args.games_dir, interface=name)
            except Exception as exc:
                broken.append(("%s/%s" % (game.id, name), exc))
    for game_id, error in broken:
        print("! %-15s %s" % (game_id, error))


def cmd_types(args):
    games, _ = _load_all(args.games_dir)
    print("%-18s %-24s %8s  %-9s %-14s %s" % ("type", "이름", "지연예산", "live", "shadow", "게임"))
    for type_id, spec in load_types().items():
        providers = spec.get("providers") or {}
        print("%-18s %-24s %6dms  %-9s %-14s %s" % (
            type_id, spec["label"], spec["latency_budget_ms"], providers.get("live", "-"),
            ",".join(providers.get("shadow", [])) or "-",
            ", ".join(g.id for g in games if g.type == type_id) or "-"))
    print("\n타입별 매핑은 config/game_types.json, 게임 하나만 바꾸려면 그 게임 game.json의 \"providers\"에서 고칩니다.")


def cmd_new(args):
    target = args.games_dir / args.game
    if target.exists():
        sys.exit("이미 있습니다: %s" % target)
    if args.type not in load_types():
        sys.exit("알 수 없는 type입니다: %s (play.py types로 확인)" % args.type)
    target.mkdir(parents=True)
    for name in ("questions.json", "cases.json"):
        shutil.copy(args.games_dir / "_template" / name, target / name)
    _write_json(target / "game.json", {"name": args.game, "type": args.type})
    print("만들었습니다: %s (type=%s)" % (target, args.type))
    if args.interface:
        _add_interface(target, args.interface, args.interface, args.games_dir)
    print("다음: questions.json의 질문과 cases.json의 상태·truth(정답)를 채우세요.%s" % (
        "" if args.interface else " 실제 게임에 연결하려면 play.py add-interface %s kbm|socket" % args.game))


def cmd_add_interface(args):
    game_dir = args.games_dir / args.game
    if not (game_dir / "game.json").exists():
        sys.exit("게임이 없습니다: %s" % game_dir)
    _add_interface(game_dir, args.connector, args.name or args.connector, args.games_dir)


def _add_interface(game_dir, connector, name, games_dir):
    """Copy the connector's template and fill its actions from the game's own choices."""
    target = game_dir / "interfaces" / name
    if target.exists():
        sys.exit("이미 있습니다: %s" % target)
    shutil.copytree(games_dir / "_template" / "interfaces" / connector, target)
    options = json.loads((game_dir / "questions.json").read_text(encoding="utf-8"))["action"]["criteria"]
    config = json.loads((target / "interface.json").read_text(encoding="utf-8"))
    config["actions"] = {option: [] if connector == "kbm" else option for option in options}
    _write_json(target / "interface.json", config)
    game_path = game_dir / "game.json"
    game = json.loads(game_path.read_text(encoding="utf-8"))
    if "interface" not in game:
        game["interface"] = name
        _write_json(game_path, game)
    hint = {"kbm": "window.owner와 actions(키·마우스 입력), observer.py의 화면 영역",
            "socket": "launch.command(게임 실행 파일)와 actions(게임에 보낼 명령 이름)"}[connector]
    print("인터페이스를 추가했습니다: %s (connector=%s)\n다음: interface.json의 %s을 고치세요." % (target, connector, hint))


def _write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def cmd_doctor(args):
    game = _game(args)
    print("✓ 설정: %s (%s) — 인터페이스 %s, connector=%s" % (game.id, game.name, game.interface, game.connector))
    with connect(game, args, dry_run=True) as client:
        info = client.request("info")
        for name, granted in (info.get("permissions") or {}).items():
            print("%s 권한 %s: %s" % ("✓" if granted else "!", name, granted))
        observed = client.request("observe")
        if not observed.get("ok"):
            sys.exit("! 관찰 실패: %s" % observed.get("error"))
        window = observed.get("window")
        if window:
            print("✓ 창: %s %dx%d @ (%d, %d)" % (game.config["window"]["owner"], window["width"], window["height"], window["x"], window["y"]))
        print("✓ 상태: %s" % json.dumps(observed["state"], ensure_ascii=False))
        print("✓ 모델에 보낼 설명: %s" % (observed["description"] or "(없음, state JSON을 그대로 보냄)"))
        print("%s 게임이 주는 정답(truth): %s" % (("✓", json.dumps(observed["truth"], ensure_ascii=False)) if observed.get("truth")
                                             else ("·", "없음 (cases.json의 truth로만 채점)")))


def cmd_observe(args):
    game = _game(args)
    save = str(args.save.resolve()) if args.save else None
    with connect(game, args, dry_run=True) as client:
        observed = client.request("observe", save_frame=save)
    if not observed.get("ok"):
        sys.exit("! %s" % observed.get("error"))
    print(json.dumps({k: observed.get(k) for k in ("state", "description", "truth", "signature")}, indent=2, ensure_ascii=False))
    if save and observed.get("frame_saved"):
        print("화면 저장: %s" % save)


def cmd_run(args):
    from gamelab.decide import for_game

    game = _game(args)
    key = os.environ.get("JEV_API_KEY")
    live = args.provider or game.providers["live"]
    if live == "jev" and not key:
        sys.exit("! %s 타입의 live 모델이 jev인데 JEV_API_KEY가 없습니다. 키를 설정하거나 --provider laya-mlx로 실행하세요." % game.type)
    shadows = [] if args.no_shadow else [p for p in game.providers["shadow"] if p != live]
    if "jev" in shadows and not key:
        print("Jev shadow 건너뜀: JEV_API_KEY가 없습니다.", file=sys.stderr)
        shadows.remove("jev")
    decider = for_game(live, game, key)
    shadow_deciders = [for_game(p, game, key) for p in shadows]
    escalate_to = (game.policy.get("escalate") or {}).get("to")
    escalate_decider = None
    if args.policy and escalate_to and escalate_to != live:
        if escalate_to == "jev" and not key:
            print("escalate 건너뜀: JEV_API_KEY가 없습니다.", file=sys.stderr)
        else:
            escalate_decider = for_game(escalate_to, game, key)
    policy = Policy(game.policy, game.questions, enabled=args.policy, escalate_decider=escalate_decider)
    kbm = game.connector == "kbm"
    realtime = game.type_spec["timing"] == "realtime"
    mode = ("DRY-RUN(입력 안 보냄)" if args.dry_run else "실제 입력") if kbm else "게임 훅"
    print("%s 실행: type=%s interface=%s(%s) live=%s shadow=%s %s, policy=%s." % (
        game.id, game.type, game.interface, game.connector, live, ",".join(shadows) or "-", mode,
        ("켜짐" if args.policy else "기록만") if policy else "없음"))
    if kbm:
        print("마우스를 화면 왼쪽 위 모서리로 옮기거나 Ctrl+C로 멈춥니다.")
    pause = kbm and (args.pause or bool(game.config["pause_key"] and not args.no_pause))
    with connect(game, args, dry_run=args.dry_run, reflex=args.reflex) as client:
        session = play(client, decider, game.config, steps=args.steps, pause=pause,
                       record=not args.no_record, save_frames=args.frames, shadows=shadow_deciders,
                       shadow_wait_ms=game.config["interval_ms"] if realtime else None,
                       questions=game.questions, policy=policy, episodes=args.episodes)
    if session:
        print("기록: %s" % session)


def cmd_export(args):
    game = GameCase(args.game, args.games_dir)
    out = args.output or game.dir / "cases.recorded.json"
    added = export_cases(args.session, out)
    print("%d개 케이스를 %s에 추가했습니다. truth가 비어 있는 케이스는 채운 뒤 compare --cases로 비교하세요." % (added, out))


def cmd_report(args):
    summary = report(args.session)
    print(format_session(summary) if summary["steps"] else "기록된 step이 없습니다.")


def cmd_compare(args):
    from gamelab.decide import for_game

    game = GameCase(args.game, args.games_dir)
    cases = game.cases(args.cases)
    providers, key = _providers(args.provider)
    deciders = [for_game(p, game, key) for p in providers]
    rows = []
    for case in cases:
        for decider in deciders:
            row = {**decider.decide(case["state"]), "case_id": case["id"], "truth": case.get("truth")}
            row["grade"] = grade.grade(row.get("answers"), row["truth"], game.questions) if row["status"] == "ok" else None
            rows.append(row)
            if row["status"] == "ok":
                correct = ((row["grade"] or {}).get("action") or {}).get("correct")
                print("%s %-22s %-8s %-16s %8.1f ms  p=%.2f  %s" % (
                    "✓" if correct else "·", case["id"], decider.provider, row["action"], row["model_ms"],
                    row.get("probability") or 0, grade.marks(row["grade"])))
            else:
                print("! %s %s: %s" % (case["id"], decider.provider, row["error"]))
    for decider in deciders:
        grades = [r["grade"] for r in rows if r["provider"] == decider.provider and r.get("grade")]
        if grades:
            print("\n[%s]\n%s" % (decider.provider, grade.format_summary(grade.summarize(grades))))
    out = args.output or RESULTS / game.id / ("compare-%s.json" % datetime.now().strftime("%Y%m%d-%H%M%S"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"created_at": datetime.now(timezone.utc).isoformat(), "game": game.id,
                               "type": game.type, "connector": game.connector, "questions": game.questions, "rows": rows},
                              indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("결과: %s" % out)


def cmd_matrix(args):
    from gamelab.matrix import run_matrix, summarize, to_markdown

    games, broken = _load_all(args.games_dir)
    for game_id, error in broken:
        print("! %s 건너뜀: %s" % (game_id, error), file=sys.stderr)
    if args.types:
        games = [g for g in games if g.type in args.types or g.type.split(".")[0] in args.types]
    if args.games:
        games = [g for g in games if g.id in args.games]
    if not games:
        sys.exit("비교할 게임 케이스가 없습니다.")
    providers, key = _providers(args.provider)
    rows = run_matrix(games, providers, key)
    summary = summarize(rows, load_types(), games)
    report = to_markdown(summary)
    print("\n" + report)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = args.output or RESULTS / ("matrix-%s.json" % stamp)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"created_at": datetime.now(timezone.utc).isoformat(), "providers": providers,
                               "games": [g.id for g in games], "summary": summary, "rows": rows},
                              indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    out.with_suffix(".md").write_text(report + "\n", encoding="utf-8")
    print("결과: %s (+ .md)" % out)


def _game(args):
    """The game with the interface chosen by --interface (or its default), ready to connect."""
    game = GameCase(args.game, args.games_dir, interface=args.interface)
    game.require_interface()
    return game


def _load_all(games_dir):
    games, broken = [], []
    for game_id in list_games(games_dir):
        try:
            games.append(GameCase(game_id, games_dir))
        except Exception as exc:
            broken.append((game_id, exc))
    return games, broken


def _providers(choice):
    providers = list(PROVIDER_GROUPS.get(choice, [choice]))
    key = os.environ.get("JEV_API_KEY")
    if "jev" in providers and not key:
        if choice == "jev":
            sys.exit("JEV_API_KEY가 설정되지 않았습니다.")
        print("Jev 건너뜀: JEV_API_KEY가 없습니다.", file=sys.stderr)
        providers.remove("jev")
    return providers, key


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--games-dir", type=Path, default=GAMES)
    parser.add_argument("--agent", choices=["app", "dev"], default="app",
                        help="kbm 게임만: app=빌드한 KbmAgent.app (권한이 앱에 붙음), dev=현재 Python (권한이 터미널에 붙음)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="게임 타입별 케이스 목록, 연결 방식, 모델 매핑").set_defaults(fn=cmd_list)
    sub.add_parser("types", help="게임 타입과 타입별 live/shadow 모델 매핑").set_defaults(fn=cmd_types)
    p = sub.add_parser("new", help="새 게임 만들기 (인터페이스 없이 시작하면 비교 전용)")
    p.add_argument("game")
    p.add_argument("--type", required=True, help="config/game_types.json의 타입 (play.py types)")
    p.add_argument("--interface", choices=sorted(CONNECTORS), help="처음부터 붙일 인터페이스")
    p.set_defaults(fn=cmd_new)
    p = sub.add_parser("add-interface", help="기존 게임에 연결 방식 추가 (kbm 또는 socket)")
    p.add_argument("game")
    p.add_argument("connector", choices=sorted(CONNECTORS))
    p.add_argument("--name", help="인터페이스 폴더 이름 (기본: connector 이름)")
    p.set_defaults(fn=cmd_add_interface)
    p = sub.add_parser("doctor", help="연결, 권한, 상태 읽기 점검 (입력은 보내지 않음)")
    p.add_argument("game")
    p.add_argument("--interface", help="games/<game>/interfaces/ 중 하나. 기본: game.json의 interface")
    p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("observe", help="상태를 한 번 읽어 출력 (kbm은 화면 보정용)")
    p.add_argument("game")
    p.add_argument("--interface", help="games/<game>/interfaces/ 중 하나. 기본: game.json의 interface")
    p.add_argument("--save", type=Path, help="kbm 게임만: 캡처 화면 PNG 저장 경로")
    p.set_defaults(fn=cmd_observe)
    p = sub.add_parser("run", help="실시간 플레이: 관찰 → 모델 판단 → 행동")
    p.add_argument("game")
    p.add_argument("--interface", help="games/<game>/interfaces/ 중 하나. 기본: game.json의 interface")
    p.add_argument("--provider", choices=["jev", "laya", "laya-mlx"],
                   help="이번 실행의 live 모델. 기본값: 게임 타입의 매핑")
    p.add_argument("--no-shadow", action="store_true", help="shadow 모델을 돌리지 않음")
    p.add_argument("--steps", type=int, default=0, help="0이면 멈출 때까지")
    p.add_argument("--dry-run", action="store_true", help="kbm 게임만: 입력 이벤트를 보내지 않고 기록만")
    p.add_argument("--pause", action="store_true", help="kbm 게임만: 판단하는 동안 pause_key로 게임을 멈춤")
    p.add_argument("--no-pause", action="store_true", help="kbm 게임만: pause_key가 있어도 쓰지 않음")
    p.add_argument("--reflex", action="store_true", help="kbm 게임만: observer.reflex() 빠른 규칙도 실행")
    p.add_argument("--frames", action="store_true", help="kbm 게임만: 매 판단 화면을 PNG로 저장")
    p.add_argument("--no-record", action="store_true")
    p.add_argument("--policy", action="store_true",
                   help="game.json policy를 실제로 적용 (기본: 발동 여부만 기록하고 행동은 바꾸지 않음)")
    p.add_argument("--episodes", type=int, default=0, help="판이 끝나면 다음 시나리오로 리셋, 이 판 수만큼 하고 멈춤 (0=제한 없음)")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("report", help="run 기록의 질문별 채점·policy 요약")
    p.add_argument("session", type=Path)
    p.set_defaults(fn=cmd_report)
    p = sub.add_parser("export-cases", help="run 기록을 오프라인 비교 케이스로 변환")
    p.add_argument("game")
    p.add_argument("session", type=Path)
    p.add_argument("--output", type=Path)
    p.set_defaults(fn=cmd_export)
    p = sub.add_parser("compare", help="같은 상태를 여러 모델에 보내 오프라인 비교")
    p.add_argument("game")
    p.add_argument("--provider", choices=["all", "local", "jev", "laya", "laya-mlx"], default="all")
    p.add_argument("--cases", type=Path, help="기본값: games/<id>/cases.json")
    p.add_argument("--output", type=Path)
    p.set_defaults(fn=cmd_compare)
    p = sub.add_parser("matrix", help="모든 케이스를 모델별로 돌려 게임 타입별 일치율·지연 정리")
    p.add_argument("--provider", choices=["all", "local", "jev", "laya", "laya-mlx"], default="all")
    p.add_argument("--types", nargs="+", help="타입(turn.card) 또는 타이밍(turn, realtime)으로 좁히기")
    p.add_argument("--games", nargs="+", help="게임 케이스 id로 좁히기")
    p.add_argument("--output", type=Path)
    p.set_defaults(fn=cmd_matrix)

    args = parser.parse_args()
    try:
        args.fn(args)
    except (LookupError, ValueError, FileNotFoundError, RuntimeError, TimeoutError) as exc:
        sys.exit("! %s" % exc)


if __name__ == "__main__":
    main()
