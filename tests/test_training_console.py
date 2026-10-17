"""The console connects reviewed cases, jobs and deployed Laya inference."""

import io
import json
import tempfile
import time
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gamelab.console import Lab, Probe
from gamelab.console_http import make_handler
from gamelab.decide import Decider, _ROUTERS
from gamelab.game_case import GameCase
from tests.test_web_console import wait_until


class WrongDecider:
    def __init__(self, provider, game, key=None):
        self.provider, self.questions = provider, game.questions

    def decide(self, state):
        return {"provider": self.provider, "status": "ok", "action": "accelerate", "model_ms": 1,
                "answers": {"action": {"choice": "accelerate", "probabilities": {"accelerate": 1}}}}


class FailureCollectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.lab = Lab(make_decider=WrongDecider, record=False, training_root=self.directory.name)
        self.addCleanup(self.lab.close)
        self.game = self.lab.game = GameCase("racer")
        self.lab.status_name = "connected"
        self.lab.options = {"provider": "laya", "shadows": [], "policy": False}

        def observe(op, **kwargs):
            self.assertEqual(op, "observe")
            return {"ok": True, "state": {"corner": 1}, "description": "Brake before the corner.",
                    "truth": {"action": ["brake"], "danger": 2, "crash_ahead": True},
                    "signature": 1, "observed_at": time.time()}
        self.lab.client = Probe(SimpleNamespace(request=observe), self.lab, self.lab.generation)

    def test_comparison_automatically_persists_pending_case_without_enabling_training(self):
        self.lab.compare(providers=["laya", "laya-mlx"])
        wait_until(lambda: not self.lab.busy)
        state = self.lab.training_status("racer")
        self.assertEqual(state["dataset"]["counts"]["pending"], 1)
        case = state["dataset"]["cases"][0]
        self.assertEqual(case["state"], "Brake before the corner.")
        self.assertEqual(case["expected"], {})
        self.assertEqual(case["suggested_expected"]["action"], "brake")
        with self.assertRaises(ValueError):
            self.lab.training_train("racer", {})
        self.lab.training_review("racer", case["id"], {"action": "brake"}, split="eval")
        self.lab.disconnect()
        state = self.lab.training_status("racer")
        self.assertEqual(state["dataset"]["counts"]["eval"], 1)

    def test_selected_record_can_be_bookmarked_and_wrong_game_is_rejected(self):
        self.lab.compare(providers=["laya", "laya-mlx"])
        wait_until(lambda: not self.lab.busy)
        call_id = self.lab.rows[0]["decision"]["call_id"]
        result = self.lab.training_collect("racer", call_id)
        self.assertEqual(result["collected"], 1)
        self.assertEqual(result["dataset"]["cases"][0]["occurrences"], 2)
        with self.assertRaises(ValueError):
            self.lab.training_collect("party", call_id)
        with self.assertRaises(ValueError):
            self.lab.training_collect("racer", 99999)

    def test_deployment_switch_requires_current_game_run_to_finish(self):
        self.lab.training = Mock()
        self.lab.busy = True
        with self.assertRaisesRegex(ValueError, "판단"):
            self.lab.training_apply("racer", "checkpoint-1")
        with self.assertRaisesRegex(ValueError, "판단"):
            self.lab.training_rollback("racer")
        self.lab.training.activate.assert_not_called()
        self.lab.training.rollback.assert_not_called()
        self.lab.busy = False
        self.lab.training_apply("racer", "checkpoint-1")
        self.lab.training.activate.assert_called_once_with(self.game, "checkpoint-1")


class DeploymentInferenceTests(unittest.TestCase):
    def test_evaluated_checkpoint_is_passed_to_next_laya_decider_only(self):
        with tempfile.TemporaryDirectory() as root:
            lab = Lab(record=False, training_root=root)
            try:
                game = GameCase("racer")
                checkpoint = Path(root) / "racer" / "checkpoint"
                lab.training.active_checkpoint = Mock(return_value=checkpoint)
                fake = SimpleNamespace(provider="laya", router_cached=False)
                with patch("gamelab.decide.for_game", return_value=fake) as factory:
                    lab._prepare_decider(lab.generation, "laya", game, None, "live")
                    factory.assert_called_once_with("laya", game, None, checkpoint=checkpoint)
                lab.training.active_checkpoint.reset_mock()
                fake.provider = "laya-mlx"
                with patch("gamelab.decide.for_game", return_value=fake) as factory:
                    lab._prepare_decider(lab.generation, "laya-mlx", game, None, "live")
                    factory.assert_called_once_with("laya-mlx", game, None, checkpoint=None)
                lab.training.active_checkpoint.assert_not_called()
            finally:
                lab.close()

    def test_custom_router_predicts_from_the_registered_local_checkpoint(self):
        router = Mock(loaded=[])
        router.predict.return_value = {"answers": {"action": {"choice": "brake"}},
                                       "routing": {"model": "game-checkpoint"}}
        factory = Mock(return_value=router)
        with tempfile.TemporaryDirectory() as root, patch.dict(_ROUTERS, {}, clear=True), \
                patch.dict("sys.modules", {"laya": SimpleNamespace(Router=factory)}):
            questions = {"action": {"type": "choice", "criteria": {"brake": "Brake"}}}
            model = Decider("laya", questions, checkpoint=root)
            answer = model.decide("corner")
            factory.assert_called_once_with(models={"game-checkpoint": str(Path(root).resolve())})
            router.predict.assert_called_once_with("corner", questions, model="game-checkpoint")
            self.assertEqual(answer["checkpoint"], str(Path(root).resolve()))
            self.assertEqual(answer["checkpoint_model"], "game-checkpoint")
            self.assertFalse(answer["checkpoint_cached"])


class TrainingHttpTests(unittest.TestCase):
    def request(self, method, path, data=None, token="token"):
        lab = Mock(token="token")
        lab.training_status.return_value = {"game": "racer"}
        lab.training_train.return_value = {"id": "job-1"}
        handler = make_handler(lab).__new__(make_handler(lab))
        handler.path, handler.headers = path, Message()
        handler.headers["X-Session-Token"] = token
        body = json.dumps(data or {}).encode()
        handler.headers["Content-Length"] = str(len(body))
        handler.rfile, handler._send = io.BytesIO(body), Mock()
        getattr(handler, "do_" + method)()
        return lab, handler._send.call_args.args

    def test_training_routes_require_session_token(self):
        lab, reply = self.request("POST", "/api/training/train", {"game": "racer"}, token="bad")
        self.assertEqual(reply[0], 403)
        lab.training_train.assert_not_called()
        lab, reply = self.request("GET", "/api/training?game=racer", token="bad")
        self.assertEqual(reply[0], 403)
        lab.training_status.assert_not_called()

    def test_training_context_is_game_and_version_scoped(self):
        lab, reply = self.request("GET", "/api/training?game=racer&version=desktop-v1")
        self.assertEqual(reply[0], 200)
        lab.training_status.assert_called_once_with("racer", "desktop-v1")
        lab, reply = self.request("POST", "/api/training/train",
                                  {"game": "racer", "version": "desktop-v1", "epochs": 2,
                                   "key": "never-save-this", "path": "/outside"})
        self.assertEqual(reply, (200, {"id": "job-1"}))
        lab.training_train.assert_called_once_with("racer", {"epochs": 2}, "desktop-v1")


if __name__ == "__main__":
    unittest.main()
