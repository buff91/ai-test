# CLAUDE.md

## 이 프로젝트의 성격

**실습·교육용 프로젝트입니다.** AI 모델(Jev, Laya, Laya-MLX)이 게임 상황을 어떻게 판단하는지 비교하고, 게임 테스트 자동화의 구조(관찰 → 판단 → 행동, 정답 채점)를 배우는 것이 목적입니다. 작업할 때 다음을 지켜 주세요.

- 상용 제품처럼 다루지 않습니다. 결과나 문서에 "모델 성능이 이렇다"고 일반화하지 말고, 이 저장소의 작은 예제에서 얻은 값이라고 적습니다.
- 게임은 직접 만든 시뮬레이션(`sims/`)과 예제 빌드(ArenaGame)만 씁니다. kbm(화면 캡처 + 키보드·마우스)을 다른 회사의 게임에 붙이는 작업은 이용약관·부정행위 방지 정책 문제가 있으니, 직접 만들었거나 테스트 허가를 받은 게임인지 먼저 확인합니다.
- 설명은 배우는 사람이 읽는다고 생각하고, 왜 그렇게 하는지까지 적습니다.

사용자와는 한국어로 대화합니다. 문서도 한국어로 씁니다.

## 구조 한눈에

- `games/<게임>/`: 게임 하나 = 폴더 하나. `game.json`(타입·policy), `questions.json`(Choice·Score·Noul 질문), `cases.json`(상태와 정답), `interfaces/<이름>/interface.json`(연결 방법), `view.js`(콘솔에 그릴 상태판, 선택)
- `gamelab/`: 연결 방식과 무관한 공통 코드. `runner.tick()`이 한 번의 관찰 → 판단 → 행동이고, CLI(`play.py run`)와 WebView 콘솔이 같이 씁니다
- `gamelab/socket_game.py`, `kbm/`: 두 가지 connector. socket은 게임의 테스트 훅, kbm은 화면 캡처 + 입력
- `web_console.py` + `web/`: 모든 게임용 로컬 WebView 콘솔. 게임 프로세스 상태, 실행 중인 빌드, 모델 호출(요청·응답)을 보여 줍니다
- `sims/`: 정답을 직접 계산하는 시뮬레이션 게임 6종
- 자세한 설명: `README.md`, `games/README.md`(파일 형식), `docs/games-guide.md`(무엇을 고치려면 어디를)

게임마다 다른 실행 방법은 `interface.json`에만 적고, 콘솔이나 러너에 게임별 코드를 넣지 않습니다.

## 새 PC에서 시작하기

Apple Silicon Mac, macOS 14 이상, Python 3.11 이상, [uv](https://docs.astral.sh/uv/)가 필요합니다.

```sh
uv sync
uv run python -m unittest          # 전체 테스트
./build_game.sh                    # ArenaGame 실행 파일 (arena의 app·kbm 인터페이스용)
./build_kbm.sh                     # KbmAgent.app (kbm을 쓸 때만)
uv run python web_console.py       # WebView 콘솔
```

`dist/`, `build/`, `.venv/`, `.hf-cache/`, `results/`는 git에 없으니 새 PC에서 다시 만듭니다. Jev를 쓰려면 `JEV_API_KEY`를 환경 변수나 콘솔 화면으로 넣고, 파일에는 절대 저장하지 않습니다. kbm은 macOS 권한이 필요합니다. 시스템 설정 → 개인정보 보호 및 보안에서 `KbmAgent.app`(또는 개발용으로 쓰는 터미널·에디터)에 화면 기록 권한을, 실제 입력까지 하려면 손쉬운 사용 권한도 줍니다.

## 이어서 할 일 (2026-10-16 기준)

- **arena kbm 인터페이스 실제 화면 검증**: 실제 ArenaGame 창 캡처 한 장(`tests/fixtures/arena_kbm/opening.png`)으로만 확인했습니다. 시작 상황 5개와 몇 가지 행동 뒤의 화면을 찍어, observer가 읽은 값을 게임이 소켓으로 알려 주는 상태(`player_x`, `enemy_x`, `enemy_windup`)와 비교해야 합니다. 특히 포탑 조준 중일 때의 빨간색(`CHARGING_RGB`)은 실제 화면에서 아직 재지 않았습니다. 게임 창이 화면에 보여야 캡처됩니다(최소화·다른 데스크톱 불가, 가려지는 것은 괜찮음).
- **kbm 콘솔 미리보기와 실제 키 입력**: 콘솔에서 kbm 연결 시 캡처 화면 미리보기와, DRY-RUN을 끈 실제 입력(ArenaGame 단축키 A·Space·R·←·→·W)은 권한 문제로 확인하지 못했습니다.
- **실제 Jev 호출**: 콘솔은 가짜 모델로만 테스트했습니다. 실제 응답에서 토큰(`usage`)·모델 정보가 어떤 형식으로 오는지 보고, 필요하면 콘솔 표시를 맞춥니다.
- **기존 Tk 창 정리**: `controller.py`(Tk 테스트 창)는 WebView 콘솔과 기능이 겹칩니다. 지울지 결정이 필요합니다.

## Git 반영 규칙

Git에는 프로젝트 소스·설정·테스트·실행 예제와 설치·실행·구조·개발 문서만 반영합니다. 강의 슬라이드·교안·워크북 및 이를 만드는 스크립트·이미지·폰트는 Git 대상이 아닙니다. `docs/teaching/`, `docs/archive/`, `output/`, `tmp/pdfs/`는 로컬 보관 경로이며 강제 추가하지 않습니다. 파일 형식만으로 프로젝트 자산이나 테스트 fixture를 제외하지 않습니다.

강의자료와 기존 미커밋 작업은 삭제하지 않습니다. 커밋 전 `git diff --cached --name-only`로 반영 경로를 확인하고, 이번 작업에 필요한 변경만 명시적으로 스테이징합니다. 프로젝트 문서는 강의 제작물 없이도 설치·실행·개발할 수 있도록 작성합니다.
