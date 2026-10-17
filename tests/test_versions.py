"""Target mapping and real socket ownership, without loading or calling any model."""

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from sims.service import SimService
from sims.racer import RacerSim
from gamelab.game_case import GAMES, GameCase, check_build
from play import _add_interface, main as play_main
from tests.test_web_console import ScriptedDecider, wait_until
from web_console import Lab


class VersionMappingTests(unittest.TestCase):
    def test_versions_select_only_mapped_interfaces_and_display_modes(self):
        sim = GameCase("party", version="sim-v1")
        self.assertEqual(sim.target["display"], "headless")
        self.assertEqual(set(sim.interfaces), {"socket", "attach"})
        desktop = GameCase("party", version="desktop-v1")
        self.assertEqual(desktop.interface, "app")
        self.assertEqual(desktop.target["display"], "visible")
        self.assertEqual(GameCase("party", interface="kbm").version, "desktop-v1")
        with self.assertRaises(LookupError):
            GameCase("party", interface="app", version="sim-v1")
        with self.assertRaises(LookupError):
            GameCase("party", version="missing")

    def test_per_version_socket_override_does_not_change_base_config(self):
        desktop = GameCase("party", interface="attach", version="desktop-v1")
        sim = GameCase("party", interface="attach", version="sim-v1")
        self.assertNotEqual(sim.config["socket"], desktop.config["socket"])
        self.assertEqual(desktop.target["mode"], "attach")
        self.assertEqual(desktop.target["display"], "visible")
        self.assertEqual(sim.target["display"], "headless")

    def test_build_report_must_identify_both_game_and_version(self):
        game = GameCase("party")
        self.assertEqual(check_build(game, None)["status"], "unverified")
        self.assertEqual(check_build(game, {"version": "sim-v1"})["status"], "unverified")
        self.assertEqual(check_build(game, {"game": "party", "version": "sim-v1"})["status"], "verified")
        self.assertEqual(check_build(game, {"game": "racer", "version": "sim-v1"})["status"], "mismatch")
        self.assertEqual(check_build(game, SimService(RacerSim).handle({"op": "info"})["build"])["status"], "mismatch")

    def test_invalid_version_mapping_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            shutil.copytree(GAMES / "party", Path(folder) / "party")
            path = Path(folder) / "party/game.json"
            config = json.loads(path.read_text())
            config["versions"]["sim-v1"]["interfaces"]["not-present"] = {}
            path.write_text(json.dumps(config))
            with self.assertRaises(ValueError):
                GameCase("party", folder)

    def test_add_interface_updates_the_active_mapping(self):
        for versioned in (True, False):
            with self.subTest(versioned=versioned), tempfile.TemporaryDirectory() as folder:
                game_dir = Path(folder) / "party"
                shutil.copytree(GAMES / "party", game_dir)
                path = game_dir / "game.json"
                config = json.loads(path.read_text())
                if not versioned:
                    config.pop("versions")
                    config.pop("version")
                path.write_text(json.dumps(config))
                with redirect_stdout(io.StringIO()):
                    _add_interface(game_dir, "socket", "custom", GAMES)
                saved = json.loads(path.read_text())
                game = GameCase("party", folder)
                self.assertIn("custom", game.interfaces)
                if versioned:
                    self.assertNotIn("interface", saved)
                    self.assertEqual(game.interface, "socket")
                else:
                    self.assertEqual(game.interface, "custom")


class CliSocketTests(unittest.TestCase):
    def test_cli_rejects_empty_and_relative_attach_paths(self):
        for command in ("doctor", "observe", "run"):
            for path in ("", "   ", "relative.sock"):
                with self.subTest(command=command, path=path):
                    args = ["play.py", command, "party", "--interface", "attach", "--socket", path]
                    with patch("sys.argv", args), self.assertRaisesRegex(SystemExit, "절대 경로"):
                        play_main()

    def test_cli_socket_override_cannot_turn_launch_into_attach(self):
        args = ["play.py", "observe", "party", "--interface", "socket", "--socket", "/tmp/game.sock"]
        with patch("sys.argv", args), self.assertRaisesRegex(SystemExit, "기존 게임"):
            play_main()

    def test_cli_observes_existing_game_without_stopping_it(self):
        with GameCase("racer").connect() as external:
            output = io.StringIO()
            args = ["play.py", "observe", "racer", "--interface", "attach", "--version", "sim-v1",
                    "--socket", " " + str(external.client.path) + " "]
            with patch("sys.argv", args), redirect_stdout(output):
                play_main()
            self.assertIsInstance(json.loads(output.getvalue())["state"], dict)
            self.assertIsNone(external.process.poll())
            self.assertTrue(external.request("observe")["ok"])


class VersionConnectionTests(unittest.TestCase):
    def setUp(self):
        self.lab = Lab(make_decider=lambda provider, game, key: ScriptedDecider(provider, game), record=False)
        self.lab.key = ""

    def tearDown(self):
        self.lab.close()

    def settle(self):
        self.assertTrue(wait_until(lambda: self.lab.status()["connection"]["status"] in ("connected", "error")))
        return self.lab.status()

    def test_all_three_headless_targets_connect_act_grade_and_export_identity(self):
        for game_id in ("party", "racer", "moba"):
            with self.subTest(game=game_id):
                self.lab.connect(game_id, version="sim-v1", mode="headless")
                status = self.settle()
                self.assertEqual(status["connection"]["status"], "connected", status["connection"]["error"])
                self.assertEqual(status["connection"]["version_check"]["status"], "verified")
                self.lab.run(provider="laya-mlx", shadows=[])
                self.assertTrue(wait_until(lambda: not self.lab.status()["busy"]))
                status = self.lab.status()
                self.assertTrue(status["rows"][0]["applied"])
                self.assertTrue(status["rows"][0]["grade"])
                exported = json.loads(self.lab.export())
                self.assertEqual(exported["target"]["version"], "sim-v1")
                self.assertEqual(exported["rows"][0]["target"]["game"], game_id)
                self.assertEqual(exported["game_build"], {"game": game_id, "version": "sim-v1"})
                self.lab.disconnect()

    def test_attach_preserves_external_process_and_rejects_wrong_version(self):
        with GameCase("party").connect() as external:
            path = str(external.client.path)
            process = external.process
            self.lab.connect("party", "attach", version="sim-v1", mode="attach", socket_path=path)
            status = self.settle()
            self.assertEqual(status["connection"]["status"], "connected")
            self.assertEqual(status["runtime"]["mode"], "attach")
            self.assertEqual(status["runtime"]["socket"], path)
            self.lab.disconnect()
            self.assertIsNone(process.poll())
            self.assertTrue(external.request("observe")["ok"])
            self.lab.connect("party", "attach", version="desktop-v1", socket_path=path)
            status = self.settle()
            self.assertEqual(status["connection"]["status"], "error")
            self.assertIn("다릅니다", status["connection"]["error"])
            self.assertIsNone(process.poll())
            self.assertTrue(external.request("observe")["ok"])
            self.lab.connect("racer", "attach", version="sim-v1", socket_path=path)
            status = self.settle()
            self.assertEqual(status["connection"]["status"], "error")
            self.assertIn("다릅니다", status["connection"]["error"])
            self.assertIsNone(process.poll())

    def test_attach_reports_when_external_game_stops(self):
        with GameCase("racer").connect() as external:
            self.lab.connect("racer", "attach", version="sim-v1", socket_path=str(external.client.path))
            self.assertEqual(self.settle()["connection"]["status"], "connected")
            external.process.terminate()
            external.process.wait(timeout=5)
            self.lab.observed_at = 0
            status = self.lab.status()
            self.assertEqual(status["connection"]["status"], "error")
            self.assertIn("끊어졌습니다", status["connection"]["error"])

    def test_mode_or_socket_override_cannot_turn_launch_into_attach(self):
        with self.assertRaisesRegex(ValueError, "실행 방식"):
            self.lab.connect("party", "socket", version="sim-v1", mode="visible")
        with self.assertRaisesRegex(ValueError, "기존 게임"):
            self.lab.connect("party", "socket", socket_path="/tmp/game.sock")
        for path in ("", "   ", "relative.sock"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "절대 경로"):
                self.lab.connect("party", "attach", socket_path=path)

    def test_recorded_session_keeps_target_and_build(self):
        with tempfile.TemporaryDirectory() as folder, patch("gamelab.runner.RESULTS", Path(folder)):
            # The console reports result paths relative to its ROOT.
            with patch("gamelab.console.ROOT", Path(folder)):
                self.lab.record = True
                self.lab.connect("racer", version="sim-v1")
                self.assertEqual(self.settle()["connection"]["status"], "connected")
                self.lab.run(provider="laya-mlx", shadows=[])
                self.assertTrue(wait_until(lambda: not self.lab.status()["busy"]))
                session = self.lab.session
                metadata = json.loads((session / "session.json").read_text())
                self.assertEqual(metadata["target"]["version"], "sim-v1")
                self.assertEqual(metadata["version_check"]["status"], "verified")
                self.lab.disconnect()


if __name__ == "__main__":
    unittest.main()
