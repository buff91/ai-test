"""Run every game case's cases.json through each provider and summarize per game type.

The summary shows, for each type, how often each model matched reference_action and
how fast it answered, and which model fits the type's latency budget best. It does
not change config/game_types.json; that mapping stays a human decision.
"""

import statistics

from gamelab.decide import for_game


def run_matrix(games, providers, key=None, log=print):
    rows = []
    warmed = set()
    for game in games:
        cases = game.cases()
        for provider in providers:
            decider = for_game(provider, game, key)
            for case in cases:
                row = decider.decide(case["state"])
                # The first call per runtime includes model loading, so keep it out of latency stats.
                row.update({"game": game.id, "type": game.type, "case_id": case["id"],
                            "reference_action": case.get("reference_action"),
                            "warmup": provider not in warmed})
                warmed.add(provider)
                rows.append(row)
                log("%s %-10s %-18s %-8s %-16s %7.1f ms" % (
                    _mark(row), game.id, case["id"], provider,
                    row.get("action") or row.get("error", "")[:40], row["model_ms"]))
    return rows


def summarize(rows, types, games):
    """Per type: per-provider match rate / median latency, the configured live model, and a suggestion."""
    live_by_type = {}
    for game in games:
        live_by_type.setdefault(game.type, set()).add(game.providers["live"])
    summary = {}
    for type_id in sorted({row["type"] for row in rows}):
        spec = types[type_id]
        budget = spec["latency_budget_ms"]
        stats = {}
        for provider in sorted({row["provider"] for row in rows if row["type"] == type_id}):
            mine = [row for row in rows if row["type"] == type_id and row["provider"] == provider]
            ok = [row for row in mine if row["status"] == "ok"]
            judged = [row for row in ok if row["reference_action"] is not None]
            latencies = [row["model_ms"] for row in ok if not row["warmup"]] or [row["model_ms"] for row in ok]
            median = statistics.median(latencies) if latencies else None
            stats[provider] = {
                "cases": len(mine),
                "errors": len(mine) - len(ok),
                "judged": len(judged),
                "matches": sum(1 for row in judged if row["action"] == row["reference_action"]),
                "match_rate": (sum(1 for row in judged if row["action"] == row["reference_action"]) / len(judged)) if judged else None,
                "median_ms": round(median, 1) if median is not None else None,
                "within_budget": median is not None and median <= budget,
            }
        # A model that matched nothing is never suggested, however fast it is.
        fit = [p for p, s in stats.items() if s["errors"] == 0 and s["within_budget"] and s["match_rate"]]
        suggested = max(fit, key=lambda p: (stats[p]["match_rate"], -stats[p]["median_ms"])) if fit else None
        live = sorted(live_by_type.get(type_id, []))
        if any(p not in stats for p in live):
            verdict = "보류: 현재 live 모델(%s)을 이번에 측정하지 않음" % ", ".join(p for p in live if p not in stats)
        elif suggested is None:
            verdict = "추천 없음: 예산 안에서 오류 없이 하나라도 맞힌 모델이 없음"
        elif live == [suggested]:
            verdict = "설정과 같음"
        else:
            verdict = "설정 변경 검토"
        summary[type_id] = {"label": spec.get("label", type_id), "timing": spec["timing"], "budget_ms": budget,
                            "configured_live": live, "suggested_live": suggested, "verdict": verdict,
                            "agrees": verdict == "설정과 같음", "providers": stats}
    return summary


def to_markdown(summary):
    lines = ["# 게임 타입별 모델 비교", "",
             "일치율은 각 케이스의 임시 기대 행동(`reference_action`)과 같은 비율이며 정확도 증명이 아닙니다. "
             "지연은 첫 호출(모델 로딩)을 뺀 중간값입니다.", ""]
    for type_id, item in summary.items():
        lines += ["## %s (`%s`, 지연 예산 %dms)" % (item["label"], type_id, item["budget_ms"]), "",
                  "| 모델 | 일치 | 중간 지연 | 예산 안 | 오류 |", "|---|---|---|---|---|"]
        for provider, s in item["providers"].items():
            rate = "%d/%d" % (s["matches"], s["judged"]) if s["judged"] else "-"
            lines.append("| %s | %s | %s | %s | %d |" % (
                provider, rate, "%.1fms" % s["median_ms"] if s["median_ms"] is not None else "-",
                "예" if s["within_budget"] else "아니요", s["errors"]))
        lines += ["", "- 현재 live: %s / 측정 기준 추천: %s → **%s**" % (
            ", ".join(item["configured_live"]) or "-", item["suggested_live"] or "-", item["verdict"]), ""]
    return "\n".join(lines)


def _mark(row):
    if row["status"] != "ok":
        return "!"
    return "✓" if row["action"] == row["reference_action"] else "·"
