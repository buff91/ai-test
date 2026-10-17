"""A live game's clock and controls are independent of observation and inference."""
import threading
import time
import unittest
from unittest.mock import patch

from gamelab.game_case import GameCase
from sims.moba import MobaSim
from sims.party import PartySim
from sims.racer import RacerSim
from sims.service import RealtimeService


@patch("sims.service.truth", return_value={})
class RealtimeServiceTests(unittest.TestCase):
    def test_both_games_run_immediately_and_after_reset_without_input(self, _truth):
        for cls in (RacerSim, MobaSim):
            with self.subTest(game=cls.__name__):
                service = RealtimeService(cls)
                self.assertFalse(service.paused)
                service.advance(.25)
                self.assertGreater(service.elapsed, 0)
                service.advance(.8)
                self.assertEqual(service.sim.turn, 2)
                response = service.handle({"op": "reset"})
                self.assertFalse(response["snapshot"]["paused"])
                service.advance(1.1)
                self.assertEqual(service.sim.turn, 2)

    def test_inputs_change_controls_without_advancing_or_locking_world(self, _truth):
        service = RealtimeService(RacerSim, "long_straight")
        for action in ("brake", "steer_left", "boost"):
            result = service.handle({"op": "step", "action": action})
            self.assertTrue(result["ok"], result)
        self.assertEqual(service.sim.turn, 1)
        self.assertEqual(service.sim.lane, 0)
        self.assertEqual(service.sim.speed, 4)
        self.assertEqual(service.input_sequence, 3)
        service.advance(.1)
        self.assertGreater(service.sim.snapshot()["realtime_position"], 20)

    def test_fresh_decisions_can_cross_frames_but_not_episode_boundaries(self, _truth):
        service = RealtimeService(RacerSim)
        revision = service.handle({"op": "state"})["revision"]
        service.advance(.1)
        request = {"op": "step", "action": "brake", "expected_revision": revision,
                   "observed_at": time.time(), "max_age_ms": 1000}
        self.assertTrue(service.handle(request)["ok"])
        service.handle({"op": "reset"})
        self.assertTrue(service.handle(request)["stale"])

    def test_expired_input_and_paused_input_do_not_apply(self, _truth):
        service = RealtimeService(RacerSim)
        result = service.handle({"op": "step", "action": "brake", "observed_at": time.time()-2,
                                 "max_age_ms": 100})
        self.assertTrue(result["stale"])
        self.assertEqual(service.sim.speed, 4)
        service.handle({"op": "pause"})
        before = service.render_state()
        service.advance(10)
        self.assertEqual(before, service.render_state())
        self.assertFalse(service.handle({"op": "step", "action": "brake"})["ok"])
        service.handle({"op": "resume"})
        service.advance(.1)
        self.assertGreater(service.elapsed, 0)

    def test_turn_game_still_waits_for_input(self, _truth):
        service = RealtimeService(PartySim)
        before = service.render_state()
        service.advance(10)
        self.assertEqual(before, service.render_state())
        self.assertFalse(service.handle({"op": "info"})["continuous"])
        self.assertTrue(service.handle({"op": "step", "action": "guard"})["ok"])
        self.assertEqual(service.sim.turn, 2)

    def test_local_ui_input_does_not_compute_search_reference(self, reference):
        service = RealtimeService(RacerSim)
        result = service.handle({"op": "step", "action": "brake", "include_truth": False})
        self.assertTrue(result["ok"])
        reference.assert_not_called()


class IndependentClockTests(unittest.TestCase):
    def test_slow_reference_search_does_not_hold_the_world_lock(self):
        service = RealtimeService(RacerSim, "long_straight")
        entered, release = threading.Event(), threading.Event()
        def slow_reference(_sim):
            entered.set()
            release.wait(2)
            return {}
        with patch("sims.service.truth", side_effect=slow_reference):
            reader = threading.Thread(target=lambda: service.handle({"op": "state"}))
            service.start_clock()
            reader.start()
            try:
                self.assertTrue(entered.wait(1))
                before = service.render_state()["snapshot"]
                deadline = time.monotonic() + 1
                while service.elapsed <= before["elapsed"] + .04 and time.monotonic() < deadline:
                    time.sleep(.01)
                after = service.render_state()["snapshot"]
                self.assertGreater(after["elapsed"], before["elapsed"])
                self.assertGreater(after["realtime_position"], before.get("realtime_position", before["position"]))
            finally:
                release.set()
                reader.join(2)
                service.stop_clock()

    def test_real_headless_process_advances_without_any_game_input(self):
        for game in ("racer", "moba"):
            with self.subTest(game=game), GameCase(game).connect() as client:
                first = client.request("observe")
                deadline = time.monotonic() + 3
                second = first
                while second["state"]["turn"] == first["state"]["turn"] and time.monotonic() < deadline:
                    time.sleep(.05)
                    second = client.request("observe")
                self.assertGreater(second["state"]["elapsed"], first["state"]["elapsed"])
                self.assertGreater(second["state"]["turn"], first["state"]["turn"])
                self.assertFalse(second["state"]["paused"])
                self.assertTrue(client.request("info")["continuous"])


if __name__ == "__main__":
    unittest.main()
