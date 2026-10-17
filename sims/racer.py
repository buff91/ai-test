"""realtime.racing: a three-lane track with speed-limited corners and lane obstacles."""

from dataclasses import dataclass
import math

from sims.base import Sim


LENGTH, MAX_SPEED, BOOST_SPEED, LOOKAHEAD = 40, 4, 5, 12
LANES = ("left", "center", "right")


@dataclass
class RacerSim(Sim):
    ACTIONS = ("accelerate", "brake", "steer_left", "steer_right", "boost", "keep")
    HOLD = "keep"
    HORIZON = 7
    MAX_VITAL = 3
    QUESTION_IDS = {"action": "action", "danger": "danger", "incoming": "crash_ahead"}
    SCENARIOS = {
        "hairpin_fast": {"pos": 10, "speed": 4, "corners": ((15, 18, 2),)},
        "long_straight": {"pos": 20, "speed": 3, "boost": 1, "corners": ((8, 12, 2),)},
        "obstacle_left": {"pos": 6, "speed": 3, "lane": 0, "obstacles": ((9, 0),)},
        "obstacle_right": {"pos": 6, "speed": 3, "lane": 2, "obstacles": ((10, 2), (10, 1))},
        "corner_exit": {"pos": 18, "speed": 1, "corners": ((12, 17, 2), (34, 37, 2))},
    }

    scenario: str = "hairpin_fast"
    pos: int = 0
    lane: int = 1
    speed: int = 2
    boost: int = 0
    integrity: int = 3
    corners: tuple = ()     # (first cell, last cell, max speed)
    obstacles: tuple = ()   # (cell, lane)
    turn: int = 1
    outcome: str = "playing"
    _realtime_position: float | None = None
    _realtime_elapsed: float = 0.0
    _realtime_advanced_seconds: float = 0.0
    _realtime_steer_cooldown: float = 0.0
    _realtime_throttle_cooldown: float = 0.0

    def snapshot(self):
        state = {"scenario": self.scenario, "turn": self.turn, "position": self.pos, "lane": LANES[self.lane],
                "speed": self.speed, "boost": self.boost, "integrity": self.integrity,
                "corners": [dict(zip(("from", "to", "max_speed"), c)) for c in self.corners],
                "obstacles": [{"cell": c, "lane": LANES[l]} for c, l in self.obstacles], "outcome": self.outcome}
        if self._realtime_position is not None:
            state["realtime_position"] = round(self._realtime_position, 4)
        return state

    def describe(self):
        ahead = []
        for first, last, limit in self.corners:
            if last > self.pos and first - self.pos <= LOOKAHEAD:
                ahead.append("a corner over cells %d-%d that must be taken at speed %d or less%s"
                             % (first, last, limit, " (the car is in it now)" if first <= self.pos else " (%d cells ahead)" % (first - self.pos)))
        for cell, lane in self.obstacles:
            if 0 < cell - self.pos <= LOOKAHEAD:
                ahead.append("an obstacle in the %s lane at cell %d (%d cells ahead)" % (LANES[lane], cell, cell - self.pos))
        return ("Race clock beat %d. The car is at cell %d of %d in the %s lane, speed %d (normal max %d, boost reaches %d), "
                "boost charges %d, integrity %d of 3. Speed is cells per clock interval. Ahead: %s. "
                "Driving through an obstacle in the car's lane, or through a corner cell faster than its limit, crashes: "
                "integrity drops by 1 and speed falls to 1. accelerate and brake change speed by 1, steer moves one lane, "
                "boost adds 2 speed, keep changes nothing. Choose the single best input now."
                % (self.turn, self.pos, LENGTH, LANES[self.lane], self.speed, MAX_SPEED, BOOST_SPEED, self.boost,
                   self.integrity, "; ".join(ahead) or "a clear track"))

    def describe_realtime(self):
        position = self._realtime_position if self._realtime_position is not None else float(self.pos)
        return self.describe() + (" Continuous track position %.2f. The car keeps moving without input. "
            "Throttle and steering change immediately with independent 0.12s control cooldowns; keep preserves current settings."
            % position)

    def realtime_input(self, action):
        """Change driving controls immediately without advancing the race clock."""
        if action not in self.ACTIONS:
            raise ValueError("Unknown action: %s" % action)
        if self.outcome != "playing":
            return []
        if self._realtime_position is None:
            self._realtime_position = float(self.pos)
        timer = ("_realtime_steer_cooldown" if action in ("steer_left", "steer_right") else
                 "_realtime_throttle_cooldown" if action in ("accelerate", "brake") else None)
        if timer and getattr(self, timer) > 1e-9:
            return []
        if timer:
            setattr(self, timer, .12)
        return self.apply(action)

    def advance_time(self, dt, tick_seconds=1.0):
        """Move continuously, checking every crossed cell even after a long frame.

        The discrete world_step remains the deterministic offline search model.
        Realtime motion keeps a separate fractional position for the renderer and
        publishes the last crossed integer cell to the existing HUD protocol.
        """
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (dt, tick_seconds)) or dt < 0 or tick_seconds <= 0:
            raise ValueError("dt must be nonnegative and tick_seconds must be positive and finite")
        if self._realtime_position is None:
            self._realtime_position = float(self.pos)
        self._realtime_advanced_seconds = 0.0
        if self.outcome != "playing":
            return []
        remaining = min(dt, max(0.0, 30 * tick_seconds - self._realtime_elapsed))
        events = []
        while remaining > 1e-9 and self.outcome == "playing":
            cell = int(math.floor(self._realtime_position + 1e-9)) + 1
            crossing_time = (cell - self._realtime_position) * tick_seconds / self.speed
            if remaining + 1e-9 < crossing_time:
                self._realtime_position += self.speed * remaining / tick_seconds
                self._realtime_advanced_seconds += remaining
                break
            self._realtime_advanced_seconds += crossing_time
            remaining = max(0.0, remaining - crossing_time)
            self._realtime_position = float(cell)
            self.pos = cell
            collision = None
            if (cell, self.lane) in self.obstacles:
                collision = "장애물에 부딪혔습니다."
            elif any(first <= cell <= last and self.speed > limit for first, last, limit in self.corners):
                collision = "코너에서 미끄러졌습니다."
            if collision:
                self.integrity -= 1
                self.speed = 1
                events.append(collision)
            if self.integrity <= 0:
                self.outcome = "lost"
            elif self.pos >= LENGTH:
                self.pos = LENGTH
                self._realtime_position = float(LENGTH)
                self.outcome = "won"
        self._realtime_elapsed += self._realtime_advanced_seconds
        self.turn = 1 + int((self._realtime_elapsed + 1e-9) / tick_seconds)
        self._realtime_steer_cooldown = max(0.0, self._realtime_steer_cooldown - self._realtime_advanced_seconds)
        self._realtime_throttle_cooldown = max(0.0, self._realtime_throttle_cooldown - self._realtime_advanced_seconds)
        if self.outcome == "playing" and self.turn > 30:
            self.outcome = "draw"
        return events

    def realtime_reference(self):
        """Return a discrete search sample without live clock or control timers."""
        reference = self.clone()
        reference._realtime_position = None
        reference._realtime_elapsed = 0.0
        reference._realtime_advanced_seconds = 0.0
        reference._realtime_steer_cooldown = reference._realtime_throttle_cooldown = 0.0
        return reference

    def apply(self, action):
        if self.outcome != "playing":
            return ["게임이 끝났습니다."]
        if action not in self.ACTIONS:
            raise ValueError("Unknown action: %s" % action)
        if action == "accelerate":
            self.speed = min(MAX_SPEED, self.speed + 1) if self.speed <= MAX_SPEED else self.speed
        elif action == "brake":
            self.speed = max(1, self.speed - 1)
        elif action == "steer_left":
            self.lane = max(0, self.lane - 1)
        elif action == "steer_right":
            self.lane = min(2, self.lane + 1)
        elif action == "boost" and self.boost > 0:
            self.boost -= 1
            self.speed = min(BOOST_SPEED, self.speed + 2)
        return []

    def world_step(self):
        if self.outcome != "playing":
            return []
        events = []
        crash = None
        for cell in range(self.pos + 1, self.pos + self.speed + 1):
            if (cell, self.lane) in self.obstacles:
                crash = (cell, "장애물에 부딪혔습니다.")
                break
            if any(first <= cell <= last and self.speed > limit for first, last, limit in self.corners):
                crash = (cell, "코너에서 미끄러졌습니다.")
                break
        if crash:
            self.pos = crash[0]
            self.integrity -= 1
            self.speed = 1
            events.append(crash[1])
        else:
            self.pos += self.speed
        self.turn += 1
        if self.integrity == 0:
            self.outcome = "lost"
        elif self.pos >= LENGTH:
            self.outcome = "won"
        elif self.turn > 30:
            self.outcome = "draw"
        return events

    def step(self, action):
        return self.apply(action) + self.world_step()

    def legal(self):
        usable = {"accelerate": self.speed < MAX_SPEED, "brake": self.speed > 1, "steer_left": self.lane > 0,
                  "steer_right": self.lane < 2, "boost": self.boost > 0}
        return tuple(a for a in self.ACTIONS if usable.get(a, True))

    def vital(self):
        return self.integrity

    def heuristic(self):
        return self.pos * 3 + self.integrity * 15 + self.speed + self.boost * 2
