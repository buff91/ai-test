"""Real-time input deadlines and stop: a slow decision must never become a late keypress."""
import threading
import time
import unittest
from contextlib import contextmanager
from contextlib import redirect_stdout
import io
from types import SimpleNamespace
from unittest.mock import patch

from gamelab.game_case import GameCase
from gamelab.policy import Policy
from gamelab.runner import tick
from gamelab.socket_game import SocketGame
from play import main as play_main
from sims.racer import RacerSim
from sims.service import RealtimeService
from web_console import Lab


class Connection:
    def __init__(self):
        self.commands = []

    def request(self, op, **kwargs):
        self.commands.append(op)
        return {"ok": True, "snapshot": {"outcome": "playing"}, "revision": 1, "description": "safe"}


class RealtimeLoopTests(unittest.TestCase):
    def client(self, continuous=True):
        game = SimpleNamespace(actions={"wait": "wait"}, config={"continuous": continuous, "max_age_ms": 100},
                               describe=lambda state, description: description)
        return SocketGame(game, Connection())

    def test_expired_observation_never_reaches_game(self):
        client = self.client()
        result = client.request("act", action="wait", signature=1, observed_at=time.time() - 1, max_age_ms=100)
        self.assertTrue(result["stale"])
        self.assertFalse(result["applied"])
        self.assertNotIn("step", client.client.commands)

    def test_turn_game_can_still_wait_for_a_long_decision(self):
        client = self.client(continuous=False)
        self.assertTrue(client.request("act", action="wait", signature=1, observed_at=0)["applied"])

    def test_stop_during_inference_cancels_input(self):
        client = self.client()
        stopped = threading.Event()
        class Model:
            def decide(self, state):
                stopped.set()
                return {"status": "ok", "action": "wait", "model_ms": 1}
        row = tick(client, Model(), {"max_age_ms": 100}, cancel_event=stopped)["row"]
        self.assertTrue(row["cancelled"])
        self.assertFalse(row["applied"])
        self.assertNotIn("step", client.client.commands)

    def test_kbm_missing_permissions_are_reported_before_window_lookup(self):
        for capture, dry_run, message in ((False, True, "화면 기록"), (True, False, "손쉬운 사용")):
            with self.subTest(capture=capture, dry_run=dry_run):
                permissions = {"screen_capture": capture, "post_events": False}
                class Client:
                    def request(self, op, **kwargs):
                        if op != "info":
                            raise AssertionError("No capture or input before permission check")
                        return {"ok": True, "permissions": permissions}
                @contextmanager
                def connect(**kwargs):
                    yield Client()
                lab = Lab(record=False)
                try:
                    game = SimpleNamespace(connector="kbm", connect=connect)
                    lab._open(lab.generation, game, "app", dry_run)
                    self.assertEqual(lab.status_name, "error")
                    self.assertIn(message, lab.conn_error)
                    self.assertEqual(lab.info["permissions"], permissions)
                finally:
                    lab.close()
    def test_added_delay_is_separate_from_model_latency_and_expires(self):
        client = self.client()
        model = SimpleNamespace(decide=lambda state: {"status": "ok", "action": "wait", "model_ms": 1})
        row = tick(client, model, {"max_age_ms": 5}, decision_delay_ms=20)["row"]
        self.assertEqual(row["decision"]["model_ms"], 1)
        self.assertEqual(row["decision_delay_ms"], 20)
        self.assertTrue(row["stale"])
        self.assertFalse(row["applied"])

    def test_reference_search_latency_counts_toward_observation_age(self):
        service = RealtimeService(RacerSim, "long_straight")
        clock = [1000.0]
        sent = []

        def request(op, **kwargs):
            sent.append(op)
            return service.handle({"op": op, **kwargs})

        def delayed_reference(_sim):
            service.advance(.25)
            clock[0] += .25
            return {}

        client = SocketGame(GameCase("racer"), SimpleNamespace(request=request))
        with patch("gamelab.socket_game.time.time", side_effect=lambda: clock[0]), \
                patch("sims.service.truth", side_effect=delayed_reference):
            observed = client.request("observe")
            self.assertEqual(observed["state"]["elapsed"], 0)
            self.assertEqual(service.elapsed, .25, "Reference work must not hold the world clock")
            self.assertEqual(observed["observed_at"], 1000.0)
            result = client.request("act", action="brake", signature=observed["signature"],
                                    observed_at=observed["observed_at"], max_age_ms=100)
        self.assertTrue(result["stale"])
        self.assertFalse(result["applied"])
        self.assertNotIn("step", sent, "The old snapshot must expire before an input is dispatched")


class RealtimeEntryPointTests(unittest.TestCase):
    def test_every_realtime_interface_uses_continuous_input(self):
        for game_id in ("racer", "moba"):
            default = GameCase(game_id)
            for version, spec in default.versions.items():
                for interface in spec["interfaces"]:
                    with self.subTest(game=game_id, version=version, interface=interface):
                        game = GameCase(game_id, version=version, interface=interface)
                        self.assertTrue(game.config.get("continuous"))
                        command = game.config.get("launch", {}).get("command", [])
                        self.assertNotIn("--tick-ms", command,
                                         "The game clock uses the shared real-time engine's default rate")

    def test_console_kbm_keeps_realtime_running_but_preserves_configured_turn_pause(self):
        for game_id, pause_expected in (("racer", False), ("moba", False), ("party", True)):
            with self.subTest(game=game_id):
                game = GameCase(game_id, interface="kbm")
                # Pause remains supported when a turn-game interface configures it.
                game.config["pause_key"] = "p"
                calls = []

                def request(op, **kwargs):
                    calls.append((op, kwargs))
                    if op == "observe":
                        return {"ok": True, "state": {"outcome": "playing"}, "description": "live game",
                                "signature": "observed-frame", "observed_at": time.time()}
                    return {"ok": True, "applied": op == "act"}

                model = SimpleNamespace(decide=lambda state: {"status": "ok", "action": next(iter(game.actions)),
                                                              "model_ms": 1})
                lab = Lab(record=False)
                try:
                    with patch.object(lab, "_deciders", return_value=(model, [], Policy(None, game.questions))):
                        lab._worker(lab.generation, game, SimpleNamespace(request=request), {}, "", True,
                                    threading.Event())
                    self.assertEqual(any(op == "pause" for op, _ in calls), pause_expected)
                    self.assertEqual(any(op == "resume" for op, _ in calls), pause_expected)
                    sent = next(kwargs for op, kwargs in calls if op == "act")
                    self.assertEqual("signature" in sent, not pause_expected,
                                     "A running game must retain observation freshness checks")
                    self.assertTrue(lab.rows[0]["applied"])
                finally:
                    lab.close()

    def test_cli_realtime_pause_is_explicit_and_configured_turn_pause_is_preserved(self):
        @contextmanager
        def connected(*args, **kwargs):
            yield SimpleNamespace()

        cases = [(game, [], False) for game in ("racer", "moba")]
        cases += [("racer", ["--pause"], True), ("party", [], True), ("party", ["--no-pause"], False)]
        for game_id, flags, expected in cases:
            with self.subTest(game=game_id, flags=flags):
                game = GameCase(game_id, interface="kbm")
                game.config["pause_key"] = "p"
                argv = ["play.py", "run", game_id, "--interface", "kbm", "--provider", "laya",
                        "--no-shadow", "--no-record", "--steps", "1", *flags]
                with patch("sys.argv", argv), patch("play._game", return_value=game), patch("play.connect", connected), \
                        patch("gamelab.decide.for_game", return_value=SimpleNamespace()), \
                        patch("play.play", return_value=None) as run, redirect_stdout(io.StringIO()):
                    play_main()
                self.assertEqual(run.call_args.kwargs["pause"], expected)


if __name__ == '__main__':
    unittest.main()
