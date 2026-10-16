"""Game type registry (config/game_types.json): timing x genre -> which model plays and which shadows."""

import json
from functools import lru_cache
from pathlib import Path


PATH = Path(__file__).resolve().parent.parent / "config" / "game_types.json"
PROVIDER_NAMES = ("jev", "laya", "laya-mlx")
TIMINGS = ("turn", "realtime")


@lru_cache(maxsize=None)
def load_types(path=PATH):
    types = json.loads(path.read_text(encoding="utf-8"))
    for type_id, spec in types.items():
        where = "%s의 %s" % (path.name, type_id)
        if spec.get("timing") not in TIMINGS:
            raise ValueError("%s: timing은 %s 중 하나입니다." % (where, "/".join(TIMINGS)))
        if not isinstance(spec.get("latency_budget_ms"), (int, float)) or spec["latency_budget_ms"] <= 0:
            raise ValueError("%s: latency_budget_ms가 필요합니다." % where)
        validate_providers(spec.get("providers") or {}, where)
    return types


def validate_providers(providers, where):
    live, shadow = providers.get("live"), providers.get("shadow", [])
    if live is not None and live not in PROVIDER_NAMES:
        raise ValueError("%s: 알 수 없는 live provider %r" % (where, live))
    if not isinstance(shadow, list) or any(p not in PROVIDER_NAMES or p == live for p in shadow):
        raise ValueError("%s: shadow는 live와 다른 provider 목록이어야 합니다." % where)
    for name in providers.get("models", {}):
        if name not in PROVIDER_NAMES:
            raise ValueError("%s: models의 알 수 없는 provider %r" % (where, name))
