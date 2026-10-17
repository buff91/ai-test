"""Validated per-connection settings shared by CLI, console, and KBM agent.

Settings never change executable paths, Python observers, or connector selection.
Those belong to the local, reviewed game catalog.
"""

from copy import deepcopy
from pathlib import Path

from gamelab.protocol import endpoint_spec


FIELDS = {
    "interval_ms": {"type": "integer", "min": 1, "max": 60000, "label": "판단 간격 (ms)"},
    "max_age_ms": {"type": "integer", "min": 1, "max": 600000, "label": "판단 유효 시간 (ms)"},
    "max_action_ms": {"type": "integer", "min": 1, "max": 60000, "label": "입력 최대 시간 (ms)"},
    "actions": {"type": "json", "label": "행동 매핑"},
    "window": {"type": "json", "label": "게임 창"},
    "socket": {"type": "string", "label": "소켓 절대 경로"},
    "endpoint": {"type": "json", "label": "모드 TCP 주소"},
}


def editable_fields(connector, config):
    fields = ["interval_ms", "max_age_ms", "max_action_ms", "actions"] if connector else []
    if connector == "kbm":
        fields.append("window")
    elif connector == "mod":
        fields.append("endpoint")
    elif connector == "socket" and not config.get("launch"):
        fields.append("socket")
    return fields


def apply_overrides(connector, config, overrides):
    result = deepcopy(config)
    if overrides is None:
        return result
    if not isinstance(overrides, dict):
        raise ValueError("설정값은 JSON 객체여야 합니다.")
    unsupported = set(overrides) - set(editable_fields(connector, config))
    if unsupported:
        raise ValueError("변경할 수 없는 설정입니다: %s" % ", ".join(sorted(unsupported)))
    for key, value in overrides.items():
        if key in ("interval_ms", "max_age_ms", "max_action_ms"):
            spec = FIELDS[key]
            if type(value) is not int or not spec["min"] <= value <= spec["max"]:
                raise ValueError("%s는 %d~%d의 정수여야 합니다." % (key, spec["min"], spec["max"]))
        elif key == "socket":
            if not isinstance(value, str) or not value.strip() or not Path(value.strip()).is_absolute():
                raise ValueError("기존 게임의 소켓 절대 경로를 입력하세요.")
            value = value.strip()
        elif key == "endpoint":
            value = endpoint_spec(value)
        elif key == "window":
            if not isinstance(value, dict) or set(value) - {"owner", "title_contains", "pid"}:
                raise ValueError("window에는 owner, title_contains, pid만 지정할 수 있습니다.")
            value = {**result.get("window", {}), **value}
            if not isinstance(value.get("owner"), str) or not value["owner"].strip():
                raise ValueError("window.owner는 비어 있지 않은 문자열이어야 합니다.")
            if not isinstance(value.get("title_contains", ""), str):
                raise ValueError("window.title_contains는 문자열이어야 합니다.")
            if "pid" in value and (type(value["pid"]) is not int or value["pid"] <= 0):
                raise ValueError("window.pid는 양의 정수여야 합니다.")
        elif key == "actions" and not isinstance(value, dict):
            raise ValueError("actions는 행동 이름을 키로 하는 JSON 객체여야 합니다.")
        result[key] = deepcopy(value)
    return result


def describe_configuration(connector, config):
    keys = editable_fields(connector, config)
    return {"connector": connector, "editable": keys,
            "values": {key: deepcopy(config.get(key)) for key in keys},
            "fields": {key: deepcopy(FIELDS[key]) for key in keys}}
