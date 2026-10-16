"""Load one games/<id>/ folder: the game itself plus, optionally, one of its interfaces.

games/<id>/game.json, questions.json, cases.json   what the game is and what is decided
games/<id>/interfaces/<name>/interface.json          how to reach it ("connector": kbm or socket)

A game can have several interfaces (e.g. kbm today, socket once the build gets a test
hook). Every connector yields a client with the same ops (info, observe, act, pause,
resume, halt), so the runner and model comparison never depend on the interface.
A game with no interface can still be compared offline on cases.json.
"""

import importlib
import importlib.util
import json
from pathlib import Path

from gamelab.game_types import load_types, validate_providers


ROOT = Path(__file__).resolve().parent.parent
GAMES = ROOT / "games"
# connector name -> module with validate(game) and connect(game, **options)
CONNECTORS = {
    "kbm": "kbm.connector",          # screen capture + keyboard/mouse (no hooks in the build)
    "socket": "gamelab.socket_game",  # the build itself serves state/step over a local socket
}
DEFAULTS = {"interval_ms": 700, "max_age_ms": 800, "max_action_ms": 1500,
            "pause_key": None, "reflex_hz": 0, "decision_keys": []}


def list_games(games_dir=GAMES):
    return sorted(p.name for p in Path(games_dir).iterdir()
                  if p.is_dir() and not p.name.startswith("_") and (p / "game.json").exists())


def connector_module(name):
    if name not in CONNECTORS:
        raise ValueError("알 수 없는 connector입니다: %s (가능: %s)" % (name, ", ".join(CONNECTORS)))
    return importlib.import_module(CONNECTORS[name])


def list_interfaces(game_dir):
    root = Path(game_dir) / "interfaces"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "interface.json").exists())


class GameCase:
    def __init__(self, game_id, games_dir=GAMES, interface=None):
        self.id = game_id
        self.dir = Path(games_dir) / game_id
        if not (self.dir / "game.json").exists():
            raise LookupError("게임 케이스가 없습니다: %s" % self.dir)
        self.game_config = _read(self.dir / "game.json")
        self.name = self.game_config.get("name", game_id)
        self.interfaces = list_interfaces(self.dir)
        self.interface = interface or self.game_config.get("interface") or (
            self.interfaces[0] if len(self.interfaces) == 1 else None)
        if self.interface and self.interface not in self.interfaces:
            raise LookupError("%s: 인터페이스 %r가 없습니다 (있는 것: %s)"
                              % (game_id, self.interface, ", ".join(self.interfaces) or "없음"))
        self.interface_dir = self.dir / "interfaces" / self.interface if self.interface else None
        interface_config = _read(self.interface_dir / "interface.json") if self.interface else {}
        self.config = {**DEFAULTS, **interface_config}
        self.connector = interface_config.get("connector")
        # Read types next to the games folder, so a built KbmAgent sees edits without a rebuild.
        types_path = self.dir.parent.resolve().parent / "config" / "game_types.json"
        types = load_types(types_path) if types_path.exists() else load_types()
        self.type = self.game_config.get("type")
        if self.type not in types:
            raise ValueError("%s: game.json의 type은 config/game_types.json에 있는 값이어야 합니다: %s"
                             % (game_id, ", ".join(types)))
        self.type_spec = types[self.type]
        # game.json "providers" overrides the type's mapping key by key.
        override = self.game_config.get("providers") or {}
        validate_providers(override, "%s/game.json" % game_id)
        base = self.type_spec.get("providers") or {}
        self.providers = {"live": override.get("live", base.get("live", "laya-mlx")),
                          "shadow": list(override.get("shadow", base.get("shadow", []))),
                          "models": {**base.get("models", {}), **override.get("models", {})}}
        self.providers["shadow"] = [p for p in self.providers["shadow"] if p != self.providers["live"]]
        self.questions = _read(self.dir / "questions.json")
        self.options = self.questions.get("action", {}).get("criteria")
        self.actions = self.config.get("actions")
        observer = self.interface_dir / "observer.py" if self.interface else None
        self.observer = (_load_module(observer, "game_%s_%s_observer" % (game_id, self.interface))
                         if observer and observer.exists() else None)
        self._validate()

    def _validate(self):
        if self.questions.get("action", {}).get("type") != "choice" or not isinstance(self.options, dict):
            raise ValueError("%s: questions.json에 choice 형식의 action 질문이 필요합니다." % self.id)
        if not self.interface:
            return
        where = "%s/interfaces/%s" % (self.id, self.interface)
        if not self.connector:
            raise ValueError("%s: interface.json에 connector(%s)가 필요합니다." % (where, ", ".join(CONNECTORS)))
        if not isinstance(self.actions, dict) or set(self.options) != set(self.actions):
            raise ValueError("%s: questions의 선택지와 interface.json actions가 다릅니다: %s / %s"
                             % (where, sorted(self.options), sorted(self.actions or {})))
        connector_module(self.connector).validate(self)

    def connect(self, **options):
        """Context manager yielding a client for the selected interface."""
        self.require_interface()
        return connector_module(self.connector).connect(self, **options)

    def require_interface(self):
        if not self.interface:
            raise RuntimeError("%s에는 연결할 인터페이스가 없어 비교(compare, matrix)만 할 수 있습니다." % self.id
                               if not self.interfaces else
                               "%s는 인터페이스가 여러 개입니다. --interface로 고르세요: %s" % (self.id, ", ".join(self.interfaces)))

    def observe(self, frame):
        result = self.observer.observe(frame)
        if not isinstance(result, dict) or not isinstance(result.get("state"), dict):
            raise ValueError("%s: observe()는 {'state': dict, 'description': str}을 반환해야 합니다." % self.id)
        return {"state": result["state"], "description": result.get("description") or ""}

    def describe(self, state, description=""):
        """Optional observer.describe(state) rewrites what the model sees for hook-based games."""
        handler = getattr(self.observer, "describe", None)
        return handler(state) if handler else description

    def signature(self, state):
        """Only these fields decide whether a slow model answer is still valid."""
        keys = self.config["decision_keys"] or sorted(state)
        return json.dumps([state.get(k) for k in keys], ensure_ascii=False, sort_keys=True)

    def reflex(self, state):
        handler = getattr(self.observer, "reflex", None)
        action = handler(state) if handler else None
        if action is not None and action not in self.actions:
            raise ValueError("%s: reflex()가 알 수 없는 행동을 반환했습니다: %s" % (self.id, action))
        return action

    def cases(self, path=None):
        path = Path(path) if path else self.dir / "cases.json"
        data = _read(path)
        if not isinstance(data, list):
            raise ValueError("%s는 JSON 목록이어야 합니다." % path)
        seen = set()
        for case in data:
            if not case.get("id") or case["id"] in seen:
                raise ValueError("%s: 케이스마다 고유한 id가 필요합니다." % path)
            if not isinstance(case.get("state"), (str, dict, list)):
                raise ValueError("%s: %s에 state가 없습니다." % (path, case["id"]))
            if case.get("reference_action") not in (None, *self.options):
                raise ValueError("%s: %s의 reference_action이 선택지에 없습니다." % (path, case["id"]))
            seen.add(case["id"])
        return data


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
