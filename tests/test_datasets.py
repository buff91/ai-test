"""Training data requires explicit labels, stable splits and isolated snapshots."""

import hashlib
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from gamelab.datasets import DatasetStore


def game():
    questions = {
        "action": {"type": "choice", "instructions": "Pick one", "criteria": {"brake": "Slow", "keep": "Keep"}},
        "danger": {"type": "score", "instructions": "Danger", "criteria": ["safe", "near", "now"]},
        "crash": {"type": "noul", "instructions": "Crash?"},
    }
    return SimpleNamespace(id="racer", version="sim-v1", questions=questions,
                           target={"game": "racer", "version": "sim-v1", "interface": "socket"})


def row(one, state="A sharp corner", correct=False):
    return {"request": {"state": state, "questions": deepcopy(one.questions)},
            "target": deepcopy(one.target), "state": {"unrelated": "do not use"},
            "description": "do not use this either", "step": 2, "episode": 1,
            "truth": {"action": ["brake"], "danger": 2, "crash": True},
            "decision": {"status": "ok", "provider": "laya", "action": "brake" if correct else "keep"},
            "grade": {"action": {"correct": correct}}, "shadow": []}


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = DatasetStore(self.root)
        self.game = game()

    def add(self, state="A sharp corner", split="train"):
        case = self.store.capture(self.game, row(self.game, state))
        return self.store.review(self.game, case["id"], {"action": "brake", "danger": 1.5, "crash": True}, split)

    def test_capture_exact_request_pending_and_suggested_labels(self):
        request = row(self.game)
        request["truth"].update(danger=float("nan"), crash=.8, unknown="keep")
        case = self.store.capture(self.game, request)
        self.assertEqual(case["state"], request["request"]["state"])
        self.assertEqual(case["questions"], request["request"]["questions"])
        self.assertEqual(case["suggested_expected"], {"action": "brake"})
        self.assertEqual(case["expected"], {})
        self.assertEqual(case["review"], "pending")
        self.assertEqual(case["source"]["target"], self.game.target)
        self.assertIsNone(case["source"]["truth"]["danger"])
        self.assertEqual(DatasetStore(self.root).status(self.game)["counts"],
                         {"total": 1, "pending": 1, "approved": 0, "train": 0, "eval": 0, "excluded": 0})

    def test_suggestions_choose_first_valid_option_without_approval(self):
        request = row(self.game)
        request["truth"]["action"] = ["teleport", "keep", "brake"]
        case = self.store.capture(self.game, request)
        self.assertEqual(case["suggested_expected"]["action"], "keep")
        with self.assertRaisesRegex(ValueError, "action label"):
            self.store.review(self.game, case["id"])

    def test_success_requires_bookmark_and_stale_shadows_are_ignored(self):
        request = row(self.game, correct=True)
        for shadow in [{"status": "error", "current_observation": False},
                       {"status": "error", "source_step": 1},
                       {"status": "error", "source_episode": 0},
                       {"status": "running"}]:
            request["shadow"] = [shadow]
            self.assertIsNone(self.store.capture(self.game, request))
        bookmarked = self.store.capture(self.game, request, force=True)
        self.assertEqual(bookmarked["reason"], "bookmark")

    def test_aligned_shadow_failure_and_wrong_answer_are_collected(self):
        request = row(self.game, correct=True)
        request["shadow"] = [{"provider": "jev", "status": "error", "current_observation": True,
                              "source_step": 2, "source_episode": 1}]
        self.assertIn("shadow:jev:model_error", self.store.capture(self.game, request)["reason"])
        request = row(self.game, "A different corner", correct=True)
        request["shadow"] = [{"provider": "jev", "status": "ok", "action": "keep"}]
        self.assertIn("shadow:jev:action_mismatch", self.store.capture(self.game, request)["reason"])

    def test_live_error_without_truth_is_collected(self):
        request = row(self.game)
        request.update(truth=None, grade=None, decision={"status": "error", "error": "load failed"})
        case = self.store.capture(self.game, request)
        self.assertEqual(case["suggested_expected"], {})
        self.assertEqual(case["reason"], "live:model_error")

    def test_dedup_preserves_review_and_updates_occurrences(self):
        case = self.add()
        same = row(self.game)
        same["truth"]["action"] = ["keep"]
        repeated = self.store.capture(self.game, same)
        self.assertEqual(repeated["id"], case["id"])
        self.assertEqual(repeated["expected"], case["expected"])
        self.assertEqual(repeated["review"], "approved")
        self.assertEqual(repeated["occurrences"], 2)
        self.assertEqual(repeated["source"]["truth"]["action"], ["brake"])
        self.assertEqual(repeated["last_source"]["truth"]["action"], ["keep"])

    def test_concurrent_store_instances_do_not_lose_occurrences(self):
        stores = [DatasetStore(self.root) for unused in range(4)]
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda number: stores[number % 4].capture(self.game, row(self.game)), range(24)))
        saved = self.store.status(self.game)
        self.assertEqual(saved["counts"]["total"], 1)
        self.assertEqual(saved["cases"][0]["occurrences"], 24)

    def test_review_strict_typed_labels(self):
        case = self.store.capture(self.game, row(self.game))
        for expected in [{}, {"action": "teleport"}, {"action": ["brake"]}, {"action": True},
                         {"action": "brake", "danger": True}, {"action": "brake", "danger": 3},
                         {"action": "brake", "danger": float("inf")}, {"action": "brake", "danger": "2"},
                         {"action": "brake", "crash": .5}, {"action": "brake", "crash": 1},
                         {"action": "brake", "unknown": 0}]:
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                self.store.review(self.game, case["id"], expected)
        reviewed = self.store.review(self.game, case["id"], {"action": "brake", "danger": .75, "crash": False}, "eval")
        self.assertEqual(reviewed["expected"]["danger"], .75)
        self.assertEqual(self.store.status(self.game)["counts"]["eval"], 1)

    def test_exclusion_needs_no_label_and_does_not_count_as_training(self):
        case = self.store.capture(self.game, row(self.game))
        self.store.review(self.game, case["id"], review="excluded")
        counts = self.store.status(self.game)["counts"]
        self.assertEqual((counts["excluded"], counts["train"]), (1, 0))

    def test_missing_snapshot_cross_game_schema_and_order_are_rejected(self):
        for mutation in (lambda value: value.pop("request"),
                         lambda value: value["target"].update(game="party"),
                         lambda value: value["target"].update(version="desktop-v1"),
                         lambda value: value["request"]["questions"]["action"]["criteria"].update(teleport="Jump"),
                         lambda value: value["request"]["questions"]["action"].update(criteria={"keep": "Keep", "brake": "Slow"})):
            request = row(self.game)
            mutation(request)
            with self.assertRaises(ValueError):
                self.store.capture(self.game, request)

    def test_identifiers_and_symlink_escape_are_rejected(self):
        for name in ("../party", "/tmp/party", "a/b", "a\\b", "..", "", "a..b"):
            with self.subTest(name=name):
                bad = deepcopy(self.game)
                bad.id = name
                with self.assertRaises(ValueError):
                    self.store.status(bad)
                with self.assertRaises(ValueError):
                    self.store.freeze(self.game, name)
                with self.assertRaises(ValueError):
                    self.store.review(self.game, name)
        with tempfile.TemporaryDirectory() as outside:
            (self.root / self.game.id).symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                self.store.capture(self.game, row(self.game))

    def test_freeze_requires_both_splits_and_ignores_unapproved(self):
        self.store.capture(self.game, row(self.game, "pending case"))
        with self.assertRaisesRegex(ValueError, "nonempty"):
            self.store.freeze(self.game, "empty")
        self.add()
        with self.assertRaisesRegex(ValueError, "nonempty"):
            self.store.freeze(self.game, "only-train")
        self.add("Another corner", "eval")
        frozen = self.store.freeze(self.game, "r001")
        self.assertEqual((frozen["train_count"], frozen["eval_count"]), (1, 1))
        for split in ("train", "eval"):
            path = Path(frozen[split + "_path"])
            sample = json.loads(path.read_text())
            self.assertEqual(set(sample), {"state", "questions", "expected"})
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), frozen["data_hashes"][split])
        manifest = json.loads(Path(frozen["manifest_path"]).read_text())
        self.assertEqual(manifest["counts"], {"train": 1, "eval": 1})
        self.assertEqual(manifest["dataset_hash"], frozen["dataset_hash"])
        before = Path(frozen["train_path"]).read_bytes()
        self.add("New data after the snapshot")
        self.assertEqual(Path(frozen["train_path"]).read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.store.freeze(self.game, "r001")

    def test_freeze_rejects_changed_version_and_question_schema(self):
        self.add()
        self.add("Another corner", "eval")
        self.game.version = "desktop-v1"
        self.assertEqual(self.store.status(self.game)["counts"]["total"], 0)
        with self.assertRaisesRegex(ValueError, "nonempty"):
            self.store.freeze(self.game, "bad-version")
        self.game.version = "sim-v1"
        self.game.questions["action"]["instructions"] = "Different task"
        with self.assertRaisesRegex(ValueError, "schema"):
            self.store.freeze(self.game, "bad-schema")

    def test_stale_cases_can_be_excluded_but_not_reapproved(self):
        case = self.add()
        self.game.questions["action"]["instructions"] = "Different task"
        self.assertFalse(self.store.status(self.game)["cases"][0]["compatible"])
        with self.assertRaisesRegex(ValueError, "schema"):
            self.store.review(self.game, case["id"], {"action": "brake"})
        excluded = self.store.review(self.game, case["id"], review="excluded")
        self.assertEqual(excluded["review"], "excluded")

    def test_reported_build_mismatch_cannot_be_approved(self):
        request = row(self.game)
        request["game_build"] = {"game": "racer", "version": "wrong-build"}
        case = self.store.capture(self.game, request)
        self.assertEqual(case["source"]["game_build"], request["game_build"])
        with self.assertRaisesRegex(ValueError, "game/version"):
            self.store.review(self.game, case["id"], {"action": "brake"})

    def test_state_overlap_rejected_even_when_dictionary_key_order_changes(self):
        self.add({"speed": 10, "lane": 1})
        # A second schema can capture the same state under a different fingerprint.
        self.game.questions["action"]["instructions"] = "A revised task"
        self.add({"lane": 1, "speed": 10}, "eval")
        with self.assertRaisesRegex(ValueError, "leakage"):
            self.store.freeze(self.game, "leak")

    def test_versions_have_independent_status_and_frozen_training_sets(self):
        sim_ids = {self.add("same train state")["id"], self.add("same eval state", "eval")["id"]}
        self.game.version = "desktop-v1"
        self.game.target["version"] = "desktop-v1"
        desktop_ids = {self.add("same train state")["id"], self.add("same eval state", "eval")["id"]}
        self.assertTrue(sim_ids.isdisjoint(desktop_ids))
        status = self.store.status(self.game)
        self.assertEqual(status["counts"]["total"], 2)
        self.assertEqual({case["id"] for case in status["cases"]}, desktop_ids)
        frozen = self.store.freeze(self.game, "desktop-run")
        manifest = json.loads(Path(frozen["manifest_path"]).read_text())
        self.assertEqual(set(manifest["sources"]), desktop_ids)
        self.assertEqual(manifest["version"], "desktop-v1")
        self.assertEqual(manifest["schema_hash"], frozen["schema_hash"])
        self.assertEqual(manifest["target"]["version"], "desktop-v1")
        self.game.version = "sim-v1"
        self.game.target["version"] = "sim-v1"
        self.assertEqual({case["id"] for case in self.store.status(self.game)["cases"]}, sim_ids)
        self.assertEqual(self.store.freeze(self.game, "sim-run")["version"], "sim-v1")


if __name__ == "__main__":
    unittest.main()
