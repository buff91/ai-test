"""Case 2: player fixed at screen centre, enemies marked by red health bars, skill/potion icons.

Placeholder rects/colors: calibrate with `play.py observe topdown_sample --save topdown_sample.png`.
"""

HP_ORB = [0.05, 0.80, 0.08, 0.15]
HP_RGB = (200, 30, 30)
SKILL_Q = [0.40, 0.90, 0.04, 0.06]
POTION_1 = [0.30, 0.90, 0.04, 0.06]
READY_RGB = (230, 230, 230)
ENEMY_BAR_RGB = (255, 20, 20)
# Split the playfield into quadrants so the observer can count enemy groups and pick the nearest.
QUADRANTS = [[0.05, 0.05, 0.45, 0.40], [0.50, 0.05, 0.45, 0.40],
             [0.05, 0.45, 0.45, 0.35], [0.50, 0.45, 0.45, 0.35]]
RANGE = 0.22


def observe(frame):
    hp = frame.fill_ratio(HP_ORB, HP_RGB, axis="y")
    enemies = [p for p in (frame.find_color(q, ENEMY_BAR_RGB) for q in QUADRANTS) if p]
    nearest = min(enemies, key=lambda p: (p[0] - 0.5) ** 2 + (p[1] - 0.5) ** 2, default=None)
    in_range = bool(nearest) and ((nearest[0] - 0.5) ** 2 + (nearest[1] - 0.5) ** 2) ** 0.5 < RANGE
    safe = None
    if enemies:
        # Step away from the enemies' average position, clamped inside the window.
        ax = sum(p[0] for p in enemies) / len(enemies)
        ay = sum(p[1] for p in enemies) / len(enemies)
        safe = [round(min(0.9, max(0.1, 0.5 - (ax - 0.5))), 3), round(min(0.85, max(0.1, 0.5 - (ay - 0.5))), 3)]
    state = {
        "hp": round(hp, 2),
        "hp_band": "critical" if hp < 0.3 else "low" if hp < 0.6 else "high",
        "enemy_count": len(enemies),
        "enemy_in_range": in_range,
        "skill_ready": frame.ratio(SKILL_Q, READY_RGB) > 0.2,
        "potion_ready": frame.ratio(POTION_1, READY_RGB) > 0.2,
        "target": nearest,
        "safe_point": safe,
    }
    text = ["Player health is %s (%d%%)." % (state["hp_band"], hp * 100)]
    text.append("%d enemy groups are visible; the nearest is %s." % (len(enemies), "in attack range" if in_range else "out of range")
                if enemies else "No enemies are visible.")
    text.append("The skill is %s and a potion is %s." % ("ready" if state["skill_ready"] else "on cooldown",
                                                          "ready" if state["potion_ready"] else "not available"))
    return {"state": state, "description": " ".join(text)}
