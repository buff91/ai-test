"""Render the complete scene to pixels and check the public observer contract."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

from gamelab import presentation, screens
from gamelab.desktop_game import DesktopService, GameWindow, KEYS, input_key
from kbm.keys import KEYCODES
from kbm.vision import Frame
from sims import SIMS


class PixelCanvas:
    """Canvas primitive rasterizer: scene overlap must not corrupt instruments.

    Text is recorded separately because the regression is cross-platform and
    does not require a locally installed Korean font or a GUI display.
    """
    def __init__(self):
        self.labels = []
        self.delete("all")

    def delete(self, _):
        self.image = Image.new("RGB", (screens.WIDTH, screens.HEIGHT), "#0b1120")
        self.draw = ImageDraw.Draw(self.image)
        self.labels = []

    def create_text(self, x, y, **kwargs):
        self.labels.append((x, y, kwargs["text"]))

    def create_rectangle(self, x1, y1, x2, y2, **kwargs):
        self.draw.rectangle((x1, y1, x2, y2), fill=kwargs.get("fill") or None,
                            outline=kwargs.get("outline") or None)

    def create_polygon(self, *points, **kwargs):
        self.draw.polygon(points, fill=kwargs.get("fill") or None,
                          outline=kwargs.get("outline") or None,
                          width=max(1, round(kwargs.get("width", 1))))

    def create_oval(self, *points, **kwargs):
        self.draw.ellipse(points, fill=kwargs.get("fill") or None,
                          outline=kwargs.get("outline") or None,
                          width=max(1, round(kwargs.get("width", 1))))

    def create_line(self, *points, **kwargs):
        self.draw.line(points, fill=kwargs["fill"], width=max(1, round(kwargs.get("width", 1))))


class DesktopPresentationTests(unittest.TestCase):
    def window(self, game, scenario):
        window = GameWindow.__new__(GameWindow)
        window.service = DesktopService(game, scenario)
        window.canvas = PixelCanvas()
        window.buttons = []
        window.visual_time = 0
        return window

    def assert_screen(self, window):
        window.draw()
        game = window.service.game_id
        observed = screens.observe(Frame(window.canvas.image), game)["state"]
        state = window.service.render_state()["snapshot"]
        expected = screens.decode(game, screens.encode(game, state))
        self.assertEqual(observed, expected)
        self.assertEqual({action for *_, action in window.buttons if not action.startswith("__")}, set(KEYS[game].values()))
        self.assertNotIn("truth", observed)

    def test_complete_render_remains_observable_for_every_scenario_and_action(self):
        for game, cls in SIMS.items():
            for scenario in cls.SCENARIOS:
                for action in cls.ACTIONS:
                    with self.subTest(game=game, scenario=scenario, action=action):
                        window = self.window(game, scenario)
                        self.assert_screen(window)
                        window.service.handle({"op": "step", "action": action})
                        self.assert_screen(window)
                        window.service.advance(3.1)
                        self.assert_screen(window)

    def test_terminal_overlay_does_not_cover_protocol_or_replay_controls(self):
        for game, cls in SIMS.items():
            for outcome in ("won", "lost", "draw"):
                with self.subTest(game=game, outcome=outcome):
                    window = self.window(game, next(iter(cls.SCENARIOS)))
                    window.service.sim.outcome = outcome
                    self.assert_screen(window)
                    self.assertIn("__reset", [action for *_, action in window.buttons])

    def test_rejected_input_is_explained_without_changing_state(self):
        window = self.window("racer", "hairpin_fast")
        window.keypress(Mock(keysym="Down"))
        before = window.service.render_state()["snapshot"]
        window.keypress(Mock(keysym="Left"))
        self.assertEqual(before, window.service.render_state()["snapshot"])
        self.assertIn("다음 박자", window.input_notice)
        self.assertTrue(any("다음 박자" in text for _, _, text in window.canvas.labels))

    def test_socket_and_manual_actions_share_visual_feedback(self):
        window = self.window("party", "goblin_pack")
        window.draw()
        window.service.handle({"op": "step", "action": "aoe_skill"})
        window.draw()
        self.assertTrue(window.effect_active)
        self.assertTrue(any(text == "최근 행동 · 빛의 파동" for _, _, text in window.canvas.labels))

    def test_feedback_and_controls_use_korean_display_names(self):
        self.assertEqual(GameWindow.event_label("Hero took 18 damage."), "영웅이 18 피해를 받았습니다.")
        self.assertEqual(GameWindow.event_label("Cleared 3 enemy minion(s)."), "적 미니언 3마리를 처치했습니다.")
        self.assertEqual(GameWindow.event_label("Hit the bot tower."), "하단 적 포탑을 공격했습니다.")
        for actions in KEYS.values():
            self.assertTrue(set(actions.values()) <= set(presentation.ACTION_LABELS))

    @patch("gamelab.desktop_game.sys.platform", "darwin")
    def test_observed_korean_p_and_arrow_events_control_the_real_service(self):
        window = self.window("racer", "hairpin_fast")
        # These exact events were recorded from the native bundled Tk window.
        korean_p = SimpleNamespace(keysym="??", char="ㅔ", keycode=587215188,
                                   keysym_num=0, state=0)
        down = SimpleNamespace(keysym="Down", char="", keycode=2097215233,
                               keysym_num=65364, state=64)
        self.assertEqual(input_key(korean_p), "p")
        window.keypress(korean_p)
        self.assertFalse(window.service.paused)
        self.assertEqual(window.service.revision, 1)
        window.keypress(korean_p)
        self.assertTrue(window.service.paused)
        self.assertEqual(window.service.revision, 2)
        window.keypress(korean_p)
        window.keypress(down)
        self.assertEqual(window.service.sim.speed, 3)
        self.assertEqual(window.service.last_action, "brake")

    @patch("gamelab.desktop_game.sys.platform", "darwin")
    def test_ime_fallback_covers_the_existing_controls_and_reset(self):
        control_keys = {key for mapping in KEYS.values() for key in mapping} | {"n", "p"}
        for key in control_keys:
            for state in (0, 1, 2, 3):  # normal, Shift, CapsLock, both
                with self.subTest(key=key, state=state):
                    event = SimpleNamespace(keysym="??", char="ㄱ",
                                            keycode=(KEYCODES[key] << 24) | ord("ㄱ"), state=state)
                    self.assertEqual(input_key(event), key)
        window = self.window("party", "goblin_pack")
        window.keypress(SimpleNamespace(keysym="??", keycode=(KEYCODES["n"] << 24) | ord("ㅜ"), state=0))
        self.assertEqual(window.service.sim.scenario, "dragon_strike")

    @patch("gamelab.desktop_game.sys.platform", "darwin")
    def test_ime_fallback_rejects_shortcut_modifiers_and_nonphysical_events(self):
        for state in (4, 8, 16, 12, 17, 31):
            with self.subTest(state=state):
                self.assertEqual(input_key(SimpleNamespace(keysym="??", keycode=587215188, state=state)), "??")
        for code in ((255 << 24) | ord("ㅔ"), (128 << 24) | ord("ㅔ"), -1, 1 << 32, None, "587215188"):
            with self.subTest(code=code):
                self.assertEqual(input_key(SimpleNamespace(keysym="??", keycode=code, state=0)), "??")
        # An unrelated physical key cannot become an action.
        self.assertEqual(input_key(SimpleNamespace(keysym="??", keycode=(KEYCODES["z"] << 24) | ord("ㅋ"), state=0)), "??")

    def test_symbolic_keys_and_other_platforms_preserve_existing_behavior(self):
        for platform in ("darwin", "win32", "linux"):
            with patch("gamelab.desktop_game.sys.platform", platform):
                # Prefer the symbolic control even if another layout puts it
                # on a different physical key, and accept existing test events.
                self.assertEqual(input_key(SimpleNamespace(keysym="W", keycode=587215188, state=0)), "w")
                self.assertEqual(input_key(SimpleNamespace(keysym="Down")), "down")
                if platform != "darwin":
                    self.assertEqual(input_key(SimpleNamespace(keysym="??", keycode=587215188, state=0)), "??")


if __name__ == "__main__":
    unittest.main()
