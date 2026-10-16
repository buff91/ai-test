# Jev × Laya × Laya-MLX 게임 실험

> **실습·교육용 프로젝트입니다.** AI 모델이 게임 상황을 어떻게 판단하는지 비교하고, 게임 테스트 자동화의 구조(상태 관찰 → 판단 → 행동, 정답 채점)를 배우기 위해 만들었습니다. 실서비스나 상용 제품이 아니며, 측정한 정확도·지연 시간은 이 저장소의 작은 예제에서 얻은 값이라 모델의 일반적인 성능을 뜻하지 않습니다. 포함된 게임은 모두 직접 만든 시뮬레이션과 예제 빌드입니다. kbm(화면 캡처 + 키보드·마우스) 기능을 다른 회사의 게임에 쓰면 이용약관이나 부정행위 방지 정책에 어긋날 수 있으니, 직접 만들었거나 테스트 허가를 받은 게임에만 쓰세요.

같은 게임 상태와 행동 선택지를 [Jev](https://docs.typesafe.ai/introduction), [Laya](https://github.com/NandhaKishorM/laya), [Laya-MLX](https://github.com/mizorewww/laya-mlx)에 보내 결과를 비교하는 독립 프로젝트입니다. 기본 예제는 플레이어의 다음 행동을 `attack`, `dodge`, `reload`, `retreat`, `advance`, `wait` 중에서 고릅니다.

여러 게임을 다루려면 [`games/`](#게임-타입별-모델-매핑과-인터페이스)에 게임마다 폴더를 하나씩 만듭니다. 폴더 이름이 게임 이름입니다.

- **게임**(`game.json`, `questions.json`, `cases.json`): 어떤 게임이고 무엇을 판단하는지. 게임 **타입**(턴제/실시간 × 장르)이 어떤 모델(Jev·Laya·Laya-MLX)이 플레이하고 어떤 모델이 옆에서 비교만 할지 정합니다.
- **인터페이스**(`interfaces/<이름>/`): 그 게임과 상태·행동을 주고받는 방법. 게임과 따로 두므로, 같은 게임을 kbm으로 시작했다가 테스트 훅이 생기면 socket 인터페이스만 추가하면 됩니다. 인터페이스가 없는 게임은 비교 전용입니다.

모델을 부르고 기록·비교하는 부분은 타입이나 인터페이스와 상관없이 같습니다.

| 인터페이스 종류 (connector) | 언제 쓰나 | 게임 상태를 얻는 법 | 행동을 넣는 법 |
|---|---|---|---|
| `socket` | 게임 빌드에 테스트 훅이 있을 때 (예: 이 프로젝트의 `sims/` 게임 6종) | 게임이 소켓으로 상태(와 정답)를 보내 줌 | 소켓으로 행동 이름을 보냄 |
| `kbm` | 훅이 없는 남의 빌드라 화면과 입력 장치만 쓸 수 있을 때 | 게임 창을 캡처해 화면을 해석 | 실제 키보드·마우스 입력을 보냄 |
| (없음) | 실제 게임 없이 상태 예시로 모델만 비교할 때 | `cases.json`의 상태 설명 | 없음 (비교 전용) |

아래 [로컬 WebView 콘솔](#로컬-webview-콘솔)에서 `games/`의 모든 게임을 골라 연결하고 조작할 수 있습니다.

## 로컬 WebView 콘솔

```sh
uv sync
uv run python web_console.py                               # 게임을 화면에서 고름
uv run python web_console.py --game arena --interface app  # 시작하자마자 연결
```

브라우저에 대시보드가 열립니다. 열리지 않으면 터미널에 출력된 `http://127.0.0.1:포트/` 주소를 여세요. 대시보드는 로컬 컴퓨터의 `127.0.0.1`에만 열리며 외부 사이트에 게시되지 않습니다.

1. **게임 연결**: 게임과 인터페이스를 고르면 그 인터페이스의 실행 방법과, 사람이 먼저 해야 할 일(`interface.json`의 `manual`)이 표시됩니다. **연결**을 누르면 실행 방법에 따라 동작합니다.
   - socket + `launch`: 콘솔이 게임을 실행하고, **연결 끊기**를 누르면 종료합니다. sims 게임 6종과 빌드한 ArenaGame(`arena`의 `app` 인터페이스, `./build_game.sh` 필요)이 여기에 해당합니다.
   - socket + `socket`: 이미 떠 있는 게임에 붙기만 합니다. 게임은 사람이 먼저 실행합니다.
   - kbm: 열려 있는 게임 창에 KbmAgent가 붙습니다. 기본은 DRY-RUN(입력을 보내지 않고 기록만)이고, 화면 기록·손쉬운 사용 권한 상태가 표시됩니다.
2. **모델 실행**: live 모델(게임에 행동을 적용)과 shadow 모델(같은 장면을 판단해 기록만)을 고릅니다. 기본값은 게임 타입의 매핑입니다. **한 턴 실행**, **자동 플레이**(`Esc`로 멈춤), **분석만 보기**(게임은 그대로), **새 판**(시작 상황 선택), 선택지별 **직접 조작**을 쓸 수 있습니다. 게임에 `policy`가 있으면 **policy 적용**을 켤 수 있습니다.
   연결하면 게임 연결 아래에 세 카드가 계속 갱신됩니다. **게임 프로세스**(PID, 가동 시간, 죽으면 exit code), **응답**(마지막 응답이 몇 초 전인지, 왕복 시간, 실패 횟수), **실행 중인 빌드**(실행 파일 경로·빌드 시각·sha256과 "빌드 뒤에 바뀐 소스" 경고, 소스 실행이면 git 커밋과 커밋 안 된 수정)입니다.
3. **게임과 모델**: 게임 상태판(`games/<게임>/view.js`, 없으면 표), kbm이면 캡처한 화면, 그리고 지금 단계(게임 관찰 → 모델 판단 → 행동 전송)와 모델 호출 목록이 나옵니다. 호출을 펼치면 엔드포인트, 모델, 보낸 요청(API 키는 가림), 받은 응답, 토큰, 걸린 시간을 볼 수 있습니다. 판단 예산(`config/game_types.json`의 `latency_budget_ms`)을 넘긴 호출은 빨갛게 표시됩니다.
4. **판단 기록**: 매 턴 질문별(Choice·Score·Noul) 답과 게임이 계산한 정답, 모델별 누적 정답률이 쌓입니다. 행동을 적용한 턴은 `play.py run`과 같은 형식으로 `results/games/<게임>/<시각>/`에 기록되고, **기록 내려받기**로 JSON을 받을 수 있습니다.

Jev를 쓰려면 화면에 API 키를 입력하거나 실행 전에 `JEV_API_KEY`를 설정하세요. 키는 콘솔 프로세스의 메모리에만 두고 게임이나 기록 파일에는 넣지 않습니다. Jev 호출에는 API 요금이 발생할 수 있습니다.

콘솔은 `play.py run`과 같은 코드(`gamelab/runner.py`의 `tick`)로 관찰 → 판단 → 행동을 돌립니다. 그래서 새 게임을 추가할 때 콘솔을 고칠 필요가 없고, 게임마다 다른 실행 방법은 `interface.json`에만 적습니다. 기존 Tk 테스트 창은 `uv run python controller.py`로 열 수 있습니다.

## 준비

Apple Silicon Mac, macOS 14 이상, Python 3.11 이상과 [uv](https://docs.astral.sh/uv/)가 필요합니다. 이 폴더에서 실행하세요.

```sh
uv sync
```

두 Laya 실행 방식의 첫 실행은 모델 파일을 내려받기 때문에 시간이 더 걸릴 수 있습니다. 이후 모델은 로컬에서 실행됩니다. 일반 Laya는 PyTorch, Laya-MLX는 Apple Silicon용 MLX를 사용합니다.

Jev를 사용하려면 [TypeSafe 콘솔](https://console.typesafe.ai/)에서 발급한 키를 현재 터미널에 설정하세요. 키를 프로젝트 파일에 저장하지 마세요.

```sh
export JEV_API_KEY='발급받은_키'
```

## 실행

```sh
uv run python compare.py --validate
uv run python compare.py --provider laya-mlx --output results/laya-mlx.json
uv run python compare.py --provider local --output results/local.json
uv run python compare.py --provider jev --output results/jev.json
uv run python compare.py --provider all --output results/all.json
```

`JEV_API_KEY`가 없으면 `all` 실행에서 Jev는 건너뜁니다. `--provider jev`는 키가 없으면 바로 종료합니다. `local`은 두 Laya 실행 방식만 비교합니다. 비교 재현을 위해 둘 다 기본적으로 `multilingual` 체크포인트에 고정합니다. 자동 선택을 시험하려면 `--laya-model auto`를 지정하세요.

## 결과를 읽는 법

화면의 `✓`는 모델의 행동이 예제에 적힌 `reference_action`과 같다는 뜻입니다. 이 값은 **임시 기대 행동**이며 정답 데이터나 정확도 증명은 아닙니다. 출력 JSON에는 각 모델의 선택, 선택 확률, 신뢰도, 전체 확률 분포, 소요 시간이 들어갑니다.

시간은 호출 전체를 잽니다. Jev에는 네트워크 왕복이 포함되고 두 Laya 방식에는 로컬 모델 실행이 포함됩니다. 각 방식의 첫 호출은 모델 로딩·다운로드가 포함될 수 있으므로 성능 비교에서는 워밍업 후 여러 번 반복하세요. Jev는 입력 토큰 요금이 발생할 수 있으며 로컬 실행에도 컴퓨터 자원이 듭니다.

첫 일반 Laya 실행에서 `multilingual`은 임시 기대 행동 5개 중 3개와 일치했습니다. `auto`가 고른 다른 체크포인트는 2개와 일치했고 확률 보정 경고를 냈습니다. 예제가 너무 적고 기대 행동도 임시로 정했으므로 이 숫자로 모델 정확도를 판단할 수 없습니다. 실제 게임 상태와 사람이 검토한 행동을 더 모아 비교하세요.

Laya-MLX를 추가한 첫 비교에서 두 로컬 방식은 5개 사례 모두 같은 행동을 골랐습니다. 첫 호출을 제외한 4개 호출의 중간값은 일반 Laya **21.05ms**, Laya-MLX **10.75ms**였습니다. MLX의 기본 장치는 Apple GPU로 확인했습니다. 이는 이 Mac과 이 다섯 사례에서 관찰한 값이며, 폭넓은 성능 평가가 아닙니다. 원자료는 `results/local.json`에 저장됩니다.

## 공통 설정과 폴더 구조

```text
config/
  questions.json     # Choice·Score·Noul의 질문과 평가 기준
  providers.json     # Jev·Laya·Laya-MLX별 모델과 Jev 연결 설정
  game_types.json    # 게임 타입(턴제/실시간 × 장르)별 지연 예산과 live·shadow 모델 매핑
settings.py          # 공통 설정 로더
cases/               # 비교용 입력 사례
results/             # 비교 결과와 실행 기록
web/                 # 로컬 대시보드
play.py              # games/ 케이스 실행·점검·비교 명령 (모든 connector 공통)
games/               # 게임 하나 = 폴더 하나. 연결 방법은 각 게임의 interfaces/ 아래
gamelab/             # 연결 방식과 무관한 공통 코드 (케이스 로더, 플레이 루프, 모델 호출)
  game_types.py      # 게임 타입 설정 로더
  matrix.py          # 타입별 모델 비교 (play.py matrix)
  socket_game.py     # socket connector
kbm/                 # kbm connector (KbmAgent 실행, 화면 캡처, 입력, 화면 해석 도구)
kbm_agent.py         # KbmAgent.app의 시작 파일 (화면 캡처와 입력 전송만 담당)
```

`config/questions.json`의 질문을 세 모델에 동일하게 전달합니다.

| 질문 ID | 유형 | 정의 | 결과 |
|---|---|---|---|
| `action` | Choice | 현재 상태에서 실행할 다음 행동 하나 | 공격·회피·재장전·후퇴·전진·대기 |
| `danger` | Score | 다음 행동을 하기 전 현재 상태의 즉각적인 위험도 | 0=안전, 1=위협, 2=즉각적인 피격 또는 사망 위험. 중간 소수값도 가능 |
| `incoming_attack` | Noul | 이동·공격·회피 등 아무 행동도 하지 않으면 이번 턴 적 공격이 맞는가 | 참일 가능성 0~1. 공격 시점·거리·엄폐를 고려 |

실제 게임은 `action`만 적용합니다. Score·Noul은 분석용이며 행동을 덮어쓰지 않습니다. CLI·WebView에서 질문별 결과를 표시하고, JSON에는 `answers` 전체(Score 단계별 확률·신뢰도 등)를 보존합니다. 기존 결과 파일은 Choice만 실행했던 기록이며 새 결과와 질문 수·소요 시간이 다릅니다.

`config/providers.json`에서 Jev는 `model`, `endpoint`, `timeout`, 두 Laya는 각각 `model`을 설정합니다. 기본값은 Jev `jev-latest`, 두 Laya `multilingual`입니다. 이 설정을 CLI·WebView·Tk 창이 공통으로 읽습니다. CLI의 `--jev-model`, `--laya-model`, `--timeout`은 해당 실행에만 우선 적용되며 파일을 수정하지 않습니다. Laya의 `auto`는 모델 자동 선택입니다.

설정을 수정한 뒤 CLI를 다시 실행하거나 WebView/Tk 프로그램을 재시작하세요. API 키는 설정 파일에 넣지 않고 `JEV_API_KEY` 또는 화면 입력을 사용합니다.

`cases/game_actions.json`에서 `state`를 실제 게임 상태 설명으로 바꾸거나 사례를 추가하세요. 각 항목에 고유 `id`, `state`, 선택 사항인 `reference_action`을 넣습니다. 임시 기대 행동은 Choice만 비교하며 Score·Noul의 정답 평가는 아직 정의하지 않았습니다. 게임의 6가지 행동 이름은 게임 명령과 연결되어 있으므로 이름이나 개수를 바꾸려면 게임 및 화면 코드도 함께 수정해야 합니다.

```sh
uv run python compare.py --validate
uv run python compare.py --provider local --output results/local-typed-decisions.json
uv run python compare.py --provider jev --output results/jev-typed-decisions.json
```

## 다른 Mac에서 설치

Apple Silicon Mac, macOS 14 이상에서만 실행됩니다. Laya-MLX가 Apple GPU를 사용하고 게임 실행 파일도 macOS용으로 빌드하므로 Windows PC나 Intel Mac에서는 실행되지 않습니다.

폴더 전체는 수 GB이지만 대부분 내려받은 패키지와 모델입니다. git에 커밋된 파일만 옮기면 됩니다. 원격 저장소를 쓰지 않는다면 `git archive`로 압축하세요.

```sh
# 기존 Mac
git archive -o jev-laya-lab.zip HEAD

# 새 Mac: 압축을 푼 폴더에서
curl -LsSf https://astral.sh/uv/install.sh | sh   # uv가 없을 때만. 설치 후 터미널을 새로 여세요
uv sync
./build_game.sh
./build_kbm.sh        # kbm 게임을 쓸 때만
uv run python compare.py --validate
uv run python web_console.py
```

`uv sync`는 `uv.lock`에 고정된 버전으로 약 1GB의 패키지를 설치합니다. Laya를 처음 실행할 때 모델 파일 약 1.4GB를 내려받습니다. 게임 실행 파일은 macOS 보안 검사(Gatekeeper)에 막힐 수 있으므로 `dist/`를 복사하지 말고 새 Mac에서 `./build_game.sh`로 다시 빌드하세요. `KbmAgent.app`도 마찬가지로 새 Mac에서 `./build_kbm.sh`로 빌드하고 권한을 새로 줘야 합니다. Jev를 쓰려면 [준비](#준비)처럼 `JEV_API_KEY`를 설정하거나 WebView에 키를 입력합니다. 키를 압축 파일이나 프로젝트 파일에 넣지 마세요.

모델을 다시 내려받지 않으려면 기존 Mac의 `.hf-cache/` 폴더를 프로젝트 폴더에 함께 복사하고 실행 전에 지정하세요.

```sh
export HF_HOME="$PWD/.hf-cache"
```

`.venv/`, `.uv-cache/`, `build/`, `dist/`는 옮기지 마세요. 새 Mac에서 다시 만들어지며 `.venv/`는 복사하면 경로가 맞지 않아 동작하지 않습니다. `results/`의 이전 비교 결과는 필요할 때만 옮기세요.

## 게임: 타입별 모델 매핑과 인터페이스

게임 하나마다 `games/` 아래에 폴더를 하나 둡니다. 폴더 이름이 명령에서 쓰는 게임 이름입니다.

```text
games/arena/
  game.json              이름, 타입, 기본 인터페이스, policy   {"name", "type": "turn.tactics", "interface": "socket", "policy": {...}}
  questions.json         모델에게 보낼 Choice·Score·Noul 질문
  cases.json             비교용 상태와 질문별 정답(truth)
  interfaces/
    socket/interface.json    socket으로 연결하는 법 (실행 명령, 행동 → 게임 명령)
    kbm/interface.json       (필요하면) 키보드·마우스로 연결하는 법 + observer.py
```

```sh
uv run python play.py list      # 타입별 게임 목록, 인터페이스, 모델 매핑
uv run python play.py types     # 타입별 지연 예산과 live/shadow 모델
```

```text
턴제 · 전술  (turn.tactics)
  arena           socket         jev +laya-mlx      Arena · 포탑 돌파 (턴제 전술)
턴제 · 카드/덱빌딩  (turn.card)
  cards           socket         jev +laya-mlx      Cards · 덱빌딩 전투 (턴제 카드)
턴제 · RPG 전투  (turn.rpg)
  party           socket         jev +laya-mlx      Party · 파티 RPG 전투 (턴제 RPG)
실시간 · 액션/슈터  (realtime.action)
  shooter         socket         laya-mlx           Shooter · 복도 돌파 (실시간 액션)
실시간 · 탑다운 ARPG/MOBA  (realtime.topdown)
  swarm           socket         laya-mlx           Swarm · 몰려오는 무리 (실시간 탑다운)
실시간 · 레이싱  (realtime.racing)
  racer           socket         laya-mlx           Racer · 3차선 레이스 (실시간 레이싱)
```

`jev +laya-mlx`는 Jev가 실제로 플레이하고 Laya-MLX가 같은 장면을 함께 판단해 기록만 한다는 뜻입니다. 인터페이스가 여러 개인 게임은 기본값에 `*`가 붙습니다.

> 게임별 Choice·Score·Noul 정의, 연결 설정, 질문·케이스·policy를 고치는 방법은 [docs/games-guide.md](docs/games-guide.md)에 정리되어 있습니다.

여섯 게임은 모두 [`sims/`](sims/)의 시뮬레이션입니다. 빌드 없이 `play.py`가 `python -m sims serve <게임>`으로 직접 띄웁니다. 규칙을 게임이 전부 알고 있으므로, 매 상태마다 **질문별 정답(truth)** 을 계산해 상태와 함께 보냅니다. 모델에게는 설명문만 가고 정답은 채점에만 씁니다.

| 게임 | 장르 | 질문 | 시나리오 |
|---|---|---|---|
| `arena` | 포탑을 부수고 출구로 (`game_core.Arena`) | action · danger · incoming_attack | 5 |
| `cards` | 덱 순서와 적 의도가 보이는 카드 전투 | action · danger · incoming_attack | 5 |
| `party` | 성기사 + 자동 기사 vs 고블린·드래곤 | action · danger (Noul 없음) | 5 |
| `shooter` | 조준 예고하는 사수, 돌진병이 있는 복도 | action · danger · incoming_attack | 6 |
| `swarm` | 다가오는 적 무리, 스킬·물약·후퇴 공간 | action · danger · incoming_attack | 5 |
| `racer` | 속도 제한 코너와 장애물이 있는 3차선 | action · danger · crash_ahead | 5 |
| `_template` | `play.py new`와 `add-interface`가 복사하는 템플릿 | | |

```sh
uv run python -m sims play swarm --show-truth     # 터미널에서 직접 플레이 (정답을 보면서)
uv run python -m sims cases racer --output games/racer/cases.json   # 정답이 붙은 케이스 다시 만들기
```

실제 게임을 붙일 때는 그 게임 이름으로 새로 만드세요. `play.py new <게임 이름> --type <타입>`으로 시작해 `add-interface`로 연결 방법을 붙입니다. kbm 연결 예시는 [tests/fixtures/games/kbm_demo](tests/fixtures/games/kbm_demo)에 있습니다(테스트용, 창 이름과 화면 좌표는 임시값).

### Choice · Score · Noul: 채점과 제어

| 유형 | 정답(truth) 형식 | 채점 | 시뮬레이션의 정답 정의 |
|---|---|---|---|
| Choice (`action`) | 맞는 행동 목록 | 정답률 | 탐색(게임마다 7~10수 앞)으로 가치가 가장 높은 행동들. 모든 행동이 같으면 채점하지 않음 |
| Score (`danger`) | 단계 번호 0..N-1 | 평균 오차, 단계 일치율 | 가만히 있을 때 이번 턴에 피해/패배 = 2, 다음 턴에 = 1, 아니면 0 (카드·RPG·레이싱은 질문 문구에 맞춘 정의) |
| Noul (`incoming_attack`) | true/false 또는 0..1 | Brier 점수(0이 완벽, 늘 0.5면 0.25), 정답률 | 이번 턴에 가만히 있으면 체력이 줄어드는가 |

`run`은 매 step을 채점해 출력하고(`[action✓ danger 1.8/2 incoming_attack 0.90/hit]`), 끝나면 모델별·질문별 요약을 `summary.json`에 저장합니다. `compare`와 `matrix`는 `cases.json`의 truth로 같은 방식으로 채점합니다.

**policy**: `game.json`에 Score·Noul 답으로 행동을 제어하는 규칙을 둡니다.

```json
"policy": {
  "guards": [{"name": "피격 직전", "when": "incoming_attack >= 0.7", "allow": ["dodge", "retreat", "attack"]}],
  "escalate": {"when": "danger >= 1.5", "to": "jev"}
}
```

- `guards`: 조건이 맞는데 고른 행동이 `allow`에 없으면, `allow` 중 모델이 Choice에서 가장 높게 본 행동으로 바꿉니다.
- `escalate`: 조건이 맞으면 `to` 모델에게 다시 묻고 그 답을 씁니다. 실시간 게임에서 평소엔 빠른 모델, 위험할 때만 강한 모델을 쓰는 용도입니다.
- 기본은 **기록만** 합니다. 규칙이 발동했는지(`⚑피격 직전`)만 남기고 행동은 바꾸지 않으므로, Choice와 Score·Noul이 서로 어긋나는 장면을 찾는 데 씁니다. `run --policy`를 붙이면 실제로 적용하고, 요약에 행동 교체가 정답을 만들었는지(정답으로 바뀜/오답으로 바뀜)가 나옵니다.

```sh
uv run python play.py run shooter --provider laya-mlx --episodes 3          # 3판, policy는 기록만
uv run python play.py run shooter --provider laya-mlx --episodes 3 --policy # policy 적용
uv run python play.py report results/games/shooter/<기록 폴더>              # 기록 다시 요약
```

판이 끝나면 게임이 다음 시나리오로 리셋됩니다. `--episodes N`은 N판 뒤에 멈춥니다.

### 게임 타입과 모델 매핑

`config/game_types.json`에 타입마다 다음을 적습니다.

| 값 | 뜻 |
|---|---|
| `timing` | `turn`(게임이 판단을 기다림) 또는 `realtime`(기다리지 않음) |
| `latency_budget_ms` | 이 타입에서 판단 한 번에 허용할 시간. `matrix`가 추천할 때 기준으로 씀 |
| `providers.live` | `run`에서 실제로 행동을 적용할 모델 |
| `providers.shadow` | 같은 장면을 함께 판단해 기록만 하는 모델 목록 |
| `providers.models` | 선택. 모델 이름을 바꿀 때 (예: `{"laya-mlx": "english"}`) |

처음 매핑은 이렇게 잡았습니다. 측정 전 가정이므로 `matrix` 결과를 보고 고치세요.

| 타입 | 지연 예산 | live | shadow | 이유 |
|---|---|---|---|---|
| `turn.tactics` · `turn.card` · `turn.rpg` | 3~5초 | Jev | Laya-MLX | 게임이 기다려 주므로 판단 품질 우선 |
| `realtime.action` | 150ms | Laya-MLX | 없음 | 늦은 판단은 버려지므로 로컬 모델 |
| `realtime.topdown` | 300ms | Laya-MLX | 없음 | 일시정지가 되는 게임이면 Jev도 시도 가능 |
| `realtime.racing` | 100ms | Laya-MLX | 없음 | 지연 예산이 가장 빡빡함 |

실시간 타입에 shadow를 두지 않은 이유가 있습니다. shadow는 매 판단마다 호출되므로 Jev를 넣으면 판단 횟수만큼 API 요금이 듭니다. 품질 비교는 `matrix`로 따로 하세요.

게임 하나만 다르게 하려면 그 게임 `game.json`에 `"providers": {"live": "laya-mlx"}`처럼 적습니다. 적은 값만 타입 설정을 덮어씁니다. 실행 한 번만 바꾸려면 `run --provider <모델>`을 쓰고, shadow를 끄려면 `--no-shadow`를 붙입니다.

shadow는 live 모델과 동시에 돌고 행동을 늦추지 않습니다. 실시간 타입에서는 한 판단 주기 안에 답하지 못한 shadow를 `timeout`으로 기록합니다. 실행 중 출력에는 `| laya-mlx=dodge`처럼 shadow의 선택이 붙고, 기록에는 live와 같은 선택이었는지(`agrees`)가 남습니다.

### 타입별로 모델 비교하기 (matrix)

```sh
uv run python play.py matrix                          # 모든 케이스 × 모든 모델
uv run python play.py matrix --provider local         # Jev 없이 Laya 두 가지만
uv run python play.py matrix --types realtime         # 실시간 타입만 (turn.card처럼 타입 하나도 가능)
```

모든 게임의 `cases.json`을 모델마다 돌려 타입별로 정리합니다. 인터페이스가 있든 없든 모든 게임이 들어갑니다. 결과는 `results/games/matrix-<시각>.json`과 같은 이름의 `.md`에 저장됩니다.

```text
## 실시간 · 액션/슈터 (`realtime.action`, 지연 예산 150ms)

| 모델 | 일치 | Score 오차 | Noul Brier | 중간 지연 | 예산 안 | 오류 |
| laya-mlx | 11/31 | 0.81 | 0.663 | 35.4ms | 예 | 0 |

- 현재 live: laya-mlx / 측정 기준 추천: laya-mlx → 설정과 같음
```

- **일치**: 케이스 truth의 정답 행동과 같은 선택을 한 수입니다.
- **Score 오차 / Noul Brier**: 위험도 단계와의 평균 거리, 확률 예측 오차입니다. 둘 다 0이 완벽합니다.
- **중간 지연**: 모델마다 첫 호출(모델 로딩)을 뺀 중간값입니다.
- **추천**: 지연 예산 안에서 오류 없이 답한 모델 중 가장 많이 맞힌 모델입니다. 동률이면 더 빠른 모델입니다. 하나도 맞히지 못한 모델은 추천하지 않습니다.
- **판정**: `설정과 같음`, `설정 변경 검토`, `보류`(현재 live 모델을 이번에 측정하지 않음, 예: Jev 키 없이 실행), `추천 없음` 중 하나입니다.

`matrix`는 설정 파일을 바꾸지 않습니다. 매핑은 결과를 보고 사람이 `config/game_types.json`에서 고칩니다. 시뮬레이션 게임의 케이스는 `python -m sims cases`로 더 늘릴 수 있습니다(`--per-scenario`, `--seed`).

### 장르 늘리기

새 장르는 두 단계로 늘립니다.

1. `config/game_types.json`에 타입을 추가합니다. 예: `"turn.puzzle"`에 `timing`, `label`, `latency_budget_ms`, `providers`를 적음
2. 그 타입으로 게임을 만듭니다. 인터페이스 없이 만들면 `questions.json`과 `cases.json`만 채워 바로 비교할 수 있습니다. 정답을 스스로 계산하는 시뮬레이션으로 만들려면 [sims/base.py](sims/base.py)의 `Sim`을 상속해 규칙을 쓰고 `sims/__init__.py`의 `SIMS`에 등록합니다.

```sh
uv run python play.py new puzzle_sample --type turn.puzzle
uv run python play.py matrix --types turn.puzzle
```

나중에 그 장르의 실제 게임이 생기면 그 게임 이름으로 새로 만들고 인터페이스를 붙입니다.

### 인터페이스 고르기와 바꾸기

1. 게임 빌드에 상태를 내보내고 명령을 받는 **테스트 훅이 있거나 만들 수 있으면 `socket`**을 쓰세요. 화면을 해석하지 않으므로 정확하고, 게임이 판단을 기다려 주므로 타이밍 문제도 없습니다.
2. **훅이 없고 만들 수도 없으면 `kbm`**을 씁니다. 화면 해석 오차와 실시간 타이밍을 감수해야 합니다.
3. **아직 실제 게임에 연결하지 않고 모델만 비교하려면 인터페이스 없이** 둡니다. `run`은 할 수 없고 `compare`와 `matrix`만 됩니다.

회사 게임을 QA하는 경우라면 먼저 개발팀에 1번(테스트 훅)을 요청해 보세요.

```sh
uv run python play.py add-interface mygame kbm          # games/mygame/interfaces/kbm/ 생성
uv run python play.py add-interface mygame socket       # 훅이 생기면 socket도 추가
uv run python play.py run mygame                        # game.json의 "interface" (처음 추가한 것)
uv run python play.py run mygame --interface socket     # 이번 실행만 다른 인터페이스로
```

`add-interface`는 게임의 선택지(`questions.json`)로 `actions`의 이름을 채워 둡니다. 그 값을 키·마우스 입력이나 게임 명령으로 바꾸면 됩니다. 기본 인터페이스를 바꾸려면 `game.json`의 `"interface"`를 고치세요. 게임·질문·비교 케이스는 인터페이스를 바꿔도 그대로입니다.

### 공통 명령

| 명령 | 하는 일 |
|---|---|
| `play.py list` / `play.py types` | 타입별 게임 목록 / 타입별 모델 매핑 |
| `play.py new <게임> --type <타입> [--interface kbm\|socket]` | 새 게임 만들기. 인터페이스 없이 만들면 비교 전용 |
| `play.py add-interface <게임> kbm\|socket` | 기존 게임에 연결 방법 추가 |
| `play.py doctor <게임>` | 연결, 권한, 상태 읽기 점검. 행동은 보내지 않음 |
| `play.py observe <게임>` | 지금 상태를 한 번 읽어 출력 |
| `play.py run <게임>` | 플레이: 관찰 → 모델 판단 → (policy) → 행동. 정답이 있으면 매 step 채점. `--provider`, `--policy`, `--episodes` |
| `play.py report <기록 폴더>` | 기록의 질문별 채점과 policy 효과 요약 |
| `play.py export-cases <게임> <기록 폴더>` | 플레이 기록을 비교 케이스로 변환 |
| `play.py compare <게임> --provider all` | 게임 하나의 케이스를 여러 모델에 보내 비교 |
| `play.py matrix` | 모든 게임을 모델별로 돌려 타입별로 정리 |

`doctor`, `observe`, `run`은 `--interface`로 인터페이스를 고를 수 있습니다.

`run` 출력의 첫 글자는 이런 뜻입니다.

| 표시 | 뜻 |
|---|---|
| `✓` | 행동을 게임에 적용함 |
| `~` | 판단이 늦어 버림. 판단하는 동안 게임 상황이 바뀜 |
| `!` | 적용하지 않음. 모델 오류, 대상을 못 찾음, 게임 창이 맨 앞에 없음 등 |

기록은 `results/games/<id>/<시각>/`에 저장됩니다.

### 게임 폴더의 파일

| 파일 | 내용 |
|---|---|
| `game.json` | `name`, `type`, `interface`(기본 인터페이스), 선택 `providers`(이 게임만 모델 매핑 바꾸기), 선택 `policy` |
| `questions.json` | 모델에게 그대로 보낼 질문. `action`(choice)은 필수, score·noul은 선택 |
| `cases.json` | 비교용 `state`와 질문별 `truth` (모르는 질문은 빼거나 null) |
| `interfaces/<이름>/interface.json` | `connector`와 연결 설정. `actions`의 이름은 `questions.json` 선택지와 같아야 함 |
| `interfaces/<이름>/observer.py` | kbm은 필수(화면 → 상태), socket은 선택(`describe(state)`로 설명문 바꾸기) |

| `interface.json` | `socket` | `kbm` |
|---|---|---|
| 연결 | `launch.command`(게임 실행 파일) 또는 `socket`(이미 떠 있는 게임) | `window.owner`(게임 앱 이름) |
| `actions` | 선택지 → 게임에 보낼 명령 이름 | 선택지 → 키·마우스 입력 단계 |
| 타이밍 | `interval_ms` | `interval_ms`, `max_age_ms`, `decision_keys`, `pause_key`, `reflex_hz` |

자세한 형식은 [games/README.md](games/README.md)에, 게임별 설정과 수정 방법은 [docs/games-guide.md](docs/games-guide.md)에 있습니다.

### socket 게임 붙이기

게임 빌드가 로컬 Unix 소켓에서 JSON 한 줄 요청에 답하면 됩니다. [sims/service.py](sims/service.py)가 그대로 예시입니다.

| 요청 | 응답 |
|---|---|
| `{"op": "state"}` | `{"ok", "snapshot": 상태, "description": 설명문, "revision": 상태 번호}` + 선택 `"truth"`, `"done"` |
| `{"op": "step", "action": 명령, "expected_revision": 번호}` | `{"ok", "error"?, "snapshot", "revision"}`. 번호가 다르면 거절 |
| `{"op": "reset"}` (선택) | 다음 판을 시작하고 `state`와 같은 응답 |

회사 게임에 훅을 요청할 때 `truth`(게임이 아는 정답: 지금 가만히 있으면 맞는지, 위험 단계 등)도 함께 내보내 달라고 하면, 사람이 라벨을 달지 않아도 모든 판단이 채점됩니다.

`launch`를 쓰면 `play.py`가 게임을 띄우면서 `--socket <경로>`를 넘깁니다. 명령의 `"{python}"`은 지금 Python으로 바뀌고, 명령은 프로젝트 폴더에서 실행됩니다. 이미 떠 있는 게임에 붙으려면 `launch` 대신 `"socket": "<소켓 경로>"`를 적으세요.

```sh
uv run python play.py run arena --steps 20                      # 타입 매핑대로 Jev가 플레이 (JEV_API_KEY 필요)
uv run python play.py run arena --provider laya-mlx --steps 20  # 키 없이 로컬 모델로
```

### kbm 게임 붙이기

남이 만든 게임은 코드를 고칠 수 없어서 게임 상태를 직접 받을 수 없습니다. 그래서 사람처럼 **화면을 보고, 키보드·마우스로 조작**합니다.

```text
게임 창 캡처 → observer.py가 화면을 상태로 해석 (예: "체력 낮음, 적 가까움")
            → 모델이 행동 선택 (예: retreat)
            → interface.json의 매핑대로 입력 (예: S 키 0.45초 누르기)
```

캡처와 입력은 별도로 빌드한 **KbmAgent.app**이 맡고, `play.py`가 이 앱을 자동으로 띄워 명령을 보냅니다. macOS는 화면 캡처와 키보드·마우스 입력에 별도 권한을 요구합니다. 이 권한을 터미널 전체가 아니라 KbmAgent.app 하나에만 주려고 따로 빌드했습니다.

**1. 빌드하고 권한 주기**

```sh
./build_kbm.sh
```

**시스템 설정 → 개인정보 보호 및 보안**에서 `dist/KbmAgent.app`에 두 권한을 켜세요.

- **화면 및 시스템 오디오 녹음**: 게임 창 캡처
- **손쉬운 사용**: 키보드·마우스 입력

정식 개발자 서명 없이 빌드하므로, **다시 빌드하면 권한을 다시 줘야 할 수 있습니다**. `doctor`에서 권한이 `False`로 나오면 목록에서 KbmAgent를 지웠다가 다시 추가하세요. `--agent dev`를 붙이면 빌드 없이 실행할 수 있지만, 이때는 권한을 터미널 앱에 줘야 합니다.

**2. 게임을 만들고 화면 맞추기**

게임을 켜 둔 상태에서 실행하세요. `doctor`와 `observe`는 입력을 보내지 않습니다.

```sh
uv run python play.py new mygame --type realtime.action --interface kbm
# interfaces/kbm/interface.json의 window.owner를 게임 앱 이름(활동 상태 보기에 보이는 이름)으로 바꿈
uv run python play.py doctor mygame
uv run python play.py observe mygame --save mygame.png
```

저장한 PNG를 보면서 `observer.py`의 영역(창 기준 0~1 비율)과 색을 고치고, 해석 결과가 화면과 맞을 때까지 `observe`를 반복합니다. `observer.py`는 빌드된 KbmAgent 안에서 실행되므로 Python 표준 라이브러리, Pillow, `kbm.vision`만 import할 수 있습니다.

**3. 플레이하기**

```sh
uv run python play.py run mygame --provider laya-mlx --dry-run --steps 10   # 입력 없이 판단만 확인
uv run python play.py run mygame --provider laya-mlx --frames               # 실제 입력, 화면도 저장
```

먼저 `--dry-run`으로 판단이 그럴듯한지 확인한 다음 실제 입력으로 실행하세요.

**멈추는 법**: 마우스를 화면 **왼쪽 위 모서리**로 옮기면 즉시 모든 입력이 멈춥니다. 터미널에서 `Ctrl+C`를 눌러도 됩니다. 다른 창을 앞으로 가져오면 그동안은 입력하지 않습니다.

**실시간 게임에서 달라지는 점**: 턴제 게임은 모델 답을 기다려 주지만 실시간 게임은 그동안에도 진행됩니다. 그래서 kbm `interface.json`에서 몇 가지를 설정합니다.

- **늦은 판단 버리기**: 입력 직전에 화면을 다시 봅니다. `decision_keys`에 적은 상태 값이 판단 중에 바뀌었거나, 관찰 후 `max_age_ms`가 지났으면 입력하지 않습니다(`~`).
- **판단하는 동안 일시정지** (`pause_key`): 게임에 일시정지 키가 있으면 판단하는 동안 멈춰 턴제처럼 돌립니다. 쓰지 않으려면 `--no-pause`를 붙이세요.
- **빠른 규칙** (`observer.py`의 `reflex`): "경고가 보이면 바로 회피"처럼 모델을 기다리면 늦는 반응을 규칙으로 처리합니다. `--reflex`로 켜고 `reflex_hz`로 초당 횟수를 정합니다.

로컬 모델(Laya-MLX)은 첫 호출 이후 한 번에 약 20~40ms라 실시간에도 쓸 만합니다. Jev는 네트워크 왕복이 있어 판단 주기(`interval_ms`)를 넉넉히 잡으세요.

온라인 게임이나 안티치트가 있는 게임에서는 자동 입력이 약관 위반이나 제재 대상이 될 수 있습니다. 싱글플레이·오프라인이거나 테스트 권한이 있는 빌드에서만 쓰세요.

### 플레이 기록으로 비교 케이스 늘리기

shadow 모델은 플레이 중 같은 장면을 함께 판단하지만, 모든 모델을 같은 조건에서 공정하게 비교하려면 기록을 케이스로 바꿔 따로 돌리는 편이 낫습니다. `socket`과 `kbm` 모두 같습니다.

```sh
uv run python play.py export-cases mygame results/games/mygame/<기록 폴더>   # → games/mygame/cases.recorded.json
# 게임이 truth를 보냈다면 그대로 들어감. 없으면 cases.recorded.json의 truth를 사람이 채움
uv run python play.py compare mygame --cases games/mygame/cases.recorded.json --provider all
```

`--cases`를 빼면 `games/<id>/cases.json`을 씁니다. 결과는 `results/games/<id>/compare-<시각>.json`에 저장되고, `✓`는 정답 행동과 같다는 뜻입니다. `matrix`는 각 케이스의 `cases.json`만 읽으므로, 검토를 마친 기록 케이스를 타입별 비교에 넣으려면 `cases.json`에 옮겨 붙이세요.

### 다른 연결 방식 추가하기

HTTP API나 로그 파일처럼 다른 방식으로 상태를 얻는 게임은 connector를 하나 더 만들면 됩니다. `gamelab/` 아래에 `validate(game)`과 `connect(game, **options)`가 있는 모듈을 만들고, [gamelab/game_case.py](gamelab/game_case.py)의 `CONNECTORS`에 등록하세요. `connect`가 돌려주는 객체는 `request(op, **kwargs)`로 `info`, `observe`, `act`에 답해야 합니다. [gamelab/socket_game.py](gamelab/socket_game.py)가 가장 짧은 예시입니다.

## 출처

- [Jev API 명세](https://api.typesafe.ai/docs): `POST /v1/systemone`, `Choice` 질문과 응답 구조
- [Laya 설치 및 `Router.predict` 예제](https://github.com/NandhaKishorM/laya)
- [Laya-MLX 설치 및 `Router.predict` 예제](https://github.com/mizorewww/laya-mlx)

