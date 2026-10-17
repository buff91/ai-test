"""Compare real game handlers and pixel adapters without posting system input."""

from types import SimpleNamespace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

from gamelab import screens
from gamelab.desktop_game import DesktopService, GameWindow, KEYS
from gamelab.game_case import GAMES, GameCase
from gamelab.socket_game import SocketGame
from kbm.agent import AgentService
from kbm.macos import DryRunInput, MacPlatform
from sims import SIMS


def window_for(service):
    """Use the actual renderer's button geometry, with drawing calls discarded."""
    window = GameWindow.__new__(GameWindow)
    window.service = service
    window.canvas = Mock()
    window.buttons = []
    window.visual_time = 0
    window.socket_path = None
    window.draw()
    window.draw = lambda: None
    return window


def hud_image(service):
    image = Image.new("RGB", (screens.WIDTH, screens.HEIGHT), (11, 17, 32))
    draw = ImageDraw.Draw(image)
    for x in (24, 1076):
        draw.rectangle((x-3, 517, x+3, 523), fill=screens.ANCHOR)
    snapshot = service.render_state()["snapshot"]
    for index, value in enumerate(screens.encode(service.game_id, snapshot)):
        for bit, (x, y) in enumerate(screens.instrument(index)[2]):
            draw.rectangle((x-2, y-3, x+2, y+3), fill=screens.ON if value & (1 << bit) else screens.OFF)
    return image


class WindowInput(DryRunInput):
    """Send only to the in-memory GameWindow handler; never to the OS."""
    dry_run = False

    def __init__(self, window):
        super().__init__()
        self.window = window

    def key(self, name, down):
        super().key(name, down)
        if down:
            self.window.keypress(SimpleNamespace(keysym=name))


def agent_for(service, backend=None):
    game = GameCase(service.game_id, version="desktop-v1", interface="kbm")
    platform = SimpleNamespace(
        find_window=lambda: {"id": 1, "x": 0, "y": 0, "width": screens.WIDTH, "height": screens.HEIGHT},
        capture=lambda _: hud_image(service), cursor=lambda: (500, 500),
        is_frontmost=lambda _: True,
    )
    return AgentService(game, platform, backend or WindowInput(window_for(service)))


@patch("sims.service.truth", return_value={})
class DualRouteTests(unittest.TestCase):
    def test_every_scenario_and_action_matches_manual_keys_mouse_and_interface(self, _truth):
        for game_id, sim_cls in SIMS.items():
            case = GameCase(game_id, version="desktop-v1", interface="app")
            keys = {action: key for key, action in KEYS[game_id].items()}
            for scenario in sim_cls.SCENARIOS:
                for action in sim_cls.ACTIONS:
                    with self.subTest(game=game_id, scenario=scenario, action=action):
                        direct = DesktopService(game_id, scenario)
                        linked = SocketGame(case, SimpleNamespace(request=lambda op, **kw: direct.handle({"op": op, **kw})))
                        self.assertTrue(linked.request("act", action=action)["applied"])
                        expected = direct.render_state()

                        keyboard = DesktopService(game_id, scenario)
                        window_for(keyboard).keypress(SimpleNamespace(keysym=keys[action]))
                        self.assertEqual(keyboard.render_state(), expected)

                        mouse = DesktopService(game_id, scenario)
                        window = window_for(mouse)
                        matches = [button for button in window.buttons if button[-1] == action]
                        self.assertEqual(len(matches), 1)
                        x, y, width, height, _ = matches[0]
                        window.click(SimpleNamespace(x=x+width/2, y=y+height/2))
                        self.assertEqual(mouse.render_state(), expected)

    def test_pixel_kbm_adapter_sends_every_catalog_action_to_same_game_handler(self, _truth):
        for game_id, sim_cls in SIMS.items():
            for action in sim_cls.ACTIONS:
                with self.subTest(game=game_id, action=action):
                    service = DesktopService(game_id)
                    agent = agent_for(service)
                    observed = agent.handle({"op": "observe"})
                    self.assertTrue(observed["ok"], observed)
                    with patch("kbm.steps.sleep_ms"):
                        result = agent.handle({"op": "act", "action": action, "signature": observed["signature"]})
                    self.assertTrue(result["input_sent"], result)
                    self.assertFalse(result["application_confirmed"])
                    expected = DesktopService(game_id)
                    expected.handle({"op": "step", "action": action})
                    self.assertEqual(service.render_state(), expected.render_state())
                    self.assertIsNone(agent.handle({"op": "observe"})["truth"])

    def test_kbm_second_input_is_dispatched_without_claiming_confirmation(self, _truth):
        service = DesktopService("moba")
        agent = agent_for(service)
        service.handle({"op": "step", "action": "attack"})
        before = service.render_state()
        with patch("kbm.steps.sleep_ms"):
            result = agent.handle({"op": "act", "action": "skill"})
        # Current HUD exposes no per-action acknowledgement. Dispatch evidence
        # must never be presented as a confirmed game transition.
        self.assertFalse(result["application_confirmed"])
        self.assertTrue(result["input_sent"], result)
        self.assertTrue(result["command"]["events"])
        self.assertGreater(service.render_state()["snapshot"]["input_sequence"], before["snapshot"]["input_sequence"])
        self.assertEqual(service.sim.turn, 1)

    def test_pause_and_resume_are_idempotent_for_both_realtime_games(self, _truth):
        for game_id in ("racer", "moba"):
            with self.subTest(game=game_id), patch("kbm.steps.sleep_ms"):
                service = DesktopService(game_id)
                agent = agent_for(service)
                self.assertTrue(agent.handle({"op": "pause"})["application_confirmed"])
                after_first_pause = list(agent.backend.events)
                self.assertTrue(agent.handle({"op": "pause"})["ok"])
                self.assertEqual(agent.backend.events, after_first_pause)
                self.assertTrue(agent.handle({"op": "resume"})["application_confirmed"])
                after_resume = list(agent.backend.events)
                self.assertTrue(agent.handle({"op": "resume"})["ok"])
                self.assertEqual(agent.backend.events, after_resume)
                self.assertFalse(service.paused)
                self.assertTrue(agent.handle({"op": "pause"})["application_confirmed"])
                after_pause = list(agent.backend.events)
                self.assertTrue(agent.handle({"op": "pause"})["ok"])
                self.assertEqual(agent.backend.events, after_pause)
                self.assertTrue(service.paused)

    def test_pause_needs_visible_state_and_never_blindly_toggles(self, _truth):
        agent = agent_for(DesktopService("racer"))
        with patch.object(agent.game, "observe", return_value={"state": {}, "description": ""}):
            for op in ("pause", "resume"):
                result = agent.handle({"op": op})
                self.assertFalse(result["ok"])
                self.assertFalse(result["input_sent"])
        self.assertEqual(agent.backend.events, [])

    def test_ignored_pause_key_is_unconfirmed_without_retrying_toggle(self, _truth):
        class IgnoredInput(DryRunInput):
            dry_run = False
        agent = agent_for(DesktopService("racer"), IgnoredInput())
        with patch("kbm.steps.sleep_ms"), patch("kbm.agent.time.monotonic", side_effect=[0, 1]):
            result = agent.handle({"op": "pause"})
        self.assertFalse(result["ok"])
        self.assertTrue(result["input_sent"])
        self.assertFalse(result["application_confirmed"])
        self.assertEqual(agent.backend.events, [("key", "p", True), ("key", "p", False)])

    def test_dry_run_does_not_claim_dispatch_or_pause_transition(self, _truth):
        service = DesktopService("racer")
        agent = agent_for(service, DryRunInput())
        with patch("kbm.steps.sleep_ms"):
            action = agent.handle({"op": "act", "action": "brake"})
            pause = agent.handle({"op": "pause"})
        self.assertTrue(action["ok"])
        self.assertFalse(action["input_sent"])
        self.assertFalse(action["application_confirmed"])
        self.assertIsNone(action["command"])
        self.assertFalse(pause["ok"])
        self.assertFalse(pause["input_sent"])
        self.assertFalse(service.paused)

    def test_visible_busy_indicator_rejects_input_before_dispatch(self, _truth):
        agent = agent_for(DesktopService("moba"))
        with patch.object(agent.game, "observe", return_value={"state": {"input_ready": False}, "description": ""}):
            result = agent.handle({"op": "act", "action": "attack"})
        self.assertFalse(result["applied"])
        self.assertFalse(result["input_sent"])
        self.assertTrue(result["busy"])
        self.assertEqual(agent.backend.events, [])


class PreparedWindowTargetTests(unittest.TestCase):
    def windows(self):
        return [{"kCGWindowOwnerName": "python", "kCGWindowName": "Teaching Lab - Lane Racer",
                 "kCGWindowNumber": number, "kCGWindowOwnerPID": pid,
                 "kCGWindowBounds": {"X": 20, "Y": 30, "Width": width, "Height": 828}}
                for number, pid, width in ((1, 456, 1600), (2, 123, 1100))]

    def test_prepared_pid_selects_exact_instance_despite_larger_duplicate_title(self):
        platform = MacPlatform("python", "Teaching Lab - Lane Racer", pid=123)
        with patch.object(platform, "_windows", return_value=self.windows()):
            window = platform.find_window()
        self.assertEqual((window["id"], window["pid"]), (2, 123))

    def test_without_pid_existing_largest_window_behavior_is_preserved(self):
        platform = MacPlatform("python", "Teaching Lab - Lane Racer")
        with patch.object(platform, "_windows", return_value=self.windows()):
            self.assertEqual(platform.find_window()["pid"], 456)

    def test_missing_prepared_pid_never_falls_back_to_duplicate_title(self):
        platform = MacPlatform("python", "Teaching Lab - Lane Racer", pid=999)
        quartz = SimpleNamespace(kCGWindowListOptionAll=0, kCGNullWindowID=0,
                                 CGWindowListCopyWindowInfo=lambda *_: self.windows())
        with patch.object(platform, "_windows", return_value=self.windows()), patch("kbm.macos._quartz", return_value=quartz):
            with self.assertRaisesRegex(LookupError, "pid=999"):
                platform.find_window()

    def test_pid_override_is_validated_and_preserves_other_window_settings(self):
        game = GameCase("racer", interface="kbm", overrides={"window": {"pid": 123}})
        self.assertEqual(game.config["window"], {"owner": "python", "title_contains": "Teaching Lab - Lane Racer", "pid": 123})
        self.assertEqual(game.configuration()["values"]["window"]["pid"], 123)
        for invalid in (None, 0, -1, True, "123", 1.5):
            with self.subTest(pid=invalid), self.assertRaisesRegex(ValueError, "window.pid"):
                GameCase("racer", interface="kbm", overrides={"window": {"pid": invalid}})

    def test_agent_entrypoint_passes_prepared_pid_from_connection_settings(self):
        from kbm_agent import main
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "settings.json"
            settings.write_text(json.dumps({"window": {"pid": 123}}))
            argv = ["kbm_agent.py", "--game", "racer", "--interface", "kbm", "--version", "desktop-v1",
                    "--games-dir", str(GAMES), "--socket", str(Path(directory) / "agent.sock"),
                    "--settings", str(settings), "--dry-run"]
            with patch("sys.argv", argv), patch("sys.stderr", io.StringIO()), patch("kbm_agent.MacPlatform") as platform, \
                    patch("kbm_agent.AgentService"), patch("kbm_agent.GameServer"), patch("kbm_agent.signal.signal"):
                main()
            platform.assert_called_once_with("python", "Teaching Lab - Lane Racer", 123)


if __name__ == "__main__":
    unittest.main()
