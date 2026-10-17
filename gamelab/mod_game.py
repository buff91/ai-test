"""Attach to an external game's localhost mod bridge through the common client API."""

from contextlib import contextmanager

from gamelab.protocol import ModClient, PROTOCOL, endpoint_spec
from gamelab.socket_game import SocketGame, _verify_target, validate_actions


def validate(game):
    if game.config.get("launch") or game.config.get("socket"):
        raise ValueError("mod 연결은 기존 게임의 endpoint만 사용합니다. launch/socket은 지정하지 마세요.")
    game.config["endpoint"] = endpoint_spec(game.config.get("endpoint"))
    validate_actions(game)


def how_to_run(game):
    return {"mode": "attach", "transport": "tcp-jsonl", "endpoint": game.config["endpoint"],
            "protocol": dict(PROTOCOL)}


@contextmanager
def connect(game, **_):
    client = ModClient(game.config["endpoint"])
    info = client.request("info")
    if not info.get("ok"):
        raise ValueError("모드 브리지 info 실패: %s" % info.get("error", "알 수 없는 오류"))
    if info.get("protocol") != PROTOCOL:
        raise ValueError("모드 브리지에는 gamelab protocol version 1이 필요합니다.")
    build = info.get("build")
    if not isinstance(build, dict) or not all(isinstance(build.get(key), str) and build[key] for key in ("game", "version")):
        raise ValueError("모드 브리지는 info.build에 game과 version을 보고해야 합니다.")
    capabilities = info.get("capabilities") or {}
    if not isinstance(capabilities, dict) or not all(capabilities.get(op) is True for op in ("observe", "act")):
        raise ValueError("모드 브리지는 observe와 act 기능을 지원해야 합니다.")
    linked = SocketGame(game, client, runtime=how_to_run(game))
    _verify_target(linked)
    # Attach owns no game process or socket. Leaving this block never calls quit,
    # shutdown, or reset on the external game.
    yield linked
