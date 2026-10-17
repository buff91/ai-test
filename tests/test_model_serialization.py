"""Cached local routers are shared across runs and must not predict concurrently."""
import threading
import time
import unittest
from unittest.mock import patch

from gamelab.decide import Decider, _ROUTERS


class ModelSerializationTests(unittest.TestCase):
    def test_separate_deciders_serialize_the_same_local_runtime_and_report_wait(self):
        entered, release, second_entered = threading.Event(), threading.Event(), threading.Event()
        class Router:
            loaded = []
            calls = 0
            def predict(self, state, questions, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    entered.set()
                    release.wait(3)
                else:
                    second_entered.set()
                return {"answers": {"action": {"choice": "wait"}}}
        questions = {"action": {"type": "choice", "criteria": {"wait": "Wait"}}}
        with patch.dict(_ROUTERS, {}, clear=True), patch("gamelab.decide.make_laya_router", return_value=Router()):
            first = Decider("laya", questions)
            second = Decider("laya", questions)
            results = []
            a = threading.Thread(target=lambda: results.append(first.decide("first")))
            b = threading.Thread(target=lambda: results.append(second.decide("second")))
            a.start()
            try:
                self.assertTrue(entered.wait(1))
                b.start()
                self.assertFalse(second_entered.wait(.03))
            finally:
                release.set()
                a.join(2)
                b.join(2)
        self.assertTrue(second_entered.is_set())
        self.assertEqual(len(results), 2)
        waited = next(row for row in results if row["runtime_wait_ms"] > 0)
        self.assertGreaterEqual(waited["runtime_wait_ms"], 20)
        self.assertGreaterEqual(waited["model_ms"], waited["runtime_wait_ms"])
        self.assertEqual(waited["model_ms_scope"], "runtime_wait_and_predict")

    def test_inference_failure_releases_the_local_runtime(self):
        class Router:
            def predict(self, *args, **kwargs):
                raise RuntimeError("test inference failure")
        questions = {"action": {"type": "choice", "criteria": {"wait": "Wait"}}}
        with patch.dict(_ROUTERS, {}, clear=True), patch("gamelab.decide.make_laya_router", return_value=Router()):
            model = Decider("laya", questions)
            self.assertEqual(model.decide("first")["status"], "error")
            self.assertEqual(model.decide("second")["status"], "error")


if __name__ == "__main__":
    unittest.main()
