"""自动战斗：看画面 → 判断局势 → WASD 走位 + 按技能键 + 一键拾取。

只看屏幕、模拟键盘鼠标（和 screen.py 一样），不读写游戏内存。

看：
- 怪：头顶纯红血条（实测 RGB 255,0,0，黑框）。血条越长越可能是精英 / BOSS。
- 自己：头顶血条是另一种红（228,47,46），比怪的长；镜头跟着英雄走，所以英雄大致在画面中间。
- 生命 / 法力：底部两条长条；压力值：顶部「敌人上限」条。按条里最右边那个有颜色的像素算比例（数字压在条中间不影响）。
- 卡住：按住方向键时画面应该在滚动（镜头跟人），用相位相关算画面位移，不动就是撞墙了，换个方向。

想（每 0.12 秒一次）：
- 撤退：血低（<30%），或血不多还被围 → 背离怪群走，能放保命 / 回血技能就放。
- 贴脸（近战流派）：去怪最密的那一堆中间站着打。
- 风筝（远程流派）：和最近的怪保持流派给的距离，太近后退并横移，太远靠近；走走停停留出普攻时间。
- 站桩（光环 / 召唤 / 反伤）：等怪过来，怪离太远才靠过去。
- 长远：压力值高 → 不管什么流派都先清怪（更激进、AOE 不等聚怪）；精英 / BOSS 在场才开爆发和变身；
  血线留余量，宁可早退一步；没怪时一键拾取，顺着上次看到怪的方向走。

动：
- 方向键按住 / 松开（只在变化时发），离开战斗或游戏不在前台立刻全部松开。
- 技能：照知识库里每个技能的放法（aoe_cluster / dash_to_cluster / heal_low / escape / buff_fight / charge / boss …），
  定向技能先把鼠标移到目标点再按键。
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field

import numpy as np

import screen as S

# 画面区域（相对比例，1920×1080 实测）
PLAY = (0.0, 0.09, 1.0, 0.70)              # 找怪的范围（去掉顶部条和底部技能栏）
HP_BAR = (0.348, 0.582, 0.938)             # x0, x1, y
MP_BAR = (0.348, 0.582, 0.964)
PRESSURE_BAR = (0.384, 0.615, 0.039)
PLAYER_DEFAULT = (0.5, 0.53)               # 英雄身体大致位置（没认出自己的血条时用）
PLAYER_BAR_TO_BODY = 0.085                 # 自己血条到身体的竖直距离
BAR_TO_BODY = 0.045                        # 怪血条到身体
PERSPECTIVE = 1.35                         # 俯视镜头竖向被压扁，竖向距离放大一点算
MOVE_KEYS = ("w", "a", "s", "d")
MANUAL_RULES = {"heal_low", "escape", "buff_fight", "boss", "charge", "manual"}


@dataclass
class Enemy:
    x: float          # 身体位置（画面比例）
    y: float
    w: float          # 血条长（画面宽比例）
    elite: bool


@dataclass
class View:
    enemies: list[Enemy] = field(default_factory=list)
    player: tuple[float, float] = PLAYER_DEFAULT
    player_seen: bool = False
    hp: float | None = None
    mp: float | None = None
    pressure: float | None = None


# ================================================================ 看

def _runs(row: np.ndarray):
    """一行布尔值里连续 True 的 (起点, 长度)。"""
    d = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1)
    return zip(starts, ends - starts)


def find_bars(a: np.ndarray, mask: np.ndarray, min_len: int, max_len: int, x0=0, y0=0):
    """在 mask 里找横向的细血条，返回 [(cx, cy, 长度)]（像素，整帧坐标）。"""
    bars: list[list[float]] = []   # [cx, cy, length, rows]
    for y in range(0, mask.shape[0], 2):
        row = mask[y]
        if not row.any():
            continue
        for s, n in _runs(row):
            if not (min_len <= n <= max_len):
                continue
            cx, cy = x0 + s + n / 2, y0 + y
            for b in bars:
                if abs(b[0] - cx) < max(8, n * 0.6) and 0 < cy - b[1] <= 12:
                    b[1] = cy
                    b[2] = max(b[2], n)
                    b[3] += 1
                    break
            else:
                bars.append([cx, cy, n, 1])
    # 血条只有几像素高；太厚的是别的红色东西（地上的光、技能特效）
    return [(b[0], b[1], b[2]) for b in bars if b[3] <= 8]


def bar_fill(a: np.ndarray, x0: float, x1: float, y: float, color: str, from_left: bool = False) -> float | None:
    """长条的填充比例：取条带里最右边那列有该颜色的位置。
    from_left：只数从左边起连续的一段（压力条右边有红色的「5%」字，不能取最右）。"""
    h, w = a.shape[:2]
    ys = slice(max(int(y * h) - 2, 0), int(y * h) + 3)
    seg = a[ys, int(x0 * w):int(x1 * w)].astype(np.int16)
    r, g, b = seg[..., 0], seg[..., 1], seg[..., 2]
    if color == "red":
        m = (r > 180) & (g < 110) & (b < 110) & (r - g > 90)
    else:  # blue
        m = (b > 170) & (r < 130) & (b - r > 80)
    has = m.any(axis=0)
    cols = np.flatnonzero(has)
    if cols.size < 2:
        return 0.0 if seg.size else None
    if from_left:
        end = cols[0]
        for c in cols:          # 允许 3 像素以内的断口
            if c - end > 3:
                break
            end = c
        return float(min(1.0, (end + 1) / seg.shape[1]))
    return float(min(1.0, (cols[-1] + 1) / seg.shape[1]))


def perceive(img) -> View:
    a = np.asarray(img.convert("RGB"))
    H, W = a.shape[:2]
    v = View()
    x0, y0, x1, y1 = (int(PLAY[0] * W), int(PLAY[1] * H), int(PLAY[2] * W), int(PLAY[3] * H))
    p = a[y0:y1, x0:x1].astype(np.int16)
    r, g, b = p[..., 0], p[..., 1], p[..., 2]
    monster = (r > 235) & (g < 30) & (b < 30)
    me = (r > 205) & (r < 245) & (g > 30) & (g < 70) & (b > 30) & (b < 70)
    # 自己：离画面中间最近的那条「自己色」长血条
    best = None
    for cx, cy, n in find_bars(a, me, int(0.04 * W), int(0.12 * W), x0, y0):
        d = abs(cx / W - 0.5) + abs(cy / H - 0.45)
        if d < 0.2 and (best is None or d < best[0]):
            best = (d, cx, cy)
    if best:
        v.player = (best[1] / W, best[2] / H + PLAYER_BAR_TO_BODY)
        v.player_seen = True
    for cx, cy, n in find_bars(a, monster, int(0.008 * W), int(0.2 * W), x0, y0):
        v.enemies.append(Enemy(cx / W, cy / H + BAR_TO_BODY, n / W, n / W > 0.06))
    v.hp = bar_fill(a, *HP_BAR, "red")
    v.mp = bar_fill(a, *MP_BAR, "blue")
    v.pressure = bar_fill(a, *PRESSURE_BAR, "red", from_left=True)
    return v


class Motion:
    """画面整体位移（相位相关），用来判断是不是撞墙卡住了。"""

    def __init__(self):
        self.prev: np.ndarray | None = None

    def shift(self, img) -> float:
        g = np.asarray(img.convert("L").resize((160, 90)), dtype=np.float32)[10:66]
        g = (g - g.mean()) * np.hanning(g.shape[0])[:, None] * np.hanning(g.shape[1])[None, :]
        prev, self.prev = self.prev, g
        if prev is None or prev.shape != g.shape:
            return 0.0
        f = np.fft.fft2(prev) * np.conj(np.fft.fft2(g))
        f /= np.abs(f) + 1e-6
        c = np.abs(np.fft.ifft2(f))
        dy, dx = np.unravel_index(np.argmax(c), c.shape)
        dy = dy - c.shape[0] if dy > c.shape[0] // 2 else dy
        dx = dx - c.shape[1] if dx > c.shape[1] // 2 else dx
        return math.hypot(dx, dy)


# ================================================================ 想

@dataclass
class SkillSlot:
    key: str
    name: str
    rule: str
    params: dict = field(default_factory=dict)
    cd: float = 6.0
    last: float = 0.0


@dataclass
class Plan:
    move: tuple[float, float] | None   # 方向（画面坐标，右 / 下为正），None = 站住
    intent: str
    why: str
    casts: list[tuple[SkillSlot, tuple[float, float] | None]] = field(default_factory=list)


def _vec(e: Enemy, p) -> tuple[float, float]:
    return e.x - p[0], (e.y - p[1]) * PERSPECTIVE * 9 / 16


def best_cluster(enemies: list[Enemy], radius=0.07) -> tuple[tuple[float, float], int]:
    """怪最密的一堆：返回（中心, 只数）。"""
    best, n_best = None, 0
    for e in enemies:
        near = [o for o in enemies if math.hypot(o.x - e.x, (o.y - e.y) * PERSPECTIVE * 9 / 16) < radius]
        n = len(near) + sum(o.elite for o in near)
        if n > n_best:
            n_best = n
            best = (sum(o.x for o in near) / len(near), sum(o.y for o in near) / len(near))
    return best, n_best


class Tactician:
    """每一跳根据看到的局势出一个计划。"""

    def __init__(self):
        self.engage = "kite"
        self.distance = 0.18
        self.melee = False
        self.slots: list[SkillSlot] = []
        self.hp_hist: list[tuple[float, float]] = []
        self.last_seen: tuple[float, float] | None = None   # 上次看到怪群的方向（相对英雄）
        self.last_seen_t = 0.0
        self.strafe = 1
        self.strafe_t = 0.0
        self.pause_until = 0.0
        self.move_until = 0.0

    def configure(self, engage: str, distance: float, melee: bool, slots: list[SkillSlot]):
        self.engage, self.distance, self.melee = engage, distance, melee
        old = {s.key: s.last for s in self.slots}
        for s in slots:
            s.last = old.get(s.key, 0.0)
        self.slots = slots

    def _hp_drop(self, now: float) -> float:
        """最近 2 秒掉了多少血（比例）。"""
        old = [h for t, h in self.hp_hist if now - t <= 2.0]
        return max(old) - self.hp_hist[-1][1] if old and self.hp_hist else 0.0

    def plan(self, v: View, now: float) -> Plan:
        p = v.player
        hp = v.hp if v.hp is not None else 1.0
        mp = v.mp if v.mp is not None else 1.0
        pressure = v.pressure or 0.0
        self.hp_hist = [h for h in self.hp_hist if now - h[0] < 4] + [(now, hp)]
        drop = self._hp_drop(now)
        es = v.enemies
        vecs = [_vec(e, p) for e in es]
        dists = [math.hypot(*d) for d in vecs]
        nearest = min(dists) if dists else 9.0
        close = sum(d < 0.06 for d in dists)
        elite = any(e.elite for e in es)
        cluster, cluster_n = best_cluster(es)
        if cluster:
            self.last_seen = (cluster[0] - p[0], cluster[1] - p[1])
            self.last_seen_t = now
        urgent = pressure > 0.6                      # 压力大：先清怪
        casts = self._casts(v, now, hp, mp, nearest, close, elite, cluster, cluster_n, urgent)

        def away() -> tuple[float, float]:
            fx = fy = 0.0
            for (dx, dy), d in zip(vecs, dists):
                k = 1 / max(d, 0.02) ** 2
                fx -= dx * k
                fy -= dy * k
            return fx, fy

        # ---- 1. 撤退
        if es and (hp < 0.3 or (hp < 0.5 and (close >= 5 or drop > 0.25))):
            return Plan(away(), "撤退", f"血 {hp:.0%}，先拉开回血", casts)
        # ---- 没怪：拾取，顺着上次看到怪的方向走
        if not es:
            if self.last_seen and now - self.last_seen_t < 6:
                return Plan(self.last_seen, "找怪", "朝刚才怪群的方向走", casts)
            return Plan(None, "待命", "附近没怪，顺手拾取", casts)
        target = cluster or (es[0].x, es[0].y)
        to_target = (target[0] - p[0], (target[1] - p[1]))
        dist_target = math.hypot(to_target[0], to_target[1] * PERSPECTIVE * 9 / 16)
        engage = "dive" if urgent and self.melee else self.engage
        want = self.distance * (0.7 if urgent else 1.0)
        # ---- 2. 贴脸
        if engage == "dive":
            if dist_target > 0.035:
                return Plan(to_target, "进攻", f"冲进最大的怪堆（{cluster_n} 只）", casts)
            return Plan(None, "输出", "站在怪堆中间打", casts)
        # ---- 3. 站桩
        if engage == "hold":
            if nearest > want * 2.2:
                return Plan(to_target, "靠近", "怪太远，靠过去", casts)
            if close >= 6 and drop > 0.12:
                return Plan(away(), "后撤", "被围了，退一步", casts)
            return Plan(None, "站桩", "等怪过来吃光环", casts)
        # ---- 4. 风筝：走走停停（停的时候普攻）
        if now - self.strafe_t > 2.5:
            self.strafe, self.strafe_t = -self.strafe, now
        if nearest < want * 0.7:
            fx, fy = away()
            n = math.hypot(fx, fy) or 1
            side = (-fy / n * 0.6 * self.strafe, fx / n * 0.6 * self.strafe)
            return Plan((fx / n + side[0], fy / n + side[1]), "风筝", "怪贴太近，边退边打", casts)
        if nearest > want * 1.4:
            return Plan(to_target, "靠近", "拉近到射程", casts)
        if now < self.move_until:
            fx, fy = away()
            n = math.hypot(fx, fy) or 1
            return Plan((-fy / n * self.strafe, fx / n * self.strafe), "走位", "横移躲攻击", casts)
        if now > self.pause_until:
            self.move_until = now + 0.3
            self.pause_until = now + 1.1
        return Plan(None, "输出", "距离正好，停下来普攻", casts)

    def _casts(self, v, now, hp, mp, nearest, close, elite, cluster, cluster_n, urgent):
        out = []
        p = v.player
        es = v.enemies
        for s in self.slots:
            if now - s.last < max(1.0, min(s.cd * 0.5, 8.0)):
                continue
            r, P = s.rule, s.params
            aim = None
            ok = False
            if r == "manual" or r == "passive":
                continue
            if mp < 0.12 and r not in ("heal_low", "escape"):
                continue
            if r == "heal_low":
                ok = hp < P.get("hp_below", 0.45)
            elif r == "escape":
                ok = hp < P.get("hp_below", 0.35) or close >= 7
                if ok and es:
                    cx = sum(e.x for e in es) / len(es)
                    cy = sum(e.y for e in es) / len(es)
                    aim = (2 * p[0] - cx, 2 * p[1] - cy)
            elif r == "buff_fight":
                ok = elite or len(es) >= (8 if not urgent else 4)
            elif r == "boss":
                ok = elite
                aim = next(((e.x, e.y) for e in es if e.elite), None)
            elif r in ("aoe_cluster", "dash_to_cluster", "charge"):
                need = P.get("min_enemies", 4)
                ok = cluster is not None and cluster_n >= (max(2, need // 2) if urgent else need)
                aim = cluster
            elif r in ("summon", "on_cd"):
                ok = nearest < 0.3
                aim = cluster
            if ok:
                out.append((s, aim))
        return out


# ================================================================ 动

class Controller:
    """后台线程：自动模式 + 战斗中才动手。"""

    def __init__(self, bot):
        self.bot = bot
        self.tac = Tactician()
        self.motion = Motion()
        self.held: set[str] = set()
        self.running = True
        self.active = False
        self.view: View | None = None
        self.last_plan: Plan | None = None
        self.casts_log: list[str] = []
        self.last_pick = 0.0
        self.stuck_since = 0.0
        self.dodge: tuple[tuple[float, float], float] | None = None
        self.last_dir = None
        self.lock = threading.RLock()
        threading.Thread(target=self._loop, daemon=True, name="combat").start()

    # ------------------------------------------------ 键
    def _set_keys(self, want: set[str]):
        with self.lock:
            for k in self.held - want:
                S.key_up(k)
            for k in want - self.held:
                S.key_down(k)
            self.held = set(want)

    def release(self):
        self._set_keys(set())

    @staticmethod
    def dir_keys(vec) -> set[str]:
        if vec is None:
            return set()
        x, y = vec
        if math.hypot(x, y) < 1e-6:
            return set()
        ang = math.degrees(math.atan2(-y, x)) % 360   # 0 = 右，90 = 上
        sector = int(((ang + 22.5) % 360) // 45)
        return [{"d"}, {"d", "w"}, {"w"}, {"w", "a"}, {"a"}, {"a", "s"}, {"s"}, {"s", "d"}][sector]

    # ------------------------------------------------ 循环
    def _loop(self):
        while self.running:
            try:
                if self.bot.combat_ready():
                    self.active = True
                    self.step()
                    time.sleep(0.08)
                else:
                    if self.active or self.held:
                        self.release()
                    self.active = False
                    time.sleep(0.25)
            except Exception as e:  # 别让一个异常把手按在键盘上
                self.release()
                self.bot.log(f"自动战斗出错：{type(e).__name__}: {e}")
                time.sleep(1)

    def step(self):
        hwnd = self.bot.hwnd or S.find_game()
        if not hwnd or not S.game_is_foreground(hwnd):
            self.release()
            return
        f = S.grab(hwnd)
        if f is None:
            self.release()
            return
        now = time.time()
        v = perceive(f.img)
        self.view = v
        self.tac.configure(*self.bot.combat_profile())
        plan = self.tac.plan(v, now)
        # 卡住：按着方向键超过 0.6 秒画面却不动 → 换个垂直方向走 0.7 秒
        moved = self.motion.shift(f.img)
        vec = plan.move
        if self.dodge and now < self.dodge[1]:
            vec = self.dodge[0]
        elif vec is not None:
            keys = self.dir_keys(vec)
            if keys == self.last_dir and moved < 0.6:
                if not self.stuck_since:
                    self.stuck_since = now
                elif now - self.stuck_since > 0.6:
                    side = 1 if int(now) % 2 else -1
                    self.dodge = ((-vec[1] * side, vec[0] * side), now + 0.7)
                    self.stuck_since = 0.0
                    vec = self.dodge[0]
            else:
                self.stuck_since = 0.0
            self.last_dir = keys
        self._set_keys(self.dir_keys(vec))
        for slot, aim in plan.casts:
            self.cast(f, slot, aim, now)
        if not v.enemies and now - self.last_pick > 3:
            self.last_pick = now
            with self.lock:
                S.key("c")
        self.last_plan = plan

    def cast(self, f, slot: SkillSlot, aim, now):
        with self.lock:
            if aim is not None:
                x = min(max(aim[0], 0.05), 0.8)       # 别把鼠标移到右边面板上
                y = min(max(aim[1], 0.12), 0.72)
                S.move_to(f, x, y)
                time.sleep(0.02)
            if slot.rule == "charge":
                S.key_down(slot.key)
                time.sleep(float(slot.params.get("hold", 1.0)))
                S.key_up(slot.key)
            else:
                S.key(slot.key)
        slot.last = now
        self.casts_log = (self.casts_log + [f"{slot.key.upper()} {slot.name}"])[-4:]

    def snapshot(self) -> dict | None:
        v, p = self.view, self.last_plan
        if not v or not self.active:
            return None
        return {"intent": p.intent if p else "", "why": p.why if p else "", "hp": v.hp, "mp": v.mp,
                "pressure": v.pressure, "enemies": len(v.enemies), "casts": self.casts_log[-3:]}
