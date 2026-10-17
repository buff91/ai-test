"""Continuous control and collision rules, separate from offline turn samples."""
import unittest

from sims.base import truth
from sims.moba import MobaSim
from sims.racer import RacerSim


class RealtimeRacerTests(unittest.TestCase):
    def test_car_moves_between_world_beats_without_input(self):
        sim = RacerSim.start("long_straight")
        sim.advance_time(.1)
        self.assertAlmostEqual(sim.snapshot()["realtime_position"], 20.3)
        self.assertEqual(sim.pos, 20)
        self.assertEqual(sim.turn, 1)
        sim.advance_time(.9)
        self.assertAlmostEqual(sim.snapshot()["realtime_position"], 23)
        self.assertEqual(sim.turn, 2)

    def test_controls_change_immediately_and_do_not_move_time(self):
        sim = RacerSim.start("obstacle_left")
        sim.realtime_input("steer_right")
        sim.realtime_input("brake")
        self.assertEqual((sim.lane, sim.speed, sim.pos, sim.turn), (1, 2, 6, 1))
        sim.advance_time(.15)
        sim.realtime_input("steer_right")
        self.assertEqual(sim.lane, 2)

    def test_long_frame_sweeps_hazards_and_continues_at_crash_speed(self):
        sim = RacerSim.start("obstacle_left")
        events = sim.advance_time(2)
        self.assertEqual(len(events), 1)
        self.assertEqual((sim.integrity, sim.speed), (2, 1))
        self.assertAlmostEqual(sim.snapshot()["realtime_position"], 10)

    def test_small_and_large_frames_have_same_collision_result(self):
        large = RacerSim.start("hairpin_fast")
        small = RacerSim.start("hairpin_fast")
        large.advance_time(2.7)
        for _ in range(27):
            small.advance_time(.1)
        self.assertEqual(large.snapshot(), small.snapshot())
        self.assertEqual(large.integrity, 2)

    def test_terminal_time_is_identical_for_large_and_small_frames(self):
        large = RacerSim.start("long_straight")
        small = RacerSim.start("long_straight")
        large.advance_time(10)
        small_elapsed = 0.0
        for _ in range(1000):
            small.advance_time(.01)
            small_elapsed += small._realtime_advanced_seconds
        self.assertEqual(large.snapshot(), small.snapshot())
        self.assertEqual((large.outcome, large.turn), ("won", 7))
        self.assertAlmostEqual(large._realtime_advanced_seconds, 20 / 3)
        self.assertAlmostEqual(small_elapsed, large._realtime_advanced_seconds)

    def test_reference_remains_an_integer_search_sample(self):
        sim = RacerSim.start("hairpin_fast")
        sim.advance_time(.1)
        reference = sim.realtime_reference()
        self.assertNotIn("realtime_position", reference.snapshot())
        self.assertIn("brake", truth(reference)["action"])

    def test_description_explains_continuous_motion(self):
        sim = RacerSim.start("long_straight")
        sim.advance_time(.1)
        description = sim.describe_realtime()
        self.assertIn("Continuous track position 20.30", description)
        self.assertIn("keeps moving without input", description)
        self.assertNotIn("Each turn", description)


class RealtimeMobaTests(unittest.TestCase):
    def test_movement_is_continuous_and_repeated_commands_do_not_stack(self):
        sim = MobaSim.start("wave_clear")
        sim.realtime_input("advance")
        for _ in range(10):
            sim.realtime_input("advance")
        sim.advance_time(.25)
        self.assertAlmostEqual(sim.snapshot()["realtime_position"], 3.25)
        self.assertEqual(sim.turn, 1)
        sim.realtime_input("wait")
        sim.advance_time(.25)
        self.assertAlmostEqual(sim.snapshot()["realtime_position"], 3.25)

    def test_attack_cooldown_does_not_block_movement_or_skill(self):
        sim = MobaSim.start("wave_clear")
        sim.realtime_input("attack")
        self.assertEqual(sim.enemy_minions[1], 3)
        sim.realtime_input("attack")
        self.assertEqual(sim.enemy_minions[1], 3)
        sim.realtime_input("advance")
        sim.realtime_input("skill")
        self.assertEqual(sim.enemy_minions[1], 0)
        sim.advance_time(.1)
        self.assertAlmostEqual(sim.snapshot()["realtime_position"], 3.1)
        self.assertEqual(sim.turn, 1)

    def test_attack_is_available_again_before_next_world_beat(self):
        sim = MobaSim.start("wave_clear")
        sim.realtime_input("attack")
        sim.advance_time(.5)
        sim.realtime_input("attack")
        self.assertEqual(sim.enemy_minions[1], 2)
        self.assertEqual(sim.turn, 1)

    def test_recall_cannot_be_spammed_for_instant_full_health(self):
        sim = MobaSim.start("unsafe_dive")
        sim.realtime_input("recall")
        self.assertEqual((sim.hp, sim.position), (54, 0))
        sim.realtime_input("recall")
        self.assertEqual(sim.hp, 54)
        sim.advance_time(4)
        sim.realtime_input("recall")
        self.assertEqual(sim.hp, 84)

    def test_skill_cooldown_uses_elapsed_seconds(self):
        sim = MobaSim.start("wave_clear")
        sim.realtime_input("skill")
        sim.advance_time(.25)
        self.assertEqual(sim.snapshot()["skill_cooldown_seconds"], 2.75)
        self.assertEqual(sim.cooldown, 3)
        sim.advance_time(2.75)
        self.assertEqual(sim.cooldown, 0)

    def test_small_and_large_frames_have_same_moving_world_result(self):
        large = MobaSim.start("wave_clear")
        small = MobaSim.start("wave_clear")
        for sim in (large, small):
            sim.realtime_input("advance")
            sim.realtime_input("attack")
        large.advance_time(2.7)
        for _ in range(27):
            small.advance_time(.1)
        self.assertEqual(large.snapshot(), small.snapshot())
        self.assertEqual(large.turn, 3)

    def test_terminal_world_does_not_consume_remaining_frame_time(self):
        large = MobaSim.start("unsafe_dive")
        small = MobaSim.start("unsafe_dive")
        large.advance_time(10)
        small_elapsed = 0.0
        for _ in range(1000):
            small.advance_time(.01)
            small_elapsed += small._realtime_advanced_seconds
        self.assertEqual(large.snapshot(), small.snapshot())
        self.assertEqual(large.outcome, "lost")
        self.assertEqual(large._realtime_advanced_seconds, 2)
        self.assertAlmostEqual(small_elapsed, 2)

    def test_reference_omits_live_movement_and_timers(self):
        sim = MobaSim.start("wave_clear")
        sim.realtime_input("advance")
        sim.realtime_input("skill")
        sim.advance_time(.3)
        reference = sim.realtime_reference()
        self.assertNotIn("realtime_position", reference.snapshot())
        self.assertEqual(reference.cooldown, 3)
        self.assertIsNotNone(truth(reference))

    def test_description_matches_live_movement_and_second_cooldowns(self):
        sim = MobaSim.start("wave_clear")
        sim.realtime_input("advance")
        sim.realtime_input("skill")
        sim.advance_time(.25)
        description = sim.describe_realtime()
        self.assertIn("position 3.25", description)
        self.assertIn("starts continuous movement", description)
        self.assertIn("wait stops it", description)
        self.assertIn("skill cooldown 2.75s", description)
        self.assertIn("Current movement command: advance", description)
        self.assertNotIn("move one cell", description)
        self.assertNotIn("cools for three beats", description)


if __name__ == "__main__":
    unittest.main()
