"""Run the same typed decision cases through Jev, Laya, and Laya-MLX."""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_CASES = ROOT / "cases" / "game_actions.json"
OPTIONS = {
    "attack": "Attack an exposed enemy when it is safe and useful.",
    "dodge": "Evade an imminent attack that is aimed at the current position.",
    "reload": "Reload an empty or nearly empty weapon while safe.",
    "retreat": "Move away from danger to survive or reach safety.",
    "advance": "Move toward the next objective when the path is clear.",
    "wait": "Hold position when acting now would be premature.",
}
QUESTIONS = {
    "action": {
        "type": "choice",
        "instructions": "Which single action should the player take next based on this state?",
        "criteria": OPTIONS,
    }
}


def load_cases(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("Cases file must contain a nonempty JSON list.")
    seen = set()
    for case in data:
        if not isinstance(case, dict):
            raise ValueError("Each case must be a JSON object.")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError("Each case needs a unique nonempty id.")
        if not isinstance(case.get("state"), (str, dict, list)):
            raise ValueError("Case %s needs a text, object, or list state." % case_id)
        reference = case.get("reference_action")
        if reference is not None and reference not in OPTIONS:
            raise ValueError("Case %s has an unknown reference_action." % case_id)
        seen.add(case_id)
    return data


def call_jev(state, key, model, timeout):
    payload = json.dumps(
        {"model": model, "state": state, "questions": QUESTIONS},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.typesafe.ai/v1/systemone",
        data=payload,
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def make_laya_router():
    try:
        from laya import Router
    except ImportError as exc:
        raise RuntimeError("Laya is not installed. Run 'uv sync' in this folder.") from exc
    return Router()


def make_laya_mlx_router():
    try:
        from laya_mlx import Router
    except ImportError as exc:
        raise RuntimeError("Laya-MLX is not installed. Run 'uv sync' in this folder.") from exc
    return Router()


def summarize_answer(result):
    answer = result["answers"]["action"]
    action = answer["choice"]
    probabilities = answer.get("probabilities", {})
    return {
        "action": action,
        "probability": probabilities.get(action),
        "confidence": answer.get("confidence"),
        "probabilities": probabilities,
        "model": result.get("model") or result.get("routing", {}).get("model"),
        "usage": result.get("usage"),
    }


def run_case(provider, case, args, routers, key):
    started = time.perf_counter()
    try:
        if provider == "jev":
            result = call_jev(case["state"], key, args.jev_model, args.timeout)
        else:
            kwargs = {} if args.laya_model == "auto" else {"model": args.laya_model}
            result = routers[provider].predict(case["state"], QUESTIONS, **kwargs)
        row = summarize_answer(result)
        row["status"] = "ok"
    except urllib.error.HTTPError as exc:
        # Never include headers or request data: they contain the API key.
        row = {"status": "error", "error": "Jev HTTP %s" % exc.code}
    except Exception as exc:
        row = {"status": "error", "error": "%s: %s" % (type(exc).__name__, exc)}
    row.update(
        {
            "provider": provider,
            "case_id": case["id"],
            "reference_action": case.get("reference_action"),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    )
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider",
        choices=["all", "local", "both", "jev", "laya", "laya-mlx"],
        default="all",
        help="all=three backends, local=both Laya runtimes, both=Jev and regular Laya",
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, help="Write the full comparison as JSON")
    parser.add_argument("--validate", action="store_true", help="Check cases without calling models")
    parser.add_argument("--jev-model", default="jev-latest")
    parser.add_argument(
        "--laya-model",
        choices=["auto", "english", "multilingual", "typed-decisions"],
        default="multilingual",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    try:
        cases = load_cases(args.cases)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print("Validated %d cases with %d action options." % (len(cases), len(OPTIONS)))
    if args.validate:
        return 0

    provider_groups = {
        "all": ["jev", "laya", "laya-mlx"],
        "local": ["laya", "laya-mlx"],
        "both": ["jev", "laya"],
    }
    providers = provider_groups.get(args.provider, [args.provider]).copy()
    key = os.environ.get("JEV_API_KEY")
    if "jev" in providers and not key:
        if args.provider == "jev":
            parser.error("JEV_API_KEY is not set.")
        print("Jev skipped: JEV_API_KEY is not set.", file=sys.stderr)
        providers.remove("jev")

    try:
        routers = {}
        if "laya" in providers:
            routers["laya"] = make_laya_router()
        if "laya-mlx" in providers:
            routers["laya-mlx"] = make_laya_mlx_router()
    except RuntimeError as exc:
        parser.error(str(exc))

    rows = []
    for case in cases:
        for provider in providers:
            row = run_case(provider, case, args, routers, key)
            rows.append(row)
            if row["status"] == "ok":
                marker = "✓" if row["action"] == case.get("reference_action") else "·"
                print(
                    "%s %-18s %-8s %-8s %8.1f ms" %
                    (marker, case["id"], provider, row["action"], row["elapsed_ms"])
                )
            else:
                print("! %s %s: %s" % (case["id"], provider, row["error"]))

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "cases": str(args.cases),
            "note": "Wall time includes API/network for Jev and local inference for Laya. Each local runtime's first call may load/download a model. Laya and Laya-MLX use the same model selection but different execution engines.",
            "rows": rows,
        }
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("Saved results to %s" % args.output)
    return 1 if any(row["status"] == "error" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
