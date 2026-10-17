# 외부 게임과 웹페이지 조작 예제

Decision Lab의 `관찰 → 모델 판단 → 행동 → 결과 확인` 흐름을 별도 게임 프로세스나 브라우저에도 연결할 수 있습니다. 아래 예제는 직접 만든 대상 또는 자동화 테스트가 허용된 대상에서 실행하세요.

| 대상 | 연결 방법 | 관찰 | 행동 |
|---|---|---|---|
| 테스트 훅이 있는 게임 | `socket` + `attach` | 게임이 보고하는 상태 | 게임이 제공하는 행동 호출 |
| 테스트 훅이 없는 게임 | `kbm` | 창 캡처를 `observer.py`로 해석 | 키 누르기·마우스 클릭 |
| DOM을 읽을 수 있는 웹페이지 | `socket` + Playwright 브리지 | DOM의 숫자·문구·버튼 상태 | 지정한 버튼 클릭 후 DOM 확인 |

`attach`는 실행 중인 외부 프로세스의 소켓에 연결하는 인터페이스 이름입니다. 새 통신 방식을 추가하는 기능은 아닙니다. 웹 예제도 기존 `socket` connector를 그대로 사용합니다.

## 1. 외부 게임 프로세스와 모드

현행 연결 계약은 [외부 게임 연결](external-games.md)에 정리되어 있습니다. 내장 예제 창을 별도 프로세스로 띄우고 attach를 연습할 수 있습니다.

```sh
uv run python -m gamelab.desktop_game --game party --socket /tmp/teaching-party.sock
# 다른 터미널
uv run python play.py doctor party --version desktop-v1 --interface attach
uv run python play.py run party --version desktop-v1 --interface attach --provider laya-mlx --no-shadow --steps 3
```

모드·플러그인에는 `mod` connector와 localhost TCP JSONL을 사용합니다. 실행 가능한 [모드 브리지 예제](../examples/external_mod/README.md)는 구조화된 행동과 revision 검증을 보여 줍니다.

## 2. 화면과 키보드·마우스

내장 세 게임의 `kbm` 인터페이스는 실제 게임 창의 숫자 계기를 읽습니다. 다른 게임은 해당 화면을 읽는 관찰기가 필요합니다. [키마 연동 절차](external-games.md#키보드마우스-연결)를 따라 창, 관찰기, 행동 매핑을 작성하세요.

```sh
./build_kbm.sh
uv run python play.py doctor party --version desktop-v1 --interface kbm
uv run python play.py observe party --version desktop-v1 --interface kbm --save party-screen.png
uv run python play.py run party --version desktop-v1 --interface kbm --provider laya-mlx --no-shadow --dry-run --steps 1
```

게임 창을 먼저 실행해야 하며 KbmAgent의 macOS 화면 기록·손쉬운 사용 권한이 필요합니다. 번들 TeachingLab 앱을 대상으로 할 때는 창 owner를 `TeachingLab`으로 변경합니다. 실제 키마 입력은 DRY-RUN을 끄고 대상 창이 앞에 있을 때 수행합니다.

## 3. 브라우저의 DOM을 읽고 버튼 클릭하기

[웹 예제](../examples/external_control/)는 `demo.html`의 현재 숫자를 목표 숫자에 맞추고 확정하는 작은 과제입니다. 모델은 `increase`, `decrease`, `confirm`, `wait` 중 하나를 고릅니다.

```text
play.py / 웹 콘솔
  → 기존 socket connector
  → browser_bridge.py의 Playwright 브리지
  → DOM에서 현재 숫자·목표·완료 상태 관찰
  → 정해진 버튼 클릭
  → DOM 변화와 결과 확인
```

브리지는 별도 Chromium을 시작합니다. 개인 브라우저의 로그인 세션이나 기존 탭에는 연결하지 않습니다. Playwright는 브라우저 창을 표시하거나 headless로 실행할 수 있으며, 설치에는 Python 패키지와 브라우저 바이너리가 모두 필요합니다. [Playwright 설치·실행 문서](https://playwright.dev/python/docs/library)

### 설치하고 모델 없이 실제 클릭 확인

프로젝트 루트에서 실행하세요. 웹 예제 의존성은 선택 그룹 `browser`에 있습니다.

```sh
uv sync --group browser
uv run --group browser python -m playwright install chromium
uv run --group browser python -m examples.external_control.smoke --headless
```

마지막 명령은 API 키나 모델 없이 브라우저를 실행해 실제 버튼 클릭과 상태 변화를 확인합니다. 창을 보면서 확인하려면 `--headless`를 빼세요. 이 검사는 연결·조작 경로를 확인하며 모델의 판단 품질을 평가하지는 않습니다.

Chromium을 프로젝트 안에 설치하려면 설치와 실행에 같은 `PLAYWRIGHT_BROWSERS_PATH`를 사용합니다. 이 작업 환경에서는 이미 `.venv/browsers`에 설치했으므로, 프로젝트 루트의 각 터미널에서 아래 설정 후 smoke나 콘솔 명령을 실행하면 됩니다.

```sh
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.venv/browsers"
# 새 환경에서 이 위치에 설치할 때만:
uv run --group browser python -m playwright install chromium
```

실행 결과는 `results/external-control/<실행 시각>/report.json`과 완료 화면 `complete.png`로 남습니다. smoke는 초기 상태, 실제 버튼 입력, 이전 revision 거절, 초기화, 완료 후 입력 거절을 확인합니다.

### CLI 또는 웹 콘솔에서 모델 연결

웹 예제의 게임 설정은 기본 세 게임과 분리된 `examples/external_control/games`에 있습니다. `--games-dir`는 전역 옵션이므로 `doctor`, `observe`, `run`보다 앞에 둡니다.

```sh
uv run --group browser python play.py --games-dir examples/external_control/games doctor web_counter --version demo-v1 --interface browser
uv run --group browser python play.py --games-dir examples/external_control/games observe web_counter --version demo-v1 --interface browser
uv run --group browser python play.py --games-dir examples/external_control/games run web_counter --version demo-v1 --interface browser --provider laya-mlx --no-shadow --steps 10
```

`browser`는 각 명령 실행 시 브리지를 시작하고 브라우저 창을 표시합니다. 화면 없이 실행하려면 `--interface headless`로 바꾸세요. 명령이 끝나면 그 명령이 시작한 브리지와 브라우저도 종료됩니다.

웹 콘솔에서는 다음처럼 예제 폴더를 지정합니다.

```sh
uv run --group browser python web_console.py --games-dir examples/external_control/games --game web_counter --interface browser
```

**웹 예제에는 `--dry-run`이 적용되지 않습니다.** 입력 없이 확인하려면 `doctor`, `observe` 또는 콘솔의 **분석만**을 사용하세요. `run`은 실제 버튼을 클릭합니다.

### 브리지를 별도로 실행하고 연결 유지

첫 번째 터미널에서 브리지를 실행합니다. 창을 숨기려면 이 명령에 `--headless`를 붙입니다.

```sh
uv run --group browser python -m examples.external_control.browser_bridge --socket /tmp/decision-lab-web-counter.sock
```

두 번째 터미널에서 `attach`로 연결하면 같은 페이지를 이어서 관찰하고 조작할 수 있습니다.

```sh
uv run --group browser python play.py --games-dir examples/external_control/games doctor web_counter --version demo-v1 --interface attach
uv run --group browser python play.py --games-dir examples/external_control/games observe web_counter --version demo-v1 --interface attach
uv run --group browser python play.py --games-dir examples/external_control/games run web_counter --version demo-v1 --interface attach --provider laya-mlx --no-shadow --steps 10
```

클라이언트 연결이 끝나도 별도로 실행한 브리지와 브라우저는 유지됩니다. 첫 번째 터미널의 `Ctrl+C`로 종료하세요.

### 다른 웹페이지에 적용할 부분

브리지의 `--url`은 데모와 같은 DOM 계약을 가진 페이지를 지정하는 옵션입니다. 임의의 웹사이트를 자동으로 이해하고 조작하는 기능은 아닙니다. 일반 페이지에 적용하려면 다음 항목을 함께 바꿉니다.

1. **관찰**: 브리지에서 필요한 DOM 값과 로딩·완료·오류 상태를 읽어 snapshot과 설명문으로 변환합니다.
2. **질문과 행동**: 게임 설정의 `questions.json`과 `interface.json`에 실제 과제의 판단 질문과 허용 행동을 정의합니다.
3. **대상 요소**: 브리지에서 각 행동을 해당 버튼·입력란의 locator에 연결합니다. 역할·라벨·테스트 ID처럼 의미가 분명한 기준을 사용하세요. [Playwright locator 문서](https://playwright.dev/python/docs/locators)
4. **결과 검증**: 클릭 호출이 끝난 것과 과제가 성공한 것을 구분하고, 예상한 DOM 상태로 바뀌었는지 확인합니다. 수동 조작으로 상태가 바뀌면 이전 관찰에 기반한 행동도 거절하도록 revision을 갱신합니다.

내장 데모는 `demo-v1`로 식별합니다. 외부 URL은 같은 페이지 구조를 사용해도 실제 배포 버전을 확인한 것으로 취급하지 않습니다. 캔버스로만 그린 웹 게임처럼 DOM에 상태가 없으면 별도 테스트 훅을 만들거나 화면을 해석하는 방식이 필요합니다.

관찰과 클릭 사이에 이미 바뀐 상태는 클릭 전에 거절하고, 클릭 대기 중 다른 입력이 끼어들면 클릭 후 revision 검사로 감지합니다. 일반 DOM 자동화가 관찰과 클릭을 하나의 원자적 작업으로 묶지는 못하므로, 입력 직전까지 조건을 보장해야 하는 대상에는 페이지나 게임 쪽 테스트 훅이 필요합니다.

새 게임·버전·인터페이스의 전체 구조는 [게임 가이드](games-guide.md)를 참고하세요.
