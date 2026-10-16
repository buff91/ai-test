# 게임 가이드: 구성과 수정 방법

이 문서는 `games/`의 게임을 읽고 고치는 사람을 위한 안내서입니다. 앞부분은 지금 무엇이 어떻게 설정되어 있는지, 뒷부분은 무엇을 고치고 싶을 때 어떤 파일을 어떻게 바꾸면 되는지를 다룹니다.

- [1. 기본 개념](#1-기본-개념)
- [2. 게임 폴더의 세 파일](#2-게임-폴더의-세-파일-game--questions--cases)
- [3. 지금 있는 게임 6종](#3-지금-있는-게임-6종)
- [4. 연결 방식: socket과 kbm](#4-연결-방식-socket과-kbm)
- [5. 수정 가이드](#5-수정-가이드)
- [6. 수정한 뒤 확인하기](#6-수정한-뒤-확인하기)
- [7. 자주 보는 에러](#7-자주-보는-에러)

---

## 1. 기본 개념

모델(Jev, Laya, Laya-MLX)에게 게임 장면을 보여 주고 세 종류의 질문을 합니다.

| 유형 | 묻는 것 | 모델의 답 | 채점 방식 |
|---|---|---|---|
| **Choice** | 다음에 무엇을 할까? | 선택지 하나 (+ 선택지별 확률) | 정답 행동 목록에 들어 있으면 정답 |
| **Score** | 지금 얼마나 위험한가? | 0 ~ (단계 수 - 1) 사이의 숫자 (예: 1.8) | 정답 단계와의 거리 (평균 오차) |
| **Noul** | 가만히 있으면 당하는가? | 0 ~ 1 확률 | Brier 점수 = (답 - 정답)². 0이 완벽, 늘 0.5라고 답하면 0.25 |

**게임에 실제로 적용하는 것은 Choice뿐입니다.** Score와 Noul은 기본적으로 채점과 기록에만 씁니다. `game.json`의 `policy`를 켜면(`play.py run --policy`) Score·Noul 값으로 Choice를 바꿀 수 있습니다.

정답(truth)은 두 군데에서 옵니다.

- **플레이할 때(`play.py run`)**: 게임이 장면과 함께 정답을 보냅니다. 지금 있는 여섯 게임은 규칙을 직접 계산하는 시뮬레이션(`sims/`)이라 모든 장면에 정답이 있습니다.
- **오프라인 비교(`play.py compare`, `play.py matrix`)**: `cases.json`에 적힌 정답을 씁니다.

모델에게는 장면 설명과 질문만 갑니다. 정답은 절대 모델에게 보내지 않습니다.

```text
run      게임 ─(장면 + 정답)→ 러너 ─(장면 + 질문)→ 모델 ─(답)→ 채점 → policy → 행동 → 게임
compare  cases.json ─(장면)→ 모델 ─(답)→ cases.json의 정답으로 채점
```

---

## 2. 게임 폴더의 세 파일: game / questions / cases

```text
games/shooter/
  game.json                         이 게임이 무엇이고 어떻게 운영할지
  questions.json                    모델에게 매번 보내는 질문지
  cases.json                        정답이 붙은 시험 문제집
  interfaces/socket/interface.json  게임에 어떻게 연결할지
```

### game.json: 게임의 신분증과 운영 규칙

```json
{
  "name": "Shooter · 복도 돌파 (실시간 액션)",
  "type": "realtime.action",
  "interface": "socket",
  "policy": {
    "guards": [{"name": "피격 직전", "when": "incoming_attack >= 0.7", "allow": ["dodge", "retreat", "attack"]}],
    "escalate": {"when": "danger >= 1.5", "to": "jev"}
  }
}
```

| 키 | 뜻 |
|---|---|
| `name` | `play.py list`에 보일 이름 |
| `type` | 장르. [config/game_types.json](../config/game_types.json)의 타입 중 하나. 어떤 모델이 플레이할지(live), 옆에서 기록만 할지(shadow), 판단 한 번에 허용되는 시간(지연 예산)이 여기서 정해짐 |
| `interface` | 기본 연결 방식. `interfaces/` 아래 폴더 이름 |
| `providers` (선택) | 이 게임만 모델 매핑을 바꿀 때. 예: `{"live": "laya-mlx"}` |
| `policy` (선택) | Score·Noul로 행동을 제어하는 규칙 ([5-3](#5-3-policy-바꾸기-gamejson)) |

### questions.json: 모델에게 보내는 질문지

이 파일은 **수정 없이 그대로** 모델에게 전송됩니다. 그래서 여기 적은 문장이 곧 모델이 읽는 지시문입니다.

```json
{
  "action": {
    "type": "choice",
    "instructions": "Which single action should the player take next based on this state?",
    "criteria": {
      "attack": "Shoot the nearest enemy ahead when it is within range and ammo is loaded.",
      "dodge": "Evade a gunner shot that will fire this turn."
    }
  },
  "danger": {
    "type": "score",
    "instructions": "How much immediate danger is the player in, before taking the next action? ...",
    "criteria": ["Safe: ...", "Threatened: ...", "Critical: ..."]
  },
  "incoming_attack": {
    "type": "noul",
    "instructions": "If the player chooses wait this turn, will an enemy attack reduce the player's health? ..."
  }
}
```

- 바깥 키(`action`, `danger`, `incoming_attack`)가 **질문 id**입니다. 정답, 채점 결과, policy 조건이 모두 이 id로 연결됩니다.
- `action`(choice)은 필수입니다. `criteria`의 키가 곧 이 게임의 행동 이름입니다.
- score의 `criteria`는 순서 있는 단계 목록입니다(2~10개). 첫 번째가 0, 그다음이 1입니다.
- noul은 `instructions`만 있습니다.

### cases.json: 정답이 붙은 시험 문제집

```json
[
  {"id": "gunner_aiming-00",
   "state": "Turn 1. The player is at tile 2 of a corridor; ...",
   "truth": {"action": ["dodge"], "danger": 2, "incoming_attack": true}}
]
```

| 키 | 뜻 |
|---|---|
| `id` | 파일 안에서 겹치지 않는 이름 |
| `state` | 모델에게 보여 줄 장면 설명 |
| `truth` | 질문 id별 정답. choice는 **맞는 행동 목록**, score는 **단계 번호**, noul은 **true/false 또는 0~1 확률** |

- 정답을 모르는 질문은 빼거나 `null`로 두면 채점하지 않습니다.
- 여섯 게임의 `cases.json`은 사람이 쓴 것이 아니라 시뮬레이션이 시나리오를 진행하며 자동으로 만든 것입니다([5-2](#5-2-케이스-바꾸기-casesjson)).

---

## 3. 지금 있는 게임 6종

모두 [sims/](../sims/)의 시뮬레이션이고 socket으로 연결됩니다.

### Choice / Score / Noul 정의

정답을 계산하는 공통 원칙은 이렇습니다.

- **Choice**: 게임이 정해진 수만큼 앞을 탐색해서, 이기는 쪽으로 가장 빠르고 체력을 많이 남기는 행동을 고릅니다. 동점인 행동은 모두 정답입니다. 모든 행동이 똑같으면 그 장면의 Choice는 채점하지 않습니다.
- **Score / Noul**: "기준 행동"(아무것도 안 하는 행동)을 했을 때 어떻게 되는지로 정합니다.
  - 공통 3단계: 이번 턴에 피해·패배 → 2, 다음 턴에 → 1, 두 턴 동안 괜찮으면 → 0
  - Noul: 이번 턴에 기준 행동을 하면 체력이 줄어드는가

| 게임 | 장르 (type) | Choice 선택지 · 탐색 깊이 | Score `danger` | Noul | 기준 행동 |
|---|---|---|---|---|---|
| `arena` | 포탑을 부수고 출구로 (`turn.tactics`) | attack, dodge, reload, retreat, advance, wait · 10수 | 공통 3단계 | `incoming_attack`: 포탑 사격에 맞는가 | wait |
| `cards` | 덱빌딩 카드 전투 (`turn.card`) | play_attack, play_block, play_power, use_potion, end_turn · 10수 | 지금 턴을 끝내면: HP 그대로 0 / HP 잃음 1 / 사망 2 | `incoming_attack`: 적 행동에 HP가 깎이는가 | end_turn |
| `party` | 파티 RPG (`turn.rpg`) | attack, aoe_skill, heal, guard, use_item · 8수 | 성기사가 그냥 공격하면: 동료가 이번 라운드에 쓰러짐 2 / 다음 라운드 1 / 아님 0 | **없음** | attack |
| `shooter` | 복도 돌파 (`realtime.action`) | attack, dodge, reload, retreat, advance, wait · 9수 | 공통 3단계 | `incoming_attack`: 사격·돌진에 맞는가 | wait |
| `swarm` | 몰려오는 무리 (`realtime.topdown`) | attack_target, skill_on_target, kite_back, drink_potion, hold · 8수 | 공통 3단계 | `incoming_attack`: 무리에게 물리는가 | hold |
| `racer` | 3차선 레이스 (`realtime.racing`) | accelerate, brake, steer_left, steer_right, boost, keep · 7수 | 속도·차선을 유지하면: 이번 이동에 충돌 2 / 다음 이동 1 / 아님 0 | `crash_ahead`: 이번 이동에 충돌하는가 | keep |

party만 기준 행동이 "공격"인 이유: 방어(guard)를 기준으로 재면 드래곤 강타 직전에도 방어하면 산다는 이유로 위험도 0이 나와서, "아무 대비 없이 공격만 하면"으로 정의했습니다.

### 시나리오

판이 끝나면 다음 시나리오로 넘어갑니다(`play.py run --episodes N`).

| 게임 | 시나리오 |
|---|---|
| `arena` | opening, opportunity, telegraph, empty_mag, crisis |
| `cards` | opening, lethal_intent, kill_window, buff_turn, no_potion_low |
| `party` | goblin_pack, dragon_strike, knight_low, knight_down, last_goblin |
| `shooter` | corridor, gunner_aiming, empty_mag, rusher_close, clear_path, last_stand |
| `swarm` | low_hp, grouped, single_in_range, swarmed, approaching |
| `racer` | hairpin_fast, long_straight, obstacle_left, obstacle_right, corner_exit |

직접 해 보려면: `uv run python -m sims play shooter --scenario gunner_aiming --show-truth`

### policy

기본은 **기록만** 합니다(로그에 `⚑피격 직전`처럼 표시). `play.py run --policy`일 때만 행동을 실제로 바꿉니다.

| 게임 | guard (조건 → 허용 행동) | escalate (조건 → 다시 물을 모델) |
|---|---|---|
| `arena` | `incoming_attack >= 0.7` → dodge, retreat, attack | 없음 |
| `cards` | `danger >= 1.5` → play_block, use_potion, play_attack | 없음 |
| `party` | `danger >= 1.5` → heal, guard, use_item, aoe_skill | 없음 |
| `shooter` | `incoming_attack >= 0.7` → dodge, retreat, attack | `danger >= 1.5` → jev |
| `swarm` | `danger >= 1.5` → attack_target, skill_on_target, kite_back, drink_potion | `danger >= 1.5` → jev |
| `racer` | `crash_ahead >= 0.6` → brake, steer_left, steer_right | `danger >= 1.5` → jev |

escalate는 평소에 빠른 로컬 모델(laya-mlx)이 플레이하는 실시간 3종에만 두었습니다. 위험할 때만 Jev에게 다시 묻습니다(`JEV_API_KEY` 필요).

### 모델 매핑 (config/game_types.json)

| 게임 | 지연 예산 | live (실제 플레이) | shadow (기록만) |
|---|---|---|---|
| `arena`, `party` | 3000ms | jev | laya-mlx |
| `cards` | 5000ms | jev | laya-mlx |
| `shooter` | 150ms | laya-mlx | 없음 |
| `swarm` | 300ms | laya-mlx | 없음 |
| `racer` | 100ms | laya-mlx | 없음 |

---

## 4. 연결 방식: socket과 kbm

| 방식 | 언제 | 정답(truth) | 지금 쓰는 곳 |
|---|---|---|---|
| **socket** (인터페이스 연동) | 게임이 상태를 내보내고 명령을 받는 테스트 훅이 있을 때 | 게임이 보내 주면 매 장면 자동 채점 | 여섯 게임 전부 |
| **kbm** (키보드·마우스) | 훅이 없어서 화면과 입력 장치만 쓸 수 있을 때 | 보통 없음. cases.json에 사람이 채움 | [tests/fixtures/games/kbm_demo](../tests/fixtures/games/kbm_demo) (테스트용 예시) |

여섯 게임의 socket 설정입니다.

```json
{
  "connector": "socket",
  "launch": {"command": ["{python}", "-m", "sims", "serve", "shooter"], "socket_arg": "--socket"},
  "interval_ms": 200,
  "actions": {"attack": "attack", "dodge": "dodge", "...": "..."}
}
```

- `launch.command`: `play.py`가 게임을 직접 띄웁니다. `{python}`은 지금 실행 중인 Python입니다.
- `interval_ms`: 판단 주기입니다. 턴제 3종은 300, 실시간 3종은 200입니다.
- `actions`: 선택지 이름 → 게임에 보낼 명령 이름입니다. 지금은 둘이 같습니다.

`arena`에는 인터페이스가 하나 더 있습니다. `interfaces/app`은 같은 게임을 빌드한 실행 파일(`dist/ArenaGame/ArenaGame`, 별도 게임 창이 뜸)로 띄웁니다. 정답(truth)은 보내지 않으므로 채점은 되지 않습니다. 먼저 `./build_game.sh`로 빌드하세요.

`interfaces/kbm`은 같은 ArenaGame 창을 **화면과 키보드로만** 다룹니다. 게임 창을 직접 띄워 두고 연결하면, KbmAgent가 창을 캡처해 플레이어·포탑 위치와 포탑 조준 여부를 색으로 읽고(`observer.py`), 행동은 키로 보냅니다(A 공격, Space 회피, R 재장전, ← 후퇴, → 전진, W 대기). 체력·탄약은 화면에 글자로만 있어 읽지 않습니다. 화면 색은 game.py의 색 값과 최대 25 정도 다르게 찍히므로, observer의 색은 실제 캡처에서 잰 값입니다. 화면 기록 권한(실제 입력은 손쉬운 사용 권한도)이 필요하고, 창이 최소화되거나 다른 데스크톱에 있으면 캡처할 수 없습니다.

게임마다 실행 방법이 달라도 `interface.json`만 다르게 적으면 됩니다. 콘솔이 띄울 수 없는 게임(Steam, 로그인이 필요한 빌드 등)은 `launch` 대신 `"socket": "/소켓/경로"`로 이미 떠 있는 게임에 붙고, 사람이 먼저 할 일은 `"manual"`에 적습니다. `uv run python web_console.py`의 연결 화면에 이 내용이 그대로 보입니다.

`kbm_demo`는 창 이름이 `GameAppName`이고 화면 좌표·색이 임시값이라 실제 게임에는 붙지 않습니다. 실제 게임을 키마로 붙이는 방법은 [5-6](#5-6-실제-게임-붙이기)을 보세요.

---

## 5. 수정 가이드

먼저 "무엇을 바꾸고 싶은가"로 고칠 파일을 찾으세요.

| 하고 싶은 것 | 고칠 곳 | 같이 해야 할 것 |
|---|---|---|
| 모델에게 보내는 질문 문장 다듬기 | `questions.json`의 `instructions`, `criteria` 설명 | 없음 (정답 정의와 뜻이 맞는지만 확인) |
| 선택지(행동) 추가·삭제·이름 변경 | `sims/<게임>.py`의 `ACTIONS`와 규칙 | `questions.json`, `interface.json`의 `actions`, 케이스 다시 만들기 |
| Score 단계 수 바꾸기 | `sims/<게임>.py`의 `danger_truth` | `questions.json`의 `criteria`, 케이스 다시 만들기 |
| Noul 질문 빼기·바꾸기 | `sims/<게임>.py`의 `QUESTION_IDS`, `incoming_truth` | `questions.json`, 케이스 다시 만들기 |
| 케이스 수 늘리기·다시 만들기 | `python -m sims cases` | 없음 |
| 손으로 케이스 추가 | `cases.json` | 없음 |
| 가드 조건·허용 행동 바꾸기 | `game.json`의 `policy` | 없음 |
| 어떤 모델이 플레이할지 | `game.json`의 `providers` 또는 `config/game_types.json` | 없음 |
| 판단 주기 | `interface.json`의 `interval_ms` | 없음 |
| 게임 규칙·수치 (피해량, 체력 등) | `sims/<게임>.py` 맨 위 상수 | 케이스 다시 만들기, 테스트 |
| 시나리오 추가 | `sims/<게임>.py`의 `SCENARIOS` | 케이스 다시 만들기 |
| 새 게임 | [5-6](#5-6-실제-게임-붙이기) 또는 [5-7](#5-7-새-시뮬레이션-게임-만들기) | |

> **중요:** 여섯 게임은 정답을 `sims/` 코드가 계산합니다. `questions.json`만 바꾸고 코드를 그대로 두면 **모델은 새 질문을 읽는데 채점은 옛 기준**으로 됩니다. 질문의 **뜻**(선택지, 단계 수, Noul이 묻는 상황)을 바꿀 때는 반드시 코드도 같이 바꾸세요. 문장만 다듬는 것은 괜찮습니다.

### 5-1. 질문 바꾸기 (questions.json)

**문장만 다듬기**: `instructions`와 선택지 설명을 자유롭게 고치세요. 영어로 두는 것을 권합니다(모델 대부분이 영어 지시문에서 더 안정적입니다). 고친 뒤 `play.py compare <게임>`으로 정답률이 어떻게 바뀌는지 비교하면, 질문 문구가 모델 판단에 주는 영향을 볼 수 있습니다.

**Score 단계 설명을 바꿀 때**: 단계 수(`criteria` 개수)와 순서는 `danger_truth`가 돌려주는 숫자와 맞아야 합니다. 지금은 모두 3단계(0, 1, 2)입니다. 설명 문장은 [3장의 정의](#choice--score--noul-정의)와 같은 뜻으로 유지하세요.

**Noul을 바꿀 때**: Noul 문장은 "기준 행동을 하면 이번 턴에 당하는가"와 같은 뜻이어야 정답과 맞습니다. 예를 들어 "앞으로 3턴 안에 죽는가?"로 바꾸려면 `sims/<게임>.py`에 `incoming_truth`를 새로 정의해야 합니다.

**선택지를 바꿀 때** (예: shooter에 `grenade` 추가):

1. `sims/shooter.py`의 `ACTIONS`에 `"grenade"`를 넣고 `step()`에 효과를 구현합니다. 쓸 수 없는 상황이 있으면 `legal()`에도 반영합니다.
2. `games/shooter/questions.json`의 `action.criteria`에 `"grenade": "언제 쓰는 행동인지"`를 추가합니다.
3. `games/shooter/interfaces/socket/interface.json`의 `actions`에 `"grenade": "grenade"`를 추가합니다.
4. 케이스를 다시 만듭니다: `uv run python -m sims cases shooter --output games/shooter/cases.json`
5. [6장](#6-수정한-뒤-확인하기)대로 확인합니다.

세 곳(코드의 `ACTIONS`, `criteria`, `actions`)의 이름이 하나라도 다르면 게임을 불러올 때나 테스트에서 에러가 납니다.

### 5-2. 케이스 바꾸기 (cases.json)

**다시 만들기 (권장)**

```sh
uv run python -m sims cases shooter --output games/shooter/cases.json
uv run python -m sims cases shooter --per-scenario 12 --seed 3 --output games/shooter/cases.json
```

| 옵션 | 뜻 | 기본값 |
|---|---|---|
| `--per-scenario` | 시나리오마다 뽑을 장면 수 | 6 |
| `--explore` | 정답 대신 무작위 행동으로 진행하는 비율. 높을수록 실수 이후의 다양한 장면이 나옴 | 0.3 |
| `--seed` | 무작위 시드. 같은 값이면 항상 같은 파일이 나옴 | 0 |

규칙이나 질문을 바꿨다면 반드시 다시 만드세요. 옛 케이스의 정답은 옛 규칙 기준입니다.

**손으로 추가하기**: 실제 게임에서 겪은 상황처럼 시뮬레이션이 만들지 않는 장면을 넣고 싶을 때 씁니다.

```json
{"id": "my-case-01", "state": "장면 설명 (모델이 읽을 문장)", "truth": {"action": ["dodge", "retreat"], "danger": 2, "incoming_attack": true}}
```

- 맞는 행동이 여러 개면 모두 넣으세요. 하나만 넣으면 나머지는 오답 처리됩니다.
- 확실하지 않은 질문은 빼세요. 틀린 정답을 넣는 것보다 채점하지 않는 편이 낫습니다.
- 다시 만들기를 하면 손으로 넣은 케이스가 **덮어써집니다**. 손으로 만든 케이스는 별도 파일(예: `cases.manual.json`)에 두고 `play.py compare <게임> --cases games/<게임>/cases.manual.json`으로 돌리는 것을 권합니다.

**플레이 기록에서 가져오기**: `play.py run`의 기록을 케이스로 바꿀 수 있습니다. 게임이 보낸 정답도 함께 들어갑니다.

```sh
uv run python play.py export-cases shooter results/games/shooter/<기록 폴더>   # → games/shooter/cases.recorded.json
```

### 5-3. policy 바꾸기 (game.json)

```json
"policy": {
  "guards": [
    {"name": "피격 직전", "when": "incoming_attack >= 0.7", "allow": ["dodge", "retreat", "attack"]},
    {"name": "빈사", "when": "danger >= 1.8", "allow": ["retreat"]}
  ],
  "escalate": {"when": "danger >= 1.5", "to": "jev"}
}
```

| 항목 | 규칙 |
|---|---|
| `when` | `질문id 비교연산자 숫자`. 연산자는 `>=`, `<=`, `>`, `<`. 질문은 이 게임의 score나 noul이어야 함 |
| `allow` | 조건이 맞을 때 허용할 행동 목록. 모델이 고른 행동이 여기 없으면, 이 중에서 모델이 Choice 확률을 가장 높게 준 행동으로 바뀜 |
| `name` | 로그와 요약에 보일 이름 (생략하면 조건식이 이름) |
| guard 순서 | 위에서부터 검사하고, 행동을 바꾸는 것은 처음 걸린 guard 하나뿐. 나머지는 발동 기록만 남김 |
| `escalate.to` | `jev`, `laya`, `laya-mlx` 중 하나. live 모델과 같으면 무시 |

**좋은 policy인지 확인하는 법**

```sh
uv run python play.py run shooter --provider laya-mlx --episodes 5            # 1) 기록만: 얼마나 자주 발동하는지
uv run python play.py run shooter --provider laya-mlx --episodes 5 --policy   # 2) 적용: 결과 비교
```

요약의 `[policy]` 줄을 보세요.

- **정답으로 바뀜**이 **오답으로 바뀜**보다 많아야 좋은 규칙입니다.
- 발동이 거의 없으면 조건 숫자가 너무 높은 것이고, 매 step 발동하면 너무 낮은 것입니다. 모델이 늘 높은 값을 내는 경우도 있습니다(예: shooter 첫 실행에서 laya-mlx는 Noul을 거의 매번 0.9 이상으로 답함).
- "실제로 보낸 행동의 정답률"이 policy를 켰을 때 올라가는지 확인하세요.

policy를 아예 없애려면 `"policy"` 키를 지우면 됩니다.

### 5-4. 모델과 판단 주기 바꾸기

**이 게임만 다른 모델로 플레이**: `game.json`에 추가합니다.

```json
"providers": {"live": "laya-mlx", "shadow": ["laya"], "models": {"laya-mlx": "english"}}
```

**장르 전체의 매핑**: [config/game_types.json](../config/game_types.json)의 `providers`를 고칩니다. 어떤 모델이 나은지는 `play.py matrix --provider local` 결과를 보고 정하세요.

**한 번만 다른 모델로**: `play.py run shooter --provider laya`

**판단 주기**: `interfaces/socket/interface.json`의 `interval_ms`를 고칩니다(밀리초). 시뮬레이션은 모델이 답할 때까지 기다려 주므로, 이 값은 사람이 지켜보기 편한 속도 정도의 의미입니다.

### 5-5. 게임 규칙과 시나리오 바꾸기 (sims/)

각 게임은 `sims/<게임>.py` 파일 하나입니다. 맨 위 상수에 피해량, 체력, 사거리 같은 수치가 모여 있습니다.

```python
# sims/shooter.py
EXIT, RANGE, SHOT, MELEE, AGGRO = 10, 5, 25, 20, 6
```

**시나리오 추가**: `SCENARIOS`에 시작 상태를 하나 더 적습니다. 적지 않은 값은 클래스 아래쪽의 기본값을 씁니다.

```python
SCENARIOS = {
    ...
    "two_gunners": {"x": 1, "enemies": (("gunner", 4, 2, False), ("gunner", 6, 2, True))},
}
```

수정한 뒤 할 일:

1. `uv run python -m sims play shooter --scenario two_gunners --show-truth`로 직접 해 보면서 정답이 납득되는지 봅니다.
2. 케이스를 다시 만듭니다.
3. 테스트를 돌립니다. 테스트 `test_following_the_truth_wins_every_scenario_but_doomed_ones`는 **정답대로 두면 모든 시나리오를 이겨야** 통과합니다. 일부러 질 수밖에 없는 시나리오를 넣었다면 이 테스트도 같이 고쳐야 합니다.

**설명문(`describe`)도 같이 고치세요.** 모델은 `describe()`가 만든 문장만 봅니다. 규칙을 바꿨는데 설명문에 그 정보가 없으면, 모델은 알 수 없는 것으로 채점받게 됩니다.

**정답이 이상하게 나올 때**

| 증상 | 원인과 해결 |
|---|---|
| 뻔한 행동이 정답에 없음 | 탐색 깊이(`HORIZON`)가 짧아 결과를 못 봄 → 늘리기. 단, 장면당 계산이 0.5초를 넘으면 테스트 실패 |
| 의미 없는 행동이 정답에 섞임 | 그 행동을 할 수 없는 상황인데 `legal()`에서 빼지 않음 |
| 이상한 행동이 정답 | `heuristic()`이 상황 가치를 잘못 매김 (탐색이 깊이 끝까지 승패를 못 볼 때 쓰는 점수) |
| Choice가 늘 채점 안 됨 | 모든 행동이 같은 결과 → 시나리오가 너무 쉽거나, 결과가 이미 정해진 상태 |

### 5-6. 실제 게임 붙이기

**훅을 만들 수 있으면 socket**: 게임이 로컬 소켓으로 JSON 한 줄 요청에 답하게 합니다. 형식은 [README의 "socket 게임 붙이기"](../README.md#socket-게임-붙이기)에 있고, [sims/service.py](../sims/service.py)가 그대로 예시입니다. 응답에 `truth`를 함께 내보내면 시뮬레이션처럼 매 장면이 자동 채점됩니다.

```sh
uv run python play.py new mygame --type realtime.action --interface socket
# games/mygame/questions.json, cases.json, interfaces/socket/interface.json의 launch.command 수정
uv run python play.py doctor mygame
```

**훅이 없으면 kbm**: 화면을 캡처해 해석하고 키보드·마우스로 입력합니다. 자세한 절차(앱 빌드, 권한, 화면 보정)는 [README의 "kbm 게임 붙이기"](../README.md#kbm-게임-붙이기)를 보세요.

```sh
uv run python play.py new mygame --type realtime.action --interface kbm
# interfaces/kbm/interface.json의 window.owner와 actions, observer.py의 화면 영역 수정
uv run python play.py observe mygame --save mygame.png
```

kbm 게임은 보통 정답이 없으므로 `cases.json`이나 `export-cases`로 만든 파일의 `truth`를 사람이 채워 채점합니다.

### 5-7. 새 시뮬레이션 게임 만들기

1. `sims/mygame.py`에 `Sim`을 상속한 클래스를 만듭니다. 기존 게임 하나(가장 짧은 것은 [sims/arena.py](../sims/arena.py), 처음부터 쓴 예는 [sims/racer.py](../sims/racer.py))를 복사해서 시작하세요.

   | 꼭 정할 것 | 뜻 |
   |---|---|
   | `ACTIONS` | 행동 이름 |
   | `HOLD` | Score·Noul 정답의 기준이 되는 "아무것도 안 하는" 행동 |
   | `HORIZON` | Choice 정답 탐색 깊이 |
   | `MAX_VITAL` | 최대 체력 (승리 시 남은 체력 가치 계산용) |
   | `SCENARIOS` | 시작 상황들 |
   | `QUESTION_IDS` | 질문 id. Noul이 없으면 `"incoming": None` |
   | 필드 | 게임 상태. **모두 바꿀 수 없는 값(숫자, 문자열, 튜플)이어야 함** (탐색이 상태를 복사·비교하기 때문) |
   | `snapshot()`, `describe()`, `step()`, `vital()`, `heuristic()`, `legal()` | 상태 출력, 설명문, 규칙, 체력, 상황 점수, 지금 할 수 있는 행동 |

2. [sims/__init__.py](../sims/__init__.py)의 `SIMS`에 등록합니다.
3. `config/game_types.json`에 맞는 타입이 없으면 추가합니다.
4. `games/mygame/`에 `game.json`, `questions.json`, `interfaces/socket/interface.json`을 만듭니다. 기존 게임 폴더를 복사해 이름만 바꾸면 됩니다. `launch.command` 끝의 게임 이름을 꼭 바꾸세요.
5. `uv run python -m sims cases mygame --output games/mygame/cases.json` (테스트는 케이스가 20개 이상이길 요구합니다)
6. [6장](#6-수정한-뒤-확인하기)대로 확인합니다.

---

## 6. 수정한 뒤 확인하기

무엇을 고쳤든 아래 순서로 확인하세요.

```sh
uv run python -m unittest discover -s tests       # 1) 설정·정답·채점 규칙 검사 (모두 OK여야 함)
uv run python play.py list                         # 2) 게임 목록에 ! 표시가 없는지
uv run python play.py doctor shooter               # 3) 연결, 설명문, 정답이 제대로 나오는지
uv run python play.py compare shooter --provider laya-mlx   # 4) 실제 모델로 채점이 도는지
```

테스트가 검사하는 것:

- 게임 폴더와 `sims/`의 게임이 1:1로 맞는지, 행동 이름과 질문 id가 일치하는지
- 모든 시나리오에서 정답이 질문 형식에 맞고 0.5초 안에 계산되는지
- 정답대로 두면 모든 시나리오를 이기는지
- `cases.json`이 올바른 형식이고 20개 이상인지
- policy 조건이 이 게임의 질문과 행동만 가리키는지

결과는 `results/games/<게임>/` 아래에 저장됩니다(git에 올라가지 않음).

---

## 7. 자주 보는 에러

| 에러 메시지 (일부) | 원인 | 해결 |
|---|---|---|
| `questions의 선택지와 interface.json actions가 다릅니다` | `criteria`와 `actions`의 행동 이름이 다름 | 두 목록을 같은 이름으로 맞춤 |
| `questions.json에 choice 형식의 action 질문이 필요합니다` | `action` 질문이 없거나 type이 choice가 아님 | `"action": {"type": "choice", ...}` 확인 |
| `score 질문 ...에는 2~10개의 단계(criteria)가 필요합니다` | score의 단계가 너무 적거나 많음 | `criteria` 개수 확인 |
| `truth의 ...는 questions.json에 없는 질문입니다` | 케이스 정답의 질문 id가 질문지에 없음 | 질문 id 철자 확인, 또는 케이스 다시 만들기 |
| `truth.action의 행동이 선택지에 없습니다` | 정답 행동이 선택지에 없음 (선택지 이름을 바꾼 뒤 흔함) | 케이스 다시 만들기 |
| `truth.danger는 0..2 사이 숫자여야 합니다` | 단계 범위를 벗어난 정답 | 단계 수와 정답 확인 |
| `조건은 '질문id >= 숫자' 형식이어야 합니다` | policy `when` 형식 오류 (예: `=>`) | `>=` 형식으로 수정 |
| `...는 이 게임의 score/noul 질문이 아닙니다` | policy가 없는 질문을 가리킴 (예: party에서 `incoming_attack`) | 이 게임의 질문 id 사용 |
| `allow는 이 게임 선택지 중 하나 이상의 목록이어야 합니다` | 없는 행동 이름 | 선택지 이름 확인 |
| `escalate 건너뜀: JEV_API_KEY가 없습니다` | Jev 키 없음 | `export JEV_API_KEY=...` 또는 무시(기록만 됨) |
| 채점 줄에 `danger:invalid` | 모델이 범위를 벗어난 값을 줌 | 모델 문제. 요약에 `무효 N`으로 집계됨 |
