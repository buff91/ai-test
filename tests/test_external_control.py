"""Contract checks without Chromium; smoke.py separately checks real browser input."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from examples.external_control.browser_bridge import BrowserServer, BrowserService
from gamelab.game_case import GameCase, ROOT, check_build


EXAMPLE_GAMES = ROOT / "examples" / "external_control" / "games"


class ExternalControlTests(unittest.TestCase):
    def page(self):
        page = Mock()
        page.locator.return_value.is_visible.return_value = True
        page.locator.return_value.inner_text.return_value = "목표: 3\n현재: 0\n완료: 아니오\n리비전: 0"
        return page

    def test_examples_load_in_the_common_console_contract(self):
        for name, mode, display in (("browser", "launch", "visible"),
                                    ("headless", "launch", "headless"),
                                    ("attach", "attach", "unknown")):
            with self.subTest(interface=name):
                game = GameCase("web_counter", EXAMPLE_GAMES, interface=name)
                self.assertEqual((game.target["mode"], game.target["display"]), (mode, display))
                self.assertEqual(game.connector, "socket")
                self.assertEqual(set(game.actions), set(game.options))
                self.assertTrue(game.cases())

    def test_manual_page_change_rejects_an_old_decision_without_clicking(self):
        page = self.page()
        service = BrowserService(page)
        previous = service.handle({"op": "state"})
        # A user clicked outside the bridge after the model observed the page.
        page.locator.return_value.inner_text.return_value = "목표: 3\n현재: 1\n완료: 아니오\n리비전: 1"
        result = service.handle({"op": "step", "action": "increase", "expected_revision": previous["revision"]})
        self.assertFalse(result["ok"])
        self.assertTrue(result["stale"])
        self.assertEqual(result["snapshot"]["count"], 1)
        page.get_by_role.assert_not_called()

    def test_a_click_with_no_dom_effect_is_not_reported_as_success(self):
        page = self.page()
        service = BrowserService(page)
        result = service.handle({"op": "step", "action": "increase"})
        page.get_by_role.return_value.click.assert_called_once()
        self.assertFalse(result["ok"])
        self.assertIn("예상과 다릅니다", result["error"])
        self.assertIn("revision", result)

    def test_invalid_action_does_not_reach_the_page(self):
        page = self.page()
        result = BrowserService(page).handle({"op": "step", "action": "unknown-button"})
        self.assertFalse(result["ok"])
        page.get_by_role.assert_not_called()

    def test_external_url_does_not_claim_the_bundled_build(self):
        game = GameCase("web_counter", EXAMPLE_GAMES)
        external = BrowserService(self.page(), bundled=False).handle({"op": "info"})
        bundled = BrowserService(self.page()).handle({"op": "info"})
        self.assertEqual(check_build(game, external["build"])["status"], "unverified")
        self.assertEqual(check_build(game, bundled["build"])["status"], "verified")

    def test_existing_socket_path_is_never_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "occupied.sock"
            path.write_text("owned by another process")
            with self.assertRaises(FileExistsError):
                BrowserServer(path, None)
            self.assertEqual(path.read_text(), "owned by another process")


if __name__ == "__main__":
    unittest.main()
