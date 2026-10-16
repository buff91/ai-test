"""Grade model answers against a truth, per question type, and summarize many grades.

truth is keyed by question id, as a game (sims/base.py) or a case in cases.json gives it:
  choice  list of acceptable options (or one option)   -> correct
  score   the right level, 0 .. len(criteria) - 1      -> abs_error, exact (rounded answer == truth)
  noul    bool, or a probability 0..1                  -> brier = (answer - truth)^2, correct (same side of 0.5)
A question missing from the truth (or set to null) is not graded.
"""

import math


def validate_truth(truth, questions, where):
    if truth is None:
        return
    if not isinstance(truth, dict):
        raise ValueError("%s: truth는 질문 id별 값의 객체여야 합니다." % where)
    for qid, value in truth.items():
        if qid not in questions:
            raise ValueError("%s: truth의 %s는 questions.json에 없는 질문입니다." % (where, qid))
        if value is None:
            continue
        kind = questions[qid]["type"]
        if kind == "choice":
            options = value if isinstance(value, list) else [value]
            if not options or any(o not in questions[qid]["criteria"] for o in options):
                raise ValueError("%s: truth.%s의 행동이 선택지에 없습니다: %s" % (where, qid, value))
        elif kind == "score":
            if not _number(value) or not 0 <= value <= len(questions[qid]["criteria"]) - 1:
                raise ValueError("%s: truth.%s는 0..%d 사이 숫자여야 합니다." % (where, qid, len(questions[qid]["criteria"]) - 1))
        elif kind == "noul":
            if not isinstance(value, bool) and not (_number(value) and 0 <= value <= 1):
                raise ValueError("%s: truth.%s는 true/false 또는 0..1 확률이어야 합니다." % (where, qid))


def grade(answers, truth, questions):
    """{question id: grade} for every question the truth answers."""
    graded = {}
    for qid, question in questions.items():
        expected = (truth or {}).get(qid)
        if expected is None:
            continue
        kind = question["type"]
        answer = (answers or {}).get(qid) or {}
        value = answer.get({"choice": "choice", "score": "score", "noul": "noul"}[kind])
        item = {"type": kind, "answer": value, "truth": expected}
        if kind == "choice":
            allowed = expected if isinstance(expected, list) else [expected]
            item.update(truth=allowed, correct=value in allowed) if value is not None else item.update(status="missing")
        elif kind == "score":
            top = len(question["criteria"]) - 1
            if _number(value) and 0 <= value <= top:
                item.update(abs_error=round(abs(value - expected), 4), exact=round(value) == round(expected))
            else:
                item["status"] = "invalid" if value is not None else "missing"
        else:
            target = float(expected)
            if _number(value) and 0 <= value <= 1:
                item.update(brier=round((value - target) ** 2, 4), correct=(value >= 0.5) == (target >= 0.5))
            else:
                item["status"] = "invalid" if value is not None else "missing"
        graded[qid] = item
    return graded


def summarize(grades):
    """Aggregate a list of grade() results into one line per question."""
    summary = {}
    for graded in grades:
        for qid, item in (graded or {}).items():
            s = summary.setdefault(qid, {"type": item["type"], "n": 0, "unusable": 0, "_values": []})
            if item.get("status"):
                s["unusable"] += 1
                continue
            s["n"] += 1
            s["_values"].append(item)
    for s in summary.values():
        items = s.pop("_values")
        if not items:
            continue
        if s["type"] == "choice":
            s["accuracy"] = _mean(i["correct"] for i in items)
        elif s["type"] == "score":
            s["mae"] = _mean(i["abs_error"] for i in items)
            s["exact"] = _mean(i["exact"] for i in items)
        else:
            s["brier"] = _mean(i["brier"] for i in items)
            s["accuracy"] = _mean(i["correct"] for i in items)
            s["mean_answer"] = _mean(i["answer"] for i in items)
            s["base_rate"] = _mean(float(i["truth"]) for i in items)
    return summary


def marks(graded):
    """Short log text such as 'action✓ danger 1.8/2 incoming_attack 0.90/hit'."""
    parts = []
    for qid, item in (graded or {}).items():
        if item.get("status"):
            parts.append("%s:%s" % (qid, item["status"]))
        elif item["type"] == "choice":
            parts.append("%s%s" % (qid, "✓" if item["correct"] else "✗(%s)" % "/".join(item["truth"])))
        elif item["type"] == "score":
            parts.append("%s %.1f/%g" % (qid, item["answer"], item["truth"]))
        else:
            truth = item["truth"]
            label = ("hit" if truth else "miss") if isinstance(truth, bool) else "%.2f" % truth
            parts.append("%s %.2f/%s" % (qid, item["answer"], label))
    return " ".join(parts)


def format_summary(summary):
    lines = []
    for qid, s in summary.items():
        if not s["n"]:
            lines.append("%-16s 채점 가능한 답 없음 (%d개 무효)" % (qid, s["unusable"]))
        elif s["type"] == "choice":
            lines.append("%-16s choice  정답률 %5.1f%%  (n=%d)" % (qid, s["accuracy"] * 100, s["n"]))
        elif s["type"] == "score":
            lines.append("%-16s score   평균오차 %.2f  단계일치 %5.1f%%  (n=%d)" % (qid, s["mae"], s["exact"] * 100, s["n"]))
        else:
            lines.append("%-16s noul    Brier %.3f  정답률 %5.1f%%  평균예측 %.2f / 실제비율 %.2f  (n=%d)"
                         % (qid, s["brier"], s["accuracy"] * 100, s["mean_answer"], s["base_rate"], s["n"]))
        if s["n"] and s["unusable"]:
            lines[-1] += "  무효 %d" % s["unusable"]
    return "\n".join(lines)


def _number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _mean(values):
    values = [float(v) for v in values]
    return round(sum(values) / len(values), 4) if values else None
