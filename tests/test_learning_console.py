"""Teaching comparisons use one observation, retain both answers, and never send input."""

import json
import threading
import time
import unittest
from unittest.mock import patch

from gamelab.decide import Decider, _ROUTERS
from gamelab.game_case import GameCase
from gamelab.runner import summarize_rows, tick
from web_console import Lab, Probe


class Observations:
    def __init__(self):
        self.commands = []

    def request(self, op, **kwargs):
        self.commands.append(op)
        if op == "observe":
            return {"ok": True, "state": {"lane": 1}, "description": "Same scene for both models.",
                    "signature": 1, "observed_at": time.time(), "truth": None}
        raise AssertionError("Analysis sent a game command: %s" % op)


class FakeDecider:
    def __init__(self, provider, game, inputs, wait_for=None):
        self.provider, self.questions = provider, game.questions
        self.action = next(iter(game.options))
        self.inputs, self.wait_for = inputs, wait_for
        self.router_cached = provider != "jev"

    def decide(self, state):
        self.inputs.append((self.provider, state, self.questions))
        if self.wait_for:
            self.wait_for.wait(3)
        return {"provider": self.provider, "status": "ok", "action": self.action, "model_ms": 2,
                "answers": {"action": {"choice": self.action, "probabilities": {self.action: 1}}}}


def eventually(check, timeout=3):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if check():
            return True
        time.sleep(.005)
    return False


class LearningConsoleTests(unittest.TestCase):
    def setUp(self):
        self.inputs = []
        self.lab = Lab(make_decider=lambda p, g, k: FakeDecider(p, g, self.inputs), record=False)
        self.lab.key = "test-only-key"
        self.lab.game = GameCase("racer")
        self.lab.game.config["interval_ms"] = 5
        self.lab.status_name = "connected"
        self.lab.options = {"provider": "laya-mlx", "shadows": [], "policy": True,
                            "decision_delay_ms": 3000, "dry_run": False}
        self.client = Observations()
        self.lab.client = Probe(self.client, self.lab, self.lab.generation)

    def tearDown(self):
        self.lab.close()

    def completed(self):
        self.assertTrue(eventually(lambda: not self.lab.busy))
        return self.lab.rows[0]

    def test_compare_preserves_settings_and_links_one_request_to_both_answers(self):
        original = dict(self.lab.options)
        self.lab.compare()
        row = self.completed()
        self.assertEqual(self.lab.options, original)
        self.assertEqual(self.client.commands, ["observe"])
        self.assertEqual(len(self.inputs), 2)
        self.assertEqual(self.inputs[0][1:], self.inputs[1][1:])
        self.assertEqual(row["request"], {"state": self.inputs[0][1], "questions": self.lab.game.questions})
        self.assertTrue(row["comparison"])
        self.assertTrue(row["analysis_only"])
        self.assertFalse(row["applied"])
        self.assertEqual(row["decision_delay_ms"], 0)
        self.assertEqual(row["policy"]["fired"], [])
        calls = {call["id"]: call for call in self.lab.calls}
        for decision in [row["decision"], *row["shadow"]]:
            call = calls[decision["call_id"]]
            self.assertEqual(call["step"], row["step"])
            self.assertTrue(call["comparison"])
            self.assertEqual(call["episode"], row["episode"])
            self.assertEqual({k: call["request"][k] for k in ("state", "questions")}, row["request"])
        exported = json.loads(self.lab.export())
        self.assertEqual(exported["rows"][0]["comparison"], True)
        self.assertEqual(len(exported["preparations"]), 2)
        self.assertEqual(exported["summary"]["comparisons"]["steps"], 1)
        self.assertEqual(exported["summary"]["comparisons"]["providers"]["laya"]["count"], 1)
        self.assertNotIn(self.lab.key, self.lab.export().decode())

    def test_realtime_comparison_waits_for_slow_second_answer(self):
        release = threading.Event()
        self.addCleanup(release.set)
        self.lab.make_decider = lambda p, g, k: FakeDecider(p, g, self.inputs, release if p == "jev" else None)
        self.lab.compare()
        self.assertTrue(eventually(lambda: len(self.inputs) == 2))
        time.sleep(.03)  # Well beyond this game's 5 ms shadow budget.
        self.assertTrue(self.lab.busy)
        self.assertEqual(self.lab.activity, "compare")
        with self.assertRaisesRegex(ValueError, "판단 중"):
            self.lab.run()
        release.set()
        row = self.completed()
        self.assertEqual(row["shadow"][0]["status"], "ok")
        self.assertEqual(self.client.commands, ["observe"])

    def test_missing_jev_key_is_rejected_before_work_starts(self):
        self.lab.key = ""
        with self.assertRaisesRegex(ValueError, "Jev API 키"):
            self.lab.compare(providers=["laya", "jev"])
        self.assertFalse(self.lab.busy)
        self.assertEqual(self.lab.calls, [])
        self.assertEqual(self.client.commands, [])
        self.lab.compare(providers=["laya", "laya-mlx"])
        self.assertEqual(self.completed()["shadow"][0]["provider"], "laya-mlx")

    def test_initialization_failure_keeps_other_answer_and_redacts_key(self):
        def make(provider, game, key):
            if provider == "jev":
                raise RuntimeError("bad credentials " + key)
            return FakeDecider(provider, game, self.inputs)
        self.lab.make_decider = make
        self.lab.compare()
        row = self.completed()
        self.assertEqual(row["decision"]["status"], "ok")
        self.assertEqual(row["shadow"][0]["status"], "error")
        self.assertEqual(row["preparations"][1]["status"], "error")
        self.assertIsNotNone(self.lab.error)
        self.assertNotIn(self.lab.key, self.lab.export().decode())

    def test_stop_during_comparison_keeps_results_without_input(self):
        release = threading.Event()
        self.addCleanup(release.set)
        self.lab.make_decider = lambda p, g, k: FakeDecider(p, g, self.inputs, release if p == "jev" else None)
        self.lab.compare()
        self.assertTrue(eventually(lambda: len(self.inputs) == 2))
        self.lab.stop_auto()
        release.set()
        row = self.completed()
        self.assertTrue(row["cancelled"])
        self.assertFalse(row["applied"])
        self.assertEqual(self.client.commands, ["observe"])

    def test_prediction_exception_keeps_both_model_call_records(self):
        class Failing(FakeDecider):
            def decide(inner, state):
                if inner.provider == "jev":
                    raise RuntimeError("provider unavailable")
                return super(Failing, inner).decide(state)
        self.lab.make_decider = lambda p, g, k: Failing(p, g, self.inputs)
        self.lab.compare()
        row = self.completed()
        self.assertEqual(row["decision"]["status"], "ok")
        self.assertEqual(row["shadow"][0]["status"], "error")
        self.assertEqual(len(self.lab.calls), 2)
        self.assertTrue(all(call["finished_at"] for call in self.lab.calls))
        self.assertEqual(self.client.commands, ["observe"])

    def test_disconnect_drops_old_comparison_results(self):
        release, finished = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        class Delayed(FakeDecider):
            def decide(inner, state):
                result = super(Delayed, inner).decide(state)
                finished.set()
                return result
        self.lab.make_decider = lambda p, g, k: Delayed(p, g, self.inputs, release)
        self.lab.compare()
        self.assertTrue(eventually(lambda: len(self.inputs) == 2))
        generation = self.lab.generation
        self.lab.disconnect()
        release.set()
        self.assertTrue(finished.wait(2))
        time.sleep(.03)
        self.assertGreater(self.lab.generation, generation)
        self.assertEqual(self.lab.rows, [])
        self.assertEqual(self.lab.calls, [])
        self.assertEqual(self.lab.preparations, [])
        self.assertEqual(self.lab.status_name, "disconnected")

    def test_analysis_never_sends_pause_even_on_pause_capable_interface(self):
        game = self.lab.game
        row = tick(self.client, FakeDecider("laya", game, self.inputs), game.config,
                   pause=True, apply=False, questions=game.questions)["row"]
        self.assertTrue(row["analysis_only"])
        self.assertEqual(self.client.commands, ["observe"])


class PreparationTimingTests(unittest.TestCase):
    def test_router_initialization_and_lazy_predict_time_remain_separate(self):
        class Router:
            loaded = []
            def predict(self, state, questions, **kwargs):
                self.loaded = ["english"]
                return {"model": "laya-rl-agent", "routing": {"model": "english", "method": "forced"},
                        "usage": {"truncated": True}, "answers": {"action": {"choice": "wait"}}}
        questions = {"action": {"type": "choice", "criteria": {"wait": "Wait"}}}
        with patch.dict(_ROUTERS, {}, clear=True), patch("gamelab.decide.make_laya_router", return_value=Router()), \
                patch("gamelab.decide.time.perf_counter", side_effect=[0, .04, 1, 1.012, 2, 2.001, 3, 3.004]):
            first = Decider("laya", questions, laya_model="english")
            first_answer = first.decide("scene")
            second = Decider("laya", questions, laya_model="english")
            second_answer = second.decide("scene")
        self.assertEqual(first.preparation_ms, 40)
        self.assertFalse(first.router_cached)
        self.assertEqual(first_answer["model_ms"], 12)
        self.assertEqual(first_answer["model_ms_scope"], "predict_including_lazy_load")
        self.assertFalse(first_answer["checkpoint_cached"])
        self.assertEqual(first_answer["model"], "laya-rl-agent")
        self.assertEqual(first_answer["checkpoint"], "english")
        self.assertEqual(first_answer["routing"], {"model": "english", "method": "forced"})
        self.assertTrue(first_answer["usage"]["truncated"])
        self.assertTrue(second.router_cached)
        self.assertTrue(second_answer["checkpoint_cached"])
        self.assertEqual(second.preparation_ms, 1)
        self.assertEqual(second_answer["model_ms"], 4)


class ComparisonSummaryTests(unittest.TestCase):
    def test_mixed_control_and_comparison_results_have_separate_aggregates(self):
        right = {"action": {"type": "choice", "correct": True}}
        wrong = {"action": {"type": "choice", "correct": False}}
        regular = {"step": 1, "decision": {"provider": "laya", "status": "ok"}, "grade": wrong,
                   "shadow": [{"provider": "jev", "status": "ok", "grade": wrong}], "final_correct": True,
                   "policy": {"fired": ["control guard"], "override": {"from": "wait", "to": "brake"}}}
        comparison = {"step": 2, "comparison": True, "decision": {"provider": "jev", "status": "ok"},
                      "grade": right, "shadow": [{"provider": "laya", "status": "ok", "grade": right}],
                      "final_correct": False,
                      "policy": {"fired": ["comparison guard"], "override": {"from": "brake", "to": "wait"}}}
        failed_comparison = {"step": 3, "comparison": True, "decision": {"provider": "laya", "status": "ok"},
                             "grade": right, "shadow": [{"provider": "jev", "status": "error", "grade": None}]}
        summary = summarize_rows([comparison, regular, failed_comparison])
        self.assertEqual(summary["steps"], 3)
        self.assertEqual(summary["live"]["provider"], "laya")
        self.assertEqual(summary["live"]["grades"]["action"]["n"], 1)
        self.assertEqual(summary["live"]["grades"]["action"]["accuracy"], 0)
        self.assertEqual(summary["shadows"]["jev"]["action"]["n"], 1)
        self.assertEqual(summary["shadows"]["jev"]["action"]["accuracy"], 0)
        self.assertEqual(summary["policy"]["fired"], {"control guard": 1})
        self.assertEqual(summary["policy"]["overrides"], 1)
        self.assertEqual(summary["final_action_accuracy"], 1)
        self.assertEqual(summary["comparisons"]["steps"], 2)
        models = summary["comparisons"]["providers"]
        self.assertEqual(models["laya"]["count"], 2)
        self.assertEqual(models["laya"]["grades"]["action"]["n"], 2)
        self.assertEqual(models["laya"]["grades"]["action"]["accuracy"], 1)
        self.assertEqual(models["jev"]["statuses"], {"ok": 1, "error": 1})
        self.assertEqual(models["jev"]["grades"]["action"]["n"], 1)

    def test_comparison_only_session_has_no_control_accuracy(self):
        summary = summarize_rows([{"comparison": True, "decision": {"provider": "laya", "status": "ok"},
                                   "grade": {"action": {"type": "choice", "correct": True}},
                                   "final_correct": True}])
        self.assertEqual(summary["live"], {"provider": None, "grades": {}})
        self.assertEqual(summary["shadows"], {})
        self.assertIsNone(summary["final_action_accuracy"])
        self.assertEqual(summary["comparisons"]["providers"]["laya"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
