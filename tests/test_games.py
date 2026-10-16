"""Game case checks: kbm agent with a fake window and dry-run input, socket adapter with a fake game."""

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from game_core import ACTIONS
from gamelab.decide import Decider
from gamelab.game_case import GAMES, GameCase, list_games
from gamelab.game_types import load_types, validate_providers
from gamelab.matrix import summarize
from gamelab.runner import play
from gamelab.socket_game import SocketGame
from kbm.agent import AgentService
from kbm.macos import DryRunInput
from kbm.steps import validate_step
from kbm.vision import Frame
from sims.arena import ArenaSim
from sims.service import SimService


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "games"
WINDOW = {"id": 1, "pid": 42, "x": 100.0, "y": 50.0, "width": 800.0, "height": 600.0}


def action_screen(hp=0.9, enemy=(0.7, 0.4), warning=False):
    image = Image.new("RGB", (1600, 1200), (20, 20, 30))
    draw = ImageDraw.Draw(image)
    draw.rectangle([48, 1104, 48 + int(352 * hp), 1134], fill=(90, 220, 120))
    draw.rectangle([1200, 1104, 1552, 1134], fill=(240, 200, 80))
    if enemy:
        cx, cy = enemy[0] * 1600, enemy[1] * 1200
        draw.ellipse([cx - 30, cy - 30, cx + 30, cy + 30], fill=(230, 50, 50))
    if warning:
        draw.rectangle([0, 0, 30, 1200], fill=(255, 40, 40))
    return image


class FakePlatform:
    def __init__(self, screen):
        self.screen = screen
        self.front = True
        self.mouse = (500, 500)

    def find_window(self):
        return dict(WINDOW)

    def capture(self, window):
        return self.screen()

    def is_frontmost(self, window):
        return self.front

    def cursor(self):
        return self.mouse

    def permissions(self):
        return {"screen_capture": True, "post_events": True}


class GameCaseTests(unittest.TestCase):
    def test_every_game_and_every_interface_loads(self):
        self.assertEqual(GameCase("arena").connector, "socket")
        self.assertEqual(GameCase("kbm_demo", FIXTURES).connector, "kbm")
        for game_id in list_games():
            game = GameCase(game_id)
            self.assertTrue(game.cases())
            for name in game.interfaces:
                with self.subTest(game=game_id, interface=name):
                    self.assertEqual(set(GameCase(game_id, interface=name).actions), set(game.options))

    def test_one_game_can_switch_interfaces(self):
        with tempfile.TemporaryDirectory() as games_dir:
            shutil.copytree(GAMES / "arena", Path(games_dir) / "arena")
            kbm = Path(games_dir) / "arena" / "interfaces" / "kbm"
            shutil.copytree(FIXTURES / "kbm_demo" / "interfaces" / "kbm", kbm)
            self.assertEqual(GameCase("arena", games_dir).connector, "socket")  # game.json default
            game = GameCase("arena", games_dir, interface="kbm")
            self.assertEqual(game.connector, "kbm")
            self.assertEqual(game.cases(), GameCase("arena", games_dir).cases())  # same game, same cases
            with self.assertRaises(LookupError):
                GameCase("arena", games_dir, interface="nope")

    def test_interface_actions_must_match_the_games_choices(self):
        with tempfile.TemporaryDirectory() as games_dir:
            shutil.copytree(GAMES / "arena", Path(games_dir) / "arena")
            path = Path(games_dir) / "arena" / "interfaces" / "socket" / "interface.json"
            config = json.loads(path.read_text(encoding="utf-8"))
            del config["actions"]["dodge"]
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(ValueError):
                GameCase("arena", games_dir)

    def test_bad_steps_are_rejected(self):
        for step in [{"tap": "nope"}, {"hold": "w"}, {"click": "middle"},
                     {"click": "left", "at": [1.5, 0]}, {"tap": "a", "click": "left"}]:
            with self.subTest(step=step), self.assertRaises(ValueError):
                validate_step(step, "test")

    def test_observer_reads_case1_hud(self):
        observed = GameCase("kbm_demo", FIXTURES).observe(Frame(action_screen(hp=0.2, warning=True)))
        state = observed["state"]
        self.assertEqual(state["hp_band"], "critical")
        self.assertTrue(state["warning"])
        self.assertAlmostEqual(state["enemy"][0], 0.7, delta=0.02)
        self.assertIn("incoming attack", observed["description"])


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.screen = {"image": action_screen()}
        self.platform = FakePlatform(lambda: self.screen["image"])
        self.backend = DryRunInput()
        self.agent = AgentService(GameCase("kbm_demo", FIXTURES), self.platform, self.backend)

    def test_click_lands_on_observed_enemy_in_screen_points(self):
        result = self.agent.handle({"op": "act", "action": "attack"})
        self.assertTrue(result["applied"], result)
        click = [e for e in self.backend.events if e[0] == "button"][0]
        self.assertAlmostEqual(click[3], 100 + 800 * 0.7, delta=10)
        self.assertAlmostEqual(click[4], 50 + 600 * 0.4, delta=10)

    def test_hold_releases_key(self):
        self.agent.handle({"op": "act", "action": "advance"})
        self.assertEqual([e for e in self.backend.events if e[0] == "key"],
                         [("key", "w", True), ("key", "w", False)])

    def test_stale_decision_is_dropped(self):
        observed = self.agent.handle({"op": "observe"})
        self.screen["image"] = action_screen(warning=True)
        result = self.agent.handle({"op": "act", "action": "advance", "signature": observed["signature"],
                                    "observed_at": observed["observed_at"], "max_age_ms": 5000})
        self.assertTrue(result["stale"])
        self.assertEqual(self.backend.events, [])

    def test_slow_decision_is_dropped(self):
        observed = self.agent.handle({"op": "observe"})
        result = self.agent.handle({"op": "act", "action": "advance", "signature": observed["signature"],
                                    "observed_at": time.time() - 2, "max_age_ms": 700})
        self.assertTrue(result["stale"])
        self.assertEqual(self.backend.events, [])

    def test_no_input_when_game_is_not_focused(self):
        self.platform.front = False
        result = self.agent.handle({"op": "act", "action": "dodge"})
        self.assertFalse(result["applied"])
        self.assertEqual(self.backend.events, [])

    def test_corner_failsafe_halts_for_good(self):
        self.platform.mouse = (0, 0)
        self.assertTrue(self.agent.handle({"op": "act", "action": "dodge"})["halted"])
        self.platform.mouse = (500, 500)
        self.assertFalse(self.agent.handle({"op": "act", "action": "dodge"})["applied"])
        self.assertEqual(self.backend.events, [])

    def test_missing_target_is_an_error_not_a_click(self):
        self.screen["image"] = action_screen(enemy=None)
        result = self.agent.handle({"op": "act", "action": "attack"})
        self.assertFalse(result["ok"])
        self.assertEqual([e for e in self.backend.events if e[0] == "button"], [])


class LocalSocket:
    """Calls a sim's SimService in-process instead of over a Unix socket."""

    def __init__(self, sim=ArenaSim, scenario=None):
        self.service = SimService(sim, scenario)

    def request(self, op, **kwargs):
        return self.service.handle({"op": op, **kwargs})


class SocketGameTests(unittest.TestCase):
    def setUp(self):
        self.game = GameCase("arena")
        self.client = SocketGame(self.game, LocalSocket())

    def test_arena_actions_match_the_game(self):
        self.assertEqual(set(self.game.actions.values()), set(ACTIONS))

    def test_observe_and_act_use_the_same_ops_as_kbm(self):
        observed = self.client.request("observe")
        self.assertTrue(observed["ok"])
        self.assertIn("turret", observed["description"].lower())
        result = self.client.request("act", action="advance", signature=observed["signature"])
        self.assertTrue(result["applied"], result)
        self.assertEqual(result["state"]["player_x"], observed["state"]["player_x"] + 1)

    def test_answer_for_an_old_turn_is_stale(self):
        observed = self.client.request("observe")
        self.client.request("act", action="wait", signature=observed["signature"])
        result = self.client.request("act", action="advance", signature=observed["signature"])
        self.assertFalse(result["applied"])
        self.assertTrue(result["stale"])

    def test_key_steps_are_rejected_for_a_socket_game(self):
        from gamelab import socket_game

        fake = type("Game", (), {"id": "x", "config": {"launch": {"command": ["a"]}},
                                 "actions": {"act": [{"tap": "space"}]}})
        with self.assertRaises(ValueError):
            socket_game.validate(fake)


class GameTypeTests(unittest.TestCase):
    def test_every_type_has_a_case_and_every_case_a_type(self):
        types = load_types()
        games = [GameCase(g) for g in list_games()]
        self.assertEqual({g.type for g in games}, set(types))
        self.assertEqual({spec["timing"] for spec in types.values()}, {"turn", "realtime"})

    def test_game_json_overrides_the_type_mapping(self):
        base = load_types()["turn.tactics"]["providers"]
        self.assertEqual(GameCase("arena").providers["live"], base["live"])
        with tempfile.TemporaryDirectory() as games_dir:
            shutil.copytree(GAMES / "arena", Path(games_dir) / "arena")
            path = Path(games_dir) / "arena" / "game.json"
            config = json.loads(path.read_text(encoding="utf-8"))
            config["providers"] = {"live": "laya-mlx", "models": {"laya-mlx": "english"}}
            path.write_text(json.dumps(config), encoding="utf-8")
            game = GameCase("arena", games_dir)
        self.assertEqual(game.providers["live"], "laya-mlx")
        self.assertEqual(game.providers["models"], {"laya-mlx": "english"})
        # The type's shadow (laya-mlx) is now live, so it must not also run as a shadow.
        self.assertNotIn("laya-mlx", game.providers["shadow"])

    def test_shadow_cannot_repeat_live(self):
        with self.assertRaises(ValueError):
            validate_providers({"live": "jev", "shadow": ["jev"]}, "test")

    def test_game_without_interface_is_compare_only(self):
        with tempfile.TemporaryDirectory() as games_dir:
            shutil.copytree(GAMES / "cards", Path(games_dir) / "cards", ignore=shutil.ignore_patterns("interfaces"))
            path = Path(games_dir) / "cards" / "game.json"
            config = json.loads(path.read_text(encoding="utf-8"))
            del config["interface"]
            path.write_text(json.dumps(config), encoding="utf-8")
            game = GameCase("cards", games_dir)
            self.assertIsNone(game.interface)
            self.assertTrue(game.cases())
            with self.assertRaises(RuntimeError):
                with game.connect():
                    pass


def matrix_row(game_type, provider, action, reference, ms, warmup=False, status="ok"):
    graded = {"action": {"type": "choice", "answer": action, "truth": [reference], "correct": action == reference}}
    return {"type": game_type, "provider": provider, "action": action, "grade": graded if reference else None,
            "model_ms": ms, "warmup": warmup, "status": status}


class MatrixTests(unittest.TestCase):
    TYPES = {"turn.x": {"label": "턴", "timing": "turn", "latency_budget_ms": 1000},
             "realtime.x": {"label": "실시간", "timing": "realtime", "latency_budget_ms": 100}}

    def games(self, live):
        return [type("Game", (), {"type": t, "providers": {"live": p}}) for t, p in live.items()]

    def test_slow_model_is_not_suggested_for_realtime(self):
        rows = [matrix_row("realtime.x", "jev", "a", "a", 400), matrix_row("realtime.x", "jev", "a", "a", 400),
                matrix_row("realtime.x", "laya-mlx", "a", "a", 20), matrix_row("realtime.x", "laya-mlx", "b", "a", 20)]
        item = summarize(rows, self.TYPES, self.games({"realtime.x": "jev"}))["realtime.x"]
        self.assertFalse(item["providers"]["jev"]["within_budget"])
        self.assertEqual(item["suggested_live"], "laya-mlx")
        self.assertEqual(item["verdict"], "설정 변경 검토")

    def test_warmup_call_is_left_out_of_latency(self):
        rows = [matrix_row("turn.x", "laya", "a", "a", 5000, warmup=True), matrix_row("turn.x", "laya", "a", "a", 30)]
        item = summarize(rows, self.TYPES, self.games({"turn.x": "laya"}))["turn.x"]
        self.assertEqual(item["providers"]["laya"]["median_ms"], 30)
        self.assertEqual(item["verdict"], "설정과 같음")

    def test_unmeasured_live_model_holds_the_verdict(self):
        rows = [matrix_row("turn.x", "laya", "a", "a", 30)]
        item = summarize(rows, self.TYPES, self.games({"turn.x": "jev"}))["turn.x"]
        self.assertTrue(item["verdict"].startswith("보류"))

    def test_model_that_matched_nothing_is_not_suggested(self):
        rows = [matrix_row("turn.x", "laya", "b", "a", 30), matrix_row("turn.x", "laya", "b", "a", 30)]
        item = summarize(rows, self.TYPES, self.games({"turn.x": "laya"}))["turn.x"]
        self.assertIsNone(item["suggested_live"])
        self.assertTrue(item["verdict"].startswith("추천 없음"))


class FakeDecider:
    def __init__(self, provider, action, delay=0, answers=None):
        self.provider, self.action, self.delay = provider, action, delay
        self.answers = {"action": {"type": "choice", "choice": action, "probabilities": {action: 1.0}}, **(answers or {})}
        self.seen = []

    def decide(self, state):
        self.seen.append(state)
        time.sleep(self.delay)
        return {"status": "ok", "provider": self.provider, "action": self.action, "model_ms": self.delay * 1000,
                "answers": self.answers, "probabilities": {self.action: 1.0}}


class ShadowTests(unittest.TestCase):
    def test_shadow_is_recorded_but_only_live_acts(self):
        client = SocketGame(GameCase("arena"), LocalSocket())
        logs = []
        play(client, FakeDecider("jev", "advance"), {"interval_ms": 0, "max_age_ms": 1000}, steps=1,
             record=False, log=logs.append, shadows=[FakeDecider("laya-mlx", "dodge")])
        self.assertIn("laya-mlx=dodge", logs[0])
        self.assertEqual(client.request("observe")["state"]["player_x"], 2)  # advance applied, dodge not

    def test_slow_shadow_does_not_hold_a_realtime_tick(self):
        client = SocketGame(GameCase("arena"), LocalSocket())
        logs = []
        started = time.time()
        play(client, FakeDecider("laya-mlx", "wait"), {"interval_ms": 50, "max_age_ms": 1000}, steps=1,
             record=False, log=logs.append, shadows=[FakeDecider("jev", "attack", delay=1.0)], shadow_wait_ms=50)
        self.assertLess(time.time() - started, 0.5)
        self.assertIn("jev=timeout", logs[0])


class DeciderTests(unittest.TestCase):
    def test_jev_receives_the_game_cases_own_questions(self):
        game = GameCase("swarm")
        answer = {"answers": {"action": {"type": "choice", "choice": "kite_back", "probabilities": {"kite_back": 0.7}},
                              "danger": {"type": "score", "score": 1.8}}}
        with patch("compare.urllib.request.urlopen") as send:
            send.return_value.__enter__.return_value.read.return_value = json.dumps(answer).encode()
            with patch("compare.json.load", return_value=answer):
                row = Decider("jev", game.questions, key="test-only-key").decide("swarmed")
        self.assertEqual(json.loads(send.call_args.args[0].data)["questions"], game.questions)
        self.assertEqual(row["action"], "kite_back")
        self.assertEqual(row["status"], "ok")


if __name__ == "__main__":
    unittest.main()
