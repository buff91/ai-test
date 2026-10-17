"""MLX conversion, independent evaluation and deployment without Metal/GPU work."""

import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from gamelab.training import TrainingManager, checkpoint_fingerprint, promotion_gate, run_worker
from tests import test_training as fixtures


class MLXTrainingTests(unittest.TestCase):
    setUp = fixtures.TrainingManagerTests.setUp
    train = fixtures.TrainingManagerTests.train

    def converter(self, source, output, *, dtype):
        fixtures.model(Path(output))
        fixtures.save(Path(output) / "mlx_config.json", {"format": "laya-mlx", "format_version": 1,
                      "dtype": dtype, "source": source, "revision": None, "subfolder": None})
        return output

    def convert(self, source, dtype="float16"):
        job = self.manager.start_convert(self.game, source["id"], dtype=dtype)
        process = self.processes[-1]
        sdk = SimpleNamespace(convert=Mock(side_effect=self.converter))
        with patch.dict(sys.modules, {"laya_mlx.convert": sdk}):
            result = run_worker(process.request)
        sdk.convert.assert_called_once_with(source["path"], Path(job["work_dir"]) / "checkpoint", dtype=dtype)
        fixtures.save(Path(job["work_dir"]) / "result.json", result)
        process.code = 0
        status = self.manager.status(self.game)
        self.assertEqual(status["jobs"][-1]["status"], "completed")
        return status["checkpoints"][-1]

    def evaluate(self, checkpoint, runtime=None):
        self.manager.start_evaluate(self.game, checkpoint["id"])
        result = {"held_out": True, "eval_count": 1, "artifact_hash": checkpoint["artifact_hash"],
                  "dataset_hash": checkpoint["dataset_hash"], "base_hash": checkpoint["base_hash"],
                  "runtime": runtime or checkpoint.get("runtime", "laya"),
                  "before": {"action_accuracy": .5, "action_count": 1, "inference_failures": 0, "invalid_labels": 0},
                  "after": {"action_accuracy": 1, "action_count": 1, "inference_failures": 0, "invalid_labels": 0}}
        if checkpoint.get("runtime") == "laya-mlx":
            result["source_artifact_hash"] = checkpoint["source_artifact_hash"]
        result["gate"] = promotion_gate(result)
        process = self.processes[-1]
        fixtures.save(Path(process.request["work_dir"]) / "result.json", {"ok": True, "evaluation": result})
        process.code = 0
        return self.manager.status(self.game)

    def test_convert_before_source_evaluation_creates_new_unapproved_checkpoint(self):
        source = self.train()
        converted = self.convert(source, dtype="float32")
        self.assertEqual(converted["runtime"], "laya-mlx")
        self.assertEqual(converted["dtype"], "float32")
        self.assertEqual(converted["source_checkpoint_id"], source["id"])
        self.assertEqual(converted["source_artifact_hash"], source["artifact_hash"])
        self.assertEqual(converted["dataset_hash"], source["dataset_hash"])
        self.assertIsNone(converted["evaluation"])
        self.assertFalse(converted["activation_allowed"])
        self.assertEqual(self.manager.status(self.game)["jobs"][-1]["runtime"], "laya-mlx")
        with self.assertRaises(ValueError):
            self.manager.activate(self.game, converted["id"])

    def test_pytorch_evaluation_cannot_be_reused_for_converted_checkpoint(self):
        source = self.train()
        self.evaluate(source)
        converted = self.convert(source)
        self.assertIsNone(converted["evaluation"])
        with self.assertRaises(ValueError):
            self.manager.activate(self.game, converted["id"])
        status = self.evaluate(converted, runtime="laya")
        self.assertEqual(status["jobs"][-1]["status"], "failed")
        self.assertIn("런타임", status["jobs"][-1]["error"])
        with self.assertRaises(ValueError):
            self.manager.activate(self.game, converted["id"])

    def test_provider_deployments_are_independent_and_persisted(self):
        source = self.train()
        self.evaluate(source)
        source_active = self.manager.activate(self.game, source["id"])
        converted = self.convert(source, dtype="bfloat16")
        self.evaluate(converted)
        mlx_active = self.manager.activate(self.game, converted["id"])
        self.assertEqual(mlx_active["runtime"], "laya-mlx")
        self.assertEqual(mlx_active["dtype"], "bfloat16")
        self.assertEqual(self.manager.active_checkpoint(self.game), Path(source["path"]))
        self.assertEqual(self.manager.active_checkpoint(self.game, provider="laya-mlx"), Path(converted["path"]))
        status = self.manager.status(self.game)
        self.assertEqual(status["active"], source_active)
        self.assertEqual(status["active_by_provider"], {"laya": source_active, "laya-mlx": mlx_active})
        restarted = TrainingManager(self.root, self.store, process_factory=self.spawn)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.active_checkpoint(self.game, "laya-mlx"), Path(converted["path"]))
        restarted.rollback(self.game, provider="laya-mlx")
        self.assertIsNone(restarted.active_checkpoint(self.game, "laya-mlx"))
        self.assertEqual(restarted.active_checkpoint(self.game), Path(source["path"]))

    def test_legacy_mapping_migrates_to_pytorch_without_activating_mlx(self):
        source = self.train()
        self.evaluate(source)
        active = self.manager.activate(self.game, source["id"])
        registry_path = self.root / "racer" / "training.json"
        registry = json.loads(registry_path.read_text())
        registry.pop("active_by_runtime_version")
        registry.pop("history_by_runtime_version")
        for checkpoint in registry["checkpoints"]:
            checkpoint.pop("runtime", None)
        fixtures.save(registry_path, registry)
        restarted = TrainingManager(self.root, self.store, process_factory=self.spawn)
        self.addCleanup(restarted.close)
        status = restarted.status(self.game)
        self.assertEqual(status["active"], active)
        self.assertIsNone(status["active_by_provider"]["laya-mlx"])
        self.assertEqual(restarted.active_checkpoint(self.game), Path(source["path"]))
        self.assertEqual(status["checkpoints"][0]["runtime"], "laya")

    def test_changed_source_or_mlx_metadata_blocks_evaluation_and_apply(self):
        source = self.train()
        converted = self.convert(source)
        self.evaluate(converted)
        for path in (Path(source["path"]) / "model.safetensors", Path(converted["path"]) / "mlx_config.json"):
            original = path.read_bytes()
            if path.suffix == ".json":
                metadata = json.loads(original)
                metadata["dtype"] = "float32"
                path.write_text(json.dumps(metadata))
            else:
                path.write_bytes(b"changed original weights")
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.manager.start_evaluate(self.game, converted["id"])
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.manager.activate(self.game, converted["id"])
            path.write_bytes(original)

    def test_invalid_dtype_or_reconverting_mlx_is_rejected(self):
        source = self.train()
        for dtype in ("int4", "auto", None, "../../weights"):
            with self.assertRaises(ValueError):
                self.manager.start_convert(self.game, source["id"], dtype=dtype)
        converted = self.convert(source)
        with self.assertRaises(ValueError):
            self.manager.start_convert(self.game, converted["id"])

    def test_partial_conversion_failure_removes_only_its_output_and_never_registers_candidate(self):
        source = self.train()
        job = self.manager.start_convert(self.game, source["id"])
        process = self.processes[-1]
        source_hash = checkpoint_fingerprint(source["path"])

        def fail(source_path, output, **kwargs):
            fixtures.model(Path(output))
            raise RuntimeError("simulated converter failure")

        with patch.dict(sys.modules, {"laya_mlx.convert": SimpleNamespace(convert=fail)}), self.assertRaises(RuntimeError):
            run_worker(process.request)
        self.assertFalse((Path(job["work_dir"]) / "checkpoint").exists())
        self.assertEqual(checkpoint_fingerprint(source["path"]), source_hash)
        fixtures.save(Path(job["work_dir"]) / "result.json", {"ok": False, "error": "conversion failed"})
        process.code = 1
        status = self.manager.status(self.game)
        self.assertEqual(len(status["checkpoints"]), 1)
        self.assertEqual(status["jobs"][-1]["status"], "failed")

    def test_conversion_checks_source_after_export_and_removes_untrusted_output(self):
        source = self.train()
        job = self.manager.start_convert(self.game, source["id"])
        process = self.processes[-1]

        def mutate(source_path, output, **kwargs):
            self.converter(source_path, output, **kwargs)
            (Path(source_path) / "model.safetensors").write_bytes(b"concurrently changed source")

        with patch.dict(sys.modules, {"laya_mlx.convert": SimpleNamespace(convert=mutate)}), self.assertRaises(ValueError):
            run_worker(process.request)
        self.assertFalse((Path(job["work_dir"]) / "checkpoint").exists())

    def test_mlx_evaluation_uses_mlx_for_baseline_and_candidate_and_matching_dtype(self):
        source = self.train()
        converted = self.convert(source, dtype="float32")
        self.manager.start_evaluate(self.game, converted["id"])
        process = self.processes[-1]
        answers = fixtures.EvaluationTests().answers()
        agents = [Mock(), Mock()]
        for agent in agents:
            agent.predict.return_value = {"answers": answers}
        sdk = SimpleNamespace(load=Mock(side_effect=agents))
        mx = SimpleNamespace(clear_cache=Mock())
        pytorch = SimpleNamespace(load=Mock(side_effect=AssertionError("MLX evaluation must not load PyTorch")))
        with patch.dict(sys.modules, {"laya_mlx": sdk, "mlx.core": mx, "mlx": SimpleNamespace(core=mx), "laya": pytorch}):
            result = run_worker(process.request)
        self.assertEqual(sdk.load.call_args_list[0].args, (converted["base_path"],))
        self.assertEqual(sdk.load.call_args_list[1].args, (converted["path"],))
        self.assertTrue(all(call.kwargs == {"dtype": "float32"} for call in sdk.load.call_args_list))
        self.assertEqual(agents[0].predict.call_args, agents[1].predict.call_args)
        self.assertEqual(result["evaluation"]["runtime"], "laya-mlx")
        self.assertEqual(result["evaluation"]["dtype"], "float32")
        self.assertTrue(result["evaluation"]["gate"]["allowed"])
        self.assertEqual(mx.clear_cache.call_count, 2)
        pytorch.load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
