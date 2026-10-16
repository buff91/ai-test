"""Case 1: HUD bars + a red enemy marker + a red screen-edge warning.

Placeholder rects/colors: capture the real game with
`play.py observe action_sample --save action_sample.png` and adjust them.
"""

HP_BAR = [0.03, 0.92, 0.22, 0.025]
HP_RGB = (90, 220, 120)
AMMO_BAR = [0.75, 0.92, 0.22, 0.025]
AMMO_RGB = (240, 200, 80)
VIEW = [0.10, 0.10, 0.80, 0.75]
ENEMY_RGB = (230, 50, 50)
WARNING_EDGE = [0.0, 0.0, 0.02, 1.0]
WARNING_RGB = (255, 40, 40)


def observe(frame):
    hp = frame.fill_ratio(HP_BAR, HP_RGB)
    ammo = frame.fill_ratio(AMMO_BAR, AMMO_RGB)
    enemy = frame.find_color(VIEW, ENEMY_RGB)
    warning = frame.ratio(WARNING_EDGE, WARNING_RGB) > 0.3
    # Distance from screen centre is a cheap proxy for "enemy is close".
    close = bool(enemy) and abs(enemy[0] - 0.5) + abs(enemy[1] - 0.5) < 0.25
    state = {
        "hp": round(hp, 2),
        "hp_band": "critical" if hp < 0.3 else "low" if hp < 0.6 else "high",
        "ammo": round(ammo, 2),
        "ammo_empty": ammo < 0.05,
        "enemy_visible": enemy is not None,
        "enemy_close": close,
        "enemy": enemy,
        "warning": warning,
    }
    parts = ["The player's health is %s (%d%%)." % (state["hp_band"], hp * 100),
             "The weapon is empty." if state["ammo_empty"] else "The weapon has %d%% ammunition." % (ammo * 100)]
    if not enemy:
        parts.append("No enemy is visible and the path ahead looks clear.")
    else:
        parts.append("An enemy is visible %s." % ("close to the player" if close else "at a distance"))
    if warning:
        parts.append("A red warning shows an incoming attack aimed at the player.")
    return {"state": state, "description": " ".join(parts)}


def reflex(state):
    """Fast rule without a model: always dodge a telegraphed attack."""
    return "dodge" if state["warning"] else None
