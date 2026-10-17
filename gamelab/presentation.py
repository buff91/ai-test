"""Native canvas presentation for the three teaching games.

All geometry is decorative: action/state rules live in the simulations, and the
pixel observation instruments retain their separate, fixed layout.
"""
import math

INK = "#0b1420"
TEXT = "#eef4ff"
MUTED = "#9fb0c8"
CYAN = "#67e8f9"
GREEN = "#7be5ad"
RED = "#ff6b81"
AMBER = "#ffb357"
SCENARIOS = {
    "goblin_pack": "고블린 무리", "dragon_strike": "드래곤의 강타", "knight_low": "위기의 기사",
    "knight_down": "쓰러진 동료", "last_goblin": "마지막 고블린",
    "hairpin_fast": "급커브 진입", "long_straight": "마지막 직선", "obstacle_left": "왼쪽 장애물",
    "obstacle_right": "막힌 차선", "corner_exit": "코너 탈출",
    "wave_clear": "미니언 정리", "tower_push": "포탑 압박", "unsafe_dive": "위험한 진입",
    "defend_bot": "하단 방어", "core_finish": "코어 마무리",
}
ACTION_LABELS = {
    "attack": "공격", "aoe_skill": "빛의 파동", "heal": "치유", "guard": "방어", "use_item": "부활 깃털",
    "accelerate": "가속", "brake": "감속", "steer_left": "왼쪽 차선", "steer_right": "오른쪽 차선",
    "boost": "부스트", "keep": "유지", "advance": "전진", "retreat": "후퇴", "skill": "회전 베기",
    "recall": "귀환 · 회복", "lane_left": "상단 이동", "lane_right": "하단 이동", "wait": "대기",
    "__reset": "다음 시나리오", "__pause": "일시정지", "none": "입력 대기",
}
KEY_LABELS = {"up": "↑", "down": "↓", "left": "←", "right": "→", "space": "SPACE"}


def polygon(w, points, fill, outline="", width=1):
    return w.canvas.create_polygon(*points, fill=fill, outline=outline, width=width)


def oval(w, x, y, rx, ry, fill, outline="", width=1):
    return w.canvas.create_oval(x-rx, y-ry, x+rx, y+ry, fill=fill, outline=outline, width=width)


def line(w, points, color, width=1, **kwargs):
    return w.canvas.create_line(*points, fill=color, width=width, **kwargs)


def meter(w, x, y, width, value, maximum, color):
    w.box(x, y, width, 5, "#253346")
    if value > 0:
        w.box(x, y, width * max(0, min(1, value / maximum)), 5, color)


def stat(w, x, y, width, label, value, maximum, color):
    w.text(x, y, label, 11, MUTED)
    w.text(x+width, y, f"{value} / {maximum}", 11, TEXT, "e", True)
    meter(w, x, y+14, width, value, maximum, color)


def tree(w, x, y, size=1, color="#24483d"):
    w.box(x-3*size, y-5*size, 6*size, 24*size, "#514739")
    for level in range(3):
        top = y-(35-level*13)*size
        span = (17+level*5)*size
        polygon(w, [x, top, x-span, top+28*size, x+span, top+28*size], color)


def warrior(w, x, y, color=CYAN, size=1, alive=True, shield=False):
    """Helmet, cape, sword and a readable selection ring; y is ground level."""
    if not alive:
        oval(w, x, y, 28*size, 7*size, "#101d28")
        line(w, [x-16*size, y-8*size, x+15*size, y+3*size], "#647180", 9*size)
        w.text(x, y-30*size, "전투 불능", 10, RED, "center")
        return
    oval(w, x, y, 29*size, 9*size, "#142b32", color)
    polygon(w, [x-14*size,y-47*size,x-26*size,y-7*size,x+22*size,y-7*size,x+12*size,y-47*size], "#335466")
    line(w, [x-8*size,y-21*size,x-10*size,y-2*size], "#90a9b5", 8*size)
    line(w, [x+7*size,y-21*size,x+12*size,y-2*size], "#90a9b5", 8*size)
    polygon(w, [x-14*size,y-45*size,x+14*size,y-45*size,x+10*size,y-20*size,x-11*size,y-20*size], color, "#e6fbff")
    oval(w, x, y-57*size, 11*size, 13*size, "#afc5d0", "#e6fbff")
    w.box(x-11*size, y-59*size, 22*size, 5*size, "#1e3345")
    polygon(w, [x-3*size,y-69*size,x+1*size,y-79*size,x+7*size,y-66*size], color)
    line(w, [x+18*size,y-32*size,x+36*size,y-65*size], "#e4f4ef", 5*size)
    line(w, [x+19*size,y-39*size,x+31*size,y-32*size], AMBER, 3*size)
    if shield:
        polygon(w, [x-29*size,y-43*size,x-10*size,y-43*size,x-10*size,y-23*size,x-20*size,y-14*size,x-29*size,y-23*size], "#526e8b", AMBER, 2)
        line(w, [x-20*size,y-39*size,x-20*size,y-23*size], AMBER, 2)
        line(w, [x-25*size,y-33*size,x-15*size,y-33*size], AMBER, 2)


def goblin(w, x, y, hp):
    oval(w, x, y, 27, 8, "#18251f")
    line(w, [x-8,y-18,x-13,y-2], "#58854f", 8)
    line(w, [x+7,y-18,x+12,y-2], "#58854f", 8)
    polygon(w, [x-16,y-38,x+15,y-38,x+18,y-14,x-17,y-14], "#745c43", "#b39867")
    oval(w, x, y-49, 20, 18, "#779c58", "#b3c07b")
    polygon(w, [x-16,y-57,x-36,y-65,x-23,y-40], "#779c58")
    polygon(w, [x+16,y-57,x+36,y-65,x+23,y-40], "#779c58")
    for side in (-1, 1):
        oval(w, x+side*8, y-51, 3, 2, AMBER)
    line(w, [x+24,y-19,x+33,y-52], "#b8b1a0", 4)
    polygon(w, [x+27,y-57,x+43,y-57,x+45,y-44,x+30,y-47], "#8b9ea7")
    meter(w, x-30, y+13, 60, hp, 20, RED)
    w.text(x, y+27, f"고블린 · {hp}", 10, MUTED, "center")


def dragon(w, x, y, phase):
    oval(w, x, y+5, 106, 20, "#1b2528")
    polygon(w, [x-20,y-70,x-125,y-165,x-103,y-58,x-65,y-94,x-46,y-39], "#803951", "#c16a73", 2)
    polygon(w, [x+10,y-65,x+98,y-164,x+96,y-50,x+58,y-91,x+39,y-30], "#803951", "#c16a73", 2)
    polygon(w, [x-5,y-54,x-90,y+6,x-143,y-8,x-119,y+22,x-62,y+27,x+19,y+3], "#91455b", "#d2747e", 2)
    oval(w, x+7, y-40, 50, 61, "#a94b60", "#e38b87", 2)
    polygon(w, [x-4,y-79,x+27,y-76,x+34,y-9,x-1,y-1,x-19,y-38], "#d69881")
    polygon(w, [x+15,y-87,x+42,y-122,x+84,y-114,x+101,y-88,x+68,y-69,x+29,y-68], "#aa4f61", "#e38b87", 2)
    polygon(w, [x+45,y-116,x+43,y-145,x+62,y-120], "#e8d7ae")
    polygon(w, [x+69,y-116,x+83,y-139,x+82,y-110], "#e8d7ae")
    oval(w, x+72, y-102, 5, 4, AMBER)
    line(w, [x+71,y-82,x+91,y-86], "#4b233e", 3)
    for dx in (-26, 28):
        line(w, [x+dx,y-8,x+dx+4,y+14], "#b46171", 13)
        line(w, [x+dx-6,y+17,x+dx+16,y+17], "#e8d7ae", 4)
    if phase == "strike":
        for i in range(3):
            polygon(w, [x+97+i*7,y-92,x+123+i*6,y-87+i*7,x+103+i*5,y-74], AMBER if i % 2 else RED)


def draw_party(w, s):
    w.box(20, 88, 1060, 340, "#12272d")
    for i, color in enumerate(("#263f48", "#223a42", "#1d343b")):
        polygon(w, [285,178+i*39,390,115+i*42,480,184+i*31,590,120+i*34,710,188+i*32,850,118+i*35,1080,177+i*45,1080,360,285,360], color)
    for x, y, size in ((320,220,1.3),(560,208,1.1),(1015,205,1.6),(950,226,.85)):
        tree(w, x, y, size)
    oval(w, 682, 345, 369, 63, "#3c4841", "#63705b")
    oval(w, 682, 345, 330, 48, "#485248")
    for x,y in ((370,360),(577,382),(904,369),(984,337),(608,319)):
        polygon(w, [x,y,x+13,y-5,x+24,y+3,x+7,y+8], "#647168")
    w.box(20, 88, 258, 340, "#132032")
    w.box(20, 88, 4, 340, CYAN)
    w.text(42, 115, "원정대", 16, TEXT, bold=True)
    w.text(42, 143, "적을 모두 물리치고 동료를 지키세요", 10, MUTED)
    stat(w, 42, 182, 210, "성기사 · 직접 조작", s["paladin_hp"], 60, GREEN)
    stat(w, 42, 230, 210, "기사 · 매 턴 자동 공격", s["knight_hp"], 80, GREEN)
    stat(w, 42, 278, 210, "마나", s["mana"], 30, CYAN)
    w.text(42, 326, "부활 깃털", 11, MUTED)
    w.text(252, 326, str(s["phoenix_feathers"]), 17, AMBER, "e", True)
    w.text(42, 369, "행동 선택 → 동료 공격 → 적 공격", 10, MUTED)
    w.text(42, 395, "1회 입력마다 전투가 진행됩니다", 10, CYAN)
    warrior(w, 398, 342, CYAN, 1.12, s["paladin_hp"] > 0, True)
    warrior(w, 525, 314, "#e7be7e", .95, s["knight_hp"] > 0)
    w.text(398, 375, "성기사", 11, CYAN, "center", True)
    if s["knight_hp"] > 0: w.text(525, 345, "기사", 11, AMBER, "center")
    if s["dragon_hp"]:
        dragon(w, 811, 338, s["dragon_next"])
        w.text(818, 116, "잿빛 드래곤", 14, TEXT, "center", True)
        meter(w, 683, 134, 270, s["dragon_hp"], 90, RED)
        w.text(963, 136, str(s["dragon_hp"]), 11, RED)
        phase = {"claw":"다음 공격: 발톱 · 피해 10", "charge":"다음 행동: 힘 모으기", "strike":"다음 공격: 강타 · 피해 45"}[s["dragon_next"]]
        w.box(664, 382, 361, 28, "#4c2935" if s["dragon_next"] == "strike" else "#23373a")
        w.text(844, 396, phase, 12, AMBER, "center", True)
    else:
        count = len(s["goblins"])
        for i, hp in enumerate(s["goblins"]):
            goblin(w, 718 + i%2*167, 283 + i//2*100, hp)
        w.text(842, 119, f"고블린 무리 · {count}마리", 14, TEXT, "center", True)
    if w.effect_active and s["last_action"] in ("heal", "guard", "use_item", "aoe_skill"):
        color = GREEN if s["last_action"] in ("heal", "use_item") else CYAN
        x = 810 if s["last_action"] == "aoe_skill" else 398
        oval(w, x, 320, 50+w.effect_age*55, 25+w.effect_age*20, "", color, 2)
        for i in range(4):
            xx = x-40+i*27
            line(w, [xx,315-w.effect_age*50,xx,300-w.effect_age*50], color, 2)
    elif w.effect_active and s["last_action"] == "attack":
        line(w, [609,290,641,307,616,329], "#f9e5ac", 4)


def road_point(lane, distance):
    depth = max(0, min(1, 1-distance/14))
    width = 95 + depth*425
    return 625 + (lane-1)*width/3, 166+depth*235, width


def car(w, x, y, scale=1, color=CYAN):
    oval(w, x, y+4*scale, 29*scale, 38*scale, "#101a27")
    for side in (-1,1):
        w.box(x+side*23*scale-4*scale, y-22*scale, 8*scale, 17*scale, "#111a21")
        w.box(x+side*23*scale-4*scale, y+12*scale, 8*scale, 17*scale, "#111a21")
    polygon(w, [x-15*scale,y-37*scale,x+15*scale,y-37*scale,x+23*scale,y-19*scale,x+22*scale,y+30*scale,x-22*scale,y+30*scale,x-23*scale,y-19*scale], color, "#d0ffff", 2)
    polygon(w, [x-13*scale,y-12*scale,x+13*scale,y-12*scale,x+16*scale,y+8*scale,x-16*scale,y+8*scale], "#173f59", "#4d879c")
    w.box(x-3*scale, y-33*scale, 6*scale, 18*scale, "#e7ffff")
    line(w, [x-21*scale,y+23*scale,x+21*scale,y+23*scale], "#1b4b61", 5*scale)
    for side in (-1,1):
        line(w, [x+side*15*scale,y-28*scale,x+side*15*scale,y-22*scale], "#fff4bc", 4*scale)
        line(w, [x+side*16*scale,y+26*scale,x+side*16*scale,y+29*scale], RED, 4*scale)


def draw_racer(w, s):
    w.box(20, 88, 1060, 340, "#15323d")
    w.box(280, 88, 800, 85, "#23495c")
    oval(w, 843, 121, 22, 22, "#edc492")
    polygon(w, [280,173,354,122,405,149,493,105,556,157,620,130,713,171,828,133,930,175,1080,129,1080,220,280,220], "#365c66")
    polygon(w, [280,182,405,156,530,189,691,155,790,185,901,164,1080,190,1080,428,280,428], "#38564d")
    polygon(w, [577,166,673,166,885,428,365,428], "#28333f", "#b8b0a2", 3)
    for i in range(14):
        x1,y1,width1 = road_point(1,i)
        x2,y2,width2 = road_point(1,i+1)
        for side in (-1,1):
            polygon(w,[x1+side*width1/2,y1,x2+side*width2/2,y2,x2+side*(width2/2+7),y2,x1+side*(width1/2+10),y1], RED if i%2 else "#e2dace")
    for lane in (.5,1.5):
        for d in range(0,14,2):
            x1,y1,_ = road_point(lane,d+.3)
            x2,y2,_ = road_point(lane,d+1.3)
            line(w,[x1,y1,x2,y2], "#a5afb2", max(1,3-d/7))
    for corner in s["corners"]:
        start, end = corner["from"]-s["position"], corner["to"]-s["position"]
        if end >= 0 and start <= 12:
            _,y1,wd1 = road_point(1,max(0,start))
            _,y2,wd2 = road_point(1,min(13,end+1))
            polygon(w,[625-wd1/2,y1,625+wd1/2,y1,625+wd2/2,y2,625-wd2/2,y2], "#615039", AMBER)
            w.text(625, (y1+y2)/2, f"코너 · 속도 {corner['max_speed']} 이하", 10, "#ffe6bd", "center", True)
    for obstacle in s["obstacles"]:
        distance = obstacle["cell"]-s["position"]
        if 0 <= distance <= 12:
            lane = ("left","center","right").index(obstacle["lane"])
            x,y,wd = road_point(lane,distance)
            size = .4+wd/520*.7
            polygon(w,[x-26*size,y,x+26*size,y,x+20*size,y-23*size,x-20*size,y-23*size], "#bc5d45", AMBER, 2)
            line(w,[x-17*size,y-19*size,x-6*size,y-3*size], "#ffe6bd", 4*size)
            line(w,[x+2*size,y-19*size,x+13*size,y-3*size], "#ffe6bd", 4*size)
            w.text(x,y-34*size,f"{distance}칸",10,AMBER,"center")
    lane = ("left","center","right").index(s["lane"])
    x,_,_ = road_point(lane,1.4)
    if not s["paused"] and s["outcome"] == "playing":
        for side in (-1,1):
            for j in range(3):
                y = 205+((w.visual_time*70+j*67)%190)
                line(w,[625+side*(155+j*18),y,625+side*(161+j*20),y+19], "#5c7c70", 2)
    if s["speed"] > 4:
        polygon(w,[x-12,391,x,420,x+12,391], "#4199ed")
        polygon(w,[x-6,392,x,411,x+6,392], CYAN)
    car(w,x,369,.81)
    w.box(20, 88, 258, 340, "#132032")
    w.box(20, 88, 4, 340, AMBER)
    w.text(42, 115, "해안 순환로", 16, TEXT, bold=True)
    w.text(42, 143, "충돌을 피하고 40칸까지 완주하세요", 10, MUTED)
    oval(w, 147, 236, 65, 65, "#192b3e", "#355065", 3)
    for i in range(5):
        a = math.radians(150+i*60)
        line(w,[147+math.cos(a)*54,236+math.sin(a)*54,147+math.cos(a)*59,236+math.sin(a)*59], AMBER if i < s["speed"] else MUTED, 3)
    w.text(147, 225, str(s["speed"]), 43, AMBER, "center", True)
    w.text(147, 269, "칸 / 박자", 11, MUTED, "center")
    w.text(42, 324, "차체 상태", 11, MUTED)
    for i in range(3):
        w.box(143+i*36, 316, 28, 16, GREEN if i < s["integrity"] else "#2d3a49")
    w.text(42, 362, "부스트", 11, MUTED)
    w.text(252, 362, f"{s['boost']}회", 16, CYAN, "e", True)
    stat(w,42,393,210,"주행 거리",min(40,s["position"]),40,CYAN)
    w.box(891, 202, 169, 199, "#142634", "#35505b")
    w.text(907,224,"전방 안내",12,TEXT,bold=True)
    upcoming = [c for c in s["corners"] if c["to"] >= s["position"] and c["from"]-s["position"] <= 12]
    if upcoming:
        c = upcoming[0]
        w.text(907,255,"급커브",17,AMBER,bold=True)
        w.text(907,284,f"속도 {c['max_speed']} 이하",12,TEXT)
        w.text(907,311,f"{max(0,c['from']-s['position'])}칸 앞 · 감속 준비",11,MUTED)
    else:
        w.text(907,255,"직선 구간",17,GREEN,bold=True)
        w.text(907,284,"차선을 확인하세요",11,TEXT)
    w.text(907,350,"완주까지",10,MUTED)
    w.text(1042,376,f"{max(0,40-s['position'])}칸",24,TEXT,"e",True)


def tower(w, x, y, hp, color):
    if not hp:
        polygon(w,[x-16,y+8,x-8,y-8,x+4,y-4,x+12,y-12,x+19,y+9],"#566161")
        return
    oval(w,x,y+8,20,9,"#273d38")
    polygon(w,[x-13,y-34,x+13,y-34,x+17,y+7,x-17,y+7],"#829295","#b9c9c7")
    w.box(x-19,y-37,38,10,color)
    for dx in (-15,0,15): w.box(x+dx-4,y-44,8,13,color)
    oval(w,x,y-17,5,7,"#173440")
    meter(w,x-23,y-54,46,hp,60,color)
    w.text(x,y+25,str(hp),9,color,"center")


def minions(w, x, y, count, color, direction):
    for j in range(min(count,6)):
        dx,dy = (j%3)*11*direction,(j//3)*15
        oval(w,x+dx,y+dy,5,6,color,"#d8e6ce")
        line(w,[x+dx+direction*5,y+dy,x+dx+direction*9,y+dy-7],"#bacacc",2)


def draw_moba(w, s):
    w.box(20,88,1060,340,"#1b3832")
    polygon(w,[585,88,633,88,805,428,750,428],"#245466")
    for x,y in ((322,122),(401,113),(566,122),(774,111),(941,116),(1025,128),(349,222),(508,222),(885,219),(1017,218),(330,420),(471,419),(594,420),(896,419),(1028,418)):
        tree(w,x,y,.57,"#2a5140")
    hero_lane = ("top","mid","bot").index(s["lane"])
    lane_names = ("상단", "중단", "하단")
    for i in range(3):
        y = 164+i*98
        line(w,[316,y,1027,y],"#344d43",42)
        line(w,[316,y,1027,y],"#78816a" if i == hero_lane else "#566750",27)
        line(w,[316,y+21,1027,y+21],"#29483e",2)
        w.text(299,y,lane_names[i],10,CYAN if i == hero_lane else MUTED,"center",i == hero_lane)
        for pos in range(7):
            x = 350+pos*106
            oval(w,x,y,3,3,"#bac0a0" if i == hero_lane else "#7d8f74")
        for pos,hp,color in ((1,s["allied_towers"][i],GREEN),(5,s["enemy_towers"][i],RED)):
            tower(w,350+pos*106,y-1,hp,color)
        front = 350+s["fronts"][i]*106
        minions(w,front-24,y+1,s["allied_minions"][i],GREEN,-1)
        minions(w,front+24,y+1,s["enemy_minions"][i],RED,1)
        w.text(front,y+38,f"아군 {s['allied_minions'][i]}  :  적 {s['enemy_minions'][i]}",9,MUTED,"center")
        if i == hero_lane:
            hx = 350+s["position"]*106
            warrior(w,hx,y+13,CYAN,.52,s["hero_hp"] > 0,True)
            meter(w,hx-20,y-36,40,s["hero_hp"],100,CYAN)
            if s["position"] >= 5 and s["enemy_towers"][i] and not s["allied_minions"][i]:
                line(w,[880,y-28,hx,y-10],RED,2,dash=(4,3))
            if w.effect_active and s["last_action"] in ("skill","attack","recall"):
                color = GREEN if s["last_action"] == "recall" else CYAN
                oval(w,hx,y,25+w.effect_age*25,15+w.effect_age*15,"",color,2)
    for x,color in ((326,GREEN),(1047,RED)):
        line(w,[x,160,x,360],color,2)
        polygon(w,[x,239,x+13,258,x,279,x-13,258],color,"#e5eece",2)
    w.box(20,88,258,340,"#132032")
    w.box(20,88,4,340,GREEN)
    w.text(42,115,"삼중 전선",16,TEXT,bold=True)
    w.text(42,143,"적 포탑을 밀고 코어를 파괴하세요",10,MUTED)
    stat(w,42,181,210,"영웅 체력",s["hero_hp"],100,CYAN)
    w.text(42,226,"현재 전선",11,MUTED)
    w.text(252,226,f"{lane_names[hero_lane]} · 위치 {s['position']}",12,TEXT,"e",True)
    w.text(42,263,"회전 베기",11,MUTED)
    w.text(252,263,"준비 완료" if not s["skill_cooldown"] else f"{s['skill_cooldown']}박자 후",12,GREEN if not s["skill_cooldown"] else AMBER,"e",True)
    w.text(42,298,"골드",11,MUTED)
    w.text(252,298,str(s["gold"]),14,AMBER,"e",True)
    stat(w,42,340,210,"아군 코어",s["allied_core"],100,GREEN)
    stat(w,42,390,210,"적 코어",s["enemy_core"],100,RED)
