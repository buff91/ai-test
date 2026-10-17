"""Late comparison replies retain their own observation and training labels."""

from copy import deepcopy
from types import SimpleNamespace
import unittest

from gamelab.console import Lab
from gamelab.game_case import GameCase


class TrainingShadowTests(unittest.TestCase):
    def setUp(self):
        self.lab = Lab(record=False)
        self.addCleanup(self.lab.close)
        self.game = self.lab.game = GameCase("racer")
        self.lab.activity = "run"
        self.lab.info = {"game_build": {"game": self.game.id, "version": self.game.version}}
        self.decider = SimpleNamespace(provider="laya", questions=deepcopy(self.game.questions),
                                       laya_model="multilingual")

    def observation(self, state="original corner", step=1, expected="brake", current=True):
        return {"step": step, "episode": 0, "target": deepcopy(self.game.target),
                "request": {"state": state, "questions": deepcopy(self.game.questions)},
                "state": {"displayed_frame": state}, "description": state,
                "truth": {"action": [expected], "danger": 2},
                "decision": {"provider": "laya-mlx", "status": "ok", "action": expected,
                             "answers": {"action": {"choice": expected}}},
                "action": expected, "grade": {"action": {"correct": True}},
                "game_build": deepcopy(self.lab.info["game_build"]),
                "shadow": [{"provider": "laya", "status": "running", "source_step": 1,
                            "source_episode": 0, "current_observation": current, "grade": None}]}

    def start(self, row):
        return self.lab._call_started(self.lab.generation, "shadow", self.decider,
                                      deepcopy(row["request"]["state"]), row["step"], row["episode"])

    def complete(self, call, status="ok"):
        decision = {"provider": "laya", "status": status, "model_ms": 30, "call_id": call["id"]}
        if status == "ok":
            decision.update(action="keep", answers={"action": {"choice": "keep"}})
        else:
            decision["error"] = "late checkpoint failure"
        self.lab._call_finished(self.lab.generation, call, decision)

    def assert_original_case(self, original):
        saved = self.lab.datasets.status(self.game)
        self.assertEqual(saved["counts"]["total"], 1)
        case = saved["cases"][0]
        self.assertEqual(case["state"], original["request"]["state"])
        self.assertEqual(case["questions"], original["request"]["questions"])
        self.assertEqual(case["suggested_expected"]["action"], "brake")
        self.assertEqual(case["review"], "pending")
        self.assertEqual(case["occurrences"], 1)
        self.assertEqual(case["source"]["target"], self.game.target)
        self.assertEqual(case["source"]["game_build"], self.lab.info["game_build"])
        return case

    def test_late_wrong_reply_grades_original_row_after_newer_truth_arrives(self):
        original = self.observation()
        call = self.start(original)
        newer = self.observation("new clear straight", step=2, expected="keep", current=False)
        self.lab.rows = [newer, original]
        self.complete(call)
        shadow = original["shadow"][0]
        self.assertEqual(shadow["status"], "ok")
        self.assertFalse(shadow["grade"]["action"]["correct"])
        self.assertEqual(shadow["source_step"], 1)
        self.assertTrue(shadow["current_observation"])
        self.assertEqual(newer["shadow"][0]["status"], "running")
        self.assertIsNone(newer["shadow"][0]["grade"])
        case = self.assert_original_case(original)
        self.assertIn("action_mismatch", case["reason"])
        # Recollection sees the completed original reply rather than losing it.
        self.assertEqual(self.lab.training_collect("racer", version=self.game.version)["collected"], 1)

    def test_late_model_error_is_captured_on_original_observation(self):
        original = self.observation()
        call = self.start(original)
        self.lab.rows = [original]
        self.complete(call, status="error")
        self.assertEqual(original["shadow"][0]["status"], "error")
        self.assertIsNone(original["shadow"][0]["grade"])
        case = self.assert_original_case(original)
        self.assertIn("model_error", case["reason"])

    def test_completion_before_row_insertion_is_reconciled_and_collected(self):
        original = self.observation()
        call = self.start(original)
        self.complete(call)
        self.assertEqual(self.lab.datasets.status(self.game)["counts"]["total"], 0)
        # Mirror the insertion path after tick() made its pending-shadow snapshot.
        with self.lab.lock:
            self.lab.rows.insert(0, original)
            self.lab._reconcile_shadows_locked(original, capture=False)
            self.lab._capture_failure(self.game, original)
        self.assertEqual(original["shadow"][0]["status"], "ok")
        self.assertFalse(original["shadow"][0]["grade"]["action"]["correct"])
        self.assert_original_case(original)

    def test_running_reply_from_another_observation_never_uses_current_truth(self):
        original = self.observation()
        call = self.start(original)
        newer = self.observation("new clear straight", step=2, expected="keep", current=False)
        self.lab.rows = [newer]
        self.complete(call)
        with self.lab.lock:
            self.lab._reconcile_shadows_locked(newer, capture=False)
            self.lab._capture_failure(self.game, newer)
        self.assertEqual(newer["shadow"][0]["status"], "running")
        self.assertIsNone(newer["shadow"][0]["grade"])
        self.assertEqual(self.lab.datasets.status(self.game)["counts"]["total"], 0)
        with self.lab.lock:
            self.lab.rows.append(original)
            self.lab._reconcile_shadows_locked(original, capture=False)
            self.lab._capture_failure(self.game, original)
        self.assertFalse(original["shadow"][0]["grade"]["action"]["correct"])
        self.assert_original_case(original)

    def test_old_generation_completion_does_not_collect_into_new_connection(self):
        original = self.observation()
        call = self.start(original)
        old_generation = self.lab.generation
        self.lab.generation += 1
        self.lab.rows = [original]
        self.lab._call_finished(old_generation, call, {"provider": "laya", "status": "error", "error": "old reply"})
        self.assertEqual(original["shadow"][0]["status"], "running")
        self.assertEqual(self.lab.datasets.status(self.game)["counts"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
