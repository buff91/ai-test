# 게임

게임 하나 = 폴더 하나이고, 폴더 이름이 게임 이름입니다. 게임 자체(무엇을 판단하는지)와 인터페이스(어떻게 연결하는지)를 나눠 둡니다.

```text
games/<게임>/
  game.json                       이름, 타입, 기본 인터페이스, policy
  questions.json                  모델에게 보낼 Choice·Score·Noul 질문
  cases.json                      비교용 상태와 질문별 정답(truth)
  interfaces/<이름>/interface.json  연결 방법 (connector: kbm 또는 socket)
  interfaces/<이름>/observer.py     kbm은 필수, socket은 선택
```

지금 있는 여섯 게임(`arena`, `cards`, `party`, `shooter`, `swarm`, `racer`)은 [sims/](../sims/)의 시뮬레이션이고, 매 상태의 정답을 게임이 직접 계산해 보냅니다.

```sh
uv run python play.py new mygame --type realtime.action    # 비교 전용으로 시작
uv run python play.py add-interface mygame kbm             # 화면 + 키보드·마우스
uv run python play.py add-interface mygame socket          # 테스트 훅
uv run python play.py run mygame --interface socket        # 인터페이스 골라 실행
```

## game.json

```json
{"name": "Arena · 포탑 돌파 (턴제 전술)", "type": "turn.tactics", "interface": "socket",
 "policy": {"guards": [{"name": "피격 직전", "when": "incoming_attack >= 0.7", "allow": ["dodge", "retreat", "attack"]}]}}
```

- `name`: 목록에 보일 이름
- `type` (필수): `config/game_types.json`의 타입. 어떤 모델이 플레이하고(live) 어떤 모델이 옆에서 비교만 할지(shadow) 정함. `uv run python play.py types`로 목록 확인
- `interface`: `--interface`를 안 줄 때 쓸 인터페이스. 인터페이스가 하나뿐이면 생략 가능
- `providers` (선택): 이 게임만 타입의 모델 매핑과 다르게 할 때. 적은 값만 덮어씀
- `policy` (선택): Score·Noul 답으로 행동을 제어하는 규칙. 기본은 기록만 하고 `run --policy`일 때 적용

```json
"providers": {"live": "laya-mlx", "shadow": ["jev"], "models": {"laya-mlx": "english"}}
```

### policy

| 키 | 형식 | 하는 일 |
|---|---|---|
| `guards[].when` | `"질문id >= 숫자"` (`>=`, `<=`, `>`, `<`) | score나 noul 질문의 답에 대한 조건 |
| `guards[].allow` | 선택지 목록 | 조건이 맞을 때 허용할 행동. 고른 행동이 없으면 이 중 모델이 가장 높게 본 것으로 바꿈 |
| `guards[].name` | 문자열 (선택) | 로그와 요약에 보일 이름 |
| `escalate.when` / `escalate.to` | 조건 / `jev`·`laya`·`laya-mlx` | 조건이 맞으면 그 모델에게 다시 물어 답을 바꿈 |

조건이 가리키는 질문이 이 게임에 없거나, `allow`에 없는 행동이 있으면 게임을 불러올 때 에러가 납니다.

## questions.json

모델(Jev, Laya, Laya-MLX)에게 **그대로** 보냅니다. 여기에는 질문 외의 키를 넣지 마세요.

- `action` (필수, `choice`): 이 게임에서 고를 수 있는 행동. 모든 인터페이스의 `actions`는 이 이름들과 정확히 같아야 함
- `score` 질문 (선택): 2~10개의 순서 있는 단계(`criteria`)
- `noul` 질문 (선택): 참/거짓 확률

## cases.json

```json
[{"id": "telegraph-00", "state": "Turn 1. Player: ...", "truth": {"action": ["dodge"], "danger": 2, "incoming_attack": true}}]
```

- `truth`의 키는 `questions.json`의 질문 id입니다. choice는 맞는 행동 목록, score는 단계 번호, noul은 true/false 또는 0~1 확률입니다.
- 모르는 질문은 빼거나 `null`로 두면 채점하지 않습니다. `truth`가 없는 케이스는 아무것도 채점하지 않습니다.
- 시뮬레이션 게임은 `uv run python -m sims cases <게임> --output games/<게임>/cases.json`으로 다시 만듭니다.

## socket 인터페이스

```json
{
  "connector": "socket",
  "launch": {"command": ["{python}", "-m", "sims", "serve", "arena"], "socket_arg": "--socket"},
  "interval_ms": 300,
  "actions": {"attack": "attack", "wait": "wait"}
}
```

- `launch.command`: 게임 실행 명령. `"{python}"`은 지금 Python. 그 밖의 첫 항목이 상대 경로면 프로젝트 폴더 기준. 프로젝트 폴더에서 실행하고 `socket_arg`와 소켓 경로를 붙임
- 이미 떠 있는 게임에 붙으려면 `launch` 대신 `"socket": "/소켓/경로"`
- `actions`의 값: 게임에 `step`으로 보낼 명령 이름. 선택지 이름과 게임 명령 이름이 다르면 여기서 연결
- `observer.py`의 `describe(state)`: 게임이 주는 설명문 대신 모델에게 보낼 문장을 만들고 싶을 때만

게임이 지켜야 할 요청 형식(`truth`, `done`, `reset` 포함)은 `gamelab/socket_game.py` 맨 위 주석과 프로젝트 README의 "socket 게임 붙이기"에 있습니다.

## kbm 인터페이스

`connector`는 `"kbm"`입니다. 예시는 [tests/fixtures/games/kbm_demo](../tests/fixtures/games/kbm_demo)에 있습니다.

- `window.owner`: 활동 상태 보기에 보이는 앱 이름. `title_contains`로 창 제목을 좁힐 수 있음
- `max_age_ms`: 관찰 후 이 시간이 지나면 판단을 버림
- `decision_keys`: 이 state 값이 판단 중 바뀌면 판단을 버림 (비우면 state 전체)
- `max_action_ms`: 행동 하나의 입력이 걸릴 수 있는 최대 시간
- `pause_key`: 게임에 일시정지가 있으면 지정. 판단 동안 멈춰 턴제처럼 실행
- `reflex_hz`: `observer.reflex()`를 초당 몇 번 돌릴지 (0이면 끔)

`actions`의 값은 입력 단계 목록입니다. 좌표는 게임 창 기준 0..1 비율이고, 문자열이면 observer가 state에 넣은 `[x, y]`를 씁니다.

```json
{"tap": "space"}                      키 한 번 (ms 기본 40)
{"tap": ["shift", "1"]}              조합키
{"hold": "w", "ms": 400}              누르고 있기
{"click": "left", "at": [0.5, 0.5]}  창 안 위치 클릭
{"click": "right", "at": "target"}   state["target"] 위치 클릭
{"move": "target"}                    커서 이동
{"look": [120, 0]}                    상대 마우스 이동 (FPS 시점)
{"wait": 100}                         대기
```

`interfaces/kbm/observer.py`:

- `observe(frame)` (필수): 화면을 `{"state": {...}, "description": "..."}`로 바꿈. 화면에서 확실히 알 수 있는 정답이 있으면 `"truth": {...}`도 넣을 수 있음. `frame.fill_ratio`, `frame.ratio`, `frame.find_color`, `frame.mean_rgb`를 쓸 수 있음 (`kbm/vision.py`)
- `reflex(state)` (선택): 모델 없이 바로 반응할 행동 이름 또는 `None`

observer.py는 빌드된 KbmAgent 안에서 실행되므로 표준 라이브러리, Pillow, `kbm.vision`만 import하세요.
