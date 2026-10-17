# 어디를 수정하나요?

| 바꾸려는 것 | 파일 |
|---|---|
| 기본 게임 목록 | `games/`의 게임 폴더 (현재 party/racer/moba) |
| 질문·행동 선택지 | 해당 게임 `questions.json` |
| 기준 사례·정답 | 해당 게임 `cases.json` |
| 모델 기본값·지연 예산 | `config/game_types.json`, 게임 `game.json`의 providers |
| 정책·버전별 프로필 | 게임 `game.json` |
| 실행 명령·창·키/마우스 매핑·TCP endpoint | `interfaces/<name>/interface.json` |
| 외부 게임 화면 해석 | 인터페이스 `observer.py` |
| 내장 게임 규칙 | `sims/party.py`, `racer.py`, `moba.py` |
| 게임 창·실시간 시계 | `gamelab/desktop_game.py` |
| 연결 전환 중 게임 창 유지 | `gamelab/game_windows.py` (`POST /api/game-window`) |
| 설정 검증·전송 | `gamelab/configuration.py`, `protocol.py` |
| 외부 모드 호스트 계약 | `gamelab/mod_bridge.py` |
| 모델 API·로컬 추론 | `gamelab/decide.py` |
| 공통 실행·기록 | `gamelab/runner.py` |
| 콘솔 세션·상태·비교 | `gamelab/console.py` |
| 로컬 HTTP API | `gamelab/console_http.py` |
| 콘솔 UI | `web/` |

새 게임을 붙일 때는 공통 러너·콘솔을 수정하지 않습니다. 별도 games 디렉터리를 `play.py --games-dir`와 `web_console.py --games-dir`로 읽힐 수 있습니다. 자세한 계약과 실행 예제는 [외부 게임 연동](external-games.md)을 참고하세요.
