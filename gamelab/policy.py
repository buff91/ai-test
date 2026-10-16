"""Score/Noul answers steering control: guards and escalation from game.json "policy".

  "policy": {
    "guards": [{"name": "피격 직전", "when": "incoming_attack >= 0.7", "allow": ["dodge", "retreat"]}],
    "escalate": {"when": "danger >= 1.5", "to": "jev"}
  }

guard     when its condition holds and the chosen action is not in "allow", the action is
          replaced by the allowed option the model itself rated most likely (Choice probabilities).
escalate  when its condition holds, the "to" model is asked again and its answer is used.

Policy is off unless the run enables it (play.py run --policy). Off, it still records
which rules would have fired, so a run shows where Choice disagrees with Score/Noul.
"""

import re


CONDITION = re.compile(r"^\s*([A-Za-z_][\w]*)\s*(>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)\s*$")
OPS = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, "<": lambda a, b: a < b}
FIELDS = {"score": "score", "noul": "noul"}


def parse_condition(text, questions, where):
    match = CONDITION.match(text or "")
    if not match:
        raise ValueError("%s: 조건은 '질문id >= 숫자' 형식이어야 합니다: %r" % (where, text))
    qid, op, value = match.group(1), match.group(2), float(match.group(3))
    if questions.get(qid, {}).get("type") not in FIELDS:
        raise ValueError("%s: %s는 이 게임의 score/noul 질문이 아닙니다." % (where, qid))
    return qid, op, value


def validate(spec, questions, options, providers, where):
    if not spec:
        return
    if not isinstance(spec, dict) or set(spec) - {"guards", "escalate"}:
        raise ValueError("%s: policy에는 guards, escalate만 둘 수 있습니다." % where)
    for i, guard in enumerate(spec.get("guards", [])):
        here = "%s guards[%d]" % (where, i)
        parse_condition(guard.get("when"), questions, here)
        allow = guard.get("allow")
        if not isinstance(allow, list) or not allow or any(a not in options for a in allow):
            raise ValueError("%s: allow는 이 게임 선택지 중 하나 이상의 목록이어야 합니다." % here)
    escalate = spec.get("escalate")
    if escalate:
        parse_condition(escalate.get("when"), questions, "%s escalate" % where)
        if escalate.get("to") not in providers:
            raise ValueError("%s escalate: to는 %s 중 하나여야 합니다." % (where, ", ".join(providers)))


class Policy:
    def __init__(self, spec, questions, enabled=False, escalate_decider=None):
        spec = spec or {}
        self.enabled = enabled
        self.guards = [{**g, "name": g.get("name") or g["when"], "_when": parse_condition(g["when"], questions, "guard")}
                       for g in spec.get("guards", [])]
        self.escalate = spec.get("escalate")
        self.escalate_when = parse_condition(self.escalate["when"], questions, "escalate") if self.escalate else None
        self.escalate_decider = escalate_decider

    def __bool__(self):
        return bool(self.guards or self.escalate)

    def apply(self, decision, state):
        """(decision to act on, record of what fired)."""
        record = {"enabled": self.enabled, "fired": [], "override": None, "escalation": None}
        if decision.get("status") != "ok":
            return decision, record
        final = decision
        if self.escalate and holds(self.escalate_when, decision.get("answers")):
            record["fired"].append("escalate")
            if self.enabled and self.escalate_decider:
                second = self.escalate_decider.decide(state)
                record["escalation"] = {k: second.get(k) for k in ("provider", "status", "action", "model_ms", "error")}
                if second.get("status") == "ok":
                    final = {**second, "model_ms": decision["model_ms"] + second["model_ms"], "escalated_from": decision["provider"]}
        for guard in self.guards:
            if holds(guard["_when"], final.get("answers")) and final["action"] not in guard["allow"]:
                record["fired"].append(guard["name"])
                if self.enabled and record["override"] is None:
                    probabilities = final.get("probabilities") or {}
                    choice = max(guard["allow"], key=lambda a: (probabilities.get(a) or 0, -guard["allow"].index(a)))
                    record["override"] = {"rule": guard["name"], "from": final["action"], "to": choice}
                    final = {**final, "action": choice}
        return final, record


def holds(condition, answers):
    qid, op, value = condition
    answer = (answers or {}).get(qid) or {}
    observed = answer.get("score", answer.get("noul"))
    if isinstance(observed, bool) or not isinstance(observed, (int, float)):
        return False
    return OPS[op](observed, value)
