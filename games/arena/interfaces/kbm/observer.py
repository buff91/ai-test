"""Read the ArenaGame window (game.py) from its pixels: where the player and turret stand, and whether it is charging.

Tiles are placed relative to two landmarks the game always draws, the cover box (tile 2) and the
green exit (tile 8), so a resized window still maps correctly. HP and ammo are only printed as
text in the HUD, so a screen-only observer does not know them; the description says so.
Colors are measured on a real capture of the built ArenaGame (screen colors differ from the hex
values in game.py by up to ~25). Re-check with `play.py observe arena --interface kbm --save f.png`.
"""

CANVAS = [0.02, 0.17, 0.96, 0.55]      # the playfield, below the title and above the HUD
LEFT = [0.02, 0.17, 0.46, 0.55]        # cover (tile 2) is always in the left half
RIGHT = [0.60, 0.17, 0.38, 0.42]       # the exit (tile 8) is at the right, rising above the floor (whose color is close)
PLAYER_RGB = (85, 145, 167)            # player body fill  (#3d93aa in game.py)
TURRET_RGB = (113, 73, 86)             # turret body fill  (#784756)
CHARGING_RGB = (244, 106, 120)         # turret outline, label and aim line when charging  (#fa6876)
COVER_RGB = (79, 102, 119)             # cover box fill  (#496779), tile 2
EXIT_RGB = (34, 58, 61)                # exit box fill  (#193b3e), tile 8; its thin outline vanishes when sampled
TOL = 20
EXIT_TOL = 10                          # the exit fill is close to the floor grid, so match it tightly
COVER_TILE, EXIT_TILE = 2, 8


def observe(frame):
    cover = frame.find_color(LEFT, COVER_RGB, tol=TOL)
    exit_ = frame.find_color(RIGHT, EXIT_RGB, tol=EXIT_TOL)
    player = frame.find_color(CANVAS, PLAYER_RGB, tol=TOL)
    turret = frame.find_color(CANVAS, TURRET_RGB, tol=TOL)
    # The charging outline is 2px: look in a small box around the turret, where sampling keeps it.
    charging = bool(turret) and frame.ratio(_around(turret, 0.07, 0.12), CHARGING_RGB, tol=TOL) > 0.01
    if not (cover and exit_) or exit_[0] <= cover[0]:
        state = {"player_tile": None, "turret_tile": None, "turret_alive": None, "charging": None, "in_cover": None,
                 "landmarks": False}
        return {"state": state, "description": "The arena is not visible (the game window may show a result screen)."}

    def tile(point):
        if not point:
            return None
        step = (exit_[0] - cover[0]) / (EXIT_TILE - COVER_TILE)
        return max(0, min(EXIT_TILE, round(COVER_TILE + (point[0] - cover[0]) / step)))

    state = {"player_tile": tile(player), "turret_tile": tile(turret), "turret_alive": turret is not None,
             "charging": charging, "landmarks": True}
    state["in_cover"] = state["player_tile"] == COVER_TILE
    parts = ["Arena seen on screen: a lane of tiles 0 to %d, cover at tile %d, the exit at tile %d." % (EXIT_TILE, COVER_TILE, EXIT_TILE)]
    if state["player_tile"] is None:
        parts.append("The player is not visible.")
    else:
        parts.append("The player stands at tile %d%s." % (state["player_tile"], " behind cover" if state["in_cover"] else ""))
    if state["turret_alive"]:
        parts.append("The turret is at tile %d, %d tiles away, and is %s." % (
            state["turret_tile"], abs(state["turret_tile"] - (state["player_tile"] or 0)),
            "charging a shot at the player" if state["charging"] else "not charging"))
    else:
        parts.append("The turret is destroyed; reach the exit.")
    parts.append("Health and ammo are not readable from the screen. Attack hits within 3 tiles; the turret threatens within 4. "
                 "Choose the single best next action.")
    return {"state": state, "description": " ".join(parts)}


def _around(point, half_w, half_h):
    x = min(max(point[0] - half_w, 0.0), 1 - 2 * half_w)
    y = min(max(point[1] - half_h, 0.0), 1 - 2 * half_h)
    return [x, y, 2 * half_w, 2 * half_h]
