"""Web console against a real sims/ game process, with a scripted model instead of Jev/Laya."""

import time
import unittest

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
        socket = next(i for i in games["arena"]["interfaces"] if i["name"] == "socket")
        app = next(i for i in games["arena"]["interfaces"] if i["name"] == "app")
        self.assertEqual(socket["run"]["mode"], "launch")
        self.assertEqual(app["run"]["command"], ["dist/ArenaGame/ArenaGame"])
        self.assertEqual(games["arena"]["default_interface"], "socket")

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
        self.connect("arena")
        with self.assertRaisesRegex(ValueError, "Jev API 키"):
            self.lab.run(provider="jev")

    def test_run_before_connect_is_refused(self):
        with self.assertRaisesRegex(ValueError, "연결"):
            self.lab.run()


if __name__ == "__main__":
    unittest.main()
