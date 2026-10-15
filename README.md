# Jev × Laya × Laya-MLX 게임 실험

같은 게임 상태와 행동 선택지를 [Jev](https://docs.typesafe.ai/introduction), [Laya](https://github.com/NandhaKishorM/laya), [Laya-MLX](https://github.com/mizorewww/laya-mlx)에 보내 결과를 비교하는 독립 프로젝트입니다. 기본 예제는 플레이어의 다음 행동을 `attack`, `dodge`, `reload`, `retreat`, `advance`, `wait` 중에서 고릅니다.

## 게임 실행 파일 + 로컬 WebView

게임은 독립된 macOS 실행 파일 `dist/ArenaGame/ArenaGame`으로 빌드합니다. 로컬 WebView는 그 실행 파일을 별도 프로세스로 열어 게임 상태를 읽고 행동 명령을 보냅니다. WebView 위쪽에는 게임 상태를 실시간으로 그린 플레이 화면, 아래쪽에는 영상 예시처럼 매 턴의 모델 판단과 선택지별 확률이 쌓입니다. 원래 게임 창도 독립적으로 실행됩니다.

```sh
uv sync
./build_game.sh
uv run python web_console.py
```

로컬 대시보드와 게임 창이 자동으로 열립니다. 브라우저가 열리지 않으면 터미널에 출력된 `http://127.0.0.1:포트/` 주소를 여세요. WebView에서 TypeSafe Jev API 키를 입력하고 **모델 한 턴 실행** 또는 **자동 플레이**를 누르세요. Jev의 선택이 로컬 소켓을 통해 게임 실행 파일로 전달되어 플레이어 행동으로 적용됩니다. 키는 모델 실행 프로세스의 메모리에만 두고 게임 실행 파일이나 기록 파일에는 전달하지 않습니다. `Esc`로 자동 플레이를 멈춥니다.

**MLX 함께 분석**을 켜면 같은 게임 상태에서 로컬 모델의 판단도 표시됩니다. 실제 게임 행동은 선택한 주 모델의 판단만 적용합니다. WebView나 게임 창에서 직접 조작하거나 시작 상황을 바꿀 수 있습니다. 모델이 판단하는 동안 게임 상태가 바뀌면 이전 상태를 바탕으로 한 행동은 적용하지 않습니다. **기록 내려받기**는 모델 결정과 게임 전후 상태를 JSON으로 저장합니다. Jev 호출에는 API 요금이 발생할 수 있습니다.

게임만 열어 직접 조작하려면 `dist/ArenaGame/ArenaGame`을 실행하면 됩니다. 기존 별도 Tk 테스트 창은 `uv run python controller.py`로 열 수 있습니다. WebView는 로컬 컴퓨터의 `127.0.0.1`에만 열리며 외부 사이트에 게시되지 않습니다.

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

## 내 사례 넣기

`cases/game_actions.json`에서 `state`를 실제 게임 상태 설명으로 바꾸거나 사례를 추가하세요. 각 항목에 고유 `id`, `state`, 선택 사항인 `reference_action`을 넣습니다. 행동 목록과 질문은 `compare.py` 상단의 `OPTIONS`, `QUESTIONS`에서 바꿀 수 있습니다. 실제 게임 연동 단계에서는 게임 상태를 이 입력 형태로 만들고, 선택된 행동을 게임 코드가 실행하도록 연결하면 됩니다.

## 출처

- [Jev API 명세](https://api.typesafe.ai/docs): `POST /v1/systemone`, `Choice` 질문과 응답 구조
- [Laya 설치 및 `Router.predict` 예제](https://github.com/NandhaKishorM/laya)
- [Laya-MLX 설치 및 `Router.predict` 예제](https://github.com/mizorewww/laya-mlx)
