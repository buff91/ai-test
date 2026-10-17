"""Explicit real runs activate once; passive operations and later ticks never do."""

import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from gamelab.console import Lab, Probe
from gamelab.game_case import GameCase
from gamelab.socket_game import SocketGame
from sims.party import PartySim
from sims.service import SimService
from tests.test_web_console import ScriptedDecider, wait_until


class ConsoleFocusTests(unittest.TestCase):
    def setUp(self):
        self.make_decider = Mock(side_effect=lambda p, g, k: ScriptedDecider(p, g, "attack"))
        self.lab = Lab(make_decider=self.make_decider, record=False)
        self.addCleanup(self.lab.close)
        self.lab.game = GameCase("party", overrides={"interval_ms": 5})
        self.lab.status_name = "connected"
        self.lab.options = {"provider": "laya", "shadows": [], "policy": False, "dry_run": False}
        self.lab.info = {"capabilities": {"focus": True}}
        service = SimService(PartySim)
        socket = SocketGame(self.lab.game, SimpleNamespace(request=lambda op, **kw: service.handle({"op": op, **kw})))
        self.commands = []
        self.focus = Mock(return_value={"ok": True, "window": {"id": 42, "pid": 123}})
        self.lose_focus_after_first_input = False

        def request(op, **kwargs):
            self.commands.append(op)
            if op == "focus":
                return self.focus()
            if op == "act" and self.lose_focus_after_first_input and self.commands.count("act") > 1:
                return {"ok": False, "applied": False, "input_sent": False, "error": "게임 창이 맨 앞에 있지 않습니다."}
            return socket.request(op, **kwargs)

        self.lab.client = Probe(SimpleNamespace(request=request), self.lab, self.lab.generation)

    def complete(self):
        self.assertTrue(wait_until(lambda: not self.lab.busy, timeout=3))

    def test_explicit_run_focuses_before_observation_and_records_target(self):
        self.lab.run()
        self.complete()
        self.assertEqual(self.commands[:2], ["focus", "observe"])
        self.focus.assert_called_once_with()
        self.assertTrue(self.lab.rows[0]["applied"])
        exported = json.loads(self.lab.export())
        self.assertEqual(exported["run_focus"]["window"], {"id": 42, "pid": 123})
        self.assertEqual(exported["rows"][0]["focus"], exported["run_focus"])

    def test_failed_focus_stops_before_model_and_input(self):
        self.focus.return_value = {"ok": False, "error": "대상 창이 변경되었습니다."}
        self.lab.run()
        self.complete()
        self.assertEqual(self.commands, ["focus"])
        self.make_decider.assert_not_called()
        self.assertIn("게임 창 전환 실패", self.lab.error)
        self.assertFalse(json.loads(self.lab.export())["run_focus"]["ok"])

    def test_auto_never_refocuses_when_user_switches_apps(self):
        self.lose_focus_after_first_input = True
        self.lab.run(auto=True)
        self.complete()
        self.focus.assert_called_once_with()
        self.assertEqual(self.commands.count("act"), 2)
        self.assertTrue(self.lab.rows[-1]["applied"])
        self.assertFalse(self.lab.rows[0]["applied"])
        self.assertNotIn("focus", self.lab.rows[0])
        self.assertIn("맨 앞", self.lab.error)

    def test_analysis_dry_run_and_comparison_never_focus(self):
        self.lab.run(apply=False)
        self.complete()
        self.lab.options["dry_run"] = True
        self.lab.run()
        self.complete()
        self.lab.compare(providers=["laya", "laya-mlx"])
        self.complete()
        self.focus.assert_not_called()
        self.assertIsNone(self.lab.run_focus)

    def test_stop_during_focus_sends_no_input_and_releases_busy(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def delayed_focus():
            entered.set()
            release.wait(3)
            return {"ok": True}

        self.focus.side_effect = delayed_focus
        self.lab.run()
        self.assertTrue(entered.wait(3))
        self.lab.stop_auto()
        release.set()
        self.complete()
        self.assertEqual(self.commands, ["focus"])
        self.make_decider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
