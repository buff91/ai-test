"""Live demonstrations warm local models before reset, then let the world run."""
import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gamelab.console import Lab, Probe
from gamelab.game_case import GameCase
from gamelab.socket_game import SocketGame
from sims.racer import RacerSim
from sims.service import RealtimeService


def eventually(check, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(.005)
    return False


class DemoDecider:
    def __init__(self, provider, game, calls, block=None, fail=False):
        self.provider, self.questions, self.calls = provider, game.questions, calls
        self.block, self.fail = block, fail
        self.entered = threading.Event()

    def decide(self, state):
        self.calls.append((self.provider, state))
        self.entered.set()
        if self.block:
            self.block.wait(3)
            self.block = None
        if self.fail:
            return {"provider": self.provider, "status": "error", "error": "checkpoint unavailable", "model_ms": 1}
        return {"provider": self.provider, "status": "ok", "action": "keep", "model_ms": 1,
                "answers": {"action": {"choice": "keep", "probabilities": {"keep": 1}}}}


class LiveDemonstrationTests(unittest.TestCase):
    def setUp(self):
        self.truth_patch = patch("sims.service.truth", return_value={})
        self.truth_patch.start()
        self.addCleanup(self.truth_patch.stop)
        self.inputs, self.models, self.commands = [], {}, []
        self.blocks, self.failures = {}, set()

        def make(provider, game, key):
            model = DemoDecider(provider, game, self.inputs, self.blocks.get(provider), provider in self.failures)
            self.models[provider] = model
            return model

        self.lab = Lab(make_decider=make, record=False)
        self.addCleanup(self.lab.close)
        self.lab.key = "test-only-key"
        self.lab.game = GameCase("racer", overrides={"interval_ms": 5, "max_age_ms": 1000})
        self.lab.options = {"provider": "laya", "shadows": [], "policy": False, "dry_run": False}
        self.service = RealtimeService(RacerSim, "long_straight")
        self.stop_after = 1
        self.act_count = 0

        def request(op, **kwargs):
            self.commands.append(op)
            response = self.service.handle({"op": op, **kwargs})
            if op == "step":
                self.act_count += 1
                if self.stop_after and self.act_count >= self.stop_after:
                    self.lab.stop_auto()
            return response

        socket = SocketGame(self.lab.game, SimpleNamespace(request=request))
        self.lab.client = Probe(socket, self.lab, self.lab.generation)
        self.lab.info = socket.request("info")
        self.lab.observed = socket.request("observe")
        self.lab.status_name = "connected"
        self.commands.clear()

    def complete(self):
        self.assertTrue(eventually(lambda: not self.lab.busy))

    def test_local_warmup_has_no_input_then_same_scenario_resets_before_live_decision(self):
        release = threading.Event()
        self.addCleanup(release.set)
        self.blocks["laya"] = release
        self.lab.run(auto=True, demonstration=True, shadows=["jev"])
        self.assertTrue(eventually(lambda: "laya" in self.models and self.models["laya"].entered.is_set()))
        self.assertEqual(self.lab.demonstration["status"], "warming")
        self.assertIn("게임 입력 없음", self.lab.phase["name"])
        self.assertNotIn("reset", self.commands)
        self.assertNotIn("step", self.commands)
        self.assertNotIn("pause", self.commands)
        self.service.advance(.25)
        self.assertGreater(self.service.render_state()["snapshot"]["realtime_position"], 20)
        release.set()
        self.complete()
        self.assertEqual(self.commands.count("reset"), 1)
        self.assertLess(self.commands.index("reset"), self.commands.index("step"))
        self.assertEqual(self.service.sim.scenario, "long_straight")
        self.assertEqual(self.service.elapsed, 0, "The live decision starts after the warm-up episode is reset")
        self.assertEqual(len(self.lab.rows), 1)
        self.assertTrue(self.lab.rows[0]["demonstration"])
        self.assertTrue(self.lab.rows[0]["applied"])
        warmups = [call for call in self.lab.calls if call["role"] == "warmup"]
        self.assertEqual([call["provider"] for call in warmups], ["laya"])
        self.assertEqual(len([provider for provider, _ in self.inputs if provider == "laya"]), 2)
        self.assertEqual(len([provider for provider, _ in self.inputs if provider == "jev"]), 1)
        self.assertTrue(any(item["timing_scope"] == "input_free_predict" for item in self.lab.preparations))
        self.assertNotIn(self.lab.key, json.dumps(json.loads(self.lab.export())))

    def test_stopping_during_warmup_prevents_reset_and_input(self):
        release = threading.Event()
        self.addCleanup(release.set)
        self.blocks["laya"] = release
        self.lab.run(auto=True, demonstration=True)
        self.assertTrue(eventually(lambda: "laya" in self.models and self.models["laya"].entered.is_set()))
        self.lab.stop_auto()
        release.set()
        self.complete()
        self.assertEqual(self.lab.demonstration["status"], "stopped")
        self.assertNotIn("reset", self.commands)
        self.assertNotIn("step", self.commands)
        self.assertEqual(self.lab.rows, [])

    def test_warmup_failure_is_visible_and_does_not_reset_or_send_input(self):
        self.failures.add("laya")
        self.lab.run(auto=True, demonstration=True)
        self.complete()
        self.assertEqual(self.lab.demonstration["status"], "error")
        self.assertIn("모델 예열 실패", self.lab.error)
        self.assertEqual(self.lab.preparations[-1]["status"], "error")
        self.assertNotIn("reset", self.commands)
        self.assertNotIn("step", self.commands)

    def test_jev_starts_with_one_live_call_and_no_extra_warmup_request(self):
        self.lab.run(auto=True, demonstration=True, provider="jev")
        self.complete()
        self.assertEqual([provider for provider, _ in self.inputs], ["jev"])
        self.assertEqual([call["role"] for call in self.lab.calls], ["live"])
        self.assertEqual(self.commands.count("reset"), 1)
        self.assertTrue(self.lab.rows[0]["applied"])

    def test_regular_one_shot_does_not_warmup_or_reset(self):
        self.lab.run()
        self.complete()
        self.assertEqual(len(self.inputs), 1)
        self.assertNotIn("reset", self.commands)
        self.assertFalse(self.lab.rows[0]["demonstration"])

    def test_slow_shadow_does_not_delay_live_actions_or_duplicate_pending_call(self):
        release = threading.Event()
        self.addCleanup(release.set)
        self.blocks["jev"] = release
        self.stop_after = 4
        self.lab.run(auto=True, demonstration=True, shadows=["jev"])
        self.complete()
        self.assertEqual(self.act_count, 4)
        self.assertEqual(len([provider for provider, _ in self.inputs if provider == "jev"]), 1)
        self.assertTrue(all(row["applied"] for row in self.lab.rows))
        pending = [call for call in self.lab.calls if call["provider"] == "jev"]
        self.assertEqual(len(pending), 1)
        self.assertIsNone(pending[0]["finished_at"])
        self.assertEqual(pending[0]["step"], 1)
        release.set()
        self.assertTrue(eventually(lambda: pending[0]["finished_at"] is not None))

    def test_comparison_keeps_world_running_without_reset_pause_or_input(self):
        release = threading.Event()
        self.addCleanup(release.set)
        self.blocks["jev"] = release
        original_options = dict(self.lab.options)
        self.lab.compare(providers=["jev", "laya"])
        self.assertTrue(eventually(lambda: "jev" in self.models and self.models["jev"].entered.is_set()))
        self.service.advance(.25)
        self.assertGreater(self.service.elapsed, 0)
        self.assertTrue(self.lab.busy)
        self.assertFalse(self.service.paused)
        status = self.lab.status()
        self.assertEqual(status["active_options"]["provider"], "jev")
        self.assertEqual(status["active_options"]["shadows"], ["laya"])
        self.assertTrue(status["active_options"]["comparison"])
        self.assertEqual(status["options"], original_options)
        release.set()
        self.complete()
        self.assertTrue(self.commands)
        self.assertEqual(set(self.commands), {"state"})
        self.assertTrue(self.lab.rows[0]["comparison"])
        self.assertFalse(self.lab.rows[0]["applied"])

    def test_pending_call_survives_fast_primary_history_and_status_limits(self):
        pending = self.lab._call_started(self.lab.generation, "shadow", SimpleNamespace(provider="jev"),
                                         "old scene", step=1, episode=0)
        for step in range(2, 65):
            call = self.lab._call_started(self.lab.generation, "live", SimpleNamespace(provider="laya"),
                                          "new scene", step=step, episode=0)
            self.lab._call_finished(self.lab.generation, call, {"status": "ok"})
        self.assertIn(pending, self.lab.calls)
        self.assertIn(pending, self.lab.status()["calls"])
        self.assertLessEqual(len(self.lab.calls), 41)
        self.lab._call_finished(self.lab.generation, pending, {"status": "ok", "action": "brake", "runtime_wait_ms": 7})
        visible = self.lab.status()["calls"]
        self.assertIn(pending, visible, "A late answer remains visible after it stops being pending")
        self.assertEqual(visible[0]["id"], pending["id"])
        self.assertEqual(visible[0]["step"], 1)
        self.assertEqual(visible[0]["runtime_wait_ms"], 7)

    def test_pending_shadow_is_not_duplicated_after_stop_and_new_run(self):
        release = threading.Event()
        self.addCleanup(release.set)
        self.blocks["jev"] = release
        self.lab.run(auto=True, shadows=["jev"])
        self.complete()
        self.assertTrue(eventually(lambda: any(provider == "jev" for provider, _ in self.inputs)))
        tracker = self.lab.shadow_tracker
        pending = next(call for call in self.lab.calls if call["provider"] == "jev")
        self.lab.run(auto=True, shadows=["jev"])
        self.complete()
        self.assertIs(self.lab.shadow_tracker, tracker)
        self.assertEqual(len([provider for provider, _ in self.inputs if provider == "jev"]), 1)
        self.assertEqual(self.act_count, 2)
        self.assertTrue(self.lab.rows[0]["applied"])
        release.set()
        self.assertTrue(eventually(lambda: pending["finished_at"] is not None))


if __name__ == "__main__":
    unittest.main()
