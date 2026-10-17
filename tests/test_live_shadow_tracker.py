"""Live comparisons cannot queue duplicate inferences or stall game control."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from types import SimpleNamespace
import unittest

from gamelab.runner import ShadowTracker, tick


class Client:
    def __init__(self):
        self.actions = []

    def request(self, op, **kwargs):
        if op == "observe":
            return {"ok": True, "description": "frame %d" % len(self.actions),
                    "state": {"outcome": "playing"}, "signature": len(self.actions),
                    "observed_at": time.time(), "truth": {"action": ["brake"]}}
        if op == "act":
            self.actions.append(kwargs["action"])
            return {"ok": True, "applied": True}
        raise AssertionError("Unexpected world control: " + op)


class LiveShadowTrackerTests(unittest.TestCase):
    def test_slow_comparison_stays_on_its_observation_without_duplicate_requests(self):
        entered, release = threading.Event(), threading.Event()
        requests = []
        def compare(state):
            requests.append(state)
            entered.set()
            if not release.wait(3):
                raise TimeoutError("comparison not released")
            return {"status": "ok", "provider": "jev", "action": "keep", "model_ms": 300}
        shadow = SimpleNamespace(provider="jev", decide=compare)
        primary = SimpleNamespace(provider="laya", decide=lambda state: {
            "status": "ok", "provider": "laya", "action": "brake", "model_ms": 1})
        tracker, client = ShadowTracker(), Client()
        with ThreadPoolExecutor(max_workers=2) as pool:
            try:
                started = time.monotonic()
                rows = [tick(client, primary, {"max_age_ms": 500}, shadows=[shadow], pool=pool,
                             shadow_tracker=tracker, shadow_wait_ms=1000, step=step, episode=2)["row"]
                        for step in range(1, 21)]
                self.assertLess(time.monotonic() - started, .5)
                self.assertTrue(entered.wait(1))
                self.assertEqual(len(client.actions), 20)
                self.assertEqual(requests, ["frame 0"])
                pending = rows[-1]["shadow"][0]
                self.assertEqual(pending["status"], "running")
                self.assertEqual((pending["source_step"], pending["source_episode"]), (1, 2))
                self.assertFalse(pending["current_observation"])
                self.assertIsNone(pending["grade"])
                self.assertNotIn("agrees", pending)
            finally:
                release.set()
            self.assertEqual(tracker.pending["jev"]["future"].result(1)["action"], "keep")

    def test_finished_comparison_can_start_again_with_the_next_fresh_observation(self):
        seen = []
        model = SimpleNamespace(provider="jev", decide=lambda state: seen.append(state) or {
            "status": "ok", "provider": "jev", "action": "brake", "model_ms": 1})
        tracker = ShadowTracker()
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = tracker.submit(pool, model, "old frame", 1, 0)
            first["future"].result(1)
            second = tracker.submit(pool, model, "fresh frame", 2, 0)
            second["future"].result(1)
        self.assertTrue(second["current_observation"])
        self.assertEqual(second["source_step"], 2)
        self.assertEqual(seen, ["old frame", "fresh frame"])

    def test_input_free_comparison_still_waits_for_both_requested_answers(self):
        release, entered = threading.Event(), threading.Event()
        primary = SimpleNamespace(provider="laya", decide=lambda state: {
            "status": "ok", "provider": "laya", "action": "brake", "model_ms": 1})
        def compare(state):
            entered.set()
            release.wait(2)
            return {"status": "ok", "provider": "jev", "action": "keep", "model_ms": 100}
        shadow = SimpleNamespace(provider="jev", decide=compare)
        client, result = Client(), []
        with ThreadPoolExecutor(max_workers=1) as pool:
            worker = threading.Thread(target=lambda: result.append(tick(
                client, primary, {"max_age_ms": 500}, shadows=[shadow], pool=pool,
                shadow_tracker=ShadowTracker(), shadow_wait_ms=0, apply=False)))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(worker.is_alive())
            finally:
                release.set()
                worker.join(2)
        self.assertEqual(client.actions, [])
        self.assertEqual(result[0]["row"]["shadow"][0]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
