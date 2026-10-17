# 게임 패키지

기본 목록에는 `party`(턴제 RPG), `racer`(레이싱), `moba`(MOBA)만 있습니다. 각 폴더는 아래 형식을 공유합니다.

```text
game.json                    이름, 타입, 정책, 버전별 인터페이스
questions.json               모델에 전달하는 choice / score / noul 질문
cases.json                   오프라인 비교용 상태와 선택적 truth
view.js                      콘솔 상태 도식 (실제 게임 화면과 구분)
interfaces/<name>/
  interface.json             connector, 실행/attach, 행동 매핑, 입력 설정
  observer.py                화면 해석 또는 상태 설명 변환 (선택)
```

`questions.json`의 `action.criteria` 키와 각 인터페이스 `actions` 키가 일치해야 합니다. `socket`/`mod`는 문자열 또는 구조화 명령으로, `kbm`은 입력 단계 목록으로 매핑합니다. `game.json`의 버전은 같은 인터페이스의 주소·표시 방식 등을 덮어쓸 수 있습니다.

세 내장 게임은 `sim-v1`(화면 없이 검증)과 `desktop-v1`(실제 게임 창)을 제공합니다. `app`은 게임을 실행하는 socket 프로필, `attach`는 이미 실행 중인 게임의 socket, `kbm`은 화면 관찰과 키마 입력입니다. 기본 CLI 버전은 sim-v1이고 강의 콘솔은 게임 창을 우선 선택합니다.

새 외부 게임 폴더를 만들거나 별도 디렉터리를 연결하는 방법은 [외부 게임 연결](../docs/external-games.md)에 있습니다. `_template`은 기본 목록에서 제외하며 `play.py new`와 `add-interface`가 사용합니다.
