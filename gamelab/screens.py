"""Visible teaching HUD protocol, shared by renderer and pixel-only KBM observers.

Every value is printed with a label and 12 binary lamps on the game window.
This is deliberately an inspectable teaching instrument, not an OCR model or a
socket shortcut. External games supply their own observer for their actual HUD.
"""
import json

WIDTH, HEIGHT = 1100, 800
ON = (103, 232, 249)
OFF = (24, 44, 60)
ANCHOR = (255, 179, 87)
# macOS color-managed captures shift cyan red by up to 42; OFF remains >140 away
# in green/blue. Keep anchors stricter and still require identity + checksum.
ON_TOLERANCE = 45
GAMES = ("party", "racer", "moba")
COMMON = ("game", "schema", "paused", "outcome", "turn")
FIELDS = {
    "party": ("paladin_hp", "knight_hp", "mana", "phoenix_feathers", "dragon_hp", "dragon_next", "goblin_count", "goblin_1", "goblin_2", "goblin_3", "goblin_4"),
    "racer": ("position", "lane", "speed", "boost", "integrity", "corner_count", "corner1_from", "corner1_to", "corner1_speed", "corner2_from", "corner2_to", "corner2_speed", "obstacle_count", "obstacle1_cell", "obstacle1_lane", "obstacle2_cell", "obstacle2_lane"),
    "moba": ("hero_hp", "lane", "position", "skill_cooldown", "gold", "allied_core", "enemy_core") + tuple(f"{field}_{i}" for field in ("allied_minions", "enemy_minions", "fronts", "allied_towers", "enemy_towers") for i in range(3)),
}
OUTCOMES = ("playing", "won", "lost", "draw")
RACER_LANES = ("left", "center", "right")
MOBA_LANES = ("top", "mid", "bot")
PHASES = (None, "claw", "charge", "strike")


def fields(game): return COMMON + FIELDS[game] + ("checksum",)


def instrument(index):
    """Label x, y, and twelve lamp centers in canvas coordinates."""
    x, y = 22 + (index % 4) * 272, 548 + (index // 4) * 25
    return x, y, [(x + 161 + bit * 6, y) for bit in range(12)]


def encode(game, snapshot):
    s = snapshot
    result = [GAMES.index(game) + 1, 1, int(s.get("paused", False)), OUTCOMES.index(s["outcome"]), s["turn"]]
    if game == "party":
        goblins = list(s.get("goblins", []))
        if len(goblins) > 4: raise ValueError("Teaching HUD supports up to four goblins.")
        result += [s[k] for k in ("paladin_hp", "knight_hp", "mana", "phoenix_feathers", "dragon_hp")]
        result += [PHASES.index(s.get("dragon_next")), len(goblins)] + (goblins + [0] * 4)[:4]
    elif game == "racer":
        result += [s["position"], RACER_LANES.index(s["lane"]), s["speed"], s["boost"], s["integrity"]]
        corners, obstacles = s.get("corners", []), s.get("obstacles", [])
        if len(corners) > 2 or len(obstacles) > 2:
            raise ValueError("Teaching HUD supports up to two corners and two obstacles.")
        result.append(len(corners))
        for i in range(2):
            c = corners[i] if i < len(corners) else {"from": 0, "to": 0, "max_speed": 0}
            result += [c["from"], c["to"], c["max_speed"]]
        result.append(len(obstacles))
        for i in range(2):
            o = obstacles[i] if i < len(obstacles) else {"cell": 0, "lane": "left"}
            result += [o["cell"], RACER_LANES.index(o["lane"])]
    else:
        result += [s["hero_hp"], MOBA_LANES.index(s["lane"]), s["position"], s["skill_cooldown"], s["gold"], s["allied_core"], s["enemy_core"]]
        for field in ("allied_minions", "enemy_minions", "fronts", "allied_towers", "enemy_towers"):
            result += list(s[field])
    if any(not 0 <= value < 4096 for value in result):
        raise ValueError("Teaching HUD value is outside its 12-bit range.")
    return result + [sum((i + 1) * v for i, v in enumerate(result)) % 4096]


def decode(game, values):
    if len(values) != len(fields(game)) or values[0:2] != [GAMES.index(game) + 1, 1]:
        raise ValueError("다른 게임 또는 지원하지 않는 HUD 버전입니다.")
    if values[-1] != sum((i + 1) * v for i, v in enumerate(values[:-1])) % 4096:
        raise ValueError("화면 HUD 체크섬이 다릅니다. 다음 프레임에서 다시 관찰하세요.")
    raw = dict(zip(fields(game), values))
    ranges = {"paused": 1, "outcome": 3}
    if game == "party": ranges.update(dragon_next=3, goblin_count=4)
    if game == "racer": ranges.update(lane=2, corner_count=2, obstacle_count=2, obstacle1_lane=2, obstacle2_lane=2)
    if game == "moba": ranges.update(lane=2)
    if any(not isinstance(v, int) or not 0 <= v < 4096 for v in values) or any(raw[k] > upper for k, upper in ranges.items()):
        raise ValueError("화면 계기의 값이 지원 범위를 벗어났습니다.")
    s = {"paused": bool(raw["paused"]), "outcome": OUTCOMES[raw["outcome"]], "turn": raw["turn"]}
    if game == "party":
        s.update({k: raw[k] for k in ("paladin_hp", "knight_hp", "mana", "phoenix_feathers", "dragon_hp")})
        s["dragon_next"] = PHASES[raw["dragon_next"]]
        s["goblins"] = [raw[f"goblin_{i+1}"] for i in range(raw["goblin_count"])]
    elif game == "racer":
        s.update({k: raw[k] for k in ("position", "speed", "boost", "integrity")})
        s["lane"] = RACER_LANES[raw["lane"]]
        s["corners"] = [{"from": raw[f"corner{i+1}_from"], "to": raw[f"corner{i+1}_to"], "max_speed": raw[f"corner{i+1}_speed"]} for i in range(raw["corner_count"])]
        s["obstacles"] = [{"cell": raw[f"obstacle{i+1}_cell"], "lane": RACER_LANES[raw[f"obstacle{i+1}_lane"]]} for i in range(raw["obstacle_count"])]
    else:
        s.update({k: raw[k] for k in ("hero_hp", "position", "skill_cooldown", "gold", "allied_core", "enemy_core")})
        s["lane"] = MOBA_LANES[raw["lane"]]
        for field in ("allied_minions", "enemy_minions", "fronts", "allied_towers", "enemy_towers"):
            s[field] = [raw[f"{field}_{i}"] for i in range(3)]
    return s


RULES = {
    "party": "Turn-based RPG. Paladin attack deals 18 to weakest enemy; knight auto-attacks for 10. aoe_skill deals 9 to every enemy for 10 mana. heal restores 30 HP to the most hurt living ally for 8 mana. guard halves paladin damage. use_item revives fallen knight with 40 HP. Each living goblin hits knight for 6; dragon cycles claw (10 to knight), charge (0), strike (45 to paladin). If knight is down, claw/goblins hit paladin. Mana regenerates 2. Paladin death loses; clearing enemies wins. danger asks if anyone falls this or next round when choosing attack.",
    "racer": "Three-lane race, finish at cell 40. accelerate/brake change speed by 1 (normal range 1-4); steer_left/right change one lane; boost consumes a charge and adds 2 speed up to 5; keep keeps settings. The race starts running immediately; the car moves continuously at speed cells per simulation second, regardless of input or model response time. Inputs change controls immediately and do not advance time. Crossing an obstacle in your lane or a corner above max_speed costs one integrity and drops speed to 1. Zero integrity loses. danger: 2 if keep crashes next beat, 1 if second beat, 0 otherwise. crash_ahead asks whether keep crashes next beat.",
    "moba": "Three-lane MOBA. Lane arrays are top/mid/bot. Home position 0; enemy core 6. The battle starts running immediately. advance/retreat set continuous movement at one cell per simulation second; wait stops movement. lane_left/right change lane (0.2 second cooldown). Inputs never advance time or wait for a turn. attack clears one enemy minion within one cell of front, with a 0.5 second cooldown; skill clears three, with a 3 second cooldown. Without enemy minions, position 5+ attacks tower for 14 (skill 24); at position 6 after tower falls it hits core. Enemy minions within one cell hit for 3 each; diving a live enemy tower at position 5+ without allied minions hits hero for 18. recall moves home, stops movement and heals 30, with a 4 second cooldown. Minions clash and push to attack towers/cores; two spawn per side per lane every fourth beat. Destroy enemy core; hero/allied core death loses. danger/incoming_attack refer to health loss if waiting one/two beats.",
}


def observe(frame, game):
    scale = frame.width / WIDTH
    title = frame.height - HEIGHT * scale
    if not 0 <= title <= 80 * scale:
        raise ValueError("Teaching game 창의 크기를 인식하지 못했습니다.")
    def sample(x, y, rgb, tolerance=30):
        return frame.ratio([(x-1) * scale / frame.width, (title+(y-1)*scale) / frame.height,
                            2*scale/frame.width, 2*scale/frame.height], rgb, tol=tolerance) > .65
    if not all(sample(x, 520, ANCHOR) for x in (24, 1076)):
        raise ValueError("게임의 화면 관측 계기를 찾지 못했습니다. 게임 창과 화면 기록 권한을 확인하세요.")
    values = []
    for index in range(len(fields(game))):
        _, _, centers = instrument(index)
        values.append(sum(1 << bit for bit, (x, y) in enumerate(centers) if sample(x, y, ON, ON_TOLERANCE)))
    state = decode(game, values)
    return {"state": state, "description": "Observed ONLY from visible labeled HUD pixels. " + RULES[game] + " Current state: " + json.dumps(state, ensure_ascii=False)}
