"""화면 관찰을 구현한 후 연결하세요. 캡처 보정 예시는 kbm/vision.py를 참고하세요."""

HP_BAR = [0.02, 0.02, 0.25, 0.03]   # [x, y, w, h], window-relative 0..1
HP_RGB = (220, 60, 60)


def observe(frame):
    # TODO: 게임의 실제 HUD 영역, 색상, 대상 좌표를 측정해 state를 반환하세요.
    # 예: hp = frame.fill_ratio(HP_BAR, HP_RGB)
    # return {"state": {"hp": hp}, "description": "HP: %d%%" % (hp * 100)}
    raise RuntimeError("KBM observer가 아직 구현되지 않았습니다. 화면을 보정하고 observe(frame)을 구현하세요.")
