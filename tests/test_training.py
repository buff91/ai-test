"""Learning lifecycle tests without downloading models or starting accelerator work."""

import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from gamelab.training import (TrainingManager, _context, checkpoint_fingerprint,
                              dataset_fingerprints, evaluate_agent, held_out_rows,
                              promotion_gate, run_worker, validate_options)


QUESTIONS = {"action": {"type": "choice", "instructions": "Act", "criteria": {"go": "Go", "stop": "Stop"}},
             "danger": {"type": "noul"},
             "level": {"type": "score", "criteria": ["safe", "danger"]}}


def game(version="v1"):
    return SimpleNamespace(id="racer", version=version, questions=json.loads(json.dumps(QUESTIONS)), target={"game": "racer", "version": version})


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def model(path):
    save(path / "rl_agent_config.json", {"encoder": "fixture"})
    save(path / "encoder" / "config.json", {"model_type": "fixture"})
    save(path / "tokenizer" / "tokenizer.json", {"vocab": []})
    (path / "model.safetensors").write_bytes(b"fixture weights")
    return path


class FixtureStore:
    def __init__(self, root):
        self.root = root

    def freeze(self, selected, run_id):
        folder = self.root / selected.id / "runs" / run_id
        rows = [{"state": "different-%s" % split, "questions": QUESTIONS, "expected": {"action": "go", "danger": False, "level": 0}}
                for split in ("train", "eval")]
        for split, row in zip(("train", "eval"), rows):
            save(folder / (split + ".jsonl"), row)
        save(folder / "manifest.json", {"game": selected.id, "version": selected.version})
        return {"train_path": str(folder / "train.jsonl"), "eval_path": str(folder / "eval.jsonl"),
                "manifest_path": str(folder / "manifest.json"), "train_count": 1, "eval_count": 1,
                "dataset_hash": "dataset-fixture", **_context(selected)}


class FakeProcess:
    def __init__(self):
        self.code = None
        self.terminated = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = -15

    def wait(self, timeout):
        return self.code


class TrainingManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.processes = []
        self.invocations = []

        def spawn(command, **kwargs):
            process = FakeProcess()
            process.request = json.loads(Path(command[-1]).read_text())
            self.processes.append(process)
            self.invocations.append((command, kwargs))
            return process

        self.spawn = spawn
        self.store = FixtureStore(self.root)
        self.manager = TrainingManager(self.root, self.store, process_factory=spawn)
        self.addCleanup(self.manager.close)
        self.game = game()

    def train(self):
        job = self.manager.start_train(self.game, {})
        process = self.processes[-1]
        request = process.request
        folder = Path(request["work_dir"])
        candidate = model(folder.parent / "checkpoint")
        base = model(self.root / "base")
        checkpoint = {"id": job["checkpoint_id"], "run_id": job["run_id"], "game": self.game.id,
                      "version": self.game.version, "schema_hash": _context(self.game)["schema_hash"],
                      "path": str(candidate), "base": "multilingual", "base_path": str(base),
                      "base_hash": checkpoint_fingerprint(base), "artifact_hash": checkpoint_fingerprint(candidate),
                      "dataset_hash": request["snapshot"]["dataset_hash"], "snapshot": request["snapshot"],
                      "dataset_fingerprints": request["dataset_fingerprints"], "created_at": 1}
        save(folder / "result.json", {"ok": True, "checkpoint": checkpoint})
        process.code = 0
        self.assertEqual(self.manager.status(self.game)["jobs"][-1]["status"], "completed")
        return checkpoint

    def evaluate(self, checkpoint, before=.5, after=1, failures=0):
        self.manager.start_evaluate(self.game, checkpoint["id"])
        result = {"held_out": True, "eval_count": 1, "artifact_hash": checkpoint["artifact_hash"],
                  "dataset_hash": checkpoint["dataset_hash"], "base_hash": checkpoint["base_hash"],
                  "before": {"action_accuracy": before, "action_count": 1, "inference_failures": 0, "invalid_labels": 0},
                  "after": {"action_accuracy": after, "action_count": 1, "inference_failures": failures, "invalid_labels": 0}}
        result["gate"] = promotion_gate(result)
        process = self.processes[-1]
        save(Path(process.request["work_dir"]) / "result.json", {"ok": True, "evaluation": result})
        process.code = 0
        return self.manager.status(self.game)

    def test_async_work_has_no_credentials_and_polling_collects_bounded_logs(self):
        with patch.dict("os.environ", {"JEV_API_KEY": "secret", "OPENAI_API_KEY": "secret", "HF_TOKEN": "secret", "AWS_SECRET_ACCESS_KEY": "secret"}):
            job = self.manager.start_train(self.game, {"freeze_encoder": True})
        command, options = self.invocations[-1]
        self.assertEqual(command[1:4], ["-u", "-m", "gamelab.training"])
        self.assertIs(options["stdin"], subprocess.DEVNULL)
        self.assertTrue(options["start_new_session"])
        self.assertFalse(set(options["env"]) & {"JEV_API_KEY", "OPENAI_API_KEY", "HF_TOKEN", "AWS_SECRET_ACCESS_KEY"})
        folder = Path(job["work_dir"])
        (folder / "worker.log").write_text("line\n" * 10000 + "epoch 2/4 mean loss 0.1\n")
        status = self.manager.status(self.game)["jobs"][-1]
        self.assertEqual(status["status"], "running")
        self.assertLessEqual(len(status["log_tail"]), 80)
        self.assertGreaterEqual(status["progress"], .45)
        with self.assertRaises(ValueError):
            self.manager.start_train(game("v2"), {})

    def test_explicit_evaluation_then_activation_and_persistent_rollback(self):
        checkpoint = self.train()
        with self.assertRaises(ValueError):
            self.manager.activate(self.game, checkpoint["id"])
        self.assertIsNone(self.manager.active_checkpoint(self.game))
        status = self.evaluate(checkpoint)
        self.assertTrue(status["checkpoints"][0]["activation_allowed"])
        self.manager.activate(self.game, checkpoint["id"])
        with patch("gamelab.training.checkpoint_fingerprint", wraps=checkpoint_fingerprint) as hasher:
            self.assertEqual(self.manager.active_checkpoint(self.game), Path(checkpoint["path"]))
            self.manager.active_checkpoint(self.game)
            self.assertEqual(hasher.call_count, 0, "unchanged weights should not be rehashed in game loop")
        restarted = TrainingManager(self.root, self.store, process_factory=self.spawn)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.active_checkpoint(self.game), Path(checkpoint["path"]))
        self.assertIsNone(restarted.rollback(self.game)["active"])
        self.assertIsNone(restarted.active_checkpoint(self.game))

    def test_regression_or_inference_failure_cannot_be_applied(self):
        checkpoint = self.train()
        for before, after, failures in ((1, .5, 0), (.5, 1, 1), (0, 0, 0)):
            with self.subTest(before=before, after=after, failures=failures):
                status = self.evaluate(checkpoint, before, after, failures)
                self.assertFalse(status["checkpoints"][0]["activation_allowed"])
                with self.assertRaises(ValueError):
                    self.manager.activate(self.game, checkpoint["id"])

    def test_artifact_and_dataset_and_original_base_mutation_block_activation(self):
        checkpoint = self.train()
        self.evaluate(checkpoint)
        for path in (Path(checkpoint["path"]) / "model.safetensors", Path(checkpoint["base_path"]) / "model.safetensors",
                     Path(checkpoint["snapshot"]["manifest_path"]), Path(checkpoint["snapshot"]["eval_path"])):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(original + b" ")
                with self.assertRaises(ValueError):
                    self.manager.activate(self.game, checkpoint["id"])
                path.write_bytes(original)

    def test_game_version_and_schema_are_scoped(self):
        checkpoint = self.train()
        self.evaluate(checkpoint)
        self.manager.activate(self.game, checkpoint["id"])
        other_version = game("v2")
        self.assertIsNone(self.manager.status(other_version)["active"])
        self.assertIsNone(self.manager.active_checkpoint(other_version))
        self.assertFalse(self.manager.status(other_version)["checkpoints"][0]["compatible"])
        with self.assertRaises(ValueError):
            self.manager.activate(other_version, checkpoint["id"])
        changed_schema = game()
        changed_schema.questions["action"]["criteria"]["wait"] = "Wait"
        with self.assertRaises(ValueError):
            self.manager.active_checkpoint(changed_schema)
        reordered = game()
        reordered.questions["action"]["criteria"] = {"stop": "Stop", "go": "Go"}
        with self.assertRaises(ValueError):
            self.manager.active_checkpoint(reordered)
        with self.assertRaises(LookupError):
            self.manager.activate(SimpleNamespace(id="party", version="v1", questions=QUESTIONS), checkpoint["id"])

    def test_rollback_restores_builtin_after_switching_between_two_custom_checkpoints(self):
        first = self.train()
        self.evaluate(first)
        self.manager.activate(self.game, first["id"])
        second = self.train()
        self.evaluate(second)
        self.manager.activate(self.game, second["id"])
        self.assertEqual(self.manager.active_checkpoint(self.game), Path(second["path"]))
        self.assertIsNone(self.manager.rollback(self.game)["active"])
        self.assertIsNone(self.manager.active_checkpoint(self.game))

    def test_huggingface_base_symlinks_hash_target_bytes_but_candidates_reject_links(self):
        checkpoint = self.train()
        base = Path(checkpoint["base_path"])
        weight = base / "model.safetensors"
        blob = self.root / "blobs" / "weight-blob"
        blob.parent.mkdir()
        weight.replace(blob)
        weight.symlink_to(blob)
        self.assertEqual(checkpoint_fingerprint(base, trusted_base=True), checkpoint["base_hash"])
        with self.assertRaises(ValueError):
            checkpoint_fingerprint(base)
        self.evaluate(checkpoint)
        self.manager.activate(self.game, checkpoint["id"])
        self.assertEqual(self.manager.active_checkpoint(self.game), Path(checkpoint["path"]))
        blob.write_bytes(b"mutated source weights")
        with self.assertRaises(ValueError):
            self.manager.active_checkpoint(self.game)

    def test_restart_and_process_exit_leave_failed_jobs_not_phantom_checkpoints(self):
        self.manager.start_train(self.game, {})
        self.processes[-1].code = 9
        status = self.manager.status(self.game)
        self.assertEqual(status["jobs"][-1]["status"], "failed")
        self.assertEqual(status["checkpoints"], [])
        self.manager.start_train(self.game, {})
        restarted = TrainingManager(self.root, self.store, process_factory=self.spawn)
        self.addCleanup(restarted.close)
        status = restarted.status(self.game)
        self.assertEqual(status["jobs"][-1]["status"], "failed")
        self.assertIn("재시작", status["jobs"][-1]["error"])

    def test_path_injection_and_unsafe_options_rejected_without_subprocess(self):
        for selected in ("../party", "/tmp/other", "a/b", "a..b"):
            with self.assertRaises(ValueError):
                self.manager.status(selected)
        for settings in ({"base": "/tmp/model"}, {"epochs": True}, {"epochs": 101}, {"head_lr": float("nan")},
                         {"encoder_lr": 1}, {"freeze_encoder": "false"}, {"output_dir": "/tmp/hijack"}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                self.manager.start_train(self.game, settings)
        self.assertEqual(self.invocations, [])

    def test_close_terminates_own_worker_and_preserves_failure_record(self):
        self.manager.start_train(self.game, {})
        self.manager.close()
        self.assertTrue(self.processes[0].terminated)
        self.assertEqual(self.manager.status(self.game)["jobs"][-1]["stage"], "interrupted")


class EvaluationTests(unittest.TestCase):
    def row(self, action="go"):
        return {"state": "held out state", "questions": QUESTIONS, "expected": {"action": action, "danger": True, "level": 1}}

    def answers(self):
        return {"action": {"choice": "go", "probabilities": {"go": .8, "stop": .2}},
                "danger": {"noul": .9}, "level": {"score": .7, "probabilities": {"0": .3, "1": .7}}}

    def test_real_inference_contract_metrics_and_typed_questions(self):
        agent = Mock()
        agent.predict.return_value = {"answers": self.answers()}
        metrics = evaluate_agent(agent, [self.row()])
        agent.predict.assert_called_once_with("held out state", QUESTIONS)
        self.assertEqual(metrics["action_accuracy"], 1)
        self.assertEqual(metrics["per_question"]["action"]["brier"], .08)
        self.assertEqual(metrics["per_question"]["action"]["ece"], .2)
        self.assertEqual(metrics["per_question"]["danger"]["accuracy"], 1)
        self.assertEqual(metrics["per_question"]["level"]["accuracy"], 1)
        self.assertEqual(metrics["inference_failures"], 0)
        self.assertIsNotNone(metrics["p95_latency_ms"])

    def test_invalid_action_or_auxiliary_probabilities_fail_gate(self):
        agent = Mock()
        for qid, key, value in (("action", "choice", "unknown"), ("danger", "noul", float("nan")), ("level", "probabilities", {"0": -1, "1": 2})):
            answers = self.answers()
            answers[qid][key] = value
            agent.predict.return_value = {"answers": answers}
            metrics = evaluate_agent(agent, [self.row()])
            self.assertEqual(metrics["inference_failures"], 1)
            evaluation = {"held_out": True, "eval_count": 1, "before": metrics, "after": metrics}
            self.assertFalse(promotion_gate(evaluation)["allowed"])

    def test_out_of_range_or_missing_score_is_an_inference_failure(self):
        agent = Mock()
        for score in (None, float("nan"), 2, True):
            answers = self.answers()
            answers["level"]["score"] = score
            agent.predict.return_value = {"answers": answers}
            self.assertEqual(evaluate_agent(agent, [self.row()])["inference_failures"], 1)

    def test_unlabeled_or_invalid_eval_actions_cannot_be_silently_skipped(self):
        row = self.row()
        row["expected"].pop("action")
        with self.assertRaises(ValueError):
            evaluate_agent(Mock(), [row])
        with self.assertRaises(ValueError):
            evaluate_agent(Mock(), [self.row("illegal")])

    def test_input_overlap_is_detected_even_when_labels_differ(self):
        with tempfile.TemporaryDirectory() as folder:
            snapshot = FixtureStore(Path(folder)).freeze(game(), "run")
            row = json.loads(Path(snapshot["train_path"]).read_text())
            row["expected"]["action"] = "stop"
            save(Path(snapshot["eval_path"]), row)
            with self.assertRaises(ValueError):
                held_out_rows(snapshot)

    def test_evaluation_worker_loads_pinned_base_and_candidate_on_same_frozen_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            snapshot = FixtureStore(root).freeze(game(), "run")
            checkpoint = {"path": str(model(root / "candidate")), "base_path": str(model(root / "base")),
                          "snapshot": snapshot, "dataset_hash": snapshot["dataset_hash"]}
            checkpoint["artifact_hash"] = checkpoint_fingerprint(checkpoint["path"])
            checkpoint["base_hash"] = checkpoint_fingerprint(checkpoint["base_path"])
            checkpoint["dataset_fingerprints"] = dataset_fingerprints(snapshot)
            agents = [Mock(), Mock()]
            for agent in agents:
                agent.predict.return_value = {"answers": self.answers()}
            sdk = SimpleNamespace(load=Mock(side_effect=agents))
            torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
            with patch.dict("sys.modules", {"laya": sdk, "torch": torch}):
                result = run_worker({"kind": "evaluate", "work_dir": str(root / "eval-job"), "checkpoint": checkpoint})
            self.assertTrue(result["ok"])
            self.assertEqual(sdk.load.call_args_list[0].args, (checkpoint["base_path"],))
            self.assertEqual(sdk.load.call_args_list[1].args, (checkpoint["path"],))
            self.assertEqual(agents[0].predict.call_args, agents[1].predict.call_args)
            self.assertTrue(result["evaluation"]["gate"]["allowed"])

    def test_training_worker_calls_sdk_with_frozen_paths_and_publishes_only_valid_heldout_run(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            snapshot = FixtureStore(root).freeze(game(), "run")
            base = model(root / "base")
            configs = []
            prepared = []

            def prepare(path):
                path = Path(path)
                prepared.append(path)
                config_file = path / "tokenizer" / "tokenizer_config.json"
                # Reproduce SDK's on-load compatibility rewrite. It must happen
                # before either baseline or candidate fingerprint is sealed.
                config_file.write_text(json.dumps({"tokenizer_class": "PreTrainedTokenizerFast"}))

            def config(**kwargs):
                configs.append(kwargs)
                return SimpleNamespace(**kwargs)

            def finetune(data, model_dir, output_dir, **kwargs):
                self.assertEqual((data, model_dir), (snapshot["train_path"], str(base)))
                self.assertEqual(kwargs["config"].eval_data, snapshot["eval_path"])
                model(Path(output_dir))
                return {"train_items": 1, "eval_items": 1, "train_report": {"is_held_out": True}, "epoch_loss": [.5]}

            sdk = SimpleNamespace(TrainConfig=Mock(side_effect=config), finetune=Mock(side_effect=finetune),
                                  resolve_checkpoint_dir=Mock(return_value=str(base)))
            request = {"kind": "train", "work_dir": str(root / "racer" / "runs" / "run" / "training"),
                       "snapshot": snapshot, "dataset_fingerprints": dataset_fingerprints(snapshot),
                       "options": {"freeze_encoder": True}, "checkpoint_id": "laya-fixture", "run_id": "run",
                       "game": "racer", "context": _context(game())}
            agent_sdk = SimpleNamespace(_fix_tokenizer_config=prepare)
            with patch.dict("sys.modules", {"laya.train": sdk, "laya.agent": agent_sdk}):
                result = run_worker(request)
            self.assertTrue(result["ok"])
            self.assertEqual(result["checkpoint"]["base_path"], str(base.resolve()))
            self.assertTrue(configs[0]["freeze_encoder"])
            self.assertEqual(configs[0]["loss"], "soft-ce")
            self.assertEqual(configs[0]["shuffle_options"], ("choice",))
            self.assertEqual([path.resolve() for path in prepared], [base.resolve(), Path(result["checkpoint"]["path"])])
            sealed = result["checkpoint"]["artifact_hash"]
            prepare(result["checkpoint"]["path"])
            self.assertEqual(checkpoint_fingerprint(result["checkpoint"]["path"]), sealed,
                             "SDK's later model load must not change the sealed artifact")
            self.assertTrue((Path(request["work_dir"]).parent / "checkpoint-manifest.json").is_file())

            sdk.finetune.side_effect = None
            sdk.finetune.return_value = {"eval_items": 1, "train_report": {"is_held_out": False}}
            with patch.dict("sys.modules", {"laya.train": sdk, "laya.agent": agent_sdk}), self.assertRaises(ValueError):
                run_worker(request)


if __name__ == "__main__":
    unittest.main()
