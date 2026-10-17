"""Real-time input deadlines and stop: a slow decision must never become a late keypress."""
import threading
import time
import unittest
from contextlib import contextmanager
from types import SimpleNamespace

from gamelab.runner import tick
from gamelab.socket_game import SocketGame
from web_console import Lab


class Connection:
    def __init__(self):
        self.commands = []

    def request(self, op, **kwargs):
        self.commands.append(op)
        return {"ok": True, "snapshot": {"outcome": "playing"}, "revision": 1, "description": "safe"}


class RealtimeLoopTests(unittest.TestCase):
    def client(self, continuous=True):
        game = SimpleNamespace(actions={"wait": "wait"}, config={"continuous": continuous, "max_age_ms": 100},
                               describe=lambda state, description: description)
        return SocketGame(game, Connection())

    def test_expired_observation_never_reaches_game(self):
        client = self.client()
        result = client.request("act", action="wait", signature=1, observed_at=time.time() - 1, max_age_ms=100)
        self.assertTrue(result["stale"])
        self.assertFalse(result["applied"])
        self.assertNotIn("step", client.client.commands)

    def test_turn_game_can_still_wait_for_a_long_decision(self):
        client = self.client(continuous=False)
        self.assertTrue(client.request("act", action="wait", signature=1, observed_at=0)["applied"])

    def test_stop_during_inference_cancels_input(self):
        client = self.client()
        stopped = threading.Event()
        class Model:
            def decide(self, state):
                stopped.set()
                return {"status": "ok", "action": "wait", "model_ms": 1}
        row = tick(client, Model(), {"max_age_ms": 100}, cancel_event=stopped)["row"]
        self.assertTrue(row["cancelled"])
        self.assertFalse(row["applied"])
        self.assertNotIn("step", client.client.commands)

    def test_kbm_missing_permissions_are_reported_before_window_lookup(self):
        for capture, dry_run, message in ((False, True, "화면 기록"), (True, False, "손쉬운 사용")):
            with self.subTest(capture=capture, dry_run=dry_run):
                permissions = {"screen_capture": capture, "post_events": False}
                class Client:
                    def request(self, op, **kwargs):
                        if op != "info":
                            raise AssertionError("No capture or input before permission check")
                        return {"ok": True, "permissions": permissions}
                @contextmanager
                def connect(**kwargs):
                    yield Client()
                lab = Lab(record=False)
                try:
                    game = SimpleNamespace(connector="kbm", connect=connect)
                    lab._open(lab.generation, game, "app", dry_run)
                    self.assertEqual(lab.status_name, "error")
                    self.assertIn(message, lab.conn_error)
                    self.assertEqual(lab.info["permissions"], permissions)
                finally:
                    lab.close()
    def test_added_delay_is_separate_from_model_latency_and_expires(self):
        client = self.client()
        model = SimpleNamespace(decide=lambda state: {"status": "ok", "action": "wait", "model_ms": 1})
        row = tick(client, model, {"max_age_ms": 5}, decision_delay_ms=20)["row"]
        self.assertEqual(row["decision"]["model_ms"], 1)
        self.assertEqual(row["decision_delay_ms"], 20)
        self.assertTrue(row["stale"])
        self.assertFalse(row["applied"])


if __name__ == '__main__':
    unittest.main()
