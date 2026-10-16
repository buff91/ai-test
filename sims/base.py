"""Deterministic simulation games that know the right answer to their own questions.

A Sim is a small dataclass whose fields are all immutable (ints, strs, tuples), so
copy.copy is a full copy and the field values are a hashable search key. Because the
game owns every rule, it can compute the truth for each question it asks the model:

  action (choice)          actions whose depth-limited search value is the best
  danger (score)           0 safe / 1 threatened / 2 critical, by holding still for two steps
  incoming_attack (noul)   would the player lose health if it held still this step

The model never sees this truth; the socket service sends it beside the state and
the runner only uses it to grade answers.
"""

import copy


WIN, LOSS, DRAW = 1000.0, -1000.0, -500.0


class Sim:
    ACTIONS = ()
    HOLD = "wait"            # the "do nothing" action the danger/noul truths are defined by
    HORIZON = 6              # search depth (player actions) for the action truth
    MAX_VITAL = 100          # full health; a win keeps up to 10 turns' worth of value for it
    SCENARIOS = {}           # scenario id -> field values
    QUESTION_IDS = {"action": "action", "danger": "danger", "incoming": "incoming_attack"}
    outcome = "playing"

    @classmethod
    def start(cls, scenario=None):
        name = scenario or next(iter(cls.SCENARIOS))
        if name not in cls.SCENARIOS:
            raise ValueError("알 수 없는 시작 상황입니다: %s (가능: %s)" % (name, ", ".join(cls.SCENARIOS)))
        return cls(scenario=name, **cls.SCENARIOS[name])

    # Each game implements these.
    def snapshot(self):
        raise NotImplementedError

    def describe(self):
        raise NotImplementedError

    def step(self, action):
        """Apply one player action and the world's answer; return event strings."""
        raise NotImplementedError

    def vital(self):
        """The player's health; a drop is what danger and incoming_attack measure."""
        raise NotImplementedError

    def heuristic(self):
        """Value of an unfinished position at the search horizon (higher is better)."""
        raise NotImplementedError

    def legal(self):
        """Actions that do something now; the rest are wasted inputs the truth never names."""
        return self.ACTIONS

    # Shared machinery.
    def clone(self):
        return copy.copy(self)

    def key(self):
        return tuple(self.__dict__.values())

    def after(self, action):
        child = self.clone()
        child.step(action)
        return child

    def danger_truth(self):
        """2 if holding loses health or the game now, 1 if it does on the second hold, else 0."""
        start = self.vital()
        first = self.after(self.HOLD)
        if first.outcome == "lost" or first.vital() < start:
            return 2
        if first.outcome == "playing":
            second = first.after(self.HOLD)
            if second.outcome == "lost" or second.vital() < start:
                return 1
        return 0

    def incoming_truth(self):
        first = self.after(self.HOLD)
        return first.outcome == "lost" or first.vital() < self.vital()


def values(sim, horizon=None):
    """Search value of every legal action from this position."""
    memo = {}
    depth = (horizon or sim.HORIZON) - 1
    return {action: _value(sim.after(action), depth, memo) for action in sim.legal()}


def best_actions(sim, horizon=None):
    """The legal actions tied for the best value, or None when every action is equally good."""
    scores = values(sim, horizon)
    top = max(scores.values())
    best = [a for a in scores if scores[a] >= top - 1e-6]
    return None if len(best) == len(sim.ACTIONS) else best


def _value(sim, depth, memo):
    # A sooner win (more depth left) with more health left scores higher; a sooner loss lower.
    if sim.outcome == "won":
        return WIN + depth * 5 + 50 * sim.vital() / sim.MAX_VITAL
    if sim.outcome == "lost":
        return LOSS - depth
    if sim.outcome == "draw":
        return DRAW
    if depth == 0:
        return sim.heuristic()
    key = (sim.key(), depth)
    if key not in memo:
        memo[key] = max(_value(sim.after(a), depth - 1, memo) for a in sim.legal())
    return memo[key]


def truth(sim):
    """The game's own answer to each question it defines, keyed by question id."""
    if sim.outcome != "playing":
        return None
    ids = sim.QUESTION_IDS
    result = {}
    if ids.get("action"):
        result[ids["action"]] = best_actions(sim)
    if ids.get("danger"):
        result[ids["danger"]] = sim.danger_truth()
    if ids.get("incoming"):
        result[ids["incoming"]] = sim.incoming_truth()
    return result
