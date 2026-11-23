"""地下城竞技场 军师：从选英雄开始一路辅助，按 F8 切成自动玩。

- 选英雄：正在看哪个英雄，就讲它的技能怎么用、3 个常见流派（攻略库 knowledge/，秒出）
- 选地图：讲每个地图 / 难度是什么、适合谁
- 进游戏：从 3 个流派里挑一个，之后三选一、商店、出装都按这个流派推荐，并按前期 / 中期 / 后期做长线规划
- 自动玩：选卡、买书买装备买锻造器、开下一波；战斗里自己走位（WASD）、放技能、拾取（combat.py）

只看屏幕、模拟鼠标键盘，不改游戏文件、不读写游戏内存。
快捷键：F8 推荐 / 自动　F7 爽 / 稳　F6 问 AI（看当前画面）　F9 AI 再配 3 个流派　F10 隐藏面板
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import difflib
import json
import os
import queue
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

import combat
import screen as S
from brain import CHALLENGE_DANGER, DANGER_RANK, STYLE_NAME, TABLES, Brain, base_skill, fuzzy
from kb import KB

ROOT = Path(__file__).resolve().parent
LOG = ROOT / "data" / "assistant.log"
CONFIG = ROOT / "assistant.json"
DEFAULTS = {
    "playstyle": "爽",              # 爽 / 稳
    "auto": False,                  # 启动时是否直接自动玩
    "only_carnival": True,          # 自动玩只在狂欢模式里动手（狂欢模式不上排行榜）；推荐模式不受限
    "auto_move": True,              # 自动玩时自己走位、放技能、拾取（关掉就只交给游戏自带的自动战斗）
    "hero_key": "q",                # 英雄自带技能的按键（游戏默认：Q 格是英雄技能）
    "skill_keys": ["e", "r", "t"],  # 学来的三个主动技能
    "build_pick_seconds": 20,       # 自动玩时开局等你挑流派的秒数，过了就用默认（上次选的 / 第 1 个）
    "buy_equipment": True,
    "buy_books": True,
    "buy_forging": True,
    "gold_reserve": 300,
    "max_forging_per_wave": 10,
    "auto_retry": True,
    "ai_plan": True,                # 允许 F9 让 AI 再配流派
    "ai_model": "claude-sonnet-5-5",
    "ai_effort": "low",
    "tick_seconds": 0.6,
    "hotkeys": {"auto": "f8", "playstyle": "f7", "ask": "f6", "replan": "f9", "hide": "f10"},
}

# ---------------------------------------------------------------- 界面位置（相对游戏画面的比例，1920×1080 下实测）
BANNER = (0.33, 0.12, 0.67, 0.19)
CARD_X = (0.278, 0.5, 0.721)
CARD_HALF = 0.095
NAME_Y, RARITY_Y, DESC_Y = (0.425, 0.485), (0.49, 0.53), (0.53, 0.68)
CARD_CLICK_Y = 0.6
WAVE_BOX = (0.42, 0.06, 0.58, 0.11)
START_BOX = (0.42, 0.135, 0.58, 0.19)
START_BTN = (0.5, 0.161)
TUTORIAL_BOX, TUTORIAL_BTN = (0.4, 0.78, 0.6, 0.84), (0.5, 0.81)
GOLD_BOX = (0.915, 0.02, 0.975, 0.075)
CHAT_BOX = (0.02, 0.5, 0.32, 0.76)
RESULT_BOX = (0.3, 0.38, 0.7, 0.53)
HERO_BOX = (0.26, 0.95, 0.335, 0.99)          # 战斗中头像下面的名字（白字描边，要高阈值黑白化才认得）
HERO_BOX_TIGHT = (0.27, 0.955, 0.325, 0.985)
SELECT_TITLE_BOX = (0.02, 0.02, 0.2, 0.08)     # 选英雄界面左上「选择英雄」
SELECT_1P_BOX = (0.77, 0.11, 0.99, 0.2)        # 右上队伍栏「你·房主 幻想法师 已选择」
SELECT_BROWSE_BOX = (0.77, 0.54, 0.99, 0.62)   # 右下「当前浏览」的英雄名
BAG = [(x, y) for y in (0.617, 0.696) for x in (0.882, 0.927, 0.971)]
EQUIP_SLOTS = [(x, y) for y in (0.858, 0.940) for x in (0.617, 0.661, 0.705)]
SKILL_SLOTS = [(0.435, 0.873), (0.495, 0.873), (0.556, 0.873)]   # E R T（Q 是英雄技能，在 0.375）
# 三选一卡片右上角的刷新按钮（点一下只换那一张）
REFRESH_DX, REFRESH_Y = 0.083, 0.233
# 「请替换一个技能」界面：技能栏变大上移；点要换掉的格子，或点「放弃」
REPLACE_BOX = (0.36, 0.6, 0.62, 0.67)
REPLACE_SLOTS = [(0.389, 0.79), (0.469, 0.79), (0.549, 0.79)]
GIVEUP_BTN = (0.29, 0.63)
NEUTRAL = (0.62, 0.42)
SHOP_TABS_BOX = (0.04, 0.09, 0.2, 0.14)
TAB_EQUIP, TAB_SKILL = (0.079, 0.117), (0.157, 0.117)
# 装备页：7 列固定顺序网格，第 1 行是锻造道具，之后按装备表顺序。格子小字认不出，按位置买，买前悬停读说明框确认。
GRID_X = (0.0742, 0.1305, 0.1841, 0.2404, 0.2953, 0.3516, 0.408)
GRID_Y = (0.2015, 0.315, 0.4274, 0.5372)
SHOP_INDEX = {"属性锻造器": 1, "黄金属性锻造器": 2,
              **{n: 6 + i for i, n in enumerate(
                  ["无尽之刃", "狂战斧", "饮血剑", "破败王者之刃", "穿甲弓", "灭世之帽", "溶解之杖", "痛苦面具", "日炎斗篷",
                   "狂徒铠甲", "振奋铠甲", "圣杖之证", "巫师手套", "女神之泪", "钢铁之心", "力量之戒", "迅捷之戒",
                   "生命之戒", "圣者的项链", "忍者刀鞘", "抗魔斗篷"], start=1)}}
BOOK_PRICE = {"A": 30, "B": 10, "C": 5, "D": 1}
BOOKS = {("D", "active"): (0.093, 0.232), ("D", "passive"): (0.19, 0.232),
         ("C", "active"): (0.288, 0.232), ("C", "passive"): (0.387, 0.232),
         ("B", "active"): (0.093, 0.44), ("B", "passive"): (0.19, 0.44),
         ("A", "active"): (0.288, 0.44), ("A", "passive"): (0.387, 0.44)}
DIGIT_FIX = (("O", "0"), ("o", "0"), ("I", "1"), ("l", "1"), ("|", "1"), ("丨", "1"), ("!", "1"), ("！", "1"))
KIND_NAME = {"relic": "遗物", "active": "主动技能", "passive": "被动技能", "challenge": "挑战", "attribute": "属性",
             "other": "选项"}
MAP_NAMES = ("哥布林王国", "亡灵废墟")

user32 = ctypes.windll.user32
user32.WindowFromPoint.argtypes = [wt.POINT]


def load_cfg() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        user = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
        hk = user.pop("hotkeys", {})
        cfg.update(user)
        cfg["hotkeys"].update(hk)
    except FileNotFoundError:
        CONFIG.write_text(json.dumps(DEFAULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("assistant.json 读取失败，用默认值：", e)
    return cfg


def save_cfg_key(key: str, value):
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    except Exception:
        data = dict(DEFAULTS)
    data[key] = value
    CONFIG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def filled(f: S.Frame, pos, d=0.02, thresh=22.0) -> bool:
    x, y = pos
    return f.region_std(x - d, y - d * 1.6, x + d, y + d * 1.6) > thresh


def skill_filled(f: S.Frame, pos) -> bool:
    return filled(f, pos, thresh=15.0)


def autocast_on(f: S.Frame, pos) -> bool:
    """开了自动释放的技能图标有一圈黄框（实测：开 ≈ 0.039，关 = 0）。"""
    x, y = pos
    a = np.asarray(f.crop(x - 0.03, y - 0.05, x + 0.03, y + 0.05)).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return float(((r > 170) & (g > 130) & (b < 110) & (r - b > 90)).mean()) > 0.012


def digits(text: str) -> int | None:
    for a, b in DIGIT_FIX:
        text = text.replace(a, b)  # 游戏字体的 1 长得像 I
    d = "".join(ch for ch in text if ch.isdigit())
    return int(d) if d else None


def known_name(text: str) -> str:
    """OCR 出来的卡片名对得上游戏里的技能 / 遗物 / 挑战名吗？对上返回标准名字。"""
    for pool in (TABLES["active"], TABLES["passive"], TABLES["relic"], CHALLENGE_DANGER):
        hit = fuzzy(text, pool, 0.6)
        if hit:
            return hit
    return ""


def wave_number(text: str) -> int:
    m = re.search(r"第\s*(\d+)\s*/", text or "")
    return int(m.group(1)) if m else 0


class Paused(Exception):
    pass


# ================================================================ 看、想、动

class Bot:
    def __init__(self, cfg: dict, publish, kb: KB):
        self.cfg, self.publish, self.kb = cfg, publish, kb
        self.brain = Brain(cfg["playstyle"])
        self.auto = bool(cfg["auto"])
        self.hwnd: int | None = None
        self.last_frame: S.Frame | None = None
        self.scene = "waiting"       # waiting / hero_select / map / prep / battle / choice / result
        self.autocast_tries: dict[int, int] = {}
        self.prepared_wave = ""
        self.prep_logged = ""
        self.hero_hint = ""          # 选英雄界面上选定的英雄，开局读不出名字时用
        self.browse = ""
        self.last_wave = ""
        self.map_name = ""           # 「哥布林王国：狂欢1」
        self.mode_ok: bool | None = None
        self.bad_bag: dict[int, int] = {}   # 背包格 → 用不掉的次数（每波清零）
        self.last_bag_use = 0.0
        self.owned_equips: list[str] = []
        self.retries = 0
        self.pending_challenge: tuple[str, int] | None = None
        self.seen_chat: set[str] = set()
        self.last_choice_key = ""
        self.last_advice_key = ""
        self.ticks = 0
        self.run_started = 0.0       # 本局开局时间（等你挑流派）
        self.build_confirmed = False
        self.peak_pressure = 0.0
        self.refresh_left: int | None = None   # 三选一剩余刷新次数（None = 不知道）
        self.refresh_used: set[int] = set()    # 这一屏刷过的卡
        self.refresh_done = 0
        self.pending_active = ""               # 技能格满了时刚选的新主动技能，等替换
        self.slots_scanned = False             # 本局悬停读过技能格了
        self.replace_tries = 0
        self.controller: combat.Controller | None = None
        self.ui_lock = threading.RLock()   # 点鼠标时先把走位的键松开

    # ------------------------------------------------ 工具

    def log(self, msg: str):
        line = f"{datetime.now():%H:%M:%S} {msg}"
        print(line, flush=True)
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fp:
            fp.write(line + "\n")
        self.publish("log", msg)

    def set_scene(self, scene: str):
        if scene != self.scene:
            self.scene = scene
            self.publish("scene", scene)

    def frame(self) -> S.Frame:
        self.hwnd = S.find_game()
        if not self.hwnd:
            raise Paused("没找到游戏窗口，开游戏后自动接上")
        if not S.game_is_foreground(self.hwnd):
            raise Paused("游戏不在前台")
        f = S.grab(self.hwnd)
        if f is None:
            raise Paused("游戏窗口最小化了")
        self.last_frame = f
        return f

    def click(self, pos, button="left", double=False, wait=0.5):
        if not self.auto:
            raise Paused("推荐模式不动手")
        f = self.frame()
        if not S.point_is_game(f, self.hwnd, *pos):
            raise Paused("有别的窗口挡在游戏上面（比如悬浮窗），先不点")
        ctl = self.controller
        if ctl:
            ctl.release()
        with (ctl.lock if ctl else self.ui_lock):
            S.click(f, pos[0], pos[1], button, double)
            S.move_to(f, *NEUTRAL)
        time.sleep(wait)

    def acting(self) -> bool:
        """自动模式且（需要时）确认是狂欢模式，才动手。"""
        return self.auto and (not self.cfg["only_carnival"] or self.mode_ok is not False)

    # ------------------------------------------------ 给战斗线程
    def combat_ready(self) -> bool:
        return (self.cfg["auto_move"] and self.acting() and self.scene == "battle"
                and self.hwnd is not None and S.game_is_foreground(self.hwnd))

    def current_build(self) -> dict:
        return self.brain.ai_plan or {}

    def combat_profile(self):
        """(打法, 距离, 是否近战, 技能槽)：流派 + 英雄 + 已学技能。"""
        hero = self.kb.hero(self.brain.hero) or {}
        b = self.current_build()
        c = b.get("combat") or {}
        melee = hero.get("range", "melee") == "melee"
        engage = c.get("engage") or ("dive" if melee else "kite")
        dist = float(c.get("distance", 0.05 if melee else 0.2))
        slots = []
        for key, name in zip(self.cfg["skill_keys"], self.brain.actives):
            note = self.kb.active_note(name)
            cd = TABLES["active"].get(name, {}).get("cd") or 6.0
            slots.append(combat.SkillSlot(key, base_skill(name), note.get("auto", "on_cd"), {}, float(cd)))
        sk = hero.get("skill") or {}
        auto = dict(sk.get("auto") or {})
        rule = auto.pop("rule", "passive")
        if sk.get("kind") == "主动" and rule != "passive":
            slots.append(combat.SkillSlot(self.cfg["hero_key"], sk.get("name", "英雄技能"), rule, auto, 8.0))
        return engage, dist, melee, slots

    # ------------------------------------------------ 主循环

    def is_choice(self, f: S.Frame, banner: str) -> bool:
        if "选择" in banner or "请选" in banner:
            return True
        hits = 0
        for cx in CARD_X:
            box = (cx - CARD_HALF, NAME_Y[0], cx + CARD_HALF, NAME_Y[1])
            name, hit = S.ocr_best(f, box, known_name, tries=((1.5, 190), (1.5, False)))
            if len(name) >= 2 and hit:
                hits += 1
        return hits >= 2

    def tick(self):
        f = self.frame()
        self.ticks += 1
        if "选择英雄" in S.ocr_text(f, SELECT_TITLE_BOX, 1.5):
            self.set_scene("hero_select")
            self.hero_select(f)
            return
        if self.scene in ("waiting", "hero_select", "map", "result") and self.ticks % 2 == 0 and self.map_select(f):
            return
        if self.ticks % 3 == 1:
            self.read_chat(f)
        if not self.brain.hero and self.ticks % 8 == 1:  # 兜底：从头像下面读英雄名
            self.set_hero(self.read_hud_hero(f), "头像")
        if "替换" in S.ocr_text(f, REPLACE_BOX, 1.5):
            self.set_scene("replace")
            self.replace_skill(f)
            return
        self.replace_tries = 0
        banner = S.ocr_text(f, BANNER, 1.5)
        if self.is_choice(f, banner):
            self.set_scene("choice")
            self.choose(f, banner)
            return
        self.publish("marker", None)
        self.last_choice_key = ""
        self.refresh_used, self.refresh_done = set(), 0
        if "知道" in S.ocr_text(f, TUTORIAL_BOX, 1.5):
            if self.acting():
                self.click(TUTORIAL_BTN)
            return
        result = S.ocr_text(f, RESULT_BOX, 1.0)
        if "失败" in result:
            self.set_scene("result")
            self.on_defeat(f)
            return
        if "胜利" in result or "通关" in result:
            self.set_scene("result")
            self.publish("now", {"title": "通关了！", "lines": ["结算、回大厅交给你。下一局选英雄后我接着帮你"]})
            if self.auto:
                self.auto = False
                self.publish("mode", None)
            return
        if "开始冒险" in S.ocr_text(f, START_BOX, 1.5):
            self.set_scene("prep")
            self.between_waves(f)
            return
        self.set_scene("battle")
        self.in_battle(f)

    # ------------------------------------------------ 英雄

    @staticmethod
    def hero_name(text: str) -> str:
        text = re.sub(r"[\d/／。，.,·•：:]|你|房主|已选择|选择中|1P|当前浏览", "", text)
        return fuzzy(text, TABLES["hero"], 0.5) or ""

    def read_hud_hero(self, f: S.Frame) -> str:
        tries = ((1.5, 225), (2.0, 225), (1.5, 200), (2.0, 200), (3.0, 225), (1.5, False))
        for box in (HERO_BOX, HERO_BOX_TIGHT):
            _, name = S.ocr_best(f, box, self.hero_name, tries)
            if name:
                return name
        return ""

    def set_hero(self, name: str, source: str):
        if name and name != self.brain.hero:
            self.brain.hero = name
            self.log(f"认出英雄：{name}（{source}）")
            self.publish("hero", name)

    def hero_select(self, f: S.Frame):
        """选英雄界面：正在看哪个英雄就讲哪个；选定了就记下。"""
        picked = self.hero_name(S.ocr_text(f, SELECT_1P_BOX, 1.5))
        if picked:
            self.hero_hint = picked
            if picked != self.brain.hero:
                self.brain.reset("")  # 换英雄 = 新的一局，清掉上一把的技能和流派
                self.build_confirmed = False
            self.set_hero(picked, "选英雄界面")
            self.publish("now", {"title": f"已选「{picked}」", "tag": "选英雄",
                                 "lines": ["下面是它的技能玩法和 3 个常见流派，先看看；进游戏后再正式挑一个"]})
            return
        browsing = self.hero_name(S.ocr_text(f, SELECT_BROWSE_BOX, 1.5))
        if browsing and browsing != self.browse:
            self.browse = browsing
            self.publish("browse", browsing)
        self.publish("now", {"title": "选英雄", "tag": "选英雄",
                             "lines": ["点到哪个英雄，我就讲它的技能怎么用、常见流派"]})

    # ------------------------------------------------ 选地图

    def map_select(self, f: S.Frame) -> bool:
        """地图选择界面：整屏粗读一遍，认出地图名和难度就讲解。"""
        lines = S.ocr(f, (0, 0, 1, 1), 0.6)
        text = "".join(l.text for l in lines)
        if not ("地图选择" in text or "关卡/地图" in text or "关卡" in text and any(m in text for m in MAP_NAMES)):
            return False
        seen = []
        for l in lines:
            for m in MAP_NAMES:
                if m[:3] in l.text:
                    d = re.search(r"(难度\s*\d|狂欢\s*\d)", l.text)
                    seen.append((m, d.group(1).replace(" ", "") if d else ""))
        for l in lines:   # 单独一行的「难度 3」「狂欢2」
            d = re.fullmatch(r".{0,3}(难度\s*\d|狂欢\s*\d).{0,3}", l.text)
            if d:
                seen.append(("", d.group(1).replace(" ", "")))
        self.set_scene("map")
        self.publish("maps", seen)
        self.publish("now", {"title": "选地图", "tag": "地图",
                             "lines": ["下面是每张地图 / 难度的说明", "单人爽玩推荐狂欢模式（不上排行榜，自动玩只在狂欢里动手）"]})
        return True

    # ------------------------------------------------ 读聊天框：学了什么技能、选了什么遗物 / 挑战（手动玩时也能跟上）

    def read_chat(self, f: S.Frame):
        lines = [l.text for l in S.ocr(f, CHAT_BOX, 2.0)]
        for text in lines:
            if "当前地" in text:
                if "狂欢" in text:
                    self.mode_ok = True
                elif "难度" in text:
                    self.mode_ok = False
                m = re.search(r"((?:哥布林王国|亡灵废墟)[:：]?(?:难度|狂欢)\d)", text)
                if m and m.group(1) != self.map_name:
                    self.map_name = m.group(1)
                    self.publish("map_name", self.map_name)
                if not self.brain.hero:
                    self.set_hero(self.hero_name(text.split("当前地")[0]), "聊天框")
            if text in self.seen_chat:
                continue
            if "刷新次数增加" in text:
                self.refresh_left = None
            m = re.search(r"学习了(主动|被动)技能(.+)", text)
            if m:
                kind = "active" if m.group(1) == "主动" else "passive"
                name = fuzzy(m.group(2), TABLES[kind], 0.6)
                if name:
                    self.brain.learn(kind, name)
                    self.publish("build", None)
            m = re.search(r"选择了遗物[:：]?(.+)", text)
            if m:
                name = fuzzy(m.group(1), TABLES["relic"], 0.6) or m.group(1)
                self.brain.learn("relic", name)
                self.publish("build", None)
            m = re.search(r"选择了挑战[:：]?(.+)", text)
            if m and not self.pending_challenge:
                known = fuzzy(m.group(1), CHALLENGE_DANGER, 0.6)
                if known:
                    self.pending_challenge = (self.last_wave, DANGER_RANK.get(CHALLENGE_DANGER[known], 3))
            self.seen_chat.add(text)
        if len(self.seen_chat) > 300:
            self.seen_chat = set(lines)

    # ------------------------------------------------ 战斗中

    def in_battle(self, f: S.Frame):
        ctl = self.controller
        if ctl and ctl.view and ctl.view.pressure is not None:
            self.peak_pressure = max(self.peak_pressure, ctl.view.pressure)
        # 背包满了再捡就浪费：趁身边怪少把东西用掉（长线比多打几下更值）
        if (self.acting() and time.time() - self.last_bag_use > 8 and self.bag_full(f)
                and ctl and ctl.view and len(ctl.view.enemies) <= 2 and (ctl.view.hp or 1) > 0.5):
            self.last_bag_use = time.time()
            self.log("背包满了，趁怪少先用掉")
            self.use_bag(limit=6)
        keys = self.cfg["skill_keys"]
        off = [i for i, p in enumerate(SKILL_SLOTS) if skill_filled(f, p) and not autocast_on(f, p)
               and self.wants_autocast(i)]
        if off and self.acting():
            self.ensure_autocast(f)
        lines = []
        if off and not self.acting():
            lines.append(f"{'、'.join(keys[i].upper() for i in off)} 技能还没开自动释放：右键点一下技能图标")
        b = self.current_build()
        lines.append(f"{b.get('name') or STYLE_NAME[self.brain.style]} · 已学 {len(self.brain.skills)} 个技能 · {self.brain.playstyle}")
        if self.auto and not self.acting():
            lines.append("非狂欢模式：自动玩不动手（assistant.json 里 only_carnival 可关）")
        elif self.auto and not self.cfg["auto_move"]:
            lines.append("自动走位已关：战斗交给游戏自带的自动战斗")
        self.publish("now", {"title": self.last_wave or "战斗中", "tag": "战斗", "lines": lines})

    def wants_autocast(self, i: int) -> bool:
        """自动走位时，回血 / 保命 / 变身 / 蓄力这类由我们挑时机放，不开游戏的自动释放。"""
        if not (self.cfg["auto_move"] and self.acting()) or i >= len(self.brain.actives):
            return True
        rule = self.kb.active_note(self.brain.actives[i]).get("auto", "on_cd")
        return rule not in combat.MANUAL_RULES

    def on_defeat(self, f: S.Frame):
        if self.pending_challenge:
            self.brain.challenge_result(False, self.pending_challenge[1])
            self.pending_challenge = None
        if not (self.acting() and self.cfg["auto_retry"]):
            self.publish("now", {"title": "这波输了", "tag": "结算", "lines": [
                "点「从当前波次重试」回到这波准备阶段", "下次挑战我会选保守一点的" if self.brain.playstyle == "爽" else ""]})
            return
        for line in S.ocr(f, (0.1, 0.76, 0.9, 0.86), 1.3):
            if "重试" in line.text:
                self.retries += 1
                self.log(f"这波输了，第 {self.retries} 次重试")
                self.prepared_wave = ""
                self.click((line.x, line.y), wait=3.0)
                return

    # ------------------------------------------------ 三选一

    def read_cards(self, f: S.Frame) -> list[dict]:
        cards = []
        for cx in CARD_X:
            x0, x1 = cx - CARD_HALF, cx + CARD_HALF
            name, _ = S.ocr_best(f, (x0, NAME_Y[0], x1, NAME_Y[1]), known_name)
            any_text = lambda s: s  # noqa: E731  有字就行
            tries = ((1.5, 190), (1.5, False), (2.0, 160))
            cards.append({"name": name, "rarity": S.ocr_best(f, (x0, RARITY_Y[0], x1, RARITY_Y[1]), any_text, tries)[0],
                          "desc": S.ocr_best(f, (x0, DESC_Y[0], x1, DESC_Y[1]), any_text, tries)[0]})
        return cards

    def choose(self, f: S.Frame, banner: str):
        cards = self.read_cards(f)
        if not any(c["name"] or c["desc"] for c in cards):
            return
        kind = self.brain.infer_kind(banner, cards)
        i = self.brain.pick(kind, cards)
        summary = ""
        if kind == "active":
            # 技能格满没满、格子里是什么 → 每张卡写清楚「放进空格 / 替换哪个 / 不值得拿」
            full = len(self.brain.actives) >= 3 or all(skill_filled(f, p) for p in SKILL_SLOTS)
            if full and len(self.brain.actives) < 3 and self.acting() and not self.slots_scanned:
                self.slots_scanned = True
                names = self.scan_slots(SKILL_SLOTS)
                if all(names):
                    for n in names:
                        self.brain.learn("active", n)
                    self.brain.actives = list(names)
                    self.log("认出技能格：" + "、".join(base_skill(n) for n in names))
            i, summary = self.brain.plan_active_choice(cards, self.brain.actives[:3], self.cfg["skill_keys"], full)
        for c in cards:
            n = c.get("matched", "")
            c["note"] = (self.kb.active_note(n).get("note", "") if kind == "active" else
                         self.kb.passive_note(n) if kind == "passive" else "")
        r, r_why = self.brain.refresh_pick(kind, cards, self.refresh_left, self.refresh_used, self.refresh_done)
        key = "|".join(c["matched"] for c in cards) + kind + self.brain.playstyle + str(r) + str(i)
        if key != self.last_choice_key:
            self.last_choice_key = key
            self.publish("choice", {"kind": kind, "cards": cards, "best": i, "summary": summary, "refresh": r,
                                    "refresh_why": r_why, "refresh_left": self.refresh_left})
            self.publish("marker", i)
        if not self.acting():
            return
        if r is not None and self.refresh_card(f, r, cards[r], r_why):
            return            # 刷出了新卡，下一跳重新看
        if i is None:
            # 三张都不值得：随便点一张（分最高的），到替换界面点放弃
            i = max(range(len(cards)), key=lambda j: cards[j]["score"])
            self.log(f"[主动技能] {summary}")
        c = cards[i]
        self.log(f"[{KIND_NAME.get(kind, kind)}] 选「{c['matched']}」：{c.get('replace') or c['why']}")
        self.publish("marker", None)
        for y in (CARD_CLICK_Y, 0.33, 0.455):
            self.click((CARD_X[i], y), wait=1.0)
            f2 = self.frame()
            if not self.is_choice(f2, S.ocr_text(f2, BANNER, 1.5)):
                break
        else:
            self.log("点了卡片没反应，过一会儿再试")
            return
        self.last_choice_key = ""
        self.refresh_used, self.refresh_done = set(), 0
        if kind == "active" and c.get("matched") and len(self.brain.actives) >= 3:
            self.pending_active = c["matched"]   # 技能格满了：接下来会出「请替换一个技能」
        elif kind in ("active", "passive", "relic") and c.get("matched"):
            self.brain.learn(kind, c["matched"])
            self.publish("build", None)
        if kind == "challenge":
            self.pending_challenge = (self.last_wave, c.get("danger", 3))

    def refresh_card(self, f: S.Frame, i: int, card: dict, why: str) -> bool:
        """点第 i 张卡右上角的刷新；卡名变了就算刷成功。"""
        x0, x1 = CARD_X[i] - CARD_HALF, CARD_X[i] + CARD_HALF
        old = card.get("matched") or card.get("name")
        self.click((CARD_X[i] + REFRESH_DX, REFRESH_Y), wait=0.9)
        f2 = self.frame()
        new, _ = S.ocr_best(f2, (x0, NAME_Y[0], x1, NAME_Y[1]), known_name)
        first = not self.refresh_used and self.refresh_done == 0
        self.refresh_used.add(i)
        if new and new != old and fuzzy(new, {old: 1}, 0.8) is None:
            self.refresh_done += 1
            if self.refresh_left is not None:
                self.refresh_left = max(self.refresh_left - 1, 0)
            self.log(f"刷新：{why}（「{old}」→「{new}」）")
            self.last_choice_key = ""
            return True
        if first:
            self.refresh_left = 0
            self.log("刷新没反应：刷新次数大概用完了，之后先不刷")
        return False

    # ------------------------------------------------ 技能格满了：替换还是放弃

    def scan_slots(self, slots) -> list[str | None]:
        """鼠标依次悬停在技能格上，读说明框里的技能名（军师开之前学的技能，靠这个补上）。"""
        names = []
        f = self.frame()
        for x, y in slots:
            S.move_to(f, x, y)
            time.sleep(0.4)
            f = self.frame()
            hit = None
            for line in S.ocr(f, (max(x - 0.16, 0), max(y - 0.42, 0), min(x + 0.26, 1), y - 0.03), 1.3):
                hit = fuzzy(line.text, TABLES["active"], 0.6)
                if hit:
                    break
            names.append(hit)
        S.move_to(f, *NEUTRAL)
        return names

    def replace_skill(self, f: S.Frame):
        if len(self.brain.actives) < 3 and self.acting() and self.replace_tries == 0:
            names = self.scan_slots(REPLACE_SLOTS)
            if all(names):
                self.brain.actives = []
                for n in names:
                    self.brain.learn("active", n)
                self.brain.actives = list(names)
                self.log("认出技能格：" + "、".join(f"{k.upper()} {base_skill(n)}" for k, n in zip(self.cfg["skill_keys"], names)))
        cur = list(self.brain.actives[:3])
        new = self.pending_active
        keys = self.cfg["skill_keys"]
        unknown = len(cur) < 3
        if unknown:
            i, why = None, "技能格里有我没记下的技能（可能是开军师前学的），自己决定换哪个"
        else:
            i, why = self.brain.replace_pick(new, cur)
        if i is not None:
            act = f"把 {keys[i].upper()}「{base_skill(cur[i])}」换成「{base_skill(new)}」"
        else:
            act = "点「放弃」，不要新技能"
        lines = [f"新技能：{base_skill(new) or '没认出来'}"]
        if cur:
            lines.append("现有：" + "、".join(f"{keys[j].upper()} {base_skill(n)}" for j, n in enumerate(cur)))
        if not unknown:
            lines.append("建议：" + act)
        lines.append(why)
        self.publish("now", {"title": "替换技能", "tag": "技能格满了", "lines": lines})
        if not self.acting():
            return
        if unknown and self.replace_tries == 0:
            self.log("技能格满了，但不清楚格子里是什么：放弃新技能（最稳）")
        self.replace_tries += 1
        if self.replace_tries > 4:
            return    # 点了好几次都没反应：别一直点，交给玩家
        self.click(REPLACE_SLOTS[i] if i is not None else GIVEUP_BTN, wait=1.0)
        if "替换" in S.ocr_text(self.frame(), REPLACE_BOX, 1.5):
            if self.replace_tries == 4:
                self.log("替换界面点了没反应，请手动点一下")
            return
        if i is not None:
            self.log(f"替换技能：{act}")
            self.brain.learn("active", new)
            self.brain.actives[i] = new
            self.autocast_tries.pop(i, None)
            self.publish("build", None)
        else:
            self.log(f"放弃新技能：{why}")
        self.pending_active = ""

    # ------------------------------------------------ 波次之间

    def between_waves(self, f: S.Frame):
        wave = S.ocr_text(f, WAVE_BOX, 1.5)
        if wave and wave != self.last_wave:
            if self.pending_challenge and self.pending_challenge[0] != wave:
                self.brain.challenge_result(True, self.pending_challenge[1])
                self.pending_challenge = None
            if re.search(r"第1/", wave):
                self.new_run(f)
            elif self.peak_pressure:
                self.brain.after_wave(self.peak_pressure)
                if self.peak_pressure > 0.75:
                    self.log(f"上一波压力值到过 {self.peak_pressure:.0%}，挑战收一收")
            self.peak_pressure = 0.0
            self.last_wave = wave
            self.brain.wave = wave_number(wave) or self.brain.wave
            self.autocast_tries.clear()
            self.bad_bag.clear()          # 每波重新判断哪些格子用不掉
            self.publish("build", None)
        self.advise(f, wave)
        if not self.acting():
            return
        if not self.build_confirmed:
            left = self.cfg["build_pick_seconds"] - (time.time() - self.run_started)
            if left > 0:
                return    # 等你挑流派
            self.publish("auto_build", None)
            return
        if wave and wave == self.prepared_wave:
            self.log(f"{wave} 准备完毕，开打")
            self.click(START_BTN, wait=2.0)
            return
        if self.prep_logged != wave:  # 被挡住时会反复重试，日志只记一次
            self.prep_logged = wave
            self.log(f"{wave or '波次间'}：整理背包、逛商店")
        self.use_bag()
        if self.cfg["buy_books"] or self.cfg["buy_equipment"] or self.cfg["buy_forging"]:
            self.shop()
            self.use_bag()
        self.ensure_autocast(self.frame())
        self.prepared_wave = wave or "?"

    def advise(self, f: S.Frame, wave: str):
        skulls, gold = self.skulls(f), self.gold(f)
        free_skill = sum(not skill_filled(f, p) for p in SKILL_SLOTS)
        free_equip = sum(not filled(f, p) for p in EQUIP_SLOTS)
        bag = sum(filled(f, p) for p in BAG)
        key = (f"{wave}|{skulls}|{gold // 100}|{free_skill}|{free_equip}|{bag}|{self.brain.playstyle}|{self.brain.risk}"
               f"|{self.build_confirmed}|{self.current_build().get('name')}")
        if key == self.last_advice_key:
            return
        self.last_advice_key = key
        tips = self.brain.shop_advice(skulls, gold, free_skill, free_equip, bag, self.owned_equips)
        b = self.current_build()
        if b.get("plan"):
            tips.insert(0, f"长线：{b['plan'][self.brain.stage]}")
        if b.get("challenge_tip"):
            tips.insert(len(tips) - 1, f"流派挑战建议：{b['challenge_tip']}")
        off = [self.cfg["skill_keys"][i].upper() for i, p in enumerate(SKILL_SLOTS)
               if skill_filled(f, p) and not autocast_on(f, p) and self.wants_autocast(i)]
        if off:
            tips.insert(0, f"{'、'.join(off)} 技能没开自动释放：右键点技能图标")
        if not self.build_confirmed:
            tips.insert(0, "先在面板上挑一个流派（点卡片）")
        self.publish("now", {"title": f"{wave or '波次之间'} · 准备", "tag": "准备", "lines": tips})

    def new_run(self, f: S.Frame):
        hero = self.read_hud_hero(f) or self.hero_hint or self.brain.hero
        plan = self.brain.ai_plan
        self.brain.reset(hero)
        self.brain.ai_plan = plan if hero and plan else {}
        self.autocast_tries.clear()
        self.bad_bag.clear()
        self.owned_equips.clear()
        self.pending_challenge = None
        self.mode_ok = None
        self.run_started = time.time()
        self.build_confirmed = False
        self.peak_pressure = 0.0
        self.refresh_left, self.pending_active, self.slots_scanned = None, "", False
        self.log(f"新的一局{('：' + hero) if hero else ''}")
        self.publish("new_run", hero)

    def resolve_choices(self, limit: int = 6) -> int:
        n = 0
        for _ in range(limit):
            time.sleep(0.4)
            f = self.frame()
            banner = S.ocr_text(f, BANNER, 1.5)
            if not self.is_choice(f, banner):
                break
            self.choose(f, banner)
            n += 1
        return n

    def use_bag(self, limit: int = 12):
        """双击用掉背包里的东西（技能书、锻造器、神符…）。用书引出的三选一、替换技能界面当场处理完再用下一件。"""
        for _ in range(limit):
            f = self.frame()
            if "替换" in S.ocr_text(f, REPLACE_BOX, 1.5):
                self.replace_skill(f)
                if "替换" in S.ocr_text(self.frame(), REPLACE_BOX, 1.5):
                    return            # 替换没处理掉：先别再用东西（会一直提示「请先选择一个技能」）
                continue
            banner = S.ocr_text(f, BANNER, 1.5)
            if self.is_choice(f, banner):
                self.choose(f, banner)
                continue
            slots = [i for i, p in enumerate(BAG) if filled(f, p) and self.bad_bag.get(i, 0) < 2]
            if not slots:
                return
            i = slots[0]
            self.click(BAG[i], double=True, wait=1.2)
            f2 = self.frame()
            if ("替换" not in S.ocr_text(f2, REPLACE_BOX, 1.5) and not self.is_choice(f2, S.ocr_text(f2, BANNER, 1.5))
                    and filled(f2, BAG[i])):
                self.bad_bag[i] = self.bad_bag.get(i, 0) + 1   # 连续 2 次用不掉才跳过（比如任务道具）

    def bag_full(self, f: S.Frame) -> bool:
        return all(filled(f, p) for p in BAG)

    def ensure_autocast(self, f: S.Frame):
        """该开的开、该关的关（回血 / 保命 / 变身由自动战斗挑时机放）。"""
        for i, pos in enumerate(SKILL_SLOTS):
            if not skill_filled(f, pos):
                continue
            want = self.wants_autocast(i)
            if autocast_on(f, pos) == want:
                continue
            tries = self.autocast_tries.get(i, 0)
            if tries >= 3:
                continue
            self.autocast_tries[i] = tries + 1
            self.click(pos, button="right", wait=0.4)
            if autocast_on(self.frame(), pos) == want:
                self.log(f"{self.cfg['skill_keys'][i].upper()} 技能{'开启' if want else '关闭'}自动释放"
                         + ("" if want else "（留给自动战斗挑时机）"))

    # ------------------------------------------------ 商店

    def open_shop(self) -> bool:
        f = self.frame()
        if "装备" not in S.ocr_text(f, SHOP_TABS_BOX, 1.5):
            S.key("tab")
            time.sleep(0.8)
            f = self.frame()
        txt = S.ocr_text(f, SHOP_TABS_BOX, 1.5)
        return "装备" in txt or "技能" in txt

    def close_shop(self):
        f = self.frame()
        if "装备" in S.ocr_text(f, SHOP_TABS_BOX, 1.5):
            S.key("tab")
            time.sleep(0.6)

    def bag_count(self) -> int:
        f = self.frame()
        return sum(filled(f, p) for p in BAG)

    def shop(self):
        f0 = self.frame()
        free_skill = sum(not skill_filled(f0, p) for p in SKILL_SLOTS)
        free_equip = sum(not filled(f0, p) for p in EQUIP_SLOTS)
        if not self.open_shop():
            self.log("商店没打开，跳过")
            return
        try:
            if self.cfg["buy_books"]:
                self.buy_books(free_skill)
            if self.cfg["buy_equipment"] and free_equip:
                free_equip = self.buy_equipment(free_equip)
            if self.cfg["buy_forging"]:
                # 长线：前中期还有空装备格、核心装备没买齐，就给下一件大件留 2500，不全花在锻造器上
                saving = free_equip and self.brain.equip_wishlist(self.owned_equips) and self.brain.stage != "late"
                self.buy_forging(2500 if saving else 0)
        finally:
            self.close_shop()

    def skulls(self, f: S.Frame | None = None) -> int:
        """骷髅币：单个数字认不出，和金币一起读一行，取骷髅图标右边那个词。"""
        f = f or self.frame()
        for b in (True, False):
            for w in S.ocr_words(f, (0.85, 0.02, 0.98, 0.075), 1.5, b):
                if 0.862 < w.x < 0.9:
                    v = digits(w.text)
                    if v is not None:
                        return v
        return 0

    def gold(self, f: S.Frame | None = None) -> int:
        return S.ocr_number(f or self.frame(), GOLD_BOX) or 0

    def book_prices(self) -> dict:
        f = self.frame()
        prices = {}
        for row_y, grades in ((0.325, ("D", "C")), (0.52, ("B", "A"))):
            cols = {0.093: (grades[0], "active"), 0.19: (grades[0], "passive"),
                    0.288: (grades[1], "active"), 0.387: (grades[1], "passive")}
            for b in (True, False):
                for w in S.ocr_words(f, (0.05, row_y - 0.02, 0.43, row_y + 0.02), 2.0, b):
                    v = digits(w.text)
                    if v is None:
                        continue
                    cx = min(cols, key=lambda c: abs(c - w.x))
                    if abs(cx - w.x) < 0.04:
                        prices.setdefault(cols[cx], v % 1000)
        return prices

    def buy_books(self, free_skill: int):
        self.click(TAB_SKILL, wait=0.6)
        failed: set = set()
        for _ in range(10):
            have = self.skulls()
            if have <= 0 or self.bag_count() >= len(BAG) or len(failed) >= 4:
                return
            prices = self.book_prices()
            options = []
            for kind in (("active", "passive") if free_skill > 0 else ("passive",)):
                for g in "ABCD":
                    p = prices.get((g, kind))
                    price = p if p and p >= BOOK_PRICE[g] else BOOK_PRICE[g]
                    if have >= price and (g, kind) not in failed:
                        options.append((g, kind))
            if not options:
                return
            grade, kind = sorted(options, key=lambda o: ("ABCD".index(o[0]), o[1] != "active"))[0]
            self.click(BOOKS[(grade, kind)], button="right", wait=0.6)
            if self.skulls() >= have:
                failed.add((grade, kind))
                continue
            if kind == "active":
                free_skill -= 1
            self.log(f"买了 {grade} 级{'主动' if kind == 'active' else '被动'}技能书")

    def tooltip_has(self, pos, name: str) -> bool:
        f = self.frame()
        S.move_to(f, *pos)
        time.sleep(0.35)
        f = self.frame()
        x, y = pos
        text = S.ocr_text(f, (x, max(y - 0.18, 0), min(x + 0.32, 1), min(y + 0.25, 1)), 1.5)
        return name in text or any(difflib.SequenceMatcher(None, text[i:i + len(name)], name).ratio() >= 0.6
                                   for i in range(max(len(text) - len(name) + 1, 1)))

    def buy_by_position(self, name: str, price: int, reserve: int = 0) -> bool:
        idx = SHOP_INDEX.get(name)
        if idx is None or idx // 7 >= len(GRID_Y):
            return False
        pos = (GRID_X[idx % 7], GRID_Y[idx // 7])
        if not self.tooltip_has(pos, name):
            self.log(f"商店里没对上「{name}」的位置，跳过")
            return False
        g0 = self.gold()
        if g0 < price + self.cfg["gold_reserve"] + reserve:
            return False
        self.click(pos, button="right", wait=0.7)
        return self.gold() < g0

    def buy_equipment(self, free_equip: int) -> int:
        self.click(TAB_EQUIP, wait=0.6)
        S.scroll(self.frame(), 0.25, 0.4, 15)
        for want in self.brain.equip_wishlist(self.owned_equips):
            if free_equip <= 0 or self.gold() < 2500 + self.cfg["gold_reserve"]:
                break
            if self.buy_by_position(want, 2500):
                free_equip -= 1
                self.owned_equips.append(want)
                self.log(f"买了装备：{want}")
                self.publish("build", None)
                if self.resolve_choices():
                    self.open_shop()
                    self.click(TAB_EQUIP, wait=0.5)
        return free_equip

    def buy_forging(self, keep: int = 0):
        self.click(TAB_EQUIP, wait=0.5)
        S.scroll(self.frame(), 0.25, 0.4, 15)
        for _ in range(self.cfg["max_forging_per_wave"]):
            if self.bag_count() >= len(BAG):
                return
            if not self.buy_by_position("属性锻造器", 450, keep):
                if keep:
                    self.log("金币留着下一波买核心装备")
                return
            self.log("买了属性锻造器")
            if self.resolve_choices():
                self.open_shop()
                self.click(TAB_EQUIP, wait=0.5)

    # ------------------------------------------------ 给 AI 的本局情况

    def context(self) -> str:
        b = self.brain
        return "\n".join([
            f"英雄：{b.hero or '未知'}；流派：{self.current_build().get('name') or STYLE_NAME[b.style]}；风格：{b.playstyle}；"
            f"波次：{self.last_wave or '未知'}；地图：{self.map_name or '未知'}",
            f"已学技能：{'、'.join(b.skills) or '无'}",
            f"遗物：{'、'.join(b.relics) or '无'}",
            f"装备：{'、'.join(self.owned_equips) or '未知（手动买的看不到）'}",
        ])


# ================================================================ 快捷键

MODS = {"ctrl": 0x2, "alt": 0x1, "shift": 0x4}
VKEYS = {f"f{i}": 0x6F + i for i in range(1, 13)}


def parse_hotkey(spec):
    mods, vk = 0x4000, 0
    for p in spec.lower().split("+"):
        mods |= MODS.get(p, 0)
        vk = VKEYS.get(p, vk)
    return mods, vk


def hotkey_thread(bindings: dict, q: queue.Queue):
    """注册全局快捷键；被占用的每 5 秒重试一次（比如旧的军师还没关，关掉后自动接上）。"""
    names = list(bindings)
    missing = set()
    for i, name in enumerate(names, 1):
        if not user32.RegisterHotKey(None, i, *parse_hotkey(bindings[name])):
            missing.add(i)
    if missing:
        keys = "、".join(bindings[names[i - 1]].upper() for i in sorted(missing))
        q.put(("log", f"快捷键 {keys} 被别的程序占用，会自动重试；一直不行就去 assistant.json 换一个"))
    user32.SetTimer(None, 0, 5000, None)
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == 0x0312:
            q.put(("key", names[msg.wParam - 1]))
        elif msg.message == 0x0113 and missing:   # WM_TIMER
            for i in sorted(missing):
                if user32.RegisterHotKey(None, i, *parse_hotkey(bindings[names[i - 1]])):
                    missing.discard(i)
                    q.put(("log", f"快捷键 {bindings[names[i - 1]].upper()} 已可用"))


# ================================================================ 面板（ui/ 里的网页，pywebview 显示）

class Api:
    """网页里 window.pywebview.api.xxx 调到这里。方法都很快返回，真正的事丢回主循环做。"""

    def __init__(self, app: "App"):
        self._app = app

    def ready(self):
        self._app.q.put(("ui_ready", None))

    def set_auto(self, on):
        self._app.q.put(("ui", ("auto", bool(on))))

    def set_style(self, v):
        self._app.q.put(("ui", ("style", str(v))))

    def choose_build(self, i):
        self._app.q.put(("ui", ("build", int(i))))

    def ask(self, q):
        self._app.q.put(("ui", ("ask", str(q))))

    def replan(self):
        self._app.q.put(("key", "replan"))

    def hide(self):
        self._app.q.put(("key", "hide"))

    def quit(self):
        self._app.q.put(("ui", ("quit", None)))

    def reload(self):
        self._app.q.put(("ui", ("reload", None)))

    def focus_game(self):
        self._app.panel.focus_game()


class App:
    def __init__(self):
        import panel
        self.cfg = load_cfg()
        self.kb = KB()
        self.q: queue.Queue = queue.Queue()
        self.bot = Bot(self.cfg, lambda k, v: self.q.put((k, v)), self.kb)
        if "--auto" in sys.argv:
            self.bot.auto = True
        self.bot.controller = combat.Controller(self.bot)
        self.panel = panel.Panel(Api(self))
        self.marker = None
        self.now = {"title": "等游戏", "tag": "现在", "lines": ["进游戏后我会自动跟上：选英雄、选地图、三选一、商店、战斗都在这里给建议"]}
        self.choice: dict | None = None
        self.maps_seen: list | None = None
        self.ai_text, self.ai_busy = "", False
        self.ai_options: dict = {}        # 英雄 → AI 配的流派（F9）
        self.ai_busy_hero = ""
        self.chosen: dict[str, int] = self._load_choices()
        self.logs: list[str] = []
        self.status = "启动中"
        self.marker_idx: int | None = None
        self.dirty = True

    # ------------------------------------------------ 流派
    CHOICE_FILE = ROOT / "data" / "build_choice.json"

    def _load_choices(self) -> dict:
        try:
            return json.loads(self.CHOICE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_choices(self):
        self.CHOICE_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.CHOICE_FILE.write_text(json.dumps(self.chosen, ensure_ascii=False), encoding="utf-8")

    def hero_shown(self) -> str:
        b = self.bot
        return b.brain.hero or b.browse

    def builds_for(self, hero: str) -> tuple[list[dict], str]:
        ai = self.ai_options.get(hero)
        if ai:
            return ai.get("options", []), "AI 配的"
        h = self.kb.hero(hero)
        if h:
            return h.get("builds", []), "攻略库"
        import ai as ai_mod
        cached = ai_mod.cached_options(hero)
        return (cached.get("options", []), "AI 配的") if cached else ([], "")

    def apply_build(self, i: int | None = None, confirm: bool = False):
        hero = self.bot.brain.hero
        builds, _ = self.builds_for(hero)
        if not builds:
            return
        if i is None:
            i = self.chosen.get(hero, 0)
        i = max(0, min(i, len(builds) - 1))
        self.chosen[hero] = i
        self._save_choices()
        self.bot.brain.ai_plan = builds[i]
        self.bot.last_choice_key = self.bot.last_advice_key = ""
        if confirm and not self.bot.build_confirmed:
            self.bot.build_confirmed = True
            self.add_log(f"流派：{builds[i]['name']}，开打！")
        elif confirm:
            self.add_log(f"流派换成：{builds[i]['name']}")
        self.dirty = True

    # ------------------------------------------------ 拼面板状态
    def state(self) -> dict:
        b = self.bot
        hero = self.hero_shown()
        hs = None
        if hero:
            h = self.kb.hero(hero) or {}
            t = TABLES["hero"].get(hero, {})
            hs = {"name": hero, "browsing": not b.brain.hero, "role": h.get("role", ""), "range": h.get("range", ""),
                  "skill": h.get("skill") or ({"name": t.get("skill"), "kind": t.get("skill_kind"),
                                               "how": t.get("skill_info") or t.get("info", "")} if t else {}),
                  "tips": h.get("tips", []),
                  "stats": (f"每级 力量+{t.get('STRadd')} 敏捷+{t.get('AGIadd')} 耐力+{t.get('STAadd')} · "
                            f"生命 {t.get('hp')} 法力 {t.get('mp')}") if t else ""}
        builds_state = None
        if hero:
            builds, source = self.builds_for(hero)
            if builds:
                in_run = b.scene in ("prep", "battle", "choice") and b.brain.hero == hero
                hint = ""
                if in_run and not b.build_confirmed:
                    left = int(self.cfg["build_pick_seconds"] - (time.time() - b.run_started))
                    hint = ("点一个流派开始" + (f"（自动玩：{left} 秒后用第 {self.chosen.get(hero, 0) + 1} 个）"
                                          if b.auto and left > 0 else ""))
                have = [base_skill(s) for s in b.brain.skills]
                builds_state = {"list": builds, "chosen": self.chosen.get(hero, 0), "confirmed": b.build_confirmed,
                                "in_run": in_run, "stage": b.brain.stage if in_run else "", "source": source,
                                "hint": hint, "have": have, "owned": b.owned_equips}
            elif self.ai_busy_hero == hero:
                builds_state = {"list": [], "hint": "AI 正在配流派…"}
        maps = None
        if b.scene == "map" or (self.maps_seen and b.scene in ("waiting", "hero_select")):
            maps = self.map_cards()
        run = None
        if b.brain.hero and (b.brain.skills or b.brain.relics or b.owned_equips):
            run = {"skills": b.brain.skills, "relics": b.brain.relics[-6:], "owned": b.owned_equips,
                   "wish": b.brain.equip_wishlist(b.owned_equips)}
        ch = None
        if self.choice and b.scene == "choice":
            ch = {"kind_name": KIND_NAME.get(self.choice["kind"], "选项"), "best": self.choice["best"],
                  "refresh": self.choice.get("refresh"), "refresh_why": self.choice.get("refresh_why", ""),
                  "summary": self.choice.get("summary", ""),
                  "cards": [{"name": c.get("matched") or c.get("name") or "?", "score": c.get("score", 0),
                             "why": c.get("why", ""), "note": c.get("note", ""), "replace": c.get("replace", ""),
                             "worth": c.get("worth", True)} for c in self.choice["cards"]]}
        ctl = b.controller
        return {
            "auto": b.auto, "playstyle": b.brain.playstyle, "status": self.status, "live": bool(b.hwnd),
            "scene": b.scene, "now": self.now, "hero": hs, "builds": builds_state, "choice": ch, "maps": maps,
            "map_advice": self.map_advice() if maps else "",
            "combat": ctl.snapshot() if ctl and b.scene == "battle" else None, "run": run,
            "ai": {"text": self.ai_text, "busy": self.ai_busy}, "logs": self.logs[-5:],
        }

    def map_cards(self) -> list[dict]:
        M = self.kb.maps
        seen = self.maps_seen or []
        seen_names = {n for n, _ in seen if n} or {n for n in MAP_NAMES if n in M}
        cur_diffs = {d for _, d in seen if d}
        out = []
        for name in MAP_NAMES:
            if name not in seen_names or name not in M:
                continue
            m = M[name]
            out.append({"name": name, "diff": "", "line": m.get("theme", "")})
            table = {**m.get("difficulties", {}), **m.get("carnival", {})}
            for d, info in table.items():
                if cur_diffs and d not in cur_diffs:
                    continue
                out.append({"name": name, "diff": d, "line": info["line"], "for": info["for"], "tip": info["tip"],
                            "current": len(cur_diffs) == 1 and d in cur_diffs})
        return out

    def map_advice(self) -> str:
        b = self.bot
        h = self.kb.hero(b.brain.hero or b.browse)
        if not h:
            return "先选英雄，我再按英雄推荐难度"
        build = (h.get("builds") or [{}])[self.chosen.get(b.brain.hero or b.browse, 0)]
        if b.brain.playstyle == "爽":
            return f"{build.get('name', '')}：推荐狂欢 1 起步，清得动再上狂欢 2 / 3"
        return f"{build.get('name', '')}：求稳先打难度 1~2 熟悉，再挑战更高难度"

    # ------------------------------------------------ 事件
    def add_log(self, msg: str):
        self.logs.append(f"{datetime.now():%H:%M} {msg}")
        self.logs = self.logs[-20:]
        self.dirty = True

    def handle(self, kind, val):
        b = self.bot
        if kind == "status":
            if val != self.status:
                self.status = val
                self.dirty = True
        elif kind == "log":
            self.add_log(val)
        elif kind == "now":
            self.now = val
        elif kind == "choice":
            self.choice = val
        elif kind == "marker":
            self.show_marker(val)
            return
        elif kind == "scene":
            if val != "choice":
                self.choice = None
        elif kind == "maps":
            self.maps_seen = val
        elif kind == "browse":
            self.ai_text = ""
        elif kind in ("hero", "new_run"):
            if kind == "new_run":
                self.ai_text = ""
                if b.brain.hero:
                    self.apply_build()
                    self.add_log("新的一局：在面板上挑一个流派")
            elif b.brain.hero:
                self.apply_build()
        elif kind == "auto_build":
            self.apply_build(confirm=True)
        elif kind == "map_name":
            self.add_log(f"地图：{val}")
        elif kind == "options":
            hero, data = val
            self.ai_busy_hero = ""
            self.ai_options[hero] = data
            self.chosen[hero] = 0
            if hero == b.brain.hero:
                self.apply_build(0)
            self.add_log(f"AI 给 {hero} 配了 {len(data.get('options', []))} 个新流派")
        elif kind == "options_fail":
            self.ai_busy_hero = ""
            self.add_log(f"AI 配流派没成：{str(val)[:40]}")
        elif kind == "ai_answer":
            self.ai_text, self.ai_busy = val, False
        elif kind == "ui_ready":
            self.panel.ready = True
            self.panel._last_state = ""
        elif kind == "ui":
            self.ui_action(*val)
        elif kind == "key":
            {"auto": self.toggle_auto, "playstyle": self.toggle_playstyle,
             "ask": lambda: self.ask_ai("现在该怎么选、怎么做？"), "replan": self.replan,
             "hide": self.panel.toggle}[val]()
        self.dirty = True

    def ui_action(self, what, val):
        if what == "auto":
            if val != self.bot.auto:
                self.toggle_auto()
        elif what == "style":
            if val != self.bot.brain.playstyle:
                self.toggle_playstyle()
        elif what == "build":
            hero = self.bot.brain.hero or self.bot.browse
            if self.bot.brain.hero:
                self.apply_build(val, confirm=self.bot.scene in ("prep", "battle", "choice"))
            elif hero:
                self.chosen[hero] = val
                self.dirty = True
        elif what == "ask":
            self.ask_ai(val)
        elif what == "quit":
            self.quit()
            return
        elif what == "reload":
            self.reload()
            return
        if what != "ask":
            self.panel.focus_game()   # 点完把焦点还给游戏

    def toggle_auto(self):
        b = self.bot
        b.auto = not b.auto
        b.prepared_wave = ""
        if not b.auto and b.controller:
            b.controller.release()
        self.show_marker(None if b.auto else self.marker_idx)
        self.add_log("切到自动玩：选卡、出装、走位、放技能都交给我" if b.auto else "切到推荐模式：你来玩，我出主意")

    def toggle_playstyle(self):
        b = self.bot.brain
        b.playstyle = "稳" if b.playstyle == "爽" else "爽"
        save_cfg_key("playstyle", b.playstyle)
        self.bot.last_choice_key = self.bot.last_advice_key = ""
        self.add_log(f"风格：{b.playstyle}" + ("（挑大奖励、高伤害，输了自动收一收）" if b.playstyle == "爽" else "（求稳，挑战只接最安全的）"))

    def replan(self):
        hero = self.bot.brain.hero or self.bot.browse
        if not hero or not self.cfg["ai_plan"] or self.ai_busy_hero:
            return
        self.ai_busy_hero = hero
        self.add_log(f"让 AI 给 {hero} 再配 3 个流派（十几秒）")
        playstyle = self.bot.brain.playstyle

        def work():
            import ai
            try:
                self.q.put(("options", (hero, ai.build_options(hero, playstyle, self.cfg["ai_model"], self.cfg["ai_effort"]))))
            except Exception as e:
                self.q.put(("options_fail", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def ask_ai(self, question: str):
        if self.ai_busy:
            return
        self.ai_busy = True
        self.ai_text = f"想一下：{question}"
        self.dirty = True
        hwnd = S.find_game()
        frame = S.grab(hwnd) if hwnd else None
        ctx = self.bot.context()
        if self.choice:
            ctx += "\n当前三选一：" + "；".join(f"{c.get('matched')}（{c.get('why')}）" for c in self.choice["cards"])

        def work():
            import ai
            try:
                png = ai.png_bytes(frame.img) if frame else None
                self.q.put(("ai_answer", ai.answer(question, png, ctx, self.cfg["ai_model"], self.cfg["ai_effort"])))
            except Exception as e:
                self.q.put(("ai_answer", f"没问成：{e}"))
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------ 推荐标记
    def show_marker(self, idx: int | None):
        self.marker_idx = idx
        if self.marker is None:
            return
        hwnd = S.find_game()
        rect = S.game_rect(hwnd) if hwnd else None
        if idx is None or rect is None or self.bot.auto or not self.panel.visible:
            self.marker.hide()
            return
        import marker
        x, y, w, h = rect
        f = self.bot.last_frame
        cx, cy = CARD_X[idx], 0.785
        back = f.crop(cx - 0.06, cy - 0.025, cx + 0.06, cy + 0.025) if f else None
        img = marker.render_pill("★ 推荐这张", back, user32.GetDpiForSystem() / 96)
        self.marker.show(x + int(cx * w), y + int(cy * h), img)

    # ------------------------------------------------ 循环
    def worker(self):
        while True:
            try:
                self.bot.tick()
                self.q.put(("status", "自动玩中" if self.bot.auto else "看着呢"))
            except Paused as e:
                self.q.put(("status", f"⏸ {e}"))
                self.q.put(("marker", None))
            except Exception as e:
                self.bot.log(f"出错：{type(e).__name__}: {e}")
                time.sleep(1)
            self.panel.backdrop(self.bot.last_frame)
            time.sleep(self.cfg["tick_seconds"])

    def pump(self):
        last_place = 0.0
        while True:
            try:
                while True:
                    self.handle(*self.q.get(timeout=0.15))
            except queue.Empty:
                pass
            except Exception as e:
                print("面板事件出错：", e)
            now = time.time()
            if self.bot.scene == "battle" or (not self.bot.build_confirmed and self.bot.scene == "prep"):
                self.dirty = True     # 战斗数据、倒计时要刷新
            try:   # 单次出错只记一下，不能让刷新线程停掉（停了面板就一直卡在旧内容）
                if self.dirty:
                    self.dirty = False
                    self.panel.push(self.state())
                if now - last_place > 3:
                    last_place = now
                    self.panel.place()
                    self.panel.keep_on_top()
            except Exception as e:
                self.bot.log(f"面板刷新出错：{type(e).__name__}: {e}")
                time.sleep(1)

    def on_start(self):
        import marker
        self.panel.on_shown()
        try:
            self.marker = marker.Marker()
        except Exception as e:
            print("推荐标记窗口没建起来：", e)
        threading.Thread(target=self.worker, daemon=True, name="bot").start()
        threading.Thread(target=hotkey_thread, args=(self.cfg["hotkeys"], self.q), daemon=True, name="hotkeys").start()
        if "--debug" in sys.argv:
            import faulthandler
            faulthandler.dump_traceback_later(20, repeat=True, file=open(ROOT / "data" / "stacks.txt", "w"))
        self.pump()

    def quit(self):
        if self.bot.controller:
            self.bot.controller.running = False
            self.bot.controller.release()
        if self.marker:
            self.marker.hide()
        self.panel.window.destroy()
        threading.Timer(3, lambda: os._exit(0)).start()   # WebView 偶尔关不干净，兜底退出

    def reload(self):
        """重新加载：先起一个新的军师（读磁盘上最新的代码），自己马上退出。新进程会等旧进程放开单实例锁。"""
        import subprocess
        args = [a for a in sys.argv[1:] if a not in ("--auto", "--reload")]
        args.append("--reload")
        if self.bot.auto:
            args.append("--auto")          # 自动玩状态带过去
        self.add_log("重新加载中…")
        release_instance()
        exe = Path(sys.executable)
        if exe.name.lower() == "python.exe" and exe.with_name("pythonw.exe").exists():
            exe = exe.with_name("pythonw.exe")     # 不弹黑框
        subprocess.Popen([str(exe), str(Path(__file__).resolve()), *args], cwd=str(ROOT),
                         creationflags=0x00000008 | 0x00000200)   # DETACHED_PROCESS | NEW_PROCESS_GROUP
        self.quit()

    def run(self):
        import webview
        webview.start(self.on_start, debug="--devtools" in sys.argv)


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateMutexW.restype = wt.HANDLE
_k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
_k32.CloseHandle.argtypes = [wt.HANDLE]
_MUTEX = None
MUTEX_NAME = "DungeonBrawlAdvisor.SingleInstance"


def single_instance(wait: float = 0.0) -> bool:
    """只允许开一个军师：两个同时跑会抢键盘鼠标、抢快捷键。wait：重新加载时等旧进程退出的秒数。"""
    global _MUTEX
    deadline = time.time() + wait
    while True:
        _MUTEX = _k32.CreateMutexW(None, False, MUTEX_NAME)
        if ctypes.get_last_error() != 183:   # ERROR_ALREADY_EXISTS
            return True
        _k32.CloseHandle(_MUTEX)
        _MUTEX = None
        if time.time() >= deadline:
            break
        time.sleep(0.2)
    user32.MessageBoxW(None, "地下城军师已经在运行了。\n按 F10 显示面板；要载入新版本点面板右上角 ↻。",
                       "地下城军师", 0x40 | 0x40000)
    return False


def release_instance():
    global _MUTEX
    if _MUTEX:
        _k32.CloseHandle(_MUTEX)
        _MUTEX = None


if __name__ == "__main__":
    if single_instance(wait=10.0 if "--reload" in sys.argv else 0.0):
        App().run()
