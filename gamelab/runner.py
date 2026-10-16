"""Model-side loop: observe through any connector, decide, and send the action back."""

import json
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path

from gamelab.game_case import ROOT


RESULTS = ROOT / "results" / "games"


def play(client, decider, config, steps=0, pause=False, record=True, save_frames=False, log=print,
         shadows=(), shadow_wait_ms=None):
    """Observe -> decide -> act. A late answer is dropped instead of acting on an old screen.

    Shadow deciders judge the same observation in parallel and are only recorded.
    They never delay the action; with shadow_wait_ms set (real-time games) a shadow
    that is still thinking when the tick ends is recorded as a timeout.
    """
    pool = ThreadPoolExecutor(max_workers=2 * (1 + len(shadows)))
    session = RESULTS / client.request("info")["game"] / datetime.now().strftime("%Y%m%d-%H%M%S")
    if record:
        session.mkdir(parents=True, exist_ok=True)
    interval = config["interval_ms"] / 1000
    count = 0
    try:
        while not steps or count < steps:
            count += 1
            tick = time.time()
            frame_path = str(session / ("%04d.png" % count)) if record and save_frames else None
            observed = client.request("observe", save_frame=frame_path)
            if not observed.get("ok"):
                log("! observe: %s" % observed.get("error"))
                time.sleep(interval)
                continue
            if pause:
                _check(client.request("pause"), log)
            state = observed["description"] or observed["state"]
            pending = {d.provider: pool.submit(d.decide, state) for d in shadows}
            decision = decider.decide(state)
            if pause:
                _check(client.request("resume"), log)
            if decision["status"] != "ok":
                result = {"ok": False, "applied": False, "error": decision["error"]}
            elif pause:
                result = client.request("act", action=decision["action"])
            else:
                result = client.request("act", action=decision["action"], signature=observed["signature"],
                                        observed_at=observed["observed_at"], max_age_ms=config["max_age_ms"])
            budget = None if shadow_wait_ms is None else max(0, shadow_wait_ms / 1000 - (time.time() - tick))
            wait(pending.values(), timeout=budget)
            shadow = [{**future.result(), "agrees": future.result().get("action") == decision.get("action")}
                      if future.done() else {"provider": name, "status": "timeout"}
                      for name, future in pending.items()]
            row = {"step": count, "frame": frame_path if observed.get("frame_saved") else None, "state": observed["state"],
                   "description": observed["description"], "decision": decision, "shadow": shadow,
                   "applied": bool(result.get("applied")), "stale": bool(result.get("stale")),
                   "error": None if result.get("ok") else result.get("error"),
                   "after_state": result.get("state"), "loop_ms": round((time.time() - tick) * 1000, 1)}
            if record:
                with open(session / "steps.jsonl", "a", encoding="utf-8") as out:
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
            log("%s #%d %-10s %7.0fms model  %s%s" % (
                "✓" if row["applied"] else ("~" if row["stale"] else "!"), count,
                decision.get("action", "-"), decision["model_ms"], row["error"] or "적용",
                "".join("  | %s=%s" % (r["provider"], r.get("action") or r.get("status")) for r in shadow)))
            if result.get("halted"):
                break
            time.sleep(max(0, interval - (time.time() - tick)))
    except KeyboardInterrupt:
        client.request("halt", reason="Ctrl+C")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return session if record else None


def _check(response, log):
    if not response.get("ok"):
        log("! %s" % response.get("error"))


def export_cases(session_dir, out_path):
    """Turn recorded screens into offline cases; fill reference_action by hand afterwards."""
    session_dir = Path(session_dir)
    cases = []
    for line in (session_dir / "steps.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        cases.append({"id": "%s-%04d" % (session_dir.name, row["step"]),
                      "state": row["description"] or row["state"],
                      "reference_action": None,
                      "live_action": row["decision"].get("action"),
                      "frame": row["frame"]})
    out_path = Path(out_path)
    existing = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else []
    seen = {case["id"] for case in existing}
    merged = existing + [case for case in cases if case["id"] not in seen]
    out_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return len(merged) - len(existing)
