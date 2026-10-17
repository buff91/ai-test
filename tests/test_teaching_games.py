"""Regressions for the three-game teaching runtime and its pixel-only adapter."""
import time
import unittest
from pathlib import Path
from PIL import Image, ImageDraw
from gamelab import screens
from gamelab.desktop_game import DesktopService, KEYS
from gamelab.game_case import GameCase, list_games
from kbm.vision import Frame
from sims import SIMS
from sims.base import truth
from sims.moba import MobaSim


class TeachingCatalogTests(unittest.TestCase):
    def test_three_builtin_games_and_external_catalog(self):
        self.assertEqual(set(list_games()), {"party", "racer", "moba", "slay_the_spire_2"})
        self.assertEqual(set(SIMS), {"party", "racer", "moba"})

    def test_every_route_has_exact_action_mapping_and_correct_display(self):
        for game, cls in SIMS.items():
            for version, routes in (("sim-v1", ("socket", "attach")), ("desktop-v1", ("app", "attach", "kbm"))):
                for route in routes:
                    with self.subTest(game=game, version=version, route=route):
                        case = GameCase(game, version=version, interface=route)
                        self.assertEqual(set(case.actions), set(cls.ACTIONS))
                        self.assertEqual(case.target["display"], "visible" if version == "desktop-v1" else "headless")
                        self.assertTrue(case.cases())
            self.assertEqual(set(KEYS[game].values()), set(cls.ACTIONS))


class MobaRulesTests(unittest.TestCase):
    def test_wave_clear_hits_minions_and_cooldown_expires(self):
        sim = MobaSim.start("wave_clear")
        sim.apply("skill")
        self.assertEqual(sim.enemy_minions[1], 1)
        self.assertEqual(sim.gold, 30)
        self.assertEqual(sim.cooldown, 3)
        sim.apply("skill")
        self.assertEqual(sim.enemy_minions[1], 1)
        sim.world_step()
        self.assertEqual(sim.cooldown, 2)

    def test_unguarded_dive_hurts_but_allied_minions_protect(self):
        sim = MobaSim.start("unsafe_dive")
        self.assertTrue(sim.incoming_truth())
        self.assertEqual(sim.danger_truth(), 2)
        sim.world_step()
        self.assertEqual(sim.hp, 6)
        safe = MobaSim.start("tower_push")
        safe.world_step()
        self.assertEqual(safe.hp, 100)

    def test_destroying_core_wins(self):
        sim = MobaSim.start("core_finish")
        sim.step("skill")
        self.assertEqual(sim.outcome, "won")

    def test_lane_pressure_destroys_towers_then_core(self):
        sim = MobaSim.start("defend_bot")
        sim.world_step()
        self.assertEqual(sim.allied_towers[2], 2)
        sim.world_step()
        self.assertEqual(sim.allied_towers[2], 0)
        sim.world_step()
        self.assertLess(sim.allied_core, 100)

    def test_scenario_truth_is_fresh_and_valid(self):
        for name in MobaSim.SCENARIOS:
            sim = MobaSim.start(name)
            answer = truth(sim)
            self.assertTrue(set(answer["action"] or sim.ACTIONS) <= set(sim.ACTIONS))
            self.assertIn(answer["danger"], (0, 1, 2))
            self.assertIsInstance(answer["incoming_attack"], bool)


class DesktopClockTests(unittest.TestCase):
    def test_turn_battle_waits_for_input(self):
        service = DesktopService("party")
        before = service.render_state()["snapshot"]
        service.advance(20)
        self.assertEqual(before, service.render_state()["snapshot"])
        self.assertTrue(service.handle({"op": "step", "action": "aoe_skill"})["ok"])
        self.assertEqual(service.sim.turn, 2)

    def test_realtime_keeps_moving_without_model(self):
        for game in ("racer", "moba"):
            with self.subTest(game=game):
                service = DesktopService(game, tick_ms=1200)
                self.assertFalse(service.paused)
                service.advance(2.5)
                self.assertEqual(service.sim.turn, 3)
                self.assertFalse(service.paused)
                info = service.handle({"op": "info"})
                self.assertTrue(info["continuous"])
                self.assertEqual(info["build"], {"game": game, "version": "desktop-v1"})

    def test_pause_halt_reset_and_stale_decisions(self):
        service = DesktopService("racer", tick_ms=1200)
        response = service.handle({"op": "step", "action": "brake"})
        self.assertTrue(response["ok"])
        revision = response["revision"]
        service.advance(1.3)
        late_frame = service.handle({"op": "step", "action": "keep", "expected_revision": revision})
        self.assertTrue(late_frame["ok"])  # A clock frame is not a model turn boundary.
        service.handle({"op": "halt"}); before = service.sim.turn
        service.advance(20); self.assertEqual(service.sim.turn, before)
        self.assertFalse(service.handle({"op": "step", "action": "keep"})["ok"])
        service.handle({"op": "reset", "scenario": "long_straight"})
        self.assertEqual(service.sim.scenario, "long_straight")
        self.assertFalse(service.paused)
        stale = service.handle({"op": "step", "action": "keep", "expected_revision": revision})
        self.assertTrue(stale["stale"])
        self.assertTrue(service.handle({"op": "step", "action": "boost"})["ok"])

    def test_realtime_input_is_independent_of_clock_and_other_controls(self):
        service = DesktopService("racer", tick_ms=1200)
        for action in ("brake", "brake", "steer_left", "accelerate"):
            self.assertTrue(service.handle({"op": "step", "action": action})["ok"])
        self.assertEqual(service.sim.turn, 1)
        self.assertEqual(service.sim.speed, 3)
        self.assertEqual(service.sim.lane, 0)
        before = service.render_state()["snapshot"]
        service.advance(.12)
        after = service.render_state()["snapshot"]
        self.assertGreater(after["realtime_position"], before["realtime_position"])
        self.assertEqual(service.sim.turn, 1)

    def test_expired_budget_rejected_without_applying(self):
        service = DesktopService("racer")
        before = service.sim.snapshot()
        r = service.handle({"op": "step", "action": "brake", "observed_at": time.time()-3, "max_age_ms": 500})
        self.assertTrue(r["stale"])
        self.assertEqual(before, service.sim.snapshot())


class VisibleHudTests(unittest.TestCase):
    def frame(self, game, state, scale=1, title=28):
        image = Image.new("RGB", (screens.WIDTH*scale, (screens.HEIGHT+title)*scale), (11, 17, 32))
        draw = ImageDraw.Draw(image)
        for x in (24, 1076):
            draw.rectangle(((x-3)*scale, (title+517)*scale, (x+3)*scale, (title+523)*scale), fill=screens.ANCHOR)
        for i, value in enumerate(screens.encode(game, state)):
            for bit, (x, y) in enumerate(screens.instrument(i)[2]):
                draw.rectangle(((x-2)*scale, (title+y-3)*scale, (x+2)*scale, (title+y+3)*scale), fill=screens.ON if value & (1<<bit) else screens.OFF)
        return Frame(image)

    def test_every_scenario_decodes_from_pixels_at_native_and_retina_scale(self):
        for game, cls in SIMS.items():
            for scenario in cls.SCENARIOS:
                state = {**cls.start(scenario).snapshot(), "paused": game != "party"}
                for scale in (1, 2):
                    with self.subTest(game=game, scenario=scenario, scale=scale):
                        result = screens.observe(self.frame(game, state, scale), game)
                        expected = {k: v for k, v in state.items() if k != "scenario"}
                        self.assertEqual(result["state"], expected)
                        self.assertNotIn("truth", result)

    def test_actual_macos_capture_decodes_color_managed_hud(self):
        path = Path(__file__).parent / "fixtures" / "teaching_party" / "actual.png"
        with Image.open(path) as capture:
            frame = Frame(capture)
        observed = screens.observe(frame, "party")
        self.assertEqual(observed["state"], {
            "paused": False, "outcome": "playing", "turn": 2,
            "paladin_hp": 60, "knight_hp": 56, "mana": 22,
            "phoenix_feathers": 1, "dragon_hp": 0, "dragon_next": None,
            "goblins": [1, 11, 11, 11],
        })
        self.assertNotIn("truth", observed)
        with self.assertRaises(ValueError):
            screens.observe(frame, "racer")
        with self.assertRaises(ValueError):
            screens.observe(frame, "moba")

    def test_wrong_game_and_corrupt_frame_are_rejected(self):
        state = DesktopService("moba").render_state()["snapshot"]
        values = screens.encode("moba", state); values[7] ^= 1
        with self.assertRaises(ValueError): screens.decode("moba", values)
        with self.assertRaises(ValueError): screens.observe(self.frame("moba", state), "party")
        blank = Frame(Image.new("RGB", (1100, 800)))
        with self.assertRaises(ValueError): screens.observe(blank, "moba")


if __name__ == "__main__": unittest.main()
