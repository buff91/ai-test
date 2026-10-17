# 외부 게임 연결

외부 게임도 `GameCase → observe → Jev/Laya → act`라는 같은 경로를 사용합니다. 공통 러너는 게임 제목이나 엔진을 알 필요가 없습니다. 게임 폴더가 모델 질문과 연결 프로필을 정의하고, 어댑터가 상태·행동을 변환합니다.

## 새 게임 폴더

```sh
uv run python play.py --games-dir /tmp/my-games new my_rpg --type turn.rpg --interface mod
uv run python play.py --games-dir /tmp/my-games add-interface my_rpg kbm
uv run python web_console.py --games-dir /tmp/my-games
```

템플릿은 저장소의 `games/_template`에서 복사하므로 외부 디렉터리에 템플릿을 준비할 필요가 없습니다. 예제 질문을 실제 게임의 선택지로 바꾸고 각 인터페이스 `actions`에 같은 선택지를 매핑합니다. 모드와 키마 프로필을 같은 게임에 함께 둘 수 있습니다.

## 키보드·마우스 연결

게임 창에서 픽셀을 읽는 `observer.py`와 입력을 보내는 `interface.json`을 작성합니다. 예제 관찰기는 실제 게임에 맞게 구현하기 전에는 실패하도록 되어 있습니다.

```json
{
  "connector": "kbm",
  "display": "visible",
  "window": {"owner": "MyGame", "title_contains": "My RPG"},
  "interval_ms": 700,
  "max_age_ms": 1500,
  "max_action_ms": 1000,
  "actions": {
    "attack": [{"tap": "a"}],
    "select_enemy": [{"click": "left", "at": "enemy_position"}],
    "move_forward": [{"hold": "w", "ms": 180}]
  }
}
```

```python
def observe(frame):
    # 게임 화면의 HUD/픽셀/OCR 결과로 계산합니다.
    # 예시 상수로 실제 게임 상태를 대신해서는 안 됩니다.
    return {"state": state, "description": description}
```

`state`의 클릭 대상 좌표는 게임 창 기준 `[0..1, 0..1]`입니다. 화면을 읽지 못했거나 대상이 없으면 예외를 내서 입력을 막습니다. 사용할 수 있는 입력은 `tap`, `hold`, `click`, `move`, `look`, `wait`이며 `kbm/steps.py`가 검증합니다.

```sh
uv run python play.py --games-dir /tmp/my-games --agent dev doctor my_rpg --interface kbm
uv run python play.py --games-dir /tmp/my-games --agent dev run my_rpg --interface kbm --dry-run --steps 1
```

`doctor`는 관찰·권한을 확인합니다. DRY-RUN은 계획한 입력을 기록하며 실제 이벤트를 보내지 않습니다. 콘솔의 실제 실행은 시작할 때 연결된 게임 창으로 한 번 전환하며, 이후 다른 앱으로 이동하면 입력을 차단합니다. 분석·비교·상태 조회는 창을 전환하지 않습니다. 동일 앱의 창이 여러 개라 대상을 구분할 수 없으면 `window.pid`를 지정합니다. CLI 실행에서는 게임 창을 앞에 두어야 합니다. 모델에 전달되는 것은 관찰기가 만든 상태와 질문이며, 이 구현이 Jev/Laya에 게임 스크린샷을 직접 보내는 것은 아닙니다.

## 모드·플러그인 연결

Slay the Spire처럼 게임 내부 상태를 모드에서 읽고 행동을 실행하는 방식에 사용할 수 있도록 localhost TCP JSONL을 지원합니다. **이 저장소는 Slay the Spire에 설치할 완성 모드를 제공하지 않습니다.** 해당 게임의 모드가 아래 계약을 구현하거나 기존 모드 프로토콜을 변환하는 브리지가 필요합니다.

```json
{
  "connector": "mod",
  "display": "visible",
  "endpoint": {"host": "127.0.0.1", "port": 8765},
  "interval_ms": 700,
  "max_age_ms": 5000,
  "actions": {
    "play_first_card": {"command": "play_card", "card_index": 0, "target_index": 0},
    "end_turn": {"command": "end_turn"}
  }
}
```

요청·응답은 UTF-8 JSON 한 줄입니다. 한 연결에서 요청 한 개와 응답 한 개를 교환합니다.

| 요청 | 응답 핵심 필드 |
|---|---|
| `{"op":"info"}` | `ok`, `build:{game,version}`, `protocol`, `capabilities` |
| `{"op":"state"}` | `ok`, `snapshot`, `description`, `revision`, `done` |
| `{"op":"step","action":{...},"expected_revision":12}` | `ok`, 변경 후 상태, 변경된 `revision` 또는 실패 사유 |
| `{"op":"reset","scenario":"..."}` | 지원할 때 초기 상태, 미지원이면 `ok:false` |

`revision`은 플레이어 입력이나 게임 진행으로 판단에 중요한 상태가 바뀔 때 증가해야 합니다. 모드가 행동 적용 직전에 버전을 다시 확인해야 오래된 판단이 새 턴에 적용되지 않습니다. 게임 엔진이 메인 스레드 접근을 요구한다면 소켓 스레드의 요청을 게임 스레드로 전달하고, 검증과 적용을 하나의 작업으로 실행합니다. 실시간 연동은 상태 버전·유효 시간·게임 자체의 시간 진행도 구현해야 합니다.

Python 호스트 예제는 `gamelab/mod_bridge.py`의 `ModBridge(observe=..., act=...)`를 사용합니다. 다른 언어의 모드도 같은 JSON 계약을 구현하면 됩니다. 게임별 카드 선택, 유효 행동, 애니메이션 완료·턴 대기는 모드 쪽 책임입니다.

실행 가능한 작은 모드 예제와 실제 소켓 테스트는 `examples/external_mod/`와 `tests/test_mod_connector.py`에 있습니다. 해당 예제는 외부 연결 구조 검증용이며 특정 상용 게임의 검증 결과가 아닙니다.

## 설정 변경과 증거

콘솔의 연결 설정은 메모리에서 적용하고 다시 연결합니다. 파일로 재현하려면 CLI의 `--settings`를 사용합니다.

```json
{"interval_ms": 500, "max_age_ms": 2500, "max_action_ms": 1000}
```

```sh
uv run python play.py --games-dir /tmp/my-games observe my_rpg --interface mod --endpoint tcp://127.0.0.1:8765 --settings /tmp/session-settings.json
```

실행 파일·관찰기 경로는 게임 패키지에 정의합니다. 콘솔에서 바꾸는 값은 시간·주소·창·행동 매핑으로 제한됩니다. 세션에는 사용한 설정, 선택 버전, 게임이 보고한 빌드, 모델 요청·응답, 입력 명령과 적용 결과가 남습니다. attach를 끊어도 외부 게임은 종료하지 않습니다.
