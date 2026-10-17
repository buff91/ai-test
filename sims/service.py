"""Serve one Sim over the gamelab.protocol JSON-line socket, with the truth beside every state.

  {"op": "state"}                               -> {"ok", "snapshot", "description", "revision", "done", "truth"}
  {"op": "step", "action", "expected_revision"} -> the same, plus "before"
  {"op": "reset", "scenario"?}                  -> the next scenario (or the named one) from its start
"""

import threading

from sims.base import truth


class SimService:
    def __init__(self, sim_cls, scenario=None):
        self.sim_cls = sim_cls
        self.lock = threading.RLock()
        self.revision = 0
        self.episode = 0
        self.events = []
        self._truth = None
        self.sim = sim_cls.start(scenario)

    def _state(self):
        if self._truth is None or self._truth[0] != self.revision:
            self._truth = (self.revision, truth(self.sim))
        return {"snapshot": self.sim.snapshot(), "description": self.sim.describe(), "revision": self.revision,
                "events": list(self.events), "done": self.sim.outcome != "playing", "episode": self.episode,
                "truth": self._truth[1]}

    def handle(self, request):
        with self.lock:
            op = request.get("op")
            if op == "state":
                return {"ok": True, **self._state()}
            if op == "info":
                return {"ok": True, "actions": list(self.sim_cls.ACTIONS), "scenarios": list(self.sim_cls.SCENARIOS),
                        "build": {"game": self.sim_cls.__module__.rsplit(".", 1)[-1], "version": "sim-v1"}}
            if op == "reset":
                names = list(self.sim_cls.SCENARIOS)
                scenario = request.get("scenario") or names[(names.index(self.sim.scenario) + 1) % len(names)]
                if scenario not in self.sim_cls.SCENARIOS:
                    return {"ok": False, "error": "알 수 없는 시작 상황입니다: %s" % scenario, **self._state()}
                self.sim = self.sim_cls.start(scenario)
                self.events = ["새 판을 시작했습니다: %s" % scenario]
                self.episode += 1
                self.revision += 1
                return {"ok": True, **self._state()}
            if op == "step":
                action = request.get("action")
                expected = request.get("expected_revision")
                if action not in self.sim_cls.ACTIONS:
                    return {"ok": False, "error": "알 수 없는 행동입니다: %s" % action, **self._state()}
                if expected is not None and expected != self.revision:
                    return {"ok": False, "error": "모델 판단 중 게임 상태가 바뀌었습니다.", **self._state()}
                if self.sim.outcome != "playing":
                    return {"ok": False, "error": "게임이 끝났습니다.", **self._state()}
                before = self.sim.snapshot()
                self.events = self.sim.step(action)
                self.revision += 1
                return {"ok": True, "before": before, **self._state()}
            return {"ok": False, "error": "알 수 없는 명령입니다: %s" % op, **self._state()}
