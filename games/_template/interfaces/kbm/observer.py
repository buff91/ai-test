"""Screen -> state for this game. Calibrate rects and colors with `play.py observe <id> --save f.png`."""

HP_BAR = [0.02, 0.02, 0.25, 0.03]   # [x, y, w, h], window-relative 0..1
HP_RGB = (220, 60, 60)


def observe(frame):
    hp = frame.fill_ratio(HP_BAR, HP_RGB)
    state = {"hp": round(hp, 2)}
    return {"state": state, "description": "The player's health is %d%%." % (hp * 100)}
