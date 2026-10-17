"""Console API boundaries and recorded evidence, without a browser or model weights."""

import io
import json
import tempfile
import threading
import unittest
from copy import deepcopy
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gamelab.console import Lab, Probe
from gamelab.console_http import make_handler
from gamelab.game_case import GameCase
from gamelab.socket_game import SocketGame
from sims.party import PartySim
from sims.service import SimService
from tests.test_web_console import ScriptedDecider, wait_until


class ConsoleHttpContractTests(unittest.TestCase):
    def setUp(self):
        self.lab = Mock(token="test-token")

    def post(self, body=b"{}", length=None, path="/api/connect", token="test-token"):
        # Exercise the real route/auth/body parser without opening a network port.
        handler_type = make_handler(self.lab)
        handler = handler_type.__new__(handler_type)
        handler.path = path
        handler.headers = Message()
        handler.headers["X-Session-Token"] = token
        handler.headers["Content-Length"] = str(len(body)) if length is None else length
        handler.rfile = Mock(wraps=io.BytesIO(body))
        handler._send = Mock()
        handler.do_POST()
        handler._send.assert_called_once()
        code, reply = handler._send.call_args.args[:2]
        return code, reply, handler

    def test_malformed_json_returns_client_error_without_calling_the_lab(self):
        for body in (b"{", b"[", b"\xff"):
            with self.subTest(body=body):
                code, reply, _ = self.post(body)
                self.assertEqual(code, 400)
                self.assertTrue(reply["error"])
        self.lab.connect.assert_not_called()

    def test_non_object_json_returns_client_error_without_calling_the_lab(self):
        for body in (b"[]", b"null", b"42", b'"text"', b"true"):
            with self.subTest(body=body):
                code, reply, _ = self.post(body)
                self.assertEqual(code, 400)
                self.assertIn("JSON 객체", reply["error"])
        self.lab.connect.assert_not_called()

    def test_invalid_content_length_is_rejected_before_body_read(self):
        for length in ("not-a-number", "1.5", "-1"):
            with self.subTest(length=length):
                code, reply, handler = self.post(length=length)
                self.assertEqual(code, 400)
                self.assertTrue(reply["error"])
                handler.rfile.read.assert_not_called()
        self.lab.connect.assert_not_called()

    def test_oversized_body_is_rejected_before_body_read(self):
        code, reply, handler = self.post(length="16385")
        self.assertEqual(code, 413)
        self.assertIn("너무 큽니다", reply["error"])
        handler.rfile.read.assert_not_called()
        self.lab.connect.assert_not_called()

    def test_unauthorized_request_does_not_parse_or_apply_settings(self):
        code, _, handler = self.post(b"not json", length="also invalid", token="wrong-token")
        self.assertEqual(code, 403)
        handler.rfile.read.assert_not_called()
        self.lab.connect.assert_not_called()

    def test_connection_route_preserves_nested_mapping_settings(self):
        settings = {"interval_ms": 125, "endpoint": {"host": "127.0.0.1", "port": 9877},
                    "actions": {"play": {"card_index": 2, "target": "enemy"}}}
        self.lab.connect.return_value = {"connecting": True}
        code, reply, _ = self.post(json.dumps({"game": "external", "interface": "mod", "version": "v2",
                                               "mode": "attach", "settings": settings}).encode())
        self.assertEqual((code, reply), (200, {"connecting": True}))
        self.lab.connect.assert_called_once_with("external", "mod", "app", False, version="v2",
                                                  socket_path=None, mode="attach", settings=settings)


class ConsoleGenerationTests(unittest.TestCase):
    def late_response(self, operation, reconnect):
        lab = Lab(record=False)
        self.addCleanup(lab.close)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        result, errors = [], []

        def request(op, **kwargs):
            self.assertEqual(op, "act" if operation == "manual" else "reset")
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test failed to release the old game")
            return {"ok": True, "applied": True, "state": {"old_game": True}, "command": {"op": op}}

        lab.game = GameCase("party")
        lab.status_name = "connected"
        lab.client = SimpleNamespace(request=request)

        def work():
            try:
                result.append(lab.manual("attack") if operation == "manual" else lab.reset())
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        self.assertTrue(entered.wait(3), "old request did not start")
        lab.disconnect()
        if reconnect:
            # Represent the next connection after a different game was selected.
            lab.generation += 1
            lab.game = GameCase("racer")
            lab.status_name = "connected"
            lab.observed = {"ok": True, "state": {"new_game": True}}
            lab.input_trace = {"action": "brake", "result": {"applied": True}}
            lab.phase = {"name": "new connection", "since": 123}
        before = deepcopy((lab.observed, lab.input_trace, lab.phase, lab.events))
        release.set()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors, errors)
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["ok"])
        self.assertTrue(result[0]["cancelled"])
        self.assertEqual((lab.observed, lab.input_trace, lab.phase, lab.events), before)

    def test_old_manual_response_cannot_modify_disconnected_or_reconnected_state(self):
        for reconnect in (False, True):
            with self.subTest(reconnect=reconnect):
                self.late_response("manual", reconnect)

    def test_old_reset_response_cannot_modify_disconnected_or_reconnected_state(self):
        for reconnect in (False, True):
            with self.subTest(reconnect=reconnect):
                self.late_response("reset", reconnect)


class ConsoleRecordingContractTests(unittest.TestCase):
    def test_override_mapping_and_actual_command_survive_recording_and_export(self):
        # The model says "attack", but this lesson deliberately maps that choice
        # to "guard". The recording must expose both without conflating them.
        original = GameCase("party")
        actions = {**original.actions, "attack": "guard"}
        overrides = {"interval_ms": 125, "max_age_ms": 4321, "max_action_ms": 567, "actions": actions}
        game = GameCase("party", overrides=overrides)
        service = SimService(PartySim)
        sent = []

        def request(op, **kwargs):
            message = {"op": op, **kwargs}
            sent.append(deepcopy(message))
            return service.handle(message)

        with tempfile.TemporaryDirectory() as folder, patch("gamelab.runner.RESULTS", Path(folder)), patch("gamelab.console.ROOT", Path(folder)):
            lab = Lab(make_decider=lambda p, g, k: ScriptedDecider(p, g, "attack"), record=True)
            try:
                lab.game = game
                lab.status_name = "connected"
                lab.options = {"provider": "laya", "shadows": [], "policy": False, "decision_delay_ms": 0}
                lab.client = Probe(SocketGame(game, SimpleNamespace(request=request)), lab, lab.generation)
                lab.info = lab.client.request("info")
                lab.run()
                self.assertTrue(wait_until(lambda: not lab.busy))
                self.assertIsNone(lab.error)
                row = lab.rows[0]
                self.assertTrue(row["applied"])
                trace = row["input_trace"]
                self.assertEqual(trace["action"], "attack")
                self.assertEqual(trace["mapped_command"], "guard")
                self.assertEqual(trace["command"], next(item for item in sent if item["op"] == "step"))
                self.assertEqual(trace["command"]["action"], "guard")
                self.assertTrue(trace["result"]["applied"])
                self.assertEqual(lab.status()["connection"]["settings"], game.configuration()["values"])
                exported = json.loads(lab.export())
                self.assertEqual(exported["settings"], game.configuration()["values"])
                self.assertEqual(exported["rows"][0]["input_trace"], trace)
                recorded = json.loads((lab.session / "session.json").read_text())
                self.assertEqual(recorded["settings"], exported["settings"])
                self.assertEqual(GameCase("party").actions, original.actions, "session edits leaked into catalog defaults")
            finally:
                lab.close()


if __name__ == "__main__":
    unittest.main()
