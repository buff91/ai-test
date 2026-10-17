"""A prepared window survives both connection changes and repeated prepare calls."""
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gamelab.console import Lab
from gamelab.game_case import GAMES
from gamelab.game_windows import GameWindows, window_case
from tests.test_console_contract import ConsoleHttpContractTests


class FakeClient:
    def __init__(self, number):
        self.client = SimpleNamespace(path=Path('/tmp/window-%s.sock' % number))
        self.alive = True
        self.number = number

    def health(self):
        return {'alive': self.alive, 'pid': self.number}

    def request(self, op):
        return {'ok': True, 'state': {}}


class GameWindowTests(unittest.TestCase):
    def setUp(self):
        self.clients = []
        self.closed = []
        @contextmanager
        def connect(case, **kwargs):
            client = FakeClient(len(self.clients) + 1)
            self.clients.append(client)
            try:
                yield client
            finally:
                self.closed.append(client.number)
                client.alive = False
        self.patch = patch('gamelab.game_case.GameCase.connect', connect)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.lab = Lab(record=False)
        self.addCleanup(self.lab.close)

    def test_builtin_windows_prepare_and_external_attach_stays_manual(self):
        catalog = self.lab.catalog()
        for game in catalog['games']:
            if not any(v['window_interface'] for v in game['versions']):
                for version in game['versions']:
                    with self.assertRaises(ValueError):
                        self.lab.prepare_game_window(game['id'], version['id'])
                continue
            desktop = next(v for v in game['versions'] if v['id'] == 'desktop-v1')
            self.assertEqual(desktop['window_interface'], 'app')
            self.assertIsNone(next(v for v in game['versions'] if v['id'] == 'sim-v1')['window_interface'])
            ready = self.lab.prepare_game_window(game['id'], 'desktop-v1')['game_window']
            self.assertTrue(ready['alive'])
            self.assertFalse(ready['reused'])
        self.assertEqual(len(self.lab.status()['game_windows']), 3)

    def test_disconnect_keeps_same_process_and_console_close_releases_it(self):
        first = self.lab.prepare_game_window('party', 'desktop-v1')['game_window']
        self.lab.disconnect()
        again = self.lab.prepare_game_window('party', 'desktop-v1')['game_window']
        self.assertEqual(first['pid'], again['pid'])
        self.assertEqual(first['socket'], again['socket'])
        self.assertTrue(again['reused'])
        self.assertEqual(self.closed, [])
        self.lab.close()
        self.assertEqual(self.closed, [first['pid']])

    def test_closed_window_can_be_reprepared_without_reusing_dead_pid(self):
        first = self.lab.prepare_game_window('racer', 'desktop-v1')['game_window']
        self.clients[0].alive = False
        second = self.lab.prepare_game_window('racer', 'desktop-v1')['game_window']
        self.assertNotEqual(first['pid'], second['pid'])
        self.assertFalse(second['reused'])
        self.assertEqual(self.closed, [first['pid']])

    def test_live_process_without_responsive_endpoint_is_not_reused(self):
        self.lab.prepare_game_window('party', 'desktop-v1')
        with patch.object(self.clients[0], 'request', side_effect=OSError('socket missing')):
            with self.assertRaisesRegex(RuntimeError, '연결할 수 없습니다'):
                self.lab.prepare_game_window('party', 'desktop-v1')
        self.assertEqual(self.closed, [])

    def test_prepared_pid_cannot_bind_another_game_or_dead_window(self):
        prepared = self.lab.prepare_game_window('party', 'desktop-v1')['game_window']
        with self.assertRaisesRegex(ValueError, '준비한 게임 창'):
            self.lab.connect('moba', 'kbm', version='desktop-v1', prepared_window_pid=prepared['pid'])
        self.clients[0].alive = False
        with self.assertRaisesRegex(ValueError, '준비한 게임 창'):
            self.lab.connect('party', 'kbm', version='desktop-v1', prepared_window_pid=prepared['pid'])

    def test_kbm_connection_binds_prepared_process(self):
        prepared = self.lab.prepare_game_window('party', 'desktop-v1')['game_window']
        with patch('gamelab.console.threading.Thread'):
            self.lab.connect('party', 'kbm', version='desktop-v1', prepared_window_pid=prepared['pid'])
        self.assertEqual(self.lab.game.config['window']['pid'], prepared['pid'])

    def test_preparation_rejects_headless_unknown_and_connected_target(self):
        for game, version in [('party', 'sim-v1'), ('../party', 'desktop-v1'), (None, None)]:
            with self.subTest(game=game), self.assertRaises(ValueError):
                self.lab.prepare_game_window(game, version)
        self.lab.status_name = 'connecting'
        with self.assertRaisesRegex(ValueError, '연결'):
            self.lab.prepare_game_window('party', 'desktop-v1')
        self.assertEqual(self.clients, [])

    def test_failed_launch_leaves_no_window_entry(self):
        @contextmanager
        def failure(case, **kwargs):
            raise RuntimeError('launch failed')
            yield
        with patch('gamelab.game_case.GameCase.connect', failure):
            with self.assertRaisesRegex(RuntimeError, 'launch failed'):
                self.lab.prepare_game_window('moba', 'desktop-v1')
        self.assertEqual(self.lab.game_windows.status(), [])


class GameWindowHttpTests(ConsoleHttpContractTests):
    def test_prepare_route_uses_only_game_and_version(self):
        self.lab.prepare_game_window.return_value = {'game_window': {'pid': 123}}
        code, reply, _ = self.post(b'{"game":"moba","version":"desktop-v1"}', path='/api/game-window')
        self.assertEqual(code, 200)
        self.assertEqual(reply['game_window']['pid'], 123)
        self.lab.prepare_game_window.assert_called_once_with('moba', 'desktop-v1')

    def test_prepare_requires_session_token(self):
        code, _, _ = self.post(path='/api/game-window', token='wrong')
        self.assertEqual(code, 403)
        self.lab.prepare_game_window.assert_not_called()
