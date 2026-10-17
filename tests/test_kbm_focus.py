"""Explicit focus targets the observed game and never emits input in these tests."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gamelab.game_case import GameCase
from kbm.connector import AgentClient, connect
from kbm.macos import MacPlatform


TARGET = {"id": 22, "pid": 123, "x": 90, "y": 33, "width": 1100, "height": 828}


def window(number=22, pid=123, owner="Game", title="My Game"):
    return {"kCGWindowNumber": number, "kCGWindowOwnerPID": pid, "kCGWindowOwnerName": owner,
            "kCGWindowName": title, "kCGWindowLayer": 0,
            "kCGWindowBounds": {"X": 90, "Y": 33, "Width": 1100, "Height": 828}}


class MacFocusTests(unittest.TestCase):
    def setUp(self):
        self.platform = MacPlatform("Game", "My Game", pid=123)
        self.application = Mock()
        self.application.isTerminated.return_value = False
        self.application.activateWithOptions_.return_value = True
        self.appkit = SimpleNamespace(NSRunningApplication=Mock(), NSApplicationActivateIgnoringOtherApps=2)
        self.appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_.return_value = self.application
        self.patch = patch("kbm.macos._appkit", return_value=self.appkit)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.front = window(1, 999, "Console", "Console")

    def test_activates_only_observed_pid_once_and_confirms_exact_window(self):
        with patch.object(self.platform, "_windows", side_effect=[[self.front, window()], [window(), self.front]]):
            result = self.platform.focus(TARGET)
        self.assertTrue(result["focused"])
        self.assertTrue(result["activation_requested"])
        self.assertEqual(result["window"], TARGET)
        self.appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_.assert_called_once_with(123)
        self.application.activateWithOptions_.assert_called_once_with(2)

    def test_already_frontmost_does_not_reactivate(self):
        with patch.object(self.platform, "_windows", return_value=[window(), self.front]):
            self.assertFalse(self.platform.focus(TARGET)["activation_requested"])
        self.application.activateWithOptions_.assert_not_called()

    def test_exact_window_confirmation_does_not_depend_on_cached_appkit_active_state(self):
        self.application.isActive.return_value = False
        with patch.object(self.platform, "_windows", side_effect=[[self.front, window()], [window(), self.front]]):
            self.assertTrue(self.platform.focus(TARGET)["focused"])
        self.application.activateWithOptions_.assert_called_once()
        self.application.isActive.assert_not_called()

    def test_mismatching_pid_missing_window_or_changed_owner_cannot_activate(self):
        for target, windows in (({**TARGET, "pid": 456}, [window()]), (TARGET, [self.front]),
                                (TARGET, [window(owner="Other Game")]),
                                (TARGET, [window(title="Other Title")])):
            with self.subTest(target=target, windows=windows), patch.object(self.platform, "_windows", return_value=windows):
                with self.assertRaises((LookupError, ValueError)):
                    self.platform.focus(target)
        self.appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_.assert_not_called()

    def test_ambiguous_owner_title_requires_pid_even_when_observer_chose_largest(self):
        platform = MacPlatform("Game", "My Game")
        for title in ("My Game", None):
            with self.subTest(title=title), patch.object(platform, "_windows", return_value=[
                    window(title=title), window(33, 456, title=title)]):
                with self.assertRaisesRegex(LookupError, "window.pid"):
                    platform.focus(TARGET)
        self.appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_.assert_not_called()

    def test_explicit_pid_and_observed_id_work_when_console_cannot_read_titles(self):
        with patch.object(self.platform, "_windows", side_effect=[
                [self.front, window(title=None), window(33, 456, title=None)],
                [window(title=None), self.front, window(33, 456, title=None)]]):
            self.assertTrue(self.platform.focus(TARGET)["focused"])
        self.application.activateWithOptions_.assert_called_once()

    def test_same_process_other_window_is_not_focus_confirmation(self):
        windows = [window(23), window(), self.front]
        with patch.object(self.platform, "_windows", return_value=windows), \
                patch("kbm.macos.time.monotonic", side_effect=[0, 2]):
            with self.assertRaisesRegex(RuntimeError, "전면으로 전환되지"):
                self.platform.focus(TARGET)
        self.application.activateWithOptions_.assert_called_once()

    def test_target_disappears_during_activation_does_not_retarget(self):
        with patch.object(self.platform, "_windows", side_effect=[[self.front, window()], [window(23), self.front]]):
            with self.assertRaisesRegex(LookupError, "사라졌거나"):
                self.platform.focus(TARGET)
        self.application.activateWithOptions_.assert_called_once()

    def test_activation_denial_or_terminated_process_fails(self):
        for terminated in (True, False):
            self.application.isTerminated.return_value = terminated
            self.application.activateWithOptions_.return_value = False
            with self.subTest(terminated=terminated), patch.object(self.platform, "_windows", return_value=[self.front, window()]):
                with self.assertRaises((LookupError, RuntimeError)):
                    self.platform.focus(TARGET)

    def test_invalid_observed_identifiers_never_reach_os(self):
        for target in (None, {}, {**TARGET, "pid": True}, {**TARGET, "id": 0}):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.platform.focus(target)
        self.appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_.assert_not_called()


class FocusConnectorTests(unittest.TestCase):
    def setUp(self):
        self.process = SimpleNamespace(pid=555, poll=lambda: None, wait=lambda **_: 0)
        self.config = {"owner": "Game", "title_contains": "My Game", "pid": 123}
        self.client = AgentClient("/tmp/test-focus.sock", self.process, "dev", window_config=self.config)

    def test_focus_observes_existing_agent_then_uses_only_its_window(self):
        with patch("kbm.connector.GameClient.request", return_value={"ok": True, "window": TARGET}) as request, \
                patch("kbm.connector.MacPlatform") as platform:
            platform.return_value.focus.return_value = {"ok": True, "focused": True, "window": TARGET}
            result = self.client.request("focus")
        request.assert_called_once_with("observe")
        platform.assert_called_once_with("Game", "My Game", 123)
        platform.return_value.focus.assert_called_once_with(TARGET)
        self.assertTrue(result["focused"])
        self.assertFalse(result["input_sent"])
        self.assertFalse(result["application_confirmed"])
        self.assertIsNone(result["command"])

    def test_focus_cannot_override_target_and_dry_run_has_no_side_effect(self):
        with patch("kbm.connector.GameClient.request") as request, patch("kbm.connector.MacPlatform") as platform:
            self.assertFalse(self.client.request("focus", pid=456)["ok"])
            self.client.dry_run = True
            self.assertFalse(self.client.request("focus")["ok"])
        request.assert_not_called()
        platform.assert_not_called()

    def test_failed_observation_does_not_attempt_focus(self):
        with patch("kbm.connector.GameClient.request", return_value={"ok": False, "error": "bad HUD"}), \
                patch("kbm.connector.MacPlatform") as platform:
            result = self.client.request("focus")
        platform.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertFalse(result["input_sent"])
        self.assertIn("bad HUD", result["error"])

    def test_focus_failure_is_reported_without_act_or_input(self):
        with patch("kbm.connector.GameClient.request", return_value={"ok": True, "window": TARGET}) as request, \
                patch("kbm.connector.MacPlatform") as platform:
            platform.return_value.focus.side_effect = RuntimeError("focus denied")
            result = self.client.request("focus")
        request.assert_called_once_with("observe")
        self.assertFalse(result["ok"])
        self.assertFalse(result["focused"])
        self.assertFalse(result["input_sent"])
        self.assertIn("focus denied", result["error"])

    def test_info_advertises_focus_without_activating(self):
        with patch("kbm.connector.GameClient.request", return_value={"ok": True}), \
                patch("kbm.connector.MacPlatform") as platform:
            self.assertTrue(self.client.request("info")["capabilities"]["focus"])
        platform.assert_not_called()

    def test_connect_launches_agent_in_background_and_passes_window_settings(self):
        game = GameCase("racer", version="desktop-v1", interface="kbm", overrides={"window": {"pid": 123}})
        with patch("kbm.connector.Path.exists", return_value=True), \
                patch("kbm.connector.subprocess.Popen", return_value=self.process) as launch, \
                patch("kbm.connector.build_info.file_info", return_value={}), \
                patch("kbm.connector._wait_ready"), patch("kbm.connector.AgentClient.request", return_value={"ok": True}):
            with connect(game, agent="app") as client:
                self.assertEqual(client.window_config, game.config["window"])
        self.assertEqual(launch.call_args.args[0][:4], ["open", "-g", "-n", "-W"])


if __name__ == "__main__":
    unittest.main()
