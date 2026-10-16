"""The arena kbm observer on a real capture of the built game, and on frames drawn with game.py's layout.

The drawn frames use the colors measured on screen (not game.py's hex values, which render ~25 off).
"""

import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from gamelab.game_case import GameCase
from kbm.vision import Frame


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "arena_kbm"
PLAYER, TURRET, CHARGING, COVER, EXIT = (85, 145, 167), (113, 73, 86), (244, 106, 120), (79, 102, 119), (34, 58, 61)


def arena_frame(player, turret, charging=False, width=800, scale=2):
    """A window like game.py draws it: title bar + header, canvas, then HUD and buttons."""
    height = 618
    image = Image.new("RGB", (width * scale, height * scale), (16, 22, 33))
    d = ImageDraw.Draw(image)
    s = lambda *v: [round(x * scale) for x in v]
    top, bottom = 94, 468                      # the canvas inside the window
    d.rectangle(s(22, top, width - 22, bottom), fill=(19, 28, 41))
    floor = bottom - 80
    x_at = lambda t: 22 + 58 + (width - 44 - 116) * t / 8
    d.rectangle(s(22, floor, width - 22, bottom), fill=(36, 50, 66))
    d.rectangle(s(x_at(2) - 24, floor - 54, x_at(2) + 24, floor), fill=COVER, outline=(101, 217, 232))
    d.rectangle(s(x_at(8) - 24, floor - 125, x_at(8) + 24, floor), fill=EXIT, outline=(162, 225, 175), width=2 * scale)
    if turret is not None:
        color = CHARGING if charging else (244, 184, 118)
        d.rectangle(s(x_at(turret) - 30, floor - 90, x_at(turret) + 30, floor - 20), fill=TURRET, outline=color, width=2 * scale)
        if charging:
            d.line(s(x_at(turret) - 30, floor - 55, x_at(player), floor - 60), fill=color, width=2 * scale)
    p = x_at(player)
    d.polygon(s(p, floor - 85, p + 20, floor - 68, p + 17, floor - 28, p - 17, floor - 28, p - 20, floor - 68),
              fill=PLAYER, outline=(101, 217, 232))
    d.text(s(40, 500), "HP 100 / 100", fill=(143, 227, 171))   # green HUD text below the canvas
    return Frame(image)


class ArenaKbmObserverTest(unittest.TestCase):
    def setUp(self):
        self.game = GameCase("arena", interface="kbm")

    def test_real_capture_of_the_built_game(self):
        # dist/ArenaGame/ArenaGame at the start of "교전 시작": player on tile 1, turret on tile 6, not charging.
        with Image.open(FIXTURES / "opening.png") as image:
            state = self.game.observe(Frame(image))["state"]
        self.assertEqual((state["player_tile"], state["turret_tile"], state["charging"]), (1, 6, False))

    def test_reads_tiles_and_charging(self):
        for player, turret, charging in ((1, 6, False), (2, 5, True), (4, 6, True), (7, None, False)):
            with self.subTest(player=player, turret=turret):
                state = self.game.observe(arena_frame(player, turret, charging))["state"]
                self.assertEqual(state["player_tile"], player)
                self.assertEqual(state["turret_tile"], turret)
                self.assertEqual(state["turret_alive"], turret is not None)
                self.assertEqual(state["charging"], charging)
                self.assertEqual(state["in_cover"], player == 2)

    def test_a_resized_window_still_maps_tiles(self):
        state = self.game.observe(arena_frame(3, 6, width=1100, scale=1))["state"]
        self.assertEqual((state["player_tile"], state["turret_tile"]), (3, 6))

    def test_without_landmarks_it_says_the_arena_is_not_visible(self):
        observed = self.game.observe(Frame(Image.new("RGB", (800, 618), (5, 5, 5))))
        self.assertFalse(observed["state"]["landmarks"])
        self.assertIn("not visible", observed["description"])


if __name__ == "__main__":
    unittest.main()
