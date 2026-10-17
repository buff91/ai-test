"""Load one games/<id>/ folder: the game itself plus, optionally, one of its interfaces.

games/<id>/game.json, questions.json, cases.json   what the game is and what is decided
games/<id>/interfaces/<name>/interface.json          how to reach it ("connector": kbm or socket),
                                                     plus an optional "manual": what a person must do first

questions.json is sent to the models as is. cases.json and the game itself may carry a
"truth" per question (gamelab/grade.py), and game.json a "policy" (gamelab/policy.py).

A game can have several interfaces (e.g. kbm today, socket once the build gets a test
hook). Every connector yields a client with the same ops (info, observe, act, pause,
resume, halt), so the runner and model comparison never depend on the interface.
A game with no interface can still be compared offline on cases.json.
"""

import importlib
import importlib.util
import json
from pathlib import Path

from gamelab import grade, policy
from gamelab.game_types import PROVIDER_NAMES, load_types, validate_providers
from gamelab.configuration import apply_overrides, describe_configuration


ROOT = Path(__file__).resolve().parent.parent
GAMES = ROOT / "games"
# connector name -> module with validate(game) and connect(game, **options)
CONNECTORS = {
    "kbm": "kbm.connector",          # screen capture + keyboard/mouse (no hooks in the build)
    "socket": "gamelab.socket_game",  # the build itself serves state/step over a local socket
    "mod": "gamelab.mod_game",        # existing external game's localhost TCP mod bridge
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


def version_specs(config, interfaces):
    """Old game folders remain usable as a single, unnamed-version target."""
    versions = config.get("versions")
    if versions is None:
        return {"default": {"name": "기본 버전", "interface": config.get("interface"),
                            "interfaces": {name: {} for name in interfaces}}}
    if not isinstance(versions, dict) or not versions:
        raise ValueError("versions는 버전 id를 키로 하는 비어 있지 않은 객체여야 합니다.")
    for version, spec in versions.items():
        if not isinstance(spec, dict) or not isinstance(spec.get("interfaces"), dict):
            raise ValueError("%s: 버전마다 interfaces 객체가 필요합니다." % version)
        for name, override in spec["interfaces"].items():
            if name not in interfaces or not isinstance(override, dict):
                raise ValueError("%s: 인터페이스 %s가 없거나 덮어쓸 설정이 객체가 아닙니다." % (version, name))
        if spec.get("interface") and spec["interface"] not in spec["interfaces"]:
            raise ValueError("%s: 기본 인터페이스가 버전 매핑에 없습니다." % version)
    return versions


def _merge(base, override):
    result = dict(base)
    for key, value in override.items():
        result[key] = _merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
    return result


def check_build(game, build):
    """A mapping is a requested target, not proof of the running game's identity."""
    build = build if isinstance(build, dict) else {}
    mismatch = ((build.get("game") and build["game"] != game.id) or
                (game.version != "default" and build.get("version") and build["version"] != game.version))
    if mismatch:
        return {"status": "mismatch", "message": "선택한 %s / %s와 실행 중인 %s / %s가 다릅니다." %
                (game.id, game.version, build.get("game", "?"), build.get("version", "?"))}
    verified = build.get("game") == game.id and build.get("version") == game.version
    return {"status": "verified" if verified else "unverified",
            "message": "게임이 보고한 버전과 일치합니다." if verified else "게임이 버전 정보를 보고하지 않아 매핑만 확인했습니다."}


class GameCase:
    def __init__(self, game_id, games_dir=GAMES, interface=None, version=None, overrides=None):
        self.id = game_id
        self.dir = Path(games_dir) / game_id
        if not (self.dir / "game.json").exists():
            raise LookupError("게임 케이스가 없습니다: %s" % self.dir)
        self.game_config = _read(self.dir / "game.json")
        self.name = self.game_config.get("name", game_id)
        available = list_interfaces(self.dir)
        self.versions = version_specs(self.game_config, available)
        self.version = version or self.game_config.get("version") or next(iter(self.versions))
        # Preserve --interface app/kbm for callers that predate version selection.
        if version is None and interface and interface not in self.versions.get(self.version, {}).get("interfaces", {}):
            self.version = next((key for key, spec in self.versions.items() if interface in spec["interfaces"]), self.version)
        if self.version not in self.versions:
            raise LookupError("%s: 알 수 없는 버전 %s" % (game_id, self.version))
        self.version_spec = self.versions[self.version]
        self.interfaces = list(self.version_spec["interfaces"])
        self.interface = interface or self.version_spec.get("interface") or (
            self.interfaces[0] if len(self.interfaces) == 1 else None)
        if self.interface and self.interface not in self.interfaces:
            raise LookupError("%s: 인터페이스 %r가 없습니다 (있는 것: %s)"
                              % (game_id, self.interface, ", ".join(self.interfaces) or "없음"))
        self.interface_dir = self.dir / "interfaces" / self.interface if self.interface else None
        interface_config = _read(self.interface_dir / "interface.json") if self.interface else {}
        interface_config = _merge(interface_config, self.version_spec["interfaces"].get(self.interface, {}))
        self.connector = interface_config.get("connector")
        self.config = apply_overrides(self.connector, {**DEFAULTS, **interface_config}, overrides)
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
        self.policy = self.game_config.get("policy") or {}
        observer = self.interface_dir / "observer.py" if self.interface else None
        self.observer = (_load_module(observer, "game_%s_%s_observer" % (game_id, self.interface))
                         if observer and observer.exists() else None)
        self._validate()
        self.config["_target"] = self.target

    @property
    def target(self):
        mode = "attach" if self.connector == "kbm" or not self.config.get("launch") else "launch"
        return {"game": self.id, "version": self.version, "version_name": self.version_spec.get("name", self.version),
                "interface": self.interface, "mode": mode,
                "display": self.config.get("display", "visible" if self.connector == "kbm" else "unknown")}

    def _validate(self):
        if self.config.get("display", "unknown") not in ("visible", "headless", "unknown"):
            raise ValueError("display는 visible, headless, unknown 중 하나여야 합니다.")
        if self.connector == "kbm" and self.config.get("display") == "headless":
            raise ValueError("kbm은 게임 창이 필요하므로 headless로 연결할 수 없습니다.")
        if self.questions.get("action", {}).get("type") != "choice" or not isinstance(self.options, dict):
            raise ValueError("%s: questions.json에 choice 형식의 action 질문이 필요합니다." % self.id)
        for qid, question in self.questions.items():
            if question.get("type") not in ("choice", "score", "noul"):
                raise ValueError("%s: questions.json의 %s 유형은 choice, score, noul 중 하나여야 합니다." % (self.id, qid))
            if question["type"] == "score" and not 2 <= len(question.get("criteria") or []) <= 10:
                raise ValueError("%s: score 질문 %s에는 2~10개의 단계(criteria)가 필요합니다." % (self.id, qid))
        policy.validate(self.policy, self.questions, self.options, PROVIDER_NAMES, "%s/game.json policy" % self.id)
        if not self.interface:
            return
        where = "%s/interfaces/%s" % (self.id, self.interface)
        if not self.connector:
            raise ValueError("%s: interface.json에 connector(%s)가 필요합니다." % (where, ", ".join(CONNECTORS)))
        if not isinstance(self.config.get("manual", ""), str):
            raise ValueError("%s: manual은 사람이 먼저 할 일을 적은 문자열이어야 합니다." % where)
        if not isinstance(self.actions, dict) or set(self.options) != set(self.actions):
            raise ValueError("%s: questions의 선택지와 interface.json actions가 다릅니다: %s / %s"
                             % (where, sorted(self.options), sorted(self.actions or {})))
        connector_module(self.connector).validate(self)

    def connect(self, **options):
        """Context manager yielding a client for the selected interface."""
        self.require_interface()
        return connector_module(self.connector).connect(self, **options)

    def how_to_run(self):
        """How the selected interface reaches the game, plus the steps a person must do first."""
        self.require_interface()
        return {**connector_module(self.connector).how_to_run(self), "manual": self.config.get("manual") or "",
                "target": self.target}

    def configuration(self):
        """Editable values and input metadata for the console and settings files."""
        return describe_configuration(self.connector, self.config)

    def require_interface(self):
        if not self.interface:
            raise RuntimeError("%s에는 연결할 인터페이스가 없어 비교(compare, matrix)만 할 수 있습니다." % self.id
                               if not self.interfaces else
                               "%s는 인터페이스가 여러 개입니다. --interface로 고르세요: %s" % (self.id, ", ".join(self.interfaces)))

    def set_socket_path(self, path):
        """Set the existing game's socket for an attach interface."""
        if self.connector != "socket" or self.config.get("launch"):
            raise ValueError("소켓 경로는 기존 게임에 연결할 때만 지정할 수 있습니다.")
        if not isinstance(path, str) or not path.strip() or not Path(path.strip()).is_absolute():
            raise ValueError("기존 게임의 소켓 절대 경로를 입력하세요.")
        self.config["socket"] = path.strip()

    def observe(self, frame):
        result = self.observer.observe(frame)
        if not isinstance(result, dict) or not isinstance(result.get("state"), dict):
            raise ValueError("%s: observe()는 {'state': dict, 'description': str}을 반환해야 합니다." % self.id)
        return {"state": result["state"], "description": result.get("description") or "", "truth": result.get("truth")}

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
            grade.validate_truth(case.get("truth"), self.questions, "%s: %s" % (path, case["id"]))
            seen.add(case["id"])
        return data


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
