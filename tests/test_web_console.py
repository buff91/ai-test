"""Web console against a real sims/ game process, with a scripted model instead of Jev/Laya."""

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from web_console import Lab


class ScriptedDecider:
    """Always picks the same option, with a full answer for every question type."""

    def __init__(self, provider, game, action=None):
        self.provider = provider
        self.options = list(game.options)
        self.action = action or self.options[0]
        self.questions = game.questions

    def decide(self, state):
        answers = {"action": {"choice": self.action, "probabilities": {o: float(o == self.action) for o in self.options}}}
        for qid, q in self.questions.items():
            if q["type"] == "score":
                answers[qid] = {"score": 1.0}
            elif q["type"] == "noul":
                answers[qid] = {"noul": 0.5}
        return {"status": "ok", "provider": self.provider, "action": self.action, "probability": 1.0,
                "probabilities": answers["action"]["probabilities"], "answers": answers, "model_ms": 1.0}


def wait_until(check, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return True
        time.sleep(0.05)
    return False


class WebConsoleTest(unittest.TestCase):
    def setUp(self):
        self.lab = Lab(make_decider=lambda provider, game, key: ScriptedDecider(provider, game), record=False)
        self.lab.key = ""

    def tearDown(self):
        self.lab.close()

    def connect(self, game="racer"):
        self.lab.connect(game)
        self.assertTrue(wait_until(lambda: self.lab.status()["connection"]["status"] in ("connected", "error")))
        status = self.lab.status()
        self.assertEqual(status["connection"]["status"], "connected", status["connection"]["error"])
        return status

    def test_catalog_describes_how_each_interface_runs(self):
        games = {g["id"]: g for g in self.lab.catalog()["games"]}
        socket = next(i for i in games["party"]["interfaces"] if i["name"] == "socket")
        app = next(i for i in games["party"]["interfaces"] if i["name"] == "app")
        self.assertEqual(socket["run"]["mode"], "launch")
        self.assertEqual(app["run"]["command"], ["{python}", "-m", "gamelab.desktop_game", "--game", "party"])
        self.assertEqual(games["party"]["default_interface"], "socket")

    def test_connect_play_reset_manual_disconnect(self):
        status = self.connect()
        self.assertTrue(status["connection"]["scenarios"])
        self.assertIsNotNone(status["observed"]["truth"])

        self.lab.run(apply=True, provider="laya-mlx", shadows=["laya"])
        self.assertTrue(wait_until(lambda: not self.lab.status()["busy"]))
        status = self.lab.status()
        self.assertEqual(status["steps"], 1, status["error"])
        row = status["rows"][0]
        self.assertTrue(row["applied"])
        self.assertIn("action", row["grade"])
        self.assertEqual(row["shadow"][0]["provider"], "laya")

        self.lab.run(apply=False)
        self.assertTrue(wait_until(lambda: not self.lab.status()["busy"]))
        self.assertTrue(self.lab.status()["rows"][0]["analysis_only"])

        self.lab.run(apply=True, auto=True)
        self.assertTrue(wait_until(lambda: self.lab.status()["steps"] >= 5))
        self.lab.stop_auto()
        self.assertTrue(wait_until(lambda: not self.lab.status()["busy"]))

        scenario = status["connection"]["scenarios"][-1]
        self.lab.reset(scenario)
        self.assertFalse(self.lab.status()["observed"]["done"])
        self.assertTrue(self.lab.manual(list(self.lab.game.options)[0])["ok"])

        process = self.lab.client.client.process
        self.lab.disconnect()
        self.assertIsNone(self.lab.stack)
        self.assertEqual(self.lab.status()["connection"]["status"], "disconnected")
        self.assertIsNotNone(process.poll(), "disconnect stops the game the console started")

    def test_status_shows_the_game_process_build_and_calls(self):
        status = self.connect()
        health, runtime = status["health"], status["runtime"]
        self.assertTrue(health["alive"])
        self.assertIsInstance(health["pid"], int)
        self.assertIsNotNone(health["rtt_ms"])
        self.assertEqual(runtime["mode"], "launch")
        self.assertEqual(runtime["build"]["kind"], "source")
        self.assertEqual(runtime["build"]["module"], "sims")

        self.lab.run(apply=True, provider="laya-mlx", shadows=["laya"])
        self.assertTrue(wait_until(lambda: not self.lab.status()["busy"]))
        calls = self.lab.status()["calls"]
        self.assertEqual({c["role"] for c in calls}, {"live", "shadow"})
        live = next(c for c in calls if c["role"] == "live")
        self.assertEqual(live["status"], "ok")
        self.assertIn("action", live["request"]["questions"])
        self.assertTrue(live["finished_at"] >= live["started_at"])

    def test_turn_game_state_refreshes_while_auto_play_is_waiting_for_model(self):
        entered = threading.Event()
        release = threading.Event()
        calls = 0

        class WaitingDecider(ScriptedDecider):
            def decide(self, state):
                nonlocal calls
                calls += 1
                if calls == 2:
                    entered.set()
                    if not release.wait(5):
                        raise TimeoutError("test did not release the model")
                return super().decide(state)

        self.lab.make_decider = lambda provider, game, key: WaitingDecider(provider, game, "attack")
        before = self.connect("party")["observed"]["state"]
        self.lab.run(apply=True, auto=True, provider="laya", shadows=[], policy=False)
        try:
            self.assertTrue(entered.wait(5), "second decision did not start")
            status = self.lab.status()
            self.assertTrue(status["auto"])
            self.assertTrue(status["busy"])
            self.assertEqual(status["phase"]["name"], "모델 판단")
            self.assertEqual(status["steps"], 1)
            self.assertEqual(status["observed"]["state"], status["rows"][0]["after_state"])
            self.assertGreater(status["observed"]["state"]["turn"], before["turn"])
        finally:
            self.lab.stop_auto()
            release.set()
            self.assertTrue(wait_until(lambda: not self.lab.status()["busy"]))

    def test_busy_kbm_does_not_capture_in_background(self):
        self.lab.game = SimpleNamespace(connector="kbm", config={"interval_ms": 1})
        self.lab.client = Mock()
        self.lab.status_name = "connected"
        self.lab.busy = True
        self.lab._refresh()
        self.lab.client.observe_background.assert_not_called()

    def test_background_result_from_old_connection_is_ignored(self):
        self.lab.game = SimpleNamespace(connector="socket", config={"interval_ms": 1})
        self.lab.status_name = "connected"
        self.lab.busy = True
        self.lab.observed = {"ok": True, "state": {"turn": 1}}

        def old_observation(**kwargs):
            self.lab.generation += 1
            return {"ok": False, "error": "old connection ended"}

        self.lab.client = SimpleNamespace(observe_background=old_observation)
        self.lab._refresh()
        self.assertEqual(self.lab.observed["state"], {"turn": 1})
        self.assertEqual(self.lab.status_name, "connected")
        self.assertIsNone(self.lab.conn_error)

    def test_background_socket_failure_stops_auto_without_replacing_model_phase(self):
        self.lab.game = SimpleNamespace(connector="socket", config={"interval_ms": 1})
        self.lab.client = SimpleNamespace(observe_background=Mock(side_effect=OSError("socket closed")))
        self.lab.status_name = "connected"
        self.lab.busy = self.lab.auto = True
        self.lab._phase_locked("모델 판단")
        self.lab._refresh()
        self.assertEqual(self.lab.status_name, "error")
        self.assertIn("socket closed", self.lab.conn_error)
        self.assertFalse(self.lab.auto)
        self.assertEqual(self.lab.phase["name"], "모델 판단")

    def test_a_dead_game_process_is_reported(self):
        self.connect()
        process = self.lab.client.client.process
        process.kill()
        process.wait()
        status = self.lab.status()
        self.assertEqual(status["connection"]["status"], "error")
        self.assertIn("종료", status["connection"]["error"])
        self.assertFalse(status["health"]["alive"])

    def test_game_views_are_served_only_for_known_games(self):
        self.assertIn(b"GameViews.register('racer'", self.lab.view_script("racer"))
        for bad in ("../web", "_template", "nope"):
            with self.assertRaises(LookupError):
                self.lab.view_script(bad)

    def test_jev_needs_a_key(self):
        self.connect("party")
        with self.assertRaisesRegex(ValueError, "Jev API 키"):
            self.lab.run(provider="jev")

    def test_run_before_connect_is_refused(self):
        with self.assertRaisesRegex(ValueError, "연결"):
            self.lab.run()


if __name__ == "__main__":
    unittest.main()
