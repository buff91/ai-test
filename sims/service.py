"""Serve one Sim over the gamelab.protocol JSON-line socket, with the truth beside every state.

  {"op": "state"}                               -> {"ok", "snapshot", "description", "revision", "done", "truth"}
  {"op": "step", "action", "expected_revision"} -> the same, plus "before"
  {"op": "reset", "scenario"?}                  -> the next scenario (or the named one) from its start
"""

import math
import threading
import time

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
                    return {"ok": False, "stale": True, "error": "모델 판단 중 게임 상태가 바뀌었습니다.", **self._state()}
                if self.sim.outcome != "playing":
                    return {"ok": False, "error": "게임이 끝났습니다.", **self._state()}
                before = self.sim.snapshot()
                self.events = self.sim.step(action)
                self.revision += 1
                return {"ok": True, "before": before, **self._state()}
            return {"ok": False, "error": "알 수 없는 명령입니다: %s" % op, **self._state()}


class RealtimeService(SimService):
    """Shared live game host. Input changes controls; a separate clock moves the world.

    SimService remains the deterministic, single-step host used by offline case
    generation. Both visible and headless live games use this host instead.
    """

    def __init__(self, sim_cls, scenario=None, tick_ms=1000, version="sim-v1", game_id=None):
        if type(tick_ms) not in (int, float) or not math.isfinite(tick_ms) or not 100 <= tick_ms <= 10000:
            raise ValueError("tick_ms must be between 100 and 10000.")
        super().__init__(sim_cls, scenario)
        self.game_id = game_id or sim_cls.__module__.rsplit(".", 1)[-1]
        self.version = version
        self.continuous = hasattr(self.sim, "world_step")
        self.tick_seconds = tick_ms / 1000
        self.paused = False
        self.elapsed = self.accumulator = 0.0
        self.last_action = "none"
        self.input_sequence = 0
        self.episode_revision = 0
        self._clock_stop = threading.Event()
        self._clock_thread = None

    def render_state(self):
        with self.lock:
            return {"snapshot": {**self.sim.snapshot(), "paused": self.paused,
                                  "elapsed": round(self.elapsed, 3), "last_action": self.last_action,
                                  "input_ready": True, "input_sequence": self.input_sequence,
                                  "tick_ms": round(self.tick_seconds * 1000)},
                    "events": list(self.events), "revision": self.revision, "episode": self.episode,
                    "done": self.sim.outcome != "playing"}

    def advance(self, dt):
        if type(dt) not in (int, float) or not math.isfinite(dt) or dt < 0:
            raise ValueError("dt must be finite and nonnegative")
        with self.lock:
            if not self.continuous or self.paused or self.sim.outcome != "playing" or not dt:
                return []
            if hasattr(self.sim, "advance_time"):
                events = self.sim.advance_time(dt, tick_seconds=self.tick_seconds)
                self.elapsed += getattr(self.sim, "_realtime_advanced_seconds", dt)
            else:
                self.elapsed += dt
                self.accumulator += dt
                events = []
                while self.accumulator >= self.tick_seconds and self.sim.outcome == "playing":
                    self.accumulator -= self.tick_seconds
                    events += self.sim.world_step()
            self.revision += 1
            if events:
                self.events = events[-5:]
            return events

    def start_clock(self):
        """Headless host clock. Desktop rendering drives advance() on its UI thread."""
        if self._clock_thread is not None and self._clock_thread.is_alive():
            return
        self._clock_stop.clear()

        def run():
            previous = time.monotonic()
            while not self._clock_stop.wait(1 / 60):
                now = time.monotonic()
                self.advance(now - previous)
                previous = now

        self._clock_thread = threading.Thread(target=run, name="game-world-clock", daemon=True)
        self._clock_thread.start()

    def stop_clock(self):
        self._clock_stop.set()
        if self._clock_thread is not None:
            self._clock_thread.join(timeout=2)

    def _capture(self):
        # Capture under the lock, then search the reference outside it. Model
        # observations and offline reference searches must never freeze the clock.
        state = self.render_state()
        state["observed_at"] = time.time()
        reference = self.sim.realtime_reference() if hasattr(self.sim, "realtime_reference") else self.sim.clone()
        description = self.sim.describe_realtime() if self.continuous and hasattr(self.sim, "describe_realtime") else self.sim.describe()
        if self.continuous:
            description += (" Live game: time advances independently of input and model inference. "
                            "One game second takes %.0f ms. Elapsed %.2f seconds. Paused: %s. "
                            "Controls can be changed at any time; movement continues until stopped."
                            % (self.tick_seconds * 1000, self.elapsed, self.paused))
        else:
            description += " The battle waits for each input."
        return state, reference, description

    def _complete(self, captured):
        state, reference, description = captured
        key = reference.key()
        with self.lock:
            cached = self._truth
        answer = cached[1] if cached is not None and cached[0] == key else truth(reference)
        with self.lock:
            self._truth = (key, answer)
        return {**state, "description": description, "truth": answer,
                "reference_kind": "sampled_next_second_search" if self.continuous else "turn_search"}

    def _state(self):
        with self.lock:
            captured = self._capture()
        return self._complete(captured)

    def handle(self, request):
        extra = {"ok": True}
        with self.lock:
            op = request.get("op")
            if op == "info":
                return {"ok": True, "actions": list(self.sim.ACTIONS), "scenarios": list(self.sim.SCENARIOS),
                        "timing": "realtime" if self.continuous else "turn", "continuous": self.continuous,
                        "supports_pause": self.continuous, "tick_ms": self.tick_seconds * 1000,
                        "build": {"game": self.game_id, "version": self.version}}
            if op in ("pause", "resume", "halt"):
                paused = self.continuous and op != "resume"
                if self.paused != paused:
                    self.paused = paused
                    self.revision += 1
            elif op == "reset":
                names = list(self.sim.SCENARIOS)
                scenario = request.get("scenario") or names[(names.index(self.sim.scenario) + 1) % len(names)]
                if scenario not in self.sim.SCENARIOS:
                    extra = {"ok": False, "error": "Unknown scenario: %s" % scenario}
                else:
                    self.sim = self.sim_cls.start(scenario)
                    self.paused = False
                    self.elapsed = self.accumulator = 0.0
                    self.last_action = "none"
                    self.input_sequence = 0
                    self.episode += 1
                    self.revision += 1
                    self.episode_revision = self.revision
                    self.events = ["New scenario: " + scenario]
            elif op == "step":
                extra = self._input(request)
            elif op != "state":
                extra = {"ok": False, "error": "Unknown operation: %s" % op}
            captured = self._capture()
        if request.get("include_truth") is False:
            state, _, description = captured
            return {**state, "description": description, **extra}
        return {**self._complete(captured), **extra}

    def _input(self, request):
        action, expected = request.get("action"), request.get("expected_revision")
        if action not in self.sim.ACTIONS:
            return {"ok": False, "error": "Unknown action."}
        # A live observation can cross many frames during inference. Reject
        # decisions from a previous episode, while age bounds control lateness.
        if expected is not None:
            valid_revision = (type(expected) is int and
                              (self.episode_revision <= expected <= self.revision if self.continuous else expected == self.revision))
            if not valid_revision:
                return {"ok": False, "stale": True, "error": "Game state changed while the model decided."}
        observed_at, limit = request.get("observed_at"), request.get("max_age_ms")
        if observed_at is not None and limit is not None:
            if not all(type(x) in (int, float) and math.isfinite(x) for x in (observed_at, limit)) or limit < 0:
                return {"ok": False, "error": "Invalid observation age budget."}
            if (time.time() - observed_at) * 1000 > limit:
                return {"ok": False, "stale": True, "error": "Decision exceeded its age budget."}
        if self.sim.outcome != "playing":
            return {"ok": False, "error": "Game ended. Reset for another scenario."}
        if self.continuous and self.paused:
            return {"ok": False, "error": "Game paused. Resume before applying input."}
        before = self.sim.snapshot()
        if self.continuous:
            self.events = self.sim.realtime_input(action) if hasattr(self.sim, "realtime_input") else self.sim.apply(action)
        else:
            self.events = self.sim.step(action)
        self.last_action = action
        self.input_sequence += 1
        self.revision += 1
        return {"ok": True, "before": before}
