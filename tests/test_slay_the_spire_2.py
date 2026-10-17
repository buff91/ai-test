"""Synthetic HTTP game + real localhost TCP contract; not real-game evidence."""

from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest

from gamelab.protocol import ModClient, ModServer
from gamelab.slay_the_spire_2 import PATCH, SlayTheSpire2Bridge, Sts2Api, _digest, normalize_state


def combat_state():
    return {"state_type": "monster", "run": {"act": 1, "floor": 1, "ascension": 0, "seed": "hidden"},
            "battle": {"round": 1, "turn": "player", "is_play_phase": True,
                       "enemies": [{"entity_id": "WORM_0", "combat_id": 900, "name": "Worm", "hp": 20, "block": 0}]},
            "player": {"hp": 80, "max_hp": 80, "energy": 3, "draw_pile_count": 5,
                       "draw_pile": [{"name": "Hidden top card"}],
                       "hand": [{"index": 0, "id": "STRIKE_R", "name": "Strike", "cost": "1",
                                 "description": "Deal 6 damage.", "can_play": True, "target_type": "AnyEnemy"},
                                {"index": 1, "id": "DEFEND_R", "name": "Defend", "cost": "1",
                                 "description": "Gain 5 block.", "can_play": True, "target_type": "Self"}]}}


class FakeGame:
    def __init__(self):
        self.state = combat_state()
        self.health = {"status": "ok", "message": "Hello from STS2 MCP v0.4.0", "mod_version": "0.4.0", "gamelab_patch": PATCH}
        self.posts = []
        self.gets = []
        self.behavior = "apply"
        self.state_error = None
        self.post_observations = 0

    def observation(self):
        if self.behavior == "oscillate" and self.posts:
            self.post_observations += 1
            self.state["player"]["energy"] = 2 if self.post_observations % 2 else 3
        return {**deepcopy(self.state), "gamelab_revision": _digest(self.state)}

    def act(self, payload):
        self.posts.append(payload)
        if self.behavior == "race":
            self.state["player"]["energy"] = 2
        if payload.get("expected_revision") != _digest(self.state):
            return {"status": "error", "stale": True, "error": "State changed on game thread"}
        if self.behavior == "reject":
            return {"status": "error", "error": "Player actions disabled"}
        if self.behavior == "invalid_ack":
            return {"message": "Unrecognized response"}
        if self.behavior == "apply":
            self.state["player"]["energy"] = 2
            self.state["battle"]["enemies"][0]["hp"] = 14
            self.state["player"]["hand"].pop(0)
            self.state["player"]["hand"][0]["index"] = 0
        return {"status": "ok", "message": "Queued"}


@contextmanager
def running_api(game):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, value, code=200):
            body = json.dumps(value).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            game.gets.append(self.path)
            if self.path == "/":
                self.send(game.health)
            elif game.state_error:
                self.send({"error": game.state_error}, 409)
            else:
                self.send(game.observation())

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if game.behavior == "lost_response":
                game.posts.append(payload)
                self.close_connection = True
                return
            self.send(game.act(payload))

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield Sts2Api("http://127.0.0.1:%d" % server.server_address[1], timeout=0.2)
        finally:
            server.shutdown()
            thread.join(2)


@contextmanager
def running_tcp(bridge):
    with ModServer({"host": "127.0.0.1", "port": 0}, bridge) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield ModClient({"host": "127.0.0.1", "port": server.server_address[1]})
        finally:
            server.shutdown()
            thread.join(2)


class NormalizationTests(unittest.TestCase):
    def test_public_observation_and_target_payload(self):
        snapshot = normalize_state(combat_state())
        text = json.dumps(snapshot)
        self.assertNotIn("Hidden top card", text)
        self.assertNotIn("seed", text)
        self.assertNotIn("combat_id", text)
        self.assertEqual(snapshot["candidates"][0]["action"], {"action": "play_card", "card_index": 0, "target": "WORM_0"})
        self.assertEqual(snapshot["candidates"][1]["action"], {"action": "play_card", "card_index": 1})
        self.assertEqual(snapshot["candidates"][2]["action"], {"action": "end_turn"})

    def test_enemy_turn_and_menu_never_offer_actions(self):
        raw = combat_state()
        raw["battle"]["turn"] = "enemy"
        self.assertEqual(normalize_state(raw)["candidates"], [])
        self.assertEqual(normalize_state({"state_type": "menu", "options": ["continue", "abandon_run"]})["candidates"], [])

    def test_overflow_fails_closed_without_silent_truncation(self):
        raw = {"state_type": "map", "map": {"next_options": [{"index": i, "type": "Monster"} for i in range(65)]}}
        state = normalize_state(raw)
        self.assertEqual(state["candidate_count"], 65)
        self.assertEqual(state["candidates"], [])
        self.assertIn("overflow", state["blocked_reason"])

    def test_screen_specific_commands_and_disabled_choices(self):
        event = normalize_state({"state_type": "event", "event": {"options": [
            {"index": 0, "title": "Locked", "is_locked": True}, {"index": 2, "title": "Leave", "is_locked": False}]}})
        self.assertEqual([c["action"] for c in event["candidates"]], [{"action": "choose_event_option", "index": 2}])
        reward = normalize_state({"state_type": "card_reward", "card_reward": {"cards": [{"index": 3, "name": "Card"}], "can_skip": True}})
        self.assertEqual([c["action"] for c in reward["candidates"]],
                         [{"action": "select_card_reward", "card_index": 3}, {"action": "skip_card_reward"}])
        grid = normalize_state({"state_type": "card_select", "card_select": {"cards": [{"index": 0}],
                                "preview_showing": True, "can_confirm": True}})
        self.assertEqual([c["action"] for c in grid["candidates"]], [{"action": "confirm_selection"}])

    def test_no_guessed_target_types_or_unplayable_cards(self):
        raw = combat_state()
        raw["player"]["hand"][0]["can_play"] = False
        raw["player"]["hand"][1]["target_type"] = "UnrecognizedTarget"
        self.assertEqual([c["action"] for c in normalize_state(raw)["candidates"]], [{"action": "end_turn"}])

    def test_local_http_url_validation(self):
        for url in ("http://example.com", "https://localhost:15526", "http://user:pw@localhost", "http://127.0.0.1:15526/redirect", "http://localhost:15526?x=1", "http://localhost:0"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                Sts2Api(url)


class BridgeTests(unittest.TestCase):
    def step(self, bridge, observation, slot=0):
        return bridge.handle({"op": "step", "expected_revision": observation["revision"],
                              "action": {"command": "choose_candidate", "slot": slot}})

    def test_http_and_tcp_action_roundtrip_requires_state_change(self):
        game = FakeGame()
        with running_api(game) as api, running_tcp(SlayTheSpire2Bridge(api)) as client:
            info = client.request("info")
            self.assertEqual(info["build"]["version"], "mod-v1")
            self.assertEqual(info["build"]["upstream_mod_version"], "0.4.0")
            self.assertIsNone(info["build"]["target_game_version"])
            observed = client.request("state")
            result = client.request("step", expected_revision=observed["revision"], action={"command": "choose_candidate", "slot": 0})
            self.assertTrue(result["applied"], result)
            self.assertEqual(result["snapshot"]["battle"]["enemies"][0]["hp"], 14)
            self.assertEqual(game.posts[0]["card_index"], 0)
            self.assertEqual(game.posts[0]["target"], "WORM_0")
            self.assertIn("deadline_unix_ms", game.posts[0])
            again = client.request("step", expected_revision=observed["revision"], action={"command": "choose_candidate", "slot": 0})
            self.assertTrue(again["stale"])
            self.assertEqual(len(game.posts), 1)

    def test_unpatched_health_rejects_before_any_state_read(self):
        game = FakeGame()
        game.health.pop("gamelab_patch")
        with running_api(game) as api:
            response = SlayTheSpire2Bridge(api).handle({"op": "state"})
        self.assertFalse(response["ok"])
        self.assertEqual(game.gets, ["/"])
        self.assertEqual(game.posts, [])

    def test_stale_preflight_never_posts(self):
        game = FakeGame()
        with running_api(game) as api:
            bridge = SlayTheSpire2Bridge(api)
            observed = bridge.handle({"op": "state"})
            game.state["player"]["energy"] = 1
            result = self.step(bridge, observed)
        self.assertTrue(result["stale"])
        self.assertFalse(result["applied"])
        self.assertEqual(game.posts, [])

    def test_atomic_upstream_race_is_rejected(self):
        game = FakeGame()
        game.behavior = "race"
        with running_api(game) as api:
            bridge = SlayTheSpire2Bridge(api)
            result = self.step(bridge, bridge.handle({"op": "state"}))
        self.assertTrue(result["stale"])
        self.assertFalse(result["applied"])
        self.assertEqual(game.state["battle"]["enemies"][0]["hp"], 20)

    def test_ack_without_effect_is_uncertain_and_not_retried(self):
        game = FakeGame()
        game.behavior = "no_effect"
        with running_api(game) as api:
            bridge = SlayTheSpire2Bridge(api, step_timeout=0.2)
            observed = bridge.handle({"op": "state"})
            result = self.step(bridge, observed)
            self.assertTrue(result["uncertain"])
            self.assertFalse(result["applied"])
            repeated = self.step(bridge, observed)
            self.assertTrue(repeated["uncertain"])
            self.assertEqual(len(game.posts), 1)
            game.state["player"]["energy"] = 2
            self.assertFalse(bridge.handle({"op": "state"})["snapshot"]["candidates"])
            self.assertTrue(bridge.handle({"op": "state"})["snapshot"]["candidates"])

    def test_changed_original_changed_is_not_a_stable_applied_effect(self):
        game = FakeGame()
        game.behavior = "oscillate"
        with running_api(game) as api:
            bridge = SlayTheSpire2Bridge(api, step_timeout=0.3)
            observed = bridge.handle({"op": "state"})
            result = self.step(bridge, observed)
            self.assertGreaterEqual(game.post_observations, 3)
            self.assertFalse(result["applied"], result)
            self.assertTrue(result["uncertain"], result)
            self.assertNotIn("application_evidence", result)
            self.assertIsNotNone(bridge.pending)
            self.assertEqual(len(game.posts), 1)

    def test_only_hidden_state_change_does_not_unblock_uncertain_action(self):
        game = FakeGame()
        game.behavior = "no_effect"
        with running_api(game) as api:
            bridge = SlayTheSpire2Bridge(api, step_timeout=0.15)
            self.step(bridge, bridge.handle({"op": "state"}))
            game.state["run"]["seed"] = "different hidden value"
            for _ in range(2):
                observed = bridge.handle({"op": "state"})
                self.assertFalse(observed["ok"])
                self.assertFalse(observed["snapshot"]["candidates"])
                self.assertTrue(self.step(bridge, observed)["uncertain"])
        self.assertEqual(len(game.posts), 1)

    def test_unsupported_screen_blocks_model_observation_but_preserves_snapshot(self):
        game = FakeGame()
        game.state = {"state_type": "menu", "menu_screen": "main"}
        with running_api(game) as api:
            observed = SlayTheSpire2Bridge(api).handle({"op": "state"})
        self.assertFalse(observed["ok"])
        self.assertEqual(observed["snapshot"]["state_type"], "menu")
        self.assertIn("manually", observed["error"])

    def test_lost_post_response_is_not_retried(self):
        game = FakeGame()
        game.behavior = "lost_response"
        with running_api(game) as api:
            bridge = SlayTheSpire2Bridge(api)
            observed = bridge.handle({"op": "state"})
            result = self.step(bridge, observed)
            self.assertTrue(result["uncertain"])
            self.assertFalse(result["applied"])
            self.step(bridge, observed)
        self.assertEqual(len(game.posts), 1)

    def test_invalid_ack_does_not_unlock_action_retry(self):
        game = FakeGame()
        game.behavior = "invalid_ack"
        with running_api(game) as api:
            bridge = SlayTheSpire2Bridge(api)
            observed = bridge.handle({"op": "state"})
            self.assertTrue(self.step(bridge, observed)["uncertain"])
            self.assertTrue(self.step(bridge, observed)["uncertain"])
        self.assertEqual(len(game.posts), 1)

    def test_invalid_slots_and_reset_cannot_send_commands(self):
        game = FakeGame()
        with running_api(game) as api:
            bridge = SlayTheSpire2Bridge(api)
            observed = bridge.handle({"op": "state"})
            for slot in (-1, 64, True, "0"):
                self.assertFalse(self.step(bridge, observed, slot)["applied"])
            self.assertFalse(bridge.handle({"op": "reset"})["ok"])
        self.assertEqual(game.posts, [])

    def test_multiplayer_conflict_never_posts(self):
        game = FakeGame()
        game.state_error = "Multiplayer run is active"
        with running_api(game) as api:
            result = SlayTheSpire2Bridge(api).handle({"op": "state"})
        self.assertFalse(result["ok"])
        self.assertIn("Multiplayer", result["error"])
        self.assertEqual(game.posts, [])

    def test_terminal_and_rejected_actions(self):
        game = FakeGame()
        with running_api(game) as api:
            bridge = SlayTheSpire2Bridge(api)
            game.behavior = "reject"
            result = self.step(bridge, bridge.handle({"op": "state"}))
            self.assertFalse(result["applied"])
            self.assertIsNone(bridge.pending)
            game.state = {"state_type": "game_over", "game_over": {"message": "Run ended"}}
            self.assertTrue(bridge.handle({"op": "state"})["done"])


if __name__ == "__main__":
    unittest.main()
