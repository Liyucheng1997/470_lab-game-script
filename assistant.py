"""地下城竞技场 助手：推荐怎么选、怎么出装，AI 规划本局路线、随时答疑；按 F8 还能切成自动玩。

推荐模式（默认）：只看不动手。三选一时右边面板给每张卡打分写理由，推荐的那张下面出现 ★；波次之间列出该买什么。
自动模式（F8）：同样的判断，直接帮你点：选卡、买书买装备买锻造器、用背包、开自动释放、开下一波、输了重试。
战斗交给游戏自带的「自动战斗 / 自动攻击 / 智能施法」。只模拟鼠标键盘，不改游戏文件、不读写内存。

快捷键：F8 推荐 / 自动　F7 爽 / 稳　F6 问 AI（看当前画面）　F9 重新规划本局路线　F10 隐藏面板
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import difflib
import json
import queue
import re
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path

import numpy as np

import screen as S
from brain import CHALLENGE_DANGER, DANGER_RANK, EQUIP_INFO, STYLE_NAME, TABLES, Brain, fuzzy

ROOT = Path(__file__).resolve().parent
LOG = ROOT / "data" / "assistant.log"
CONFIG = ROOT / "assistant.json"
DEFAULTS = {
    "playstyle": "爽",              # 爽 / 稳
    "auto": False,                  # 启动时是否直接自动玩
    "only_carnival": True,          # 自动玩只在狂欢模式里动手（狂欢模式不上排行榜）；推荐模式不受限
    "buy_equipment": True,
    "buy_books": True,
    "buy_forging": True,
    "gold_reserve": 300,
    "max_forging_per_wave": 10,
    "auto_retry": True,
    "ai_plan": True,                # 每局开始让 AI 规划路线（几秒到十几秒，后台进行）
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
SKILL_SLOTS = [(0.435, 0.873), (0.495, 0.873), (0.556, 0.873)]
SKILL_KEYS = ("Q", "E", "R")
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


class Paused(Exception):
    pass


# ================================================================ 看、想、动

class Bot:
    def __init__(self, cfg: dict, publish):
        self.cfg, self.publish = cfg, publish
        self.brain = Brain(cfg["playstyle"])
        self.auto = bool(cfg["auto"])
        self.hwnd: int | None = None
        self.last_frame: S.Frame | None = None
        self.autocast_tries: dict[int, int] = {}
        self.prepared_wave = ""
        self.prep_logged = ""
        self.hero_hint = ""          # 选英雄界面上选定的英雄，开局读不出名字时用
        self.last_wave = ""
        self.mode_ok: bool | None = None
        self.bad_bag: set[int] = set()
        self.owned_equips: list[str] = []
        self.retries = 0
        self.pending_challenge: tuple[str, int] | None = None
        self.seen_chat: set[str] = set()
        self.last_choice_key = ""
        self.last_advice_key = ""
        self.ticks = 0

    # ------------------------------------------------ 工具

    def log(self, msg: str):
        line = f"{datetime.now():%H:%M:%S} {msg}"
        print(line, flush=True)
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fp:
            fp.write(line + "\n")
        self.publish("log", msg)

    def status(self, msg: str):
        self.publish("status", msg)

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
        S.click(f, pos[0], pos[1], button, double)
        S.move_to(f, *NEUTRAL)
        time.sleep(wait)

    def acting(self) -> bool:
        """自动模式且（需要时）确认是狂欢模式，才动手。"""
        return self.auto and (not self.cfg["only_carnival"] or self.mode_ok is not False)

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
            self.hero_select(f)
            return
        if self.ticks % 3 == 1:
            self.read_chat(f)
        if not self.brain.hero and self.ticks % 8 == 1:  # 兜底：从头像下面读英雄名
            self.set_hero(self.read_hud_hero(f), "头像")
        banner = S.ocr_text(f, BANNER, 1.5)
        if self.is_choice(f, banner):
            self.choose(f, banner)
            return
        self.publish("marker", None)
        self.last_choice_key = ""
        if "知道" in S.ocr_text(f, TUTORIAL_BOX, 1.5):
            if self.acting():
                self.click(TUTORIAL_BTN)
            return
        result = S.ocr_text(f, RESULT_BOX, 1.0)
        if "失败" in result:
            self.on_defeat(f)
            return
        if "胜利" in result or "通关" in result:
            self.publish("now", {"title": "通关了！", "lines": ["结算、回大厅交给你。下一局选英雄后我接着帮你"]})
            if self.auto:
                self.auto = False
                self.publish("mode", None)
            return
        if "开始冒险" in S.ocr_text(f, START_BOX, 1.5):
            self.between_waves(f)
            return
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
        """选英雄界面：选定了就记下，面板马上给英雄技能和几个流派；还在挑就显示正在看的那个。"""
        picked = self.hero_name(S.ocr_text(f, SELECT_1P_BOX, 1.5))
        if picked:
            self.hero_hint = picked
            if picked != self.brain.hero:
                self.brain.reset("")  # 换英雄 = 新的一局，清掉上一把的技能和流派
            self.set_hero(picked, "选英雄界面")
            self.publish("now", {"title": "选英雄", "lines": [f"已选「{picked}」：下面挑一个流派，然后开始冒险"]})
            return
        browsing = self.hero_name(S.ocr_text(f, SELECT_BROWSE_BOX, 1.5))
        if browsing:
            self.publish("browse", browsing)
        self.publish("now", {"title": "选英雄", "lines": ["选定英雄后，我先看它的技能，再给几个流派"]})

    # ------------------------------------------------ 读聊天框：学了什么技能、选了什么遗物 / 挑战（手动玩时也能跟上）

    def read_chat(self, f: S.Frame):
        lines = [l.text for l in S.ocr(f, CHAT_BOX, 2.0)]
        for text in lines:
            if "当前地" in text:
                if "狂欢" in text:
                    self.mode_ok = True
                elif "难度" in text:
                    self.mode_ok = False
                if not self.brain.hero:
                    self.set_hero(self.hero_name(text.split("当前地")[0]), "聊天框")
            if text in self.seen_chat:
                continue
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
        off = [i for i, p in enumerate(SKILL_SLOTS) if skill_filled(f, p) and not autocast_on(f, p)]
        if off and self.acting():
            self.ensure_autocast(f)
        elif off:
            keys = "、".join(SKILL_KEYS[i] for i in off)
            self.publish("now", {"title": "战斗中", "lines": [f"{keys} 技能还没开自动释放：右键点一下技能图标"]})
            return
        self.publish("now", {"title": "战斗中", "lines": [
            f"{STYLE_NAME[self.brain.style]} · 已学 {len(self.brain.skills)} 个技能 · {self.brain.playstyle}"]})

    def on_defeat(self, f: S.Frame):
        if self.pending_challenge:
            self.brain.challenge_result(False, self.pending_challenge[1])
            self.pending_challenge = None
        if not (self.acting() and self.cfg["auto_retry"]):
            self.publish("now", {"title": "这波输了", "lines": [
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
        key = "|".join(c["matched"] for c in cards) + kind + self.brain.playstyle
        if key != self.last_choice_key:
            self.last_choice_key = key
            self.publish("choice", {"kind": kind, "cards": cards, "best": i})
            self.publish("marker", i)
        if not self.acting():
            return
        c = cards[i]
        self.log(f"[{KIND_NAME.get(kind, kind)}] 选「{c['matched']}」：{c['why']}")
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
        if kind in ("active", "passive", "relic") and c.get("matched"):
            self.brain.learn(kind, c["matched"])
            self.publish("build", None)
        if kind == "challenge":
            self.pending_challenge = (self.last_wave, c.get("danger", 3))

    # ------------------------------------------------ 波次之间

    def between_waves(self, f: S.Frame):
        wave = S.ocr_text(f, WAVE_BOX, 1.5)
        if wave and wave != self.last_wave:
            if self.pending_challenge and self.pending_challenge[0] != wave:
                self.brain.challenge_result(True, self.pending_challenge[1])
                self.pending_challenge = None
            if re.search(r"第1/", wave):
                self.new_run(f)
            self.last_wave = wave
            self.autocast_tries.clear()
        self.advise(f, wave)
        if not self.acting():
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
        key = f"{wave}|{skulls}|{gold // 100}|{free_skill}|{free_equip}|{bag}|{self.brain.playstyle}|{self.brain.risk}"
        if key == self.last_advice_key:
            return
        self.last_advice_key = key
        tips = self.brain.shop_advice(skulls, gold, free_skill, free_equip, bag, self.owned_equips)
        off = [SKILL_KEYS[i] for i, p in enumerate(SKILL_SLOTS) if skill_filled(f, p) and not autocast_on(f, p)]
        if off:
            tips.insert(0, f"{'、'.join(off)} 技能没开自动释放：右键点技能图标")
        self.publish("now", {"title": f"{wave or '波次之间'} · 准备", "lines": tips})

    def new_run(self, f: S.Frame):
        hero = self.read_hud_hero(f) or self.hero_hint or self.brain.hero
        self.brain.reset(hero)
        self.autocast_tries.clear()
        self.bad_bag.clear()
        self.owned_equips.clear()
        self.pending_challenge = None
        self.mode_ok = None
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

    def use_bag(self):
        for _ in range(12):
            f = self.frame()
            banner = S.ocr_text(f, BANNER, 1.5)
            if self.is_choice(f, banner):
                self.choose(f, banner)
                continue
            slots = [i for i, p in enumerate(BAG) if filled(f, p) and i not in self.bad_bag]
            if not slots:
                return
            i = slots[0]
            self.click(BAG[i], double=True, wait=1.2)
            f2 = self.frame()
            if not self.is_choice(f2, S.ocr_text(f2, BANNER, 1.5)) and filled(f2, BAG[i]):
                self.bad_bag.add(i)

    def ensure_autocast(self, f: S.Frame):
        for i, pos in enumerate(SKILL_SLOTS):
            if not skill_filled(f, pos) or autocast_on(f, pos):
                continue
            tries = self.autocast_tries.get(i, 0)
            if tries >= 3:
                continue
            self.autocast_tries[i] = tries + 1
            self.click(pos, button="right", wait=0.4)
            if autocast_on(self.frame(), pos):
                self.log(f"{SKILL_KEYS[i]} 技能开启自动释放")

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
                self.buy_equipment(free_equip)
            if self.cfg["buy_forging"]:
                self.buy_forging()
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

    def buy_by_position(self, name: str, price: int) -> bool:
        idx = SHOP_INDEX.get(name)
        if idx is None or idx // 7 >= len(GRID_Y):
            return False
        pos = (GRID_X[idx % 7], GRID_Y[idx // 7])
        if not self.tooltip_has(pos, name):
            self.log(f"商店里没对上「{name}」的位置，跳过")
            return False
        g0 = self.gold()
        if g0 < price + self.cfg["gold_reserve"]:
            return False
        self.click(pos, button="right", wait=0.7)
        return self.gold() < g0

    def buy_equipment(self, free_equip: int):
        self.click(TAB_EQUIP, wait=0.6)
        S.scroll(self.frame(), 0.25, 0.4, 15)
        for want in self.brain.equip_wishlist(self.owned_equips):
            if free_equip <= 0 or self.gold() < 2500 + self.cfg["gold_reserve"]:
                return
            if self.buy_by_position(want, 2500):
                free_equip -= 1
                self.owned_equips.append(want)
                self.log(f"买了装备：{want}")
                self.publish("build", None)
                if self.resolve_choices():
                    self.open_shop()
                    self.click(TAB_EQUIP, wait=0.5)

    def buy_forging(self):
        self.click(TAB_EQUIP, wait=0.5)
        S.scroll(self.frame(), 0.25, 0.4, 15)
        for _ in range(self.cfg["max_forging_per_wave"]):
            if self.bag_count() >= len(BAG):
                return
            if not self.buy_by_position("属性锻造器", 450):
                return
            self.log("买了属性锻造器")
            if self.resolve_choices():
                self.open_shop()
                self.click(TAB_EQUIP, wait=0.5)

    # ------------------------------------------------ 给 AI 的本局情况

    def context(self) -> str:
        b = self.brain
        return "\n".join([
            f"英雄：{b.hero or '未知'}；流派：{STYLE_NAME[b.style]}；风格：{b.playstyle}；波次：{self.last_wave or '未知'}",
            f"已学技能：{'、'.join(b.skills) or '无'}",
            f"遗物：{'、'.join(b.relics) or '无'}",
            f"装备：{'、'.join(self.owned_equips) or '未知（手动买的看不到）'}",
        ])


# ================================================================ 面板

BG, CARD, FG, DIM, ACCENT, GOOD, WARN, GOLDC = ("#15171c", "#1f2229", "#e8eaed", "#9aa0a6", "#5b9dff",
                                                "#5fd08a", "#ffb454", "#f5c451")
FONT = "Microsoft YaHei UI"

MODS = {"ctrl": 0x2, "alt": 0x1, "shift": 0x4}
VKEYS = {f"f{i}": 0x6F + i for i in range(1, 13)}


def parse_hotkey(spec):
    mods, vk = 0x4000, 0
    for p in spec.lower().split("+"):
        mods |= MODS.get(p, 0)
        vk = VKEYS.get(p, vk)
    return mods, vk


def hotkey_thread(bindings: dict, q: queue.Queue):
    names = list(bindings)
    for i, name in enumerate(names, 1):
        if not user32.RegisterHotKey(None, i, *parse_hotkey(bindings[name])):
            q.put(("log", f"快捷键 {bindings[name].upper()} 被占用，去 assistant.json 换一个"))
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == 0x0312:
            q.put(("key", names[msg.wParam - 1]))


class App:
    def __init__(self):
        self.cfg = load_cfg()
        self.q: queue.Queue = queue.Queue()
        self.bot = Bot(self.cfg, lambda k, v: self.q.put((k, v)))
        if "--auto" in sys.argv:
            self.bot.auto = True
        self.now = {"title": "等游戏", "lines": ["进游戏后我会自动跟上。三选一、商店、出装都会在这里给建议"]}
        self.choice: dict | None = None
        self.ai_text = ""
        self.ai_busy = False
        self.options: dict | None = None     # 当前英雄的几个流派（AI 给的，按英雄缓存）
        self.options_hero = ""
        self.options_busy = False
        self.browse = ""                     # 选英雄界面正在看的英雄
        self.logs: list[str] = []
        self.status = ""
        self.visible = True
        self.marker_idx: int | None = None
        self._build_ui()

    # ------------------------------------------------ 窗口

    def _build_ui(self):
        prev_fg = user32.GetForegroundWindow()
        r = self.root = tk.Tk()
        r.title("地下城助手")
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        r.attributes("-alpha", 0.94)
        r.configure(bg=BG)

        bar = tk.Frame(r, bg=CARD)
        bar.pack(fill="x")
        title = tk.Label(bar, text="⚔ 地下城助手", bg=CARD, fg=FG, font=(FONT, 10, "bold"))
        title.pack(side="left", padx=8, pady=5)
        for w in (bar, title):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
        close = tk.Label(bar, text="✕", bg=CARD, fg=DIM, font=(FONT, 10), cursor="hand2")
        close.pack(side="right", padx=8)
        close.bind("<Button-1>", lambda e: r.destroy())

        btns = tk.Frame(r, bg=BG)
        btns.pack(fill="x", padx=6, pady=(6, 2))
        self.btn_mode = self._button(btns, "", self.toggle_auto)
        self.btn_style = self._button(btns, "", self.toggle_playstyle)
        self._button(btns, "问 AI", lambda: self.ask_ai("现在该怎么选、怎么做？"))
        self.status_lbl = tk.Label(r, text="", bg=BG, fg=DIM, font=(FONT, 8), anchor="w", wraplength=300, justify="left")
        self.status_lbl.pack(fill="x", padx=8)

        bottom = tk.Frame(r, bg=CARD)
        bottom.pack(side="bottom", fill="x")  # 先放底部输入框，面板高度固定时才不会被挤掉
        self.entry = tk.Entry(bottom, bg=CARD, fg=DIM, insertbackground=FG, relief="flat", font=(FONT, 9))
        self.entry.pack(fill="x", padx=8, pady=6)
        self.entry.insert(0, "问点什么，回车（如：这把出什么装）")
        self.entry.bind("<FocusIn>", self._entry_focus)
        self.entry.bind("<Return>", self._entry_send)

        self.text = tk.Text(r, bg=BG, fg=FG, font=(FONT, 9), wrap="char", relief="flat", padx=8, pady=4,
                            spacing1=1, spacing3=2, cursor="arrow", highlightthickness=0)
        self.text.pack(fill="both", expand=True)
        t = self.text
        t.tag_configure("sec", font=(FONT, 9, "bold"), foreground=ACCENT, spacing1=6)
        t.tag_configure("h", font=(FONT, 11, "bold"), foreground=GOOD)
        t.tag_configure("best", foreground=GOLDC, font=(FONT, 9, "bold"))
        t.tag_configure("dim", foreground=DIM)
        t.tag_configure("warn", foreground=WARN)
        t.tag_configure("opt", foreground=FG)

        self.marker = tk.Toplevel(r)
        self.marker.overrideredirect(True)
        self.marker.attributes("-topmost", True)
        self.marker.configure(bg="#000001")
        self.marker.attributes("-transparentcolor", "#000001")
        tk.Label(self.marker, text="▲ 推荐这张", bg="#000001", fg=GOLDC, font=(FONT, 14, "bold")).pack()
        self.marker.withdraw()

        r.update_idletasks()
        self.hwnd = user32.GetParent(r.winfo_id()) or r.winfo_id()
        mh = user32.GetParent(self.marker.winfo_id()) or self.marker.winfo_id()
        # 标记点不中、不抢焦点（WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW）
        user32.SetWindowLongW(mh, -20, user32.GetWindowLongW(mh, -20) | 0x80000 | 0x20 | 0x08000000 | 0x80)
        self.marker_hwnd = mh
        self.place_panel()
        # 用 Tk 自己的方式显示（绕过 Tk 直接 ShowWindow 会让它以为窗口是隐藏的，切全屏时又被缩小），显示完把焦点还回去
        r.deiconify()
        r.update()
        if prev_fg:
            user32.SetForegroundWindow(prev_fg)
        self.render()

    def _button(self, parent, text, cmd):
        b = tk.Label(parent, text=text, bg=CARD, fg=FG, font=(FONT, 9), padx=8, pady=3, cursor="hand2")
        b.pack(side="left", padx=2)
        b.bind("<Button-1>", lambda e: cmd())
        return b

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()
        self._dragged = True

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def place_panel(self):
        """放在游戏画面右侧的空白处（卡片右边、背包上面），不挡三选一和背包。"""
        if getattr(self, "_dragged", False):
            return
        hwnd = S.find_game()
        rect = S.game_rect(hwnd) if hwnd else None
        if rect:
            x, y, w, h = rect
            pw, ph = int(w * 0.16), int(h * 0.46)
            self.root.geometry(f"{pw}x{ph}+{x + int(w * 0.835)}+{y + int(h * 0.095)}")
            self.status_lbl.configure(wraplength=pw - 16)
        else:
            sw = self.root.winfo_screenwidth()
            self.root.geometry(f"320x520+{sw - 336}+90")

    def raise_top(self):
        if self.visible:
            user32.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0, 0x2 | 0x1 | 0x10 | 0x40)

    def show_marker(self, idx: int | None):
        self.marker_idx = idx
        hwnd = S.find_game()
        rect = S.game_rect(hwnd) if hwnd else None
        if idx is None or rect is None or self.bot.auto or not self.visible:
            self.marker.withdraw()
            return
        x, y, w, h = rect
        self.marker.update_idletasks()
        mw = self.marker.winfo_reqwidth()
        self.marker.geometry(f"+{x + int(CARD_X[idx] * w) - mw // 2}+{y + int(0.785 * h)}")
        user32.ShowWindow(self.marker_hwnd, 4)
        user32.SetWindowPos(self.marker_hwnd, -1, 0, 0, 0, 0, 0x2 | 0x1 | 0x10 | 0x40)

    # ------------------------------------------------ 渲染

    def render(self):
        auto = self.bot.auto
        self.btn_mode.configure(text="● 自动玩" if auto else "○ 推荐", fg=GOOD if auto else FG)
        self.btn_style.configure(text=f"风格：{self.bot.brain.playstyle}", fg=WARN if self.bot.brain.playstyle == "爽" else ACCENT)
        self.status_lbl.configure(text=self.status)
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")

        if self.choice:
            ch = self.choice
            t.insert("end", f"选{KIND_NAME.get(ch['kind'], '选项')}\n", "sec")
            for i, c in enumerate(ch["cards"]):
                star = "★ " if i == ch["best"] else "   "
                t.insert("end", f"{star}{c['matched'] or '?'}  {c['score']}\n", "best" if i == ch["best"] else "")
                t.insert("end", f"   {c['why']}\n", "dim")
        else:
            t.insert("end", f"{self.now['title']}\n", "sec")
            for line in self.now["lines"]:
                if line:
                    t.insert("end", f"• {line}\n")

        b = self.bot.brain
        hero = b.hero or self.browse
        if hero:
            h = TABLES["hero"].get(hero, {})
            t.insert("end", f"英雄：{hero}" + ("" if b.hero else "（还在挑）") + "\n", "sec")
            if h.get("skill"):
                t.insert("end", f"{h['skill_kind']}「{h['skill']}」：{h.get('skill_info') or h.get('info', '')}\n")
            if h:
                t.insert("end", f"成长：力量+{h.get('STRadd')} 敏捷+{h.get('AGIadd')} 耐力+{h.get('STAadd')}"
                                f"　生命 {h.get('hp')} 法力 {h.get('mp')}\n", "dim")
        else:
            t.insert("end", "英雄\n", "sec")
            t.insert("end", "选好英雄我就给你几个流派\n", "dim")

        if b.hero:
            opts = self.options if self.options_hero == b.hero else None
            if opts:
                if opts.get("hero_tip"):
                    t.insert("end", f"{opts['hero_tip']}\n", "dim")
                t.insert("end", "流派（点一下换）\n", "sec")
                for i, o in enumerate(opts.get("options", [])):
                    tag = f"opt{i}"
                    chosen = i == opts.get("chosen", 0)
                    mark = "✔" if chosen else "○"
                    t.insert("end", f"{mark} {i + 1}. {o['name']} · {o['difficulty']}\n", (tag, "best" if chosen else "opt"))
                    t.insert("end", f"   {o['why_fun']}\n", (tag, "dim"))
                    if chosen:
                        t.insert("end", f"   主动：{'、'.join(o['core_actives'])}\n", (tag,))
                        t.insert("end", f"   被动：{'、'.join(o['core_passives'])}\n", (tag, "dim"))
                        t.insert("end", f"   出装：{' → '.join(o['equip_order'])}\n", (tag, "dim"))
                        for tip in o.get("how_to_play", [])[:3]:
                            t.insert("end", f"   • {tip}\n", (tag, "dim"))
                        if o.get("challenge_tip"):
                            t.insert("end", f"   挑战：{o['challenge_tip']}\n", (tag, "dim"))
                    t.tag_bind(tag, "<Button-1>", lambda e, i=i: self.choose_option(i))
                    t.tag_bind(tag, "<Enter>", lambda e: t.configure(cursor="hand2"))
                    t.tag_bind(tag, "<Leave>", lambda e: t.configure(cursor="arrow"))
            elif self.options_busy:
                t.insert("end", "流派\n", "sec")
                t.insert("end", "AI 正在看英雄技能、配流派…（十几秒）\n", "dim")

        if b.hero and (b.skills or b.relics or self.bot.owned_equips):
            t.insert("end", "本局\n", "sec")
            if b.skills:
                t.insert("end", f"技能：{'、'.join(b.skills)}\n", "dim")
            if b.relics:
                t.insert("end", f"遗物：{'、'.join(b.relics[-6:])}\n", "dim")
            wish = b.equip_wishlist(self.bot.owned_equips)
            owned = "、".join(f"{e}✓" for e in self.bot.owned_equips)
            t.insert("end", f"出装：{owned + ('、' if owned and wish else '')}{' → '.join(wish[:4])}\n", "dim")
        if self.ai_text:
            t.insert("end", "AI 说\n", "sec")
            t.insert("end", self.ai_text + "\n")
        if self.logs:
            t.insert("end", "刚才\n", "sec")
            for line in self.logs[-4:]:
                t.insert("end", f"{line}\n", "dim")
        t.configure(state="disabled")
        self.raise_top()

    # ------------------------------------------------ 操作

    def toggle_auto(self):
        self.bot.auto = not self.bot.auto
        self.bot.prepared_wave = ""
        self.show_marker(None if self.bot.auto else self.marker_idx)
        self.add_log("切到自动玩：交给我" if self.bot.auto else "切到推荐模式：你来点，我出主意")
        self.render()

    def toggle_playstyle(self):
        b = self.bot.brain
        b.playstyle = "稳" if b.playstyle == "爽" else "爽"
        save_cfg_key("playstyle", b.playstyle)
        self.bot.last_choice_key = self.bot.last_advice_key = ""
        self.add_log(f"风格：{b.playstyle}" + ("（挑大奖励、高伤害，输了自动收一收）" if b.playstyle == "爽" else "（求稳，挑战只接最安全的）"))
        self.render()

    def add_log(self, msg: str):
        self.logs.append(f"{datetime.now():%H:%M} {msg}")
        self.logs = self.logs[-20:]

    def _entry_focus(self, _):
        if self.entry.cget("fg") == DIM:
            self.entry.delete(0, "end")
            self.entry.configure(fg=FG)

    def _entry_send(self, _=None):
        q = self.entry.get().strip()
        if not q or self.entry.cget("fg") == DIM:
            return
        self.entry.delete(0, "end")
        self.ask_ai(q)
        hwnd = S.find_game()
        if hwnd:
            user32.SetForegroundWindow(hwnd)  # 问完把焦点还给游戏

    def ask_ai(self, question: str):
        if self.ai_busy:
            return
        self.ai_busy = True
        self.ai_text = f"（想一下：{question}）"
        self.render()
        hwnd = S.find_game()
        frame = S.grab(hwnd) if hwnd else None
        ctx = self.bot.context()
        if self.choice:
            ctx += "\n当前三选一：" + "；".join(f"{c['matched']}（{c['why']}）" for c in self.choice["cards"])

        def work():
            import ai
            try:
                png = ai.png_bytes(frame.img) if frame else None
                ans = ai.answer(question, png, ctx, self.cfg["ai_model"], self.cfg["ai_effort"])
                self.q.put(("ai_answer", ans))
            except Exception as e:
                self.q.put(("ai_answer", f"没问成：{e}"))
        threading.Thread(target=work, daemon=True).start()

    def load_options(self, hero: str, force: bool = False):
        """英雄定下来后：有缓存直接用，没有就让 AI 看英雄技能配 3 个流派。"""
        if not hero or not self.cfg["ai_plan"]:
            return
        import ai
        if not force and self.options_hero == hero and self.options:
            self.apply_option()
            return
        cached = None if force else ai.cached_options(hero)
        if cached:
            self.options, self.options_hero = cached, hero
            self.apply_option()
            return
        if self.options_busy:
            return
        self.options_busy, self.options_hero, self.options = True, hero, None
        playstyle = self.bot.brain.playstyle

        def work():
            try:
                data = ai.build_options(hero, playstyle, self.cfg["ai_model"], self.cfg["ai_effort"])
                self.q.put(("options", (hero, data)))
            except Exception as e:
                self.q.put(("options_fail", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def apply_option(self):
        """选中的流派交给 brain：推荐和自动玩都按它加分、按它出装。"""
        if not self.options or self.options_hero != self.bot.brain.hero:
            return
        opts = self.options.get("options", [])
        i = min(self.options.get("chosen", 0), len(opts) - 1)
        if i >= 0:
            self.bot.brain.ai_plan = opts[i]
            self.bot.last_choice_key = self.bot.last_advice_key = ""

    def choose_option(self, i: int):
        if not self.options:
            return
        import ai
        self.options["chosen"] = i
        ai.save_choice(self.options_hero, self.options)
        self.apply_option()
        self.add_log(f"流派换成：{self.options['options'][i]['name']}")
        self.render()
        hwnd = S.find_game()
        if hwnd:
            user32.SetForegroundWindow(hwnd)  # 点完把焦点还给游戏

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
            time.sleep(self.cfg["tick_seconds"])

    def poll(self):
        changed = False
        try:
            while True:
                kind, val = self.q.get_nowait()
                changed = True
                if kind == "status":
                    if val == self.status:
                        changed = False
                    self.status = val
                elif kind == "log":
                    self.add_log(val)
                elif kind == "now":
                    self.now, self.choice = val, None
                elif kind == "choice":
                    self.choice = val
                elif kind == "marker":
                    if val != self.marker_idx or val is None:
                        self.show_marker(val)
                    changed = False
                elif kind == "build" or kind == "mode":
                    pass
                elif kind == "browse":
                    if val == self.browse:
                        changed = False
                    self.browse = val
                elif kind in ("hero", "new_run"):
                    if kind == "new_run":
                        self.ai_text = ""
                    self.browse = ""
                    self.load_options(val)
                elif kind == "options":
                    hero, data = val
                    self.options_busy = False
                    if hero == self.bot.brain.hero:
                        self.options, self.options_hero = data, hero
                        self.apply_option()
                        self.add_log(f"{hero} 的 {len(data.get('options', []))} 个流派好了，默认第 1 个，点一下可换")
                elif kind == "options_fail":
                    self.options_busy = False
                    self.add_log(f"AI 配流派没成：{val[:40]}")
                elif kind == "ai_answer":
                    self.ai_text, self.ai_busy = val, False
                elif kind == "key":
                    {"auto": self.toggle_auto, "playstyle": self.toggle_playstyle,
                     "ask": lambda: self.ask_ai("现在该怎么选、怎么做？"), "replan": lambda: self.load_options(self.bot.brain.hero, force=True),
                     "hide": self.toggle_hide}[val]()
        except queue.Empty:
            pass
        if changed:
            self.render()
        self.root.after(150, self.poll)

    def toggle_hide(self):
        self.visible = not self.visible
        if self.visible:
            self.restore_panel()
        else:
            self.root.withdraw()
            self.marker.withdraw()

    def restore_panel(self):
        """用 Tk 的 deiconify 恢复，然后把焦点还给原来的窗口（通常是游戏）。"""
        fg = user32.GetForegroundWindow()
        self.root.deiconify()
        self.root.update_idletasks()
        if fg and fg != self.hwnd:
            user32.SetForegroundWindow(fg)

    def follow_game(self):
        # 游戏切全屏时可能把面板一起最小化（左下角变成一个小标题条），发现了就恢复
        if self.visible and (user32.IsIconic(self.hwnd) or self.root.state() != "normal"):
            self.restore_panel()
        self.place_panel()
        self.raise_top()
        self.root.after(3000, self.follow_game)

    def run(self):
        threading.Thread(target=self.worker, daemon=True).start()
        threading.Thread(target=hotkey_thread, args=(self.cfg["hotkeys"], self.q), daemon=True).start()
        if "--debug" in sys.argv:
            import faulthandler
            faulthandler.dump_traceback_later(20, repeat=True, file=open(ROOT / "data" / "stacks.txt", "w"))
        self.poll()
        self.follow_game()
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
