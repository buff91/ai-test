"""Quartz owner names vary between Python distributions, but targets stay scoped."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from kbm.macos import MacPlatform


def window(owner="python3", pid=123, number=22, title="My Game", width=1100):
    return {"kCGWindowNumber": number, "kCGWindowOwnerPID": pid, "kCGWindowOwnerName": owner,
            "kCGWindowName": title, "kCGWindowLayer": 0,
            "kCGWindowBounds": {"X": 90, "Y": 33, "Width": width, "Height": 828}}


class MacWindowOwnerTests(unittest.TestCase):
    def lookup(self, platform, visible, all_windows=None):
        quartz = SimpleNamespace(kCGWindowListOptionAll=0, kCGNullWindowID=0,
                                 CGWindowListCopyWindowInfo=lambda *_: all_windows or [])
        with patch.object(platform, "_windows", return_value=visible), \
                patch("kbm.macos._quartz", return_value=quartz):
            return platform.find_window()

    def test_generic_python_owner_accepts_runtime_names_and_case_variants(self):
        for configured in ("python", "Python", "PYTHON"):
            for actual in ("python", "Python", "python3", "Python3", "python3.12", "PYTHON3.12.11"):
                with self.subTest(configured=configured, actual=actual):
                    found = self.lookup(MacPlatform(configured, "My Game", pid=123), [window(actual)])
                    self.assertEqual((found["id"], found["pid"]), (22, 123))

    def test_generic_python_owner_rejects_similar_unrelated_process_names(self):
        for owner in ("ipython", "python-helper", "python3-malware", "Python Launcher", "python3.", "Other Game", None):
            with self.subTest(owner=owner), self.assertRaisesRegex(LookupError, "게임 창을 찾지 못했습니다"):
                self.lookup(MacPlatform("python", "My Game", pid=123), [window(owner)], [window(owner)])

    def test_explicit_runtime_and_other_owner_names_remain_exact(self):
        for configured in ("python3", "Python3", "My Game"):
            with self.subTest(configured=configured):
                self.assertEqual(self.lookup(MacPlatform(configured, "My Game"), [window(configured)])["pid"], 123)
            for actual in (configured.swapcase(), configured + ".12"):
                with self.subTest(configured=configured, actual=actual), self.assertRaises(LookupError):
                    self.lookup(MacPlatform(configured, "My Game"), [window(actual)], [window(actual)])

    def test_runtime_alias_preserves_prepared_pid_and_title_filters(self):
        platform = MacPlatform("python", "My Game", pid=123)
        candidates = [window("Python3", pid=456, number=1, width=2000),
                      window("python3.12", number=2, title="Other Title", width=1800),
                      window("python3", number=3)]
        self.assertEqual(self.lookup(platform, candidates)["id"], 3)

    def test_stale_pid_never_falls_back_to_another_python_runtime(self):
        candidates = [window("Python3", pid=456)]
        with self.assertRaisesRegex(LookupError, "pid=123"):
            self.lookup(MacPlatform("python", "My Game", pid=123), candidates, candidates)

    def test_hidden_runtime_alias_reports_hidden_window(self):
        for owner in ("python3", "Python3.12"):
            with self.subTest(owner=owner), self.assertRaisesRegex(LookupError, "창이 화면에 없습니다"):
                self.lookup(MacPlatform("python", "My Game", pid=123), [], [window(owner)])


if __name__ == "__main__":
    unittest.main()
