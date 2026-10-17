# 외부 게임 모드 연결 예제

`mod` connector는 이미 실행 중인 게임에 TCP JSONL로 연결합니다. 이 폴더는 실행 가능한 Python 브리지 예제이며, Slay the Spire 설치용 모드를 포함하지 않습니다. 실제 게임에 붙일 때는 해당 게임의 모드 API에서 상태와 행동 콜백을 구현합니다.

저장소 루트의 터미널 두 개에서 실행합니다.

```sh
# 터미널 1: 별도 게임 프로세스 역할
uv run python -m examples.external_mod.demo

# 터미널 2: 연결 확인 → 상태 읽기 → 모델 제어
uv run python play.py --games-dir examples/external_mod/games doctor mod_demo
uv run python play.py --games-dir examples/external_mod/games observe mod_demo
uv run python play.py --games-dir examples/external_mod/games run mod_demo --provider laya-mlx --steps 3 --settings examples/external_mod/settings.example.json
```

콘솔도 `--games-dir examples/external_mod/games`로 이 카탈로그를 선택할 수 있습니다. 브리지 연결을 끊어도 외부 게임은 종료되지 않습니다. 실제 게임 창은 해당 게임이 표시하며, 이 예제의 Python 상태 머신에는 별도 게임 창이 없습니다.

## 두 가지 확장 경로

```sh
uv run python play.py --games-dir /tmp/my-games new mygame --type turn.rpg --interface mod
uv run python play.py --games-dir /tmp/my-games add-interface mygame kbm
```

모드 경로에서는 `endpoint`와 `actions`를 설정합니다. 선택지 `play_first_card`를 `{"command":"play_card","card_index":0,"target_index":0}` 같은 객체에 매핑할 수 있습니다. SDK인 `gamelab.mod_bridge.ModBridge`는 같은 계약을 Python 콜백으로 구현합니다. Java/C#/Lua 모드는 아래 JSON 규약만 구현해도 됩니다.

KBM 경로에서는 `window.owner`, `window.title_contains`, `actions`와 `observer.py`를 구현합니다. 화면의 실제 UI 영역을 읽어 상태를 만든 뒤, 상태 좌표로 마우스를 움직이거나 키를 누릅니다. 제공 템플릿의 관찰 함수는 미구현 오류로 멈추므로 보정 전에는 자동 입력을 보내지 않습니다. 게임 창이 앞에 없으면 입력을 거부하고, 화면 왼쪽 위로 마우스를 옮기면 입력을 중단합니다.

## JSONL v1 규약

UTF-8 JSON 객체 하나와 줄바꿈을 요청/응답으로 주고받습니다. 요청마다 연결 하나를 사용하며 메시지 크기는 1 MiB 이하입니다. 모드 서버는 `127.0.0.1`에서만 수신합니다.

| 요청 | 필수 응답 |
| --- | --- |
| `{"op":"info"}` | `ok`, `protocol: {name:"gamelab",version:1}`, `build: {game,version}`, `capabilities: {observe:true,act:true,reset:false,pause:false,halt:false}` |
| `{"op":"state"}` | `ok`, `snapshot` 객체, `revision` 정수/문자열. 선택: `description`, `done` |
| `{"op":"step","action":{"command":"attack","target":0},"expected_revision":3}` | `ok`, `applied`, 현재 `snapshot`, `revision`. 거부 시 `ok:false`, `error`, 상태 변경 시 `stale:true` |
| `{"op":"reset"}` | 선택 기능. 새 상태 응답. 지원 여부를 `capabilities.reset`에 표시 |

게임 ID와 버전은 카탈로그에 맞게 보고합니다. 실제 게임의 결정 관련 상태가 바뀔 때마다 `revision`을 변경하고, **revision 확인과 행동 적용을 게임 스레드에서 한 연산으로 처리**해야 합니다. Python SDK의 잠금만으로 외부 엔진 스레드까지 보호되지는 않습니다. 플레이어의 수동 입력도 revision을 변경해야 오래된 모델 답이 잘못 적용되지 않습니다.

실시간 게임은 interface.json에 `continuous:true`를 넣습니다. 이때 connector는 관찰 시각과 `max_age_ms`를 검사하고 요청에도 전달합니다. SDK 역시 유효 시간을 검사합니다. 모델 응답 지연과 게임 엔진 내부의 행동 처리 지연은 별도이며, 콜백은 실제 행동 적용 후 결과를 반환해야 합니다. 실패한 명령은 `ok:false` 또는 `applied:false`로 보고합니다. 미래의 입력을 큐에 넣었다는 이유만으로 성공을 반환하지 마세요.

`--settings`와 콘솔 설정은 이번 연결에만 적용합니다. 판단 간격, 유효 시간, 입력 시간 상한, 소켓/TCP 주소, 창, 행동 매핑만 바꿀 수 있으며 실행 파일이나 observer 코드는 바꿀 수 없습니다. 설정한 행동 매핑, 실제 전송 명령, 관찰 상태와 모델 판단은 서로 구분해서 확인합니다. KBM dry-run에는 실제 전송 명령이 없습니다.
