"""Slay the Spire 2: patched STS2MCP HTTP API -> GameLab localhost JSONL.

Requires the ai-test-v1 patch: observation has no input side effects and an
expected_revision is checked in the game's main-thread action callback. The
upstream action acknowledgement only means accepted; we also wait for a changed,
stable public state. A lost response is never retried automatically.
"""

import argparse
from copy import deepcopy
import hashlib
import http.client
import json
import math
import sys
import threading
import time
from urllib.parse import urlparse

from gamelab.protocol import MAX_MESSAGE_BYTES, PROTOCOL, ModServer


PATCH = "ai-test-v1"
VERSION = "mod-v1"
MAX_CANDIDATES = 64
COMBAT = {"monster", "elite", "boss"}
CARD_FIELDS = ("index", "id", "name", "type", "cost", "star_cost", "description",
               "target_type", "can_play", "unplayable_reason", "is_upgraded", "keywords")


def _pick(value, keys):
    return {key: deepcopy(value[key]) for key in keys if key in value} if isinstance(value, dict) else {}


def _objects(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _index(value):
    return type(value) is int and value >= 0


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class ApiError(RuntimeError):
    def __init__(self, message, *, status=None):
        super().__init__(message)
        self.status = status


class Sts2Api:
    """No proxies, redirects, remote hosts, or implicit HTTP retries."""

    def __init__(self, url="http://127.0.0.1:15526", timeout=0.6):
        parsed = urlparse(url)
        try:
            port = 80 if parsed.port is None else parsed.port
        except ValueError as exc:
            raise ValueError("Invalid STS2 API port") from exc
        if (parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1")
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError("STS2 API must be http://127.0.0.1:PORT or http://localhost:PORT")
        if not 1 <= port <= 65535:
            raise ValueError("Invalid STS2 API port")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 0.75:
            raise ValueError("HTTP timeout must be > 0 and <= 0.75 seconds")
        self.host, self.port, self.timeout = parsed.hostname, port, timeout

    def request(self, method, path, payload=None, *, deadline=None):
        remaining = self.timeout if deadline is None else min(self.timeout, deadline - time.monotonic())
        if remaining <= 0:
            raise ApiError("STS2 request deadline exceeded")
        connection = http.client.HTTPConnection(self.host, self.port, timeout=remaining)
        try:
            body = None if payload is None else json.dumps(payload, allow_nan=False).encode()
            connection.request(method, path, body=body, headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            data = response.read(MAX_MESSAGE_BYTES + 1)
            if len(data) > MAX_MESSAGE_BYTES:
                raise ApiError("STS2 response exceeds 1 MiB")
            try:
                result = json.loads(data)
            except (ValueError, UnicodeDecodeError) as exc:
                raise ApiError("STS2 returned invalid JSON", status=response.status) from exc
            if not isinstance(result, dict):
                raise ApiError("STS2 must return a JSON object", status=response.status)
            if not 200 <= response.status < 300:
                raise ApiError(str(result.get("error") or "STS2 HTTP %s" % response.status), status=response.status)
            return result
        except (OSError, http.client.HTTPException) as exc:
            raise ApiError("STS2 connection failed: %s" % exc) from exc
        finally:
            connection.close()

    def health(self, *, deadline=None):
        result = self.request("GET", "/", deadline=deadline)
        if result.get("status") != "ok" or result.get("gamelab_patch") != PATCH:
            raise ApiError("STS2MCP with gamelab_patch=%s is required; unpatched API is not used" % PATCH)
        return result


def normalize_state(raw):
    """Keep visible decision data; omit draw order, seeds and internal engine IDs."""
    if not isinstance(raw, dict) or not isinstance(raw.get("state_type"), str):
        raise ApiError("STS2 state_type is missing")
    kind = raw["state_type"]
    player = _pick(raw.get("player"), ("character", "hp", "max_hp", "block", "gold", "energy", "max_energy",
                  "stars", "draw_pile_count", "discard_pile_count", "exhaust_pile_count", "status", "relics",
                  "potions", "max_potion_slots", "orbs", "orb_slots", "orb_empty_slots", "pets"))
    if isinstance(raw.get("player"), dict) and "hand" in raw["player"]:
        player["hand"] = [_pick(card, CARD_FIELDS) for card in _objects(raw["player"]["hand"])]
    battle = _pick(raw.get("battle"), ("round", "turn", "is_play_phase"))
    if isinstance(raw.get("battle"), dict):
        battle["enemies"] = [_pick(enemy, ("entity_id", "name", "hp", "max_hp", "block", "status", "intents"))
                             for enemy in _objects(raw["battle"].get("enemies"))]
    screen_fields = {
        "event": ("event_id", "event_name", "is_ancient", "in_dialogue", "body", "options"),
        "map": ("current_position", "visited", "next_options", "nodes", "boss", "bosses"),
        "rewards": ("items", "can_proceed"),
        "card_reward": ("cards", "can_skip"),
        "rest_site": ("options", "can_proceed"),
        "hand_select": ("mode", "prompt", "cards", "selected_cards", "can_confirm"),
        "card_select": ("screen_type", "prompt", "cards", "preview_showing", "preview_cards", "can_confirm", "can_cancel"),
        "treasure": ("message", "relics", "can_proceed", "can_open", "manual_open_required"),
        "shop": ("items", "can_proceed", "error", "inventory_open", "can_open", "manual_open_required"),
        "game_over": ("message", "victory"),
    }
    screen = _pick(raw.get(kind), screen_fields.get(kind, ()))
    snapshot = {"state_type": kind, "run": _pick(raw.get("run"), ("act", "floor", "ascension")),
                "player": player, "battle": battle, "screen": screen}
    if raw.get("message"):
        snapshot["message"] = str(raw["message"])
    if kind == "menu":
        snapshot["menu_screen"] = raw.get("menu_screen", "main")
    candidates = []

    def add(label, action, **parameters):
        slot = len(candidates)
        candidates.append({"slot": slot, "key": "candidate_%d" % slot, "label": label,
                           "action": {"action": action, **parameters}})

    def indexed(items, action, parameter="index", enabled=lambda item: True):
        for item in _objects(items):
            if _index(item.get("index")) and enabled(item):
                label = item.get("name") or item.get("title") or item.get("description") or item.get("type") or str(item["index"])
                detail = item.get("description")
                if detail and detail != label:
                    label = "%s — %s" % (label, detail)
                add(str(label), action, **{parameter: item["index"]})

    if kind in COMBAT and battle.get("turn") == "player" and battle.get("is_play_phase") is True:
        for card in player.get("hand", []):
            if card.get("can_play") is not True or not _index(card.get("index")):
                continue
            label = "%s (cost %s): %s" % (card.get("name", "Card"), card.get("cost", "?"), card.get("description", ""))
            target_type = card.get("target_type")
            if target_type == "AnyEnemy":
                for enemy in battle.get("enemies", []):
                    if isinstance(enemy.get("entity_id"), str) and enemy.get("hp", 0) > 0:
                        add("%s → %s" % (label, enemy.get("name", enemy["entity_id"])), "play_card",
                            card_index=card["index"], target=enemy["entity_id"])
            elif target_type in ("None", "Self", "AllEnemies", "RandomEnemy"):
                add(label, "play_card", card_index=card["index"])
        add("End turn", "end_turn")
    elif kind == "event":
        if screen.get("in_dialogue") is True:
            add("Continue visible dialogue", "advance_dialogue")
        else:
            indexed(screen.get("options"), "choose_event_option", enabled=lambda item: item.get("is_locked") is False)
    elif kind == "map":
        indexed(screen.get("next_options"), "choose_map_node")
    elif kind == "rewards":
        indexed(screen.get("items"), "claim_reward")
    elif kind == "card_reward":
        indexed(screen.get("cards"), "select_card_reward", "card_index")
        if screen.get("can_skip") is True:
            add("Skip card reward", "skip_card_reward")
    elif kind == "rest_site":
        indexed(screen.get("options"), "choose_rest_option", enabled=lambda item: item.get("is_enabled") is True)
    elif kind == "hand_select":
        indexed(screen.get("cards"), "combat_select_card", "card_index")
        if screen.get("can_confirm") is True:
            add("Confirm hand selection", "combat_confirm_selection")
    elif kind == "card_select":
        if screen.get("preview_showing") is not True:
            indexed(screen.get("cards"), "select_card")
        if screen.get("can_confirm") is True:
            add("Confirm card selection", "confirm_selection")
        if screen.get("can_cancel") is True:
            add("Cancel card selection", "cancel_selection")
    elif kind == "treasure":
        indexed(screen.get("relics"), "claim_treasure_relic")
    if kind in {"rewards", "rest_site", "treasure", "shop"} and screen.get("can_proceed") is True:
        add("Proceed to map", "proceed")
    snapshot["candidate_count"] = len(candidates)
    if len(candidates) > MAX_CANDIDATES:
        snapshot["blocked_reason"] = "Candidate overflow (%d > %d); manual action required" % (len(candidates), MAX_CANDIDATES)
        candidates = []  # Never silently drop legal choices.
    elif not candidates and kind != "game_over":
        snapshot["blocked_reason"] = ("Start or resume a singleplayer test run manually" if kind == "menu" else
                                      "No supported action is ready; wait for animation or handle this screen manually")
    snapshot["candidates"] = candidates
    snapshot["outcome"] = "run_ended" if kind == "game_over" else "playing"
    return snapshot


class SlayTheSpire2Bridge:
    def __init__(self, api=None, *, step_timeout=2.4):
        if not 0 < step_timeout <= 2.4:
            raise ValueError("step_timeout must be > 0 and <= 2.4 seconds")
        self.api = api or Sts2Api()
        self.step_timeout = step_timeout
        self.lock = threading.Lock()
        self.pending = None

    def _observe(self, *, deadline=None):
        raw = self.api.request("GET", "/api/v1/singleplayer?format=json", deadline=deadline)
        upstream_revision = raw.get("gamelab_revision")
        if not isinstance(upstream_revision, str) or not upstream_revision:
            raise ApiError("Patched STS2 state must report gamelab_revision")
        snapshot = normalize_state(raw)
        fingerprint = _digest(snapshot)
        revision = _digest([upstream_revision, fingerprint])
        if self.pending is not None:
            if fingerprint != self.pending["fingerprint"]:
                if self.pending.get("seen_fingerprint") == fingerprint:
                    self.pending = None
                else:
                    self.pending["seen_fingerprint"] = fingerprint
            else:
                self.pending.pop("seen_fingerprint", None)
            if self.pending is not None:
                snapshot["candidates"] = []
                snapshot["blocked_reason"] = "Previous action outcome is uncertain; no resend. Verify the game manually."
                snapshot["pending_action"] = deepcopy(self.pending["action"])
        description = "Slay the Spire 2 public observation. Choose only a listed candidate key.\n" + json.dumps(snapshot, ensure_ascii=False)
        return {"snapshot": snapshot, "revision": revision, "description": description,
                "done": snapshot["outcome"] != "playing", "_upstream_revision": upstream_revision,
                "_fingerprint": fingerprint}

    @staticmethod
    def _public(observed):
        return {key: value for key, value in observed.items() if not key.startswith("_")}

    def handle(self, request):
        # Return promptly instead of queuing a second action behind an uncertain one.
        if not self.lock.acquire(blocking=False):
            return {"ok": False, "applied": False, "error": "STS2 bridge is busy; observe again"}
        try:
            op = request.get("op")
            if op not in {"info", "state", "step"}:
                return {"ok": False, "applied": False, "error": "Unsupported operation; reset/start/stop are not exposed"}
            deadline = time.monotonic() + self.step_timeout
            health = self.api.health(deadline=deadline)
            if op == "info":
                upstream_version = health.get("mod_version") or health.get("version") or health.get("message")
                return {"ok": True, "protocol": dict(PROTOCOL),
                        "build": {"game": "slay_the_spire_2", "version": VERSION, "role": "adapter",
                                  "upstream_mod_version": upstream_version, "target_game_version": health.get("game_version")},
                        "upstream": health, "scenarios": [],
                        "capabilities": {"observe": True, "act": True, "reset": False, "pause": False,
                                         "halt": False, "atomic_revision_check": True},
                        "version_note": "mod-v1 identifies the adapter; target_game_version is separate and may be unknown"}
            observed = self._observe(deadline=deadline)
            if op == "state":
                available = bool(observed["snapshot"]["candidates"]) or observed["done"]
                return {**self._public(observed), "ok": available,
                        "error": None if available else observed["snapshot"].get("blocked_reason")}
            if request.get("expected_revision") != observed["revision"]:
                return {**self._public(observed), "ok": False, "applied": False, "stale": True,
                        "error": "Game state changed; observe and decide again"}
            if self.pending is not None:
                return {**self._public(observed), "ok": False, "applied": False, "uncertain": True,
                        "error": observed["snapshot"]["blocked_reason"]}
            action = request.get("action")
            if (not isinstance(action, dict) or set(action) != {"command", "slot"}
                    or action.get("command") != "choose_candidate" or not _index(action.get("slot"))):
                return {"ok": False, "applied": False, "error": "Expected {command: choose_candidate, slot: nonnegative integer}"}
            candidates = observed["snapshot"]["candidates"]
            if action["slot"] >= len(candidates):
                return {**self._public(observed), "ok": False, "applied": False, "error": "Candidate slot is not available"}
            command = deepcopy(candidates[action["slot"]]["action"])
            payload = {**command, "expected_revision": observed["_upstream_revision"],
                       "deadline_unix_ms": int((time.time() + max(0, deadline - time.monotonic())) * 1000)}
            self.pending = {"revision": observed["revision"], "fingerprint": observed["_fingerprint"], "action": command}
            try:
                accepted = self.api.request("POST", "/api/v1/singleplayer", payload, deadline=deadline)
            except ApiError as exc:
                if exc.status is not None and 400 <= exc.status < 500:
                    self.pending = None
                    return {**self._public(observed), "ok": False, "applied": False, "error": str(exc)}
                return {**self._public(observed), "ok": False, "applied": False, "uncertain": True,
                        "error": "Action delivery outcome unknown; not retried: %s" % exc}
            if accepted.get("status") == "error":
                self.pending = None
                return {**self._public(observed), "ok": False, "applied": False,
                        "stale": bool(accepted.get("stale")), "error": accepted.get("error") or "Action rejected"}
            if accepted.get("status") != "ok":
                return {**self._public(observed), "ok": False, "applied": False, "uncertain": True,
                        "error": "Action acknowledgement is invalid; not retried"}
            last_changed = None
            while time.monotonic() < deadline:
                try:
                    after = self._observe(deadline=deadline)
                except ApiError:
                    break
                if after["_fingerprint"] != observed["_fingerprint"]:
                    if last_changed == after["_fingerprint"]:
                        self.pending = None
                        return {**self._public(after), "ok": True, "applied": True,
                                "before": observed["snapshot"], "upstream_action": command,
                                "application_evidence": "upstream accepted and changed public state observed twice"}
                    last_changed = after["_fingerprint"]
                else:
                    last_changed = None
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
            self.pending = {"revision": observed["revision"], "fingerprint": observed["_fingerprint"], "action": command}
            return {**self._public(observed), "ok": False, "applied": False, "uncertain": True,
                    "error": "Action accepted but resulting stable state was not confirmed; not retried"}
        except (ApiError, ValueError, TypeError) as exc:
            return {"ok": False, "applied": False, "error": str(exc)}
        finally:
            self.lock.release()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:15526")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--probe", action="store_true", help="GET health only; never queries or changes game state")
    args = parser.parse_args(argv)
    api = Sts2Api(args.api_url)
    if args.probe:
        try:
            print(json.dumps(api.health(), ensure_ascii=False, indent=2))
        except ApiError as exc:
            parser.exit(1, str(exc) + "\n")
        return
    with ModServer({"host": "127.0.0.1", "port": args.port}, SlayTheSpire2Bridge(api)) as server:
        print("STS2 GameLab bridge on 127.0.0.1:%s; API %s" % (server.server_address[1], args.api_url), file=sys.stderr)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
