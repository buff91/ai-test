"""Sims know their own truth; answers are graded against it; policy steers on Score/Noul."""

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from gamelab import grade
from gamelab.game_case import GAMES, GameCase, list_games
from gamelab.policy import Policy, validate
from gamelab.runner import play, report
from gamelab.socket_game import SocketGame
from sims import SIMS
from sims.__main__ import generate_cases
from sims.base import truth
from tests.test_games import FakeDecider, LocalSocket


class SimContractTests(unittest.TestCase):
    def test_every_sim_has_a_game_folder_that_matches_it(self):
        self.assertEqual(set(SIMS), set(list_games()))
        for game_id, sim in SIMS.items():
            with self.subTest(game=game_id):
                game = GameCase(game_id)
                self.assertEqual(set(game.options), set(sim.ACTIONS))
                self.assertEqual(game.config["launch"]["command"][-1], game_id)
                ids = {qid for qid in sim.QUESTION_IDS.values() if qid}
                self.assertEqual(set(game.questions), ids)

    def test_truth_fits_the_questions_and_is_quick(self):
        for game_id, sim in SIMS.items():
            questions = GameCase(game_id).questions
            for scenario in sim.SCENARIOS:
                with self.subTest(game=game_id, scenario=scenario):
                    started = time.perf_counter()
                    answer = truth(sim.start(scenario))
                    self.assertLess(time.perf_counter() - started, 0.5)
                    grade.validate_truth(answer, questions, scenario)
                    self.assertEqual(set(answer), set(questions))

    def test_truth_names_the_obvious_move(self):
        expected = {("arena", "telegraph"): "dodge", ("arena", "empty_mag"): "reload",
                    ("cards", "kill_window"): "play_attack", ("party", "knight_low"): "heal",
                    ("party", "dragon_strike"): "guard", ("party", "knight_down"): "use_item",
                    ("shooter", "gunner_aiming"): "dodge", ("swarm", "single_in_range"): "attack_target",
                    ("racer", "hairpin_fast"): "brake", ("racer", "long_straight"): "boost",
                    ("racer", "obstacle_left"): "steer_right"}
        for (game_id, scenario), action in expected.items():
            with self.subTest(game=game_id, scenario=scenario):
                self.assertIn(action, truth(SIMS[game_id].start(scenario))["action"])

    def test_danger_and_incoming_follow_holding_still(self):
        arena = SIMS["arena"].start("telegraph")       # the charged shot fires this turn
        self.assertEqual(truth(arena)["danger"], 2)
        self.assertTrue(truth(arena)["incoming_attack"])
        opening = SIMS["arena"].start("opening")       # out of range, nothing can hit
        self.assertEqual(truth(opening)["danger"], 0)
        self.assertFalse(truth(opening)["incoming_attack"])

    def test_following_the_truth_wins_every_scenario_but_doomed_ones(self):
        for game_id, sim_cls in SIMS.items():
            for scenario in sim_cls.SCENARIOS:
                sim = sim_cls.start(scenario)
                for _ in range(60):
                    if sim.outcome != "playing":
                        break
                    sim.step((truth(sim)["action"] or list(sim.legal()))[0])
                with self.subTest(game=game_id, scenario=scenario):
                    self.assertEqual(sim.outcome, "won")

    def test_generated_cases_are_valid_and_stable(self):
        for game_id, sim in SIMS.items():
            with self.subTest(game=game_id):
                self.assertEqual(generate_cases(sim, per_scenario=2), generate_cases(sim, per_scenario=2))
                self.assertGreaterEqual(len(GameCase(game_id).cases()), 20)

    def test_illegal_input_does_not_stall_a_card_game(self):
        sim = SIMS["cards"].start("kill_window")
        for _ in range(200):
            sim.step("play_power")              # no power card in this hand
        self.assertEqual(sim.outcome, "draw")


class GradeTests(unittest.TestCase):
    QUESTIONS = GameCase("arena").questions

    def test_each_type_is_graded_its_own_way(self):
        answers = {"action": {"choice": "attack"}, "danger": {"score": 1.6}, "incoming_attack": {"noul": 0.9}}
        graded = grade.grade(answers, {"action": ["dodge", "attack"], "danger": 2, "incoming_attack": True}, self.QUESTIONS)
        self.assertTrue(graded["action"]["correct"])
        self.assertAlmostEqual(graded["danger"]["abs_error"], 0.4)
        self.assertTrue(graded["danger"]["exact"])
        self.assertAlmostEqual(graded["incoming_attack"]["brier"], 0.01)
        self.assertIn("action✓", grade.marks(graded))

    def test_missing_truth_is_not_graded_and_bad_values_are_flagged(self):
        graded = grade.grade({"danger": {"score": 7}, "incoming_attack": {"noul": float("nan")}},
                             {"action": None, "danger": 1, "incoming_attack": False}, self.QUESTIONS)
        self.assertNotIn("action", graded)
        self.assertEqual(graded["danger"]["status"], "invalid")
        self.assertEqual(graded["incoming_attack"]["status"], "invalid")

    def test_summary(self):
        rows = [grade.grade({"action": {"choice": a}, "incoming_attack": {"noul": p}},
                            {"action": ["dodge"], "incoming_attack": hit}, self.QUESTIONS)
                for a, p, hit in [("dodge", 1.0, True), ("wait", 0.0, True)]]
        summary = grade.summarize(rows)
        self.assertEqual(summary["action"]["accuracy"], 0.5)
        self.assertEqual(summary["incoming_attack"]["brier"], 0.5)
        self.assertEqual(summary["incoming_attack"]["base_rate"], 1.0)

    def test_bad_case_truth_is_rejected(self):
        with tempfile.TemporaryDirectory() as games_dir:
            shutil.copytree(GAMES / "arena", Path(games_dir) / "arena")
            path = Path(games_dir) / "arena" / "cases.json"
            path.write_text(json.dumps([{"id": "x", "state": "s", "truth": {"danger": 3}}]), encoding="utf-8")
            with self.assertRaises(ValueError):
                GameCase("arena", games_dir).cases()


def decision(action, probabilities, **answers):
    full = {"action": {"choice": action, "probabilities": probabilities}}
    full.update({qid: {"noul" if qid == "incoming_attack" else "score": v} for qid, v in answers.items()})
    return {"status": "ok", "provider": "laya-mlx", "action": action, "probabilities": probabilities,
            "answers": full, "model_ms": 10}


class PolicyTests(unittest.TestCase):
    QUESTIONS = GameCase("shooter").questions
    SPEC = {"guards": [{"name": "피격", "when": "incoming_attack >= 0.7", "allow": ["dodge", "retreat"]}],
            "escalate": {"when": "danger >= 1.5", "to": "jev"}}

    def test_guard_swaps_to_the_models_own_best_allowed_action(self):
        policy = Policy(self.SPEC, self.QUESTIONS, enabled=True)
        final, record = policy.apply(decision("advance", {"advance": .6, "retreat": .3, "dodge": .1}, incoming_attack=.9), "s")
        self.assertEqual(final["action"], "retreat")
        self.assertEqual(record["override"], {"rule": "피격", "from": "advance", "to": "retreat"})

    def test_disabled_policy_only_records(self):
        policy = Policy(self.SPEC, self.QUESTIONS)
        final, record = policy.apply(decision("advance", {"advance": 1}, incoming_attack=.9, danger=2), "s")
        self.assertEqual(final["action"], "advance")
        self.assertEqual(record["fired"], ["escalate", "피격"])
        self.assertIsNone(record["override"])

    def test_quiet_answers_fire_nothing(self):
        _, record = Policy(self.SPEC, self.QUESTIONS, enabled=True).apply(
            decision("advance", {"advance": 1}, incoming_attack=.1, danger=0.2), "s")
        self.assertEqual(record["fired"], [])

    def test_escalation_asks_the_stronger_model(self):
        strong = FakeDecider("jev", "dodge", answers={"incoming_attack": {"noul": .95}})
        policy = Policy(self.SPEC, self.QUESTIONS, enabled=True, escalate_decider=strong)
        final, record = policy.apply(decision("advance", {"advance": 1}, danger=1.9), "state text")
        self.assertEqual(final["action"], "dodge")
        self.assertEqual(final["escalated_from"], "laya-mlx")
        self.assertEqual(strong.seen, ["state text"])
        self.assertIsNone(record["override"])          # jev already chose an allowed action

    def test_policy_is_validated_against_the_game(self):
        options = GameCase("shooter").options
        for bad in [{"guards": [{"when": "nope >= 1", "allow": ["dodge"]}]},
                    {"guards": [{"when": "danger >= 1", "allow": ["teleport"]}]},
                    {"guards": [{"when": "danger => 1", "allow": ["dodge"]}]},
                    {"escalate": {"when": "danger >= 1", "to": "gpt"}}]:
            with self.subTest(policy=bad), self.assertRaises(ValueError):
                validate(bad, self.QUESTIONS, options, ("jev", "laya", "laya-mlx"), "test")


class RunnerTests(unittest.TestCase):
    def test_runs_are_graded_and_the_model_never_sees_the_truth(self):
        game = GameCase("shooter")
        client = SocketGame(game, LocalSocket(SIMS["shooter"], "clear_path"))
        decider = FakeDecider("laya-mlx", "advance", answers={"danger": {"score": 0}, "incoming_attack": {"noul": 0.1}})
        logs = []
        play(client, decider, {"interval_ms": 0, "max_age_ms": 1000}, record=False, log=logs.append,
             questions=game.questions, episodes=1)
        self.assertTrue(all(isinstance(s, str) and "truth" not in s for s in decider.seen))
        self.assertEqual(len(decider.seen), 4)          # four advances reach the exit, then the episode ends
        self.assertIn("— 판 1 끝: won", logs)
        self.assertIn("정답률 100.0%", "\n".join(logs))

    def test_reset_starts_the_next_scenario(self):
        game = GameCase("shooter")
        client = SocketGame(game, LocalSocket(SIMS["shooter"], "clear_path"))
        logs = []
        play(client, FakeDecider("laya-mlx", "advance"), {"interval_ms": 0, "max_age_ms": 1000}, steps=5,
             record=False, log=logs.append, questions=game.questions)
        # clear_path is won in four steps; the fifth is played in the next scenario.
        self.assertEqual(client.request("observe")["state"]["scenario"], "last_stand")

    def test_policy_override_is_applied_and_reported(self):
        game = GameCase("arena")
        client = SocketGame(game, LocalSocket(SIMS["arena"], "telegraph"))
        decider = FakeDecider("jev", "advance", answers={"incoming_attack": {"noul": 0.9}})
        policy = Policy(game.policy, game.questions, enabled=True)
        with tempfile.TemporaryDirectory() as results:
            from gamelab import runner
            old, runner.RESULTS = runner.RESULTS, Path(results)
            try:
                logs = []
                session = play(client, decider, {"interval_ms": 0, "max_age_ms": 1000}, steps=1, log=logs.append,
                               questions=game.questions, policy=policy)
            finally:
                runner.RESULTS = old
            summary = report(session)
        self.assertEqual(client.request("observe")["state"]["hp"], 100)   # dodged instead of being shot
        self.assertEqual(summary["policy"]["overrides"], 1)
        self.assertEqual(summary["policy"]["helped"], 1)
        self.assertEqual(summary["final_action_accuracy"], 1.0)


class LaunchTests(unittest.TestCase):
    def test_play_py_launches_a_sim_over_a_real_socket(self):
        game = GameCase("racer")
        with game.connect() as client:
            observed = client.request("observe")
            self.assertTrue(observed["ok"])
            self.assertEqual(observed["truth"]["action"], ["brake"])
            result = client.request("act", action="brake", signature=observed["signature"])
            self.assertTrue(result["applied"], result)


if __name__ == "__main__":
    unittest.main()
