"""External adapter contract and settings validation, using a real localhost bridge."""

import io
import json
import shutil
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from examples.external_mod.demo import DemoGame, make_bridge
from gamelab.game_case import GAMES, GameCase
from gamelab.mod_bridge import ModBridge
from gamelab.protocol import ModClient, ModServer, endpoint_spec
from kbm.agent import AgentService
from kbm.macos import DryRunInput
from kbm.steps import validate_step
from play import main as play_main


CATALOG = Path(__file__).resolve().parents[1] / "examples/external_mod/games"


@contextmanager
def running_bridge(service=None):
    with ModServer({"host": "127.0.0.1", "port": 0}, service or make_bridge()) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield {"host": "127.0.0.1", "port": server.server_address[1]}
        finally:
            server.shutdown()
            thread.join(3)


class ModConnectorTests(unittest.TestCase):
    def test_real_tcp_roundtrip_and_detach_preserves_game(self):
        with running_bridge() as endpoint:
            game = GameCase("mod_demo", CATALOG, overrides={"endpoint": endpoint})
            with game.connect() as client:
                info = client.request("info")
                self.assertEqual(info["version_check"]["status"], "verified")
                self.assertTrue(info["capabilities"]["act"])
                observed = client.request("observe")
                result = client.request("act", action="attack", signature=observed["signature"])
                self.assertTrue(result["applied"], result)
                self.assertEqual(result["state"]["enemy_hp"], 20)
                self.assertEqual(result["command"]["action"], {"command": "attack", "target": 0})
            self.assertEqual(ModClient(endpoint).request("state")["snapshot"]["enemy_hp"], 20)

    def test_old_revision_cannot_apply_twice_and_manual_gets_current_revision(self):
        with running_bridge() as endpoint:
            with GameCase("mod_demo", CATALOG, overrides={"endpoint": endpoint}).connect() as client:
                before = client.request("observe")
                self.assertTrue(client.request("act", action="guard")["applied"])
                stale = client.request("act", action="attack", signature=before["signature"])
                self.assertFalse(stale["applied"])
                self.assertTrue(stale["stale"])
                self.assertIsNotNone(stale["command"])
                self.assertEqual(client.request("observe")["state"]["enemy_hp"], 30)

    def test_build_mismatch_refuses_attach_without_stopping_bridge(self):
        bridge = make_bridge()
        bridge.build["game"] = "another_game"
        with running_bridge(bridge) as endpoint:
            with self.assertRaisesRegex(ValueError, "다릅니다"):
                with GameCase("mod_demo", CATALOG, overrides={"endpoint": endpoint}).connect():
                    self.fail("must reject the wrong game")
            self.assertTrue(ModClient(endpoint).request("state")["ok"])

    def test_missing_protocol_is_rejected(self):
        class OldService:
            def handle(self, request):
                return {"ok": True, "build": {"game": "mod_demo", "version": "demo-v1"}}
        with running_bridge(OldService()) as endpoint:
            with self.assertRaisesRegex(ValueError, "protocol version 1"):
                with GameCase("mod_demo", CATALOG, overrides={"endpoint": endpoint}).connect():
                    self.fail("must reject an unrelated service")

    def test_callback_refusal_is_not_reported_as_applied(self):
        demo = DemoGame()
        bridge = ModBridge(game="mod_demo", version="demo-v1", observe=demo.observe,
                           act=lambda _: {"ok": True, "applied": False, "error": "Not this turn"})
        result = bridge.handle({"op": "step", "action": {"command": "attack"}, "expected_revision": 0})
        self.assertFalse(result["ok"])
        self.assertFalse(result["applied"])
        self.assertEqual(result["error"], "Not this turn")
        self.assertEqual(result["revision"], 0)

    def test_bridge_age_limit_rejects_before_callback(self):
        demo = DemoGame()
        bridge = make_bridge(demo)
        result = bridge.handle({"op": "step", "action": {"command": "attack", "target": 0},
                                "expected_revision": 0, "observed_at": time.time() - 1, "max_age_ms": 10})
        self.assertFalse(result["ok"])
        self.assertTrue(result["stale"])
        self.assertEqual(demo.enemy_hp, 30)

    def test_cli_settings_can_observe_external_bridge(self):
        with running_bridge() as endpoint, tempfile.TemporaryDirectory() as folder:
            settings = Path(folder) / "settings.json"
            settings.write_text(json.dumps({"endpoint": endpoint, "interval_ms": 250}))
            args = ["play.py", "--games-dir", str(CATALOG), "observe", "mod_demo", "--settings", str(settings)]
            output = io.StringIO()
            with patch("sys.argv", args), redirect_stdout(output):
                play_main()
            self.assertEqual(json.loads(output.getvalue())["state"]["enemy_hp"], 30)


class ConfigurationTests(unittest.TestCase):
    def test_settings_are_per_connection_and_expose_only_editable_fields(self):
        game = GameCase("mod_demo", CATALOG, overrides={"interval_ms": 1200,
                                                       "endpoint": "tcp://localhost:9000"})
        self.assertEqual(game.config["interval_ms"], 1200)
        self.assertEqual(game.config["endpoint"], {"host": "localhost", "port": 9000})
        self.assertEqual(GameCase("mod_demo", CATALOG).config["interval_ms"], 700)
        schema = game.configuration()
        self.assertEqual(set(schema["editable"]), set(schema["values"]))
        self.assertEqual(set(schema["editable"]), set(schema["fields"]))
        schema["values"]["actions"]["attack"]["target"] = 9
        self.assertEqual(game.actions["attack"]["target"], 0)

    def test_settings_cannot_change_executables_or_connector(self):
        for overrides in ({"launch": {"command": ["anything"]}}, {"connector": "kbm"},
                          {"socket": "/tmp/unrelated.sock"}, {"window": {"owner": "Another"}},
                          {"interval_ms": False}, {"max_age_ms": -1}, {"max_action_ms": 1000000},
                          {"actions": {"unknown": "attack"}}, {"actions": {"attack": []}}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                GameCase("mod_demo", CATALOG, overrides=overrides)

    def test_mod_endpoint_is_loopback_only_and_port_is_validated(self):
        for endpoint in ("http://localhost:8765", "tcp://example.com:8765", "tcp://localhost:0",
                         "tcp://user:secret@localhost:8765", {"host": "0.0.0.0", "port": 1234},
                         {"host": "localhost", "port": True}):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                endpoint_spec(endpoint)

    def test_external_catalog_scaffolds_from_bundled_templates_and_kbm_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            for connector in ("mod", "kbm"):
                args = ["play.py", "--games-dir", folder, "new", connector + "_sample", "--type", "turn.rpg",
                        "--interface", connector]
                with patch("sys.argv", args), redirect_stdout(io.StringIO()):
                    play_main()
                game = GameCase(connector + "_sample", folder)
                self.assertEqual(game.connector, connector)
                if connector == "kbm":
                    with self.assertRaisesRegex(RuntimeError, "아직 구현되지"):
                        game.observe(None)
            self.assertFalse((Path(folder) / "_template").exists())

    def test_invalid_keyboard_mouse_steps_are_rejected_before_input(self):
        for step in ({"hold": "w", "ms": -100}, {"tap": 3}, {"tap": []}, {"wait": True},
                     {"click": "left", "at": [float("nan"), 0.5]}, {"look": ["x", 1]},
                     {"move": "cursor"}, {"tap": "a", "shell": "unused"}):
            with self.subTest(step=step), self.assertRaises(ValueError):
                validate_step(step, "settings")

    def test_changed_kbm_settings_reach_the_separate_agent(self):
        from kbm.connector import connect
        with tempfile.TemporaryDirectory() as folder:
            shutil.copytree(GAMES / "_template", Path(folder) / "sample")
            overrides = {"window": {"owner": "ChangedGame"}, "interval_ms": 1200,
                         "actions": {"act": [{"tap": "a", "ms": 10}], "wait": []}}
            game = GameCase("sample", folder, interface="kbm", overrides=overrides)
            captured = {}
            process = SimpleNamespace(pid=123, poll=lambda: None, wait=lambda **_: 0)

            def launch(command):
                settings_path = Path(command[command.index("--settings") + 1])
                captured["path"] = settings_path
                captured["settings"] = json.loads(settings_path.read_text())
                return process

            with patch("kbm.connector.subprocess.Popen", launch), patch("kbm.connector._wait_ready"), \
                    patch("kbm.connector.AgentClient.request", return_value={"ok": True}):
                with connect(game, agent="dev"):
                    self.assertEqual(captured["settings"]["window"]["owner"], "ChangedGame")
                    self.assertEqual(captured["settings"]["actions"]["act"], [{"tap": "a", "ms": 10}])
                    self.assertEqual(captured["settings"]["interval_ms"], 1200)
            self.assertFalse(captured["path"].exists())

    def test_doctor_reports_missing_permission_before_window_lookup(self):
        requested = []
        def request(op):
            requested.append(op)
            return {"ok": True, "permissions": {"screen_capture": False, "post_events": False}}
        @contextmanager
        def connection(*_, **__):
            yield SimpleNamespace(request=request)
        args = ["play.py", "--games-dir", str(CATALOG), "doctor", "mod_demo"]
        with patch("sys.argv", args), patch("play.connect", connection), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "KBM 권한 필요"):
                play_main()
        self.assertEqual(requested, ["info"])


class KbmEvidenceTests(unittest.TestCase):
    def agent(self, backend, state=None, actions=None):
        game = SimpleNamespace(actions=actions or {"act": [{"tap": "a", "ms": 0}]},
                               observe=lambda _: {"state": state or {}, "description": ""},
                               signature=lambda _: "same")
        platform = SimpleNamespace(find_window=lambda: {"x": 0, "y": 0, "width": 100, "height": 100},
                                   capture=lambda _: Image.new("RGB", (100, 100)),
                                   cursor=lambda: (50, 50), is_frontmost=lambda _: True)
        return AgentService(game, platform, backend)

    def test_dry_run_records_plan_without_claiming_transmission(self):
        result = self.agent(DryRunInput()).handle({"op": "act", "action": "act"})
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertIsNone(result["command"])
        self.assertEqual(result["planned_command"]["steps"], [{"tap": "a", "ms": 0}])

    def test_successful_backend_calls_are_preserved_on_partial_failure(self):
        class Backend(DryRunInput):
            dry_run = False  # simulated backend, never sends system input
        actions = {"act": [{"tap": "a", "ms": 0}, {"click": "left", "at": "missing"}]}
        result = self.agent(Backend(), actions=actions).handle({"op": "act", "action": "act"})
        self.assertFalse(result["applied"])
        self.assertEqual(result["command"]["steps"], [{"tap": "a", "ms": 0}])
        self.assertEqual(result["command"]["events"], [
            {"event": "key", "args": ["a", True]}, {"event": "key", "args": ["a", False]}])

    def test_out_of_window_observed_target_cannot_click(self):
        backend = DryRunInput()
        actions = {"act": [{"click": "left", "at": "enemy"}]}
        result = self.agent(backend, state={"enemy": [20, 1]}, actions=actions).handle({"op": "act", "action": "act"})
        self.assertFalse(result["applied"])
        self.assertEqual(backend.events, [])


if __name__ == "__main__":
    unittest.main()
