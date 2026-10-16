"""Model-side loop: observe through any connector, decide, and send the action back.

When the game sends a truth with its state (sims/ do), every answer is graded on the
spot (gamelab/grade.py), and the session ends with a summary per model and question.
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path

from gamelab import grade
from gamelab.game_case import ROOT
from gamelab.policy import Policy


RESULTS = ROOT / "results" / "games"


def play(client, decider, config, steps=0, pause=False, record=True, save_frames=False, log=print,
         shadows=(), shadow_wait_ms=None, questions=None, policy=None, episodes=0):
    """Observe -> decide -> (policy) -> act, one tick() at a time, until steps or episodes run out.

    A finished episode is reset when the game supports it; episodes > 0 stops after that many.
    """
    policy = policy or Policy(None, questions or {})
    pool = ThreadPoolExecutor(max_workers=2 * (1 + len(shadows)))
    session = new_session(client.request("info")["game"]) if record else None
    interval = config["interval_ms"] / 1000
    count, episode, rows, outcomes = 0, 0, [], []
    try:
        while not steps or count < steps:
            started = time.time()
            frame_path = str(session / ("%04d.png" % (count + 1))) if record and save_frames else None
            result = tick(client, decider, config, pause=pause, shadows=shadows, shadow_wait_ms=shadow_wait_ms,
                          questions=questions, policy=policy, pool=pool, frame_path=frame_path,
                          step=count + 1, episode=episode)
            if result["kind"] == "error":
                log("! observe: %s" % result["error"])
                time.sleep(interval)
                continue
            if result["kind"] == "done":
                outcomes.append(result["outcome"])
                episode += 1
                log("— 판 %d 끝: %s" % (episode, outcomes[-1]))
                if episodes and episode >= episodes:
                    break
                reset = client.request("reset")
                if not reset.get("ok"):
                    log("! reset: %s" % reset.get("error"))
                    break
                continue
            count += 1
            row = result["row"]
            rows.append(row)
            if record:
                record_row(session, row)
            for warning in row["warnings"]:
                log("! %s" % warning)
            log(format_row(row))
            if result["halted"]:
                break
            time.sleep(max(0, interval - (time.time() - started)))
    except KeyboardInterrupt:
        client.request("halt", reason="Ctrl+C")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    summary = summarize_rows(rows, questions, outcomes)
    if rows and (summary["live"]["grades"] or summary["policy"]["fired"]):
        log("\n" + format_session(summary))
    if record:
        write_summary(session, summary)
    return session


def tick(client, decider, config, pause=False, shadows=(), shadow_wait_ms=None, questions=None, policy=None,
         pool=None, frame_path=None, step=0, episode=0, apply=True):
    """One observe -> decide -> (policy) -> act. A late answer is dropped instead of acting on an old screen.

    Shadow deciders judge the same observation in parallel and are only recorded.
    They never delay the action; with shadow_wait_ms set (real-time games) a shadow
    that is still thinking when the tick ends is recorded as a timeout.
    apply=False only asks the models and leaves the game as it is.

    Returns {"kind": "error", "error"}, {"kind": "done", "outcome", "observed"} when the
    episode already ended, or {"kind": "step", "row", "halted"}.
    """
    policy = policy or Policy(None, questions or {})
    tick_started = time.time()
    observed = client.request("observe", save_frame=frame_path)
    if not observed.get("ok"):
        return {"kind": "error", "error": observed.get("error")}
    if observed.get("done"):
        return {"kind": "done", "outcome": (observed.get("state") or {}).get("outcome"), "observed": observed}
    own_pool = pool is None and shadows
    pool = ThreadPoolExecutor(max_workers=len(shadows)) if own_pool else pool
    warnings = []
    try:
        if pause:
            warnings += _error(client.request("pause"))
        state = observed["description"] or observed["state"]
        truth = observed.get("truth")
        pending = {d.provider: pool.submit(d.decide, state) for d in shadows}
        decision = decider.decide(state)
        final, fired = policy.apply(decision, state)
        if pause:
            warnings += _error(client.request("resume"))
        if final["status"] != "ok":
            result = {"ok": False, "applied": False, "error": final["error"]}
        elif not apply:
            result = {"ok": True, "applied": False}
        elif pause:
            result = client.request("act", action=final["action"])
        else:
            result = client.request("act", action=final["action"], signature=observed["signature"],
                                    observed_at=observed["observed_at"], max_age_ms=config["max_age_ms"])
        budget = None if shadow_wait_ms is None else max(0, shadow_wait_ms / 1000 - (time.time() - tick_started))
        wait(pending.values(), timeout=budget)
        shadow = [{**future.result(), "agrees": future.result().get("action") == final.get("action")}
                  if future.done() else {"provider": name, "status": "timeout"}
                  for name, future in pending.items()]
    finally:
        if own_pool:
            pool.shutdown(wait=False, cancel_futures=True)
    graded = _grade(decision, truth, questions)
    for row in shadow:
        row["grade"] = _grade(row, truth, questions)
    row = {"step": step, "episode": episode, "frame": frame_path if observed.get("frame_saved") else None,
           "state": observed["state"], "description": observed["description"], "truth": truth,
           "decision": decision, "action": final.get("action"), "policy": fired, "grade": graded,
           "final_correct": _action_correct(final.get("action"), truth, questions),
           "shadow": shadow, "applied": bool(result.get("applied")), "stale": bool(result.get("stale")),
           "analysis_only": not apply, "error": None if result.get("ok") else result.get("error"),
           "after_state": result.get("state"), "done": bool(result.get("done")),
           "warnings": warnings, "loop_ms": round((time.time() - tick_started) * 1000, 1)}
    return {"kind": "step", "row": row, "halted": bool(result.get("halted"))}


def format_row(row):
    """One log line for a tick, e.g. '✓ #3 dodge  412ms model  적용  [action✓ danger 1.8/2]'."""
    decision, fired = row["decision"], row["policy"]
    override = fired["override"]
    return "%s #%d %-14s %7.0fms model  %s%s%s%s" % (
        "✓" if row["applied"] else ("~" if row["stale"] else ("·" if row.get("analysis_only") else "!")), row["step"],
        (row["action"] or "-") + (" (%s→)" % override["from"] if override else ""),
        decision["model_ms"], row["error"] or ("분석만" if row.get("analysis_only") else "적용"),
        "  [%s]" % grade.marks(row["grade"]) if row["grade"] else "",
        "  ⚑" + ",".join(fired["fired"]) if fired["fired"] else "",
        "".join("  | %s=%s" % (r["provider"], r.get("action") or r.get("status")) for r in row["shadow"]))


def new_session(game_id):
    session = RESULTS / game_id / datetime.now().strftime("%Y%m%d-%H%M%S")
    session.mkdir(parents=True, exist_ok=True)
    return session


def record_row(session, row):
    with open(session / "steps.jsonl", "a", encoding="utf-8") as out:
        out.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_summary(session, summary):
    (session / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _grade(decision, truth, questions):
    if not truth or not questions or decision.get("status") != "ok":
        return None
    return grade.grade(decision.get("answers"), truth, questions)


def _action_correct(action, truth, questions):
    qid = "action"
    expected = (truth or {}).get(qid)
    if expected is None or action is None or not questions:
        return None
    return action in (expected if isinstance(expected, list) else [expected])


def summarize_rows(rows, questions=None, outcomes=()):
    """Per-model grade summaries and policy effect for one session's rows."""
    live = [r for r in rows if r.get("grade")]
    shadows = {}
    for row in rows:
        for s in row.get("shadow") or []:
            if s.get("grade"):
                shadows.setdefault(s["provider"], []).append(s["grade"])
    fired, overrides = {}, []
    for row in rows:
        policy = row.get("policy") or {}
        for name in policy.get("fired", []):
            fired[name] = fired.get(name, 0) + 1
        if policy.get("override"):
            before = (row.get("grade") or {}).get("action", {}).get("correct")
            overrides.append({"step": row["step"], **policy["override"], "was_correct": before,
                              "now_correct": row.get("final_correct")})
    return {
        "steps": len(rows),
        "outcomes": list(outcomes),
        "live": {"provider": rows[0]["decision"].get("provider") if rows else None,
                 "grades": grade.summarize(r["grade"] for r in live)},
        "shadows": {p: grade.summarize(g) for p, g in shadows.items()},
        "policy": {"fired": fired, "overrides": len(overrides),
                   "helped": sum(1 for o in overrides if o["now_correct"] and o["was_correct"] is False),
                   "hurt": sum(1 for o in overrides if o["was_correct"] and o["now_correct"] is False),
                   "detail": overrides},
        "final_action_accuracy": grade._mean(r["final_correct"] for r in rows if r.get("final_correct") is not None),
    }


def format_session(summary):
    lines = ["== 세션 요약: %d step%s" % (summary["steps"], ", 판 결과 " + ", ".join(map(str, summary["outcomes"]))
                                        if summary["outcomes"] else "")]
    if summary["live"]["grades"]:
        lines.append("[live %s]" % summary["live"]["provider"])
        lines.append(grade.format_summary(summary["live"]["grades"]))
    for provider, grades in summary["shadows"].items():
        lines.append("[shadow %s]" % provider)
        lines.append(grade.format_summary(grades))
    policy = summary["policy"]
    if policy["fired"]:
        lines.append("[policy] 발동 %s · 행동 교체 %d회 (정답으로 바뀜 %d, 오답으로 바뀜 %d)" % (
            ", ".join("%s %d회" % kv for kv in policy["fired"].items()), policy["overrides"], policy["helped"], policy["hurt"]))
    if summary["final_action_accuracy"] is not None:
        lines.append("실제로 보낸 행동의 정답률: %.1f%%" % (summary["final_action_accuracy"] * 100))
    return "\n".join(lines)


def report(session_dir):
    """Summary of a recorded session, recomputed from its steps.jsonl."""
    session_dir = Path(session_dir)
    rows = [json.loads(line) for line in (session_dir / "steps.jsonl").read_text(encoding="utf-8").splitlines() if line]
    outcomes = [(r.get("after_state") or {}).get("outcome") for r in rows if r.get("done")]
    return summarize_rows(rows, outcomes=outcomes)


def _error(response):
    return [] if response.get("ok") else [response.get("error") or "게임이 요청을 거절했습니다."]


def export_cases(session_dir, out_path):
    """Turn recorded steps into offline cases; the game's truth comes along when it sent one."""
    session_dir = Path(session_dir)
    cases = []
    for line in (session_dir / "steps.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        cases.append({"id": "%s-%04d" % (session_dir.name, row["step"]),
                      "state": row["description"] or row["state"],
                      "truth": row.get("truth"),
                      "live_action": row.get("action") or row["decision"].get("action"),
                      "frame": row["frame"]})
    out_path = Path(out_path)
    existing = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else []
    seen = {case["id"] for case in existing}
    merged = existing + [case for case in cases if case["id"] not in seen]
    out_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return len(merged) - len(existing)
