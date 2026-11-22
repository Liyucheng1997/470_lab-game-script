"""出主意：三选一选哪张、商店买什么，每个选项都给出理由。

全部本地规则，用游戏自己的数值表，毫秒级出结果；AI 给的本局路线（ai_plan）会额外加分，自动玩也跟着走。

玩法风格：
- 爽：清屏快、伤害高、奖励大。挑战按「敢接多大的危险」挑奖励最好的，赢了胆子变大，输了收一收。
- 稳：一切求稳，挑战只接最安全的。
"""
from __future__ import annotations

import difflib
import json
import math
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TABLES = json.loads((ROOT / "tables.json").read_text(encoding="utf-8"))
GRADE = {"D": 1, "C": 2, "B": 3, "A": 4, "S": 5}
RARITY_WORDS = {"普通": 1, "稀有": 2, "史诗": 3, "传说": 4, "神话": 5}

MAGIC_WORDS = ["法术", "技能", "冷却", "急速", "法力", "蓝", "魔法", "持续时间", "范围"]
PHYS_WORDS = ["攻击", "暴击", "攻速", "攻击速度", "力量", "敏捷", "吸血", "生命偷取", "分裂", "多重"]
DEFENSE_WORDS = {"护甲": 0.6, "生命": 0.6, "减伤": 0.8, "伤害减少": 0.8, "回复": 0.5, "闪避": 0.5}
GREED_WORDS = {"金钱": 1.5, "金币": 1.2, "经验": 1.0, "幸运": 0.8}
POWER_WORDS = {"三围": 2.0, "全属性": 2.0}
ELEMENT_WORDS = {"Fire": "火焰", "Ice": "冰冻", "Lightning": "雷电"}
STYLE_NAME = {"magic": "法术流", "physical": "物理流", "summon": "召唤流"}

# 挑战：危险系数和奖励（游戏 shop 表 monster_*；卡片上读到的危险系数优先）
CHALLENGE_DANGER = {"怪物群1级": "D", "怪物群2级": "D", "怪物群3级": "D", "怪物群4级": "D", "哥布林飞艇": "D",
                    "金币哥布林": "A", "巨魔王": "S", "心魔": "SSS", "哥布林赌徒": "A", "赌徒": "SSS", "塞亚": "SSS",
                    "国王驾到": "SSS"}
DANGER_RANK = {"D": 1, "C": 2, "B": 3, "A": 4, "S": 5, "SS": 6, "SSS": 7}
REWARD_WORDS = {"S级": 3, "技能书": 3, "悬赏令": 3, "纹章": 2, "三维书": 2, "属性书": 2, "大量金币": 2, "金币": 1,
                "刷新": 1, "掉落遗物": 2, "遗物": 1}

# 只放商店前 4 行（不用滚动）就能买到的装备
EQUIP_PRIORITY = {
    "magic": ["灭世之帽", "圣杖之证", "女神之泪", "溶解之杖", "巫师手套", "圣者的项链"],
    "physical": ["无尽之刃", "破败王者之刃", "狂战斧", "饮血剑", "穿甲弓", "圣者的项链"],
    "summon": ["圣杖之证", "女神之泪", "圣者的项链", "生命之戒", "灭世之帽", "钢铁之心"],
}
EQUIP_INFO = {k: v["info"] for k, v in TABLES["equip"].items()}
EQUIP_INFO["破败王者之刃"] = EQUIP_INFO.get("破败之刃", "")


def fuzzy(name: str, pool, cutoff: float = 0.5) -> str | None:
    """OCR 读出来的名字和游戏表里的名字对上（容忍错一两个字）。"""
    name = re.sub(r"[^一-鿿A-Za-z0-9·！!]", "", name)
    if not name:
        return None
    if name in pool:
        return name
    m = difflib.get_close_matches(name, list(pool), n=1, cutoff=cutoff)
    return m[0] if m else None


def base_skill(name: str) -> str:
    """D闪电 / C闪电 … 去掉品级字母，方便和 AI 路线里的名字对上。"""
    return re.sub(r"^[DCBAST](?=[一-鿿])", "", name or "")


class Brain:
    def __init__(self, playstyle: str = "爽"):
        self.playstyle = playstyle
        self.reset("")

    def reset(self, hero: str):
        self.hero = hero
        self.tags: Counter = Counter()
        self.skills: list[str] = []
        self.relics: list[str] = []
        self.risk = 3                 # 爽模式下敢接的挑战危险等级（D=1 … SSS=7），会随输赢变化
        self.ai_plan: dict = {}       # AI 给的本局路线

    # ------------------------------------------------ 流派

    @property
    def style(self) -> str:
        plan_style = self._plan_style()
        if plan_style:
            return plan_style
        if self.tags["Summon"] >= 2:
            return "summon"
        magic, phys = self.tags["Magic"], self.tags["Physical"]
        if magic or phys:
            return "magic" if magic >= phys else "physical"
        h = TABLES["hero"].get(self.hero) or {}
        if re.search(r"法|魔|祭|诗|巫|结界", self.hero):
            return "magic"
        try:
            return "physical" if float(h.get("STRadd") or 0) > float(h.get("AGIadd") or 0) + 3 else "magic"
        except ValueError:
            return "magic"

    def _plan_style(self) -> str:
        """选了 AI 流派就按流派走：看它的核心主动技能是法术、物理还是召唤居多。"""
        plan = self.ai_plan or {}
        name = plan.get("name", "")
        if "召唤" in name:
            return "summon"
        if any(w in name for w in ("法", "魔", "术")):
            return "magic"
        if any(w in name for w in ("物理", "暴击", "攻速", "力量", "近战")):
            return "physical"
        votes = Counter()
        for skill in plan.get("core_actives", []):
            for full, e in TABLES["active"].items():
                if base_skill(full) == skill:
                    votes.update(t for t in e["tags"] if t in ("Magic", "Physical", "Summon"))
                    break
        if not votes:
            return ""
        top = votes.most_common(1)[0][0]
        return {"Magic": "magic", "Physical": "physical", "Summon": "summon"}[top]

    def learn(self, kind: str, name: str):
        if kind == "relic":
            if name not in self.relics:
                self.relics.append(name)
            return
        entry = TABLES["active" if kind == "active" else "passive"].get(name)
        if entry and name not in self.skills:
            self.tags.update(entry["tags"])
            self.skills.append(name)

    def challenge_result(self, cleared: bool, danger: int):
        """挑战打过了就更敢，输了就收一收。"""
        if cleared:
            self.risk = min(7, max(self.risk, danger) + 1)
        else:
            self.risk = max(1, min(self.risk, danger) - 2)

    # ------------------------------------------------ AI 路线加分

    def _ai_bonus(self, kind: str, name: str, desc: str = "") -> tuple[float, str]:
        plan = self.ai_plan or {}
        key = {"active": "core_actives", "passive": "core_passives", "relic": "relic_focus"}.get(kind)
        wanted = plan.get(key) or []
        if not (name or desc) or not wanted:
            return 0, ""
        b = base_skill(name)
        if any(w and (w == name or w == b or w in name or (kind == "relic" and w in desc)) for w in wanted):
            return 4.0, "AI 路线核心"
        return 0, ""

    # ------------------------------------------------ 打分（返回 分数, 理由）

    def _text_score(self, text: str) -> tuple[float, list[str]]:
        style, why, s = self.style, [], 0.0
        good = MAGIC_WORDS if style in ("magic", "summon") else PHYS_WORDS
        bad = PHYS_WORDS if style in ("magic", "summon") else MAGIC_WORDS
        hit = [w for w in good if w in text]
        if hit:
            s += 1.5 * len(hit)
            why.append(f"契合{STYLE_NAME[style]}")
        miss = [w for w in bad if w in text and w not in good]
        if miss and not hit:
            s -= 0.6 * len(miss)
            why.append(f"不太适合{STYLE_NAME[style]}")
        for w, v in POWER_WORDS.items():
            if w in text:
                s += v
                why.append("全属性提升")
                break
        greed = [w for w in GREED_WORDS if w in text]
        if greed:
            s += sum(GREED_WORDS[w] for w in greed) * (1.3 if self.playstyle == "爽" else 1.0)
            why.append("赚钱 / 成长快")
        defense = [w for w in DEFENSE_WORDS if w in text]
        if defense:
            s += sum(DEFENSE_WORDS[w] for w in defense) * (0.7 if self.playstyle == "爽" else 1.6)
            why.append("保命")
        for tag, word in ELEMENT_WORDS.items():
            if word in text and self.tags[tag]:
                s += 1.5 + 0.5 * self.tags[tag]
                why.append(f"加强已有的{word}技能")
        if style == "summon" and "召唤" in text:
            s += 2
            why.append("召唤流核心")
        return s, why

    def score_active(self, name: str) -> tuple[float, list[str]]:
        e = TABLES["active"].get(name)
        if not e:
            return 0, ["没认出这个技能"]
        why = []
        dps = e["dps60"]
        s = math.log10(max(dps, 1000)) * 3 + GRADE.get(e["quality"], 0) * 1.5
        why.append(f"60秒伤害约 {dps / 10000:.0f} 万" if dps >= 10000 else "伤害一般")
        s += math.log10(max(e["mana_eff"], 1)) * 0.8
        tags = set(e["tags"])
        for t, word in (("Fire", "火"), ("Ice", "冰"), ("Lightning", "雷"), ("Summon", "召唤")):
            if t in tags and self.tags[t]:
                s += 1.5 + 0.5 * self.tags[t]
                why.append(f"和已有{word}系技能叠加")
        if "Area" in tags:
            s += 1.5 * (1.4 if self.playstyle == "爽" else 1.0)
            why.append("范围清怪")
        if self.playstyle == "爽" and tags & {"Duration", "Control"}:
            s += 0.5
        if "Heal" in tags and not any("Heal" in TABLES["active"].get(k, {}).get("tags", []) for k in self.skills):
            s += 1.0 if self.playstyle == "爽" else 2.0
            why.append("第一个回血技能")
        if self.style == "physical" and "Magic" in tags and "Physical" not in tags:
            s -= 1
            why.append("物理流用法术技能吃不满加成")
        return s, why

    def score_passive(self, name: str) -> tuple[float, list[str]]:
        e = TABLES["passive"].get(name)
        if not e:
            return 0, ["没认出这个被动"]
        s, why = self._text_score(e["info"] + name)
        s += GRADE.get(e["quality"], 0) * 1.5
        tags = set(e["tags"])
        if self.style in ("magic", "summon") and "Magic" in tags or self.style == "physical" and "Physical" in tags:
            s += 2
            if f"契合{STYLE_NAME[self.style]}" not in why:
                why.append(f"契合{STYLE_NAME[self.style]}")
        if "Defense" in tags:
            s += 0.5 if self.playstyle == "爽" else 1.5
        return s, why

    def score_other(self, kind: str, name: str | None, desc: str, rarity_text: str) -> tuple[float, list[str]]:
        s, why = self._text_score((name or "") + desc)
        if kind == "relic" and name in TABLES["relic"]:
            g = TABLES["relic"][name]["grade"]
            s += GRADE.get(g, 0) * 1.2
        rare = max(((v, w) for w, v in RARITY_WORDS.items() if w in rarity_text), default=(0, ""))
        if rare[0]:
            s += rare[0] * 1.2
            if rare[0] >= 3:
                why.insert(0, rare[1])
        nums = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)", desc)]
        if nums and "%" in desc:
            s += min(max(nums) / 40, 1.5)
        return s, why

    def score_challenge(self, name: str, desc: str) -> tuple[float, list[str], int]:
        m = re.search(r"危险系数[:：]?\s*([DCBAS]+)", desc)
        known = fuzzy(name, CHALLENGE_DANGER, 0.6)
        letter = m.group(1) if m else CHALLENGE_DANGER.get(known, "B")
        danger = DANGER_RANK.get(letter, 3)
        reward = sum(v for w, v in REWARD_WORDS.items() if w in desc)
        if known == "哥布林飞艇":
            reward += 3
        if known and known.startswith("怪物群"):
            reward += 0.5
        why = [f"危险 {letter}"]
        if reward >= 3:
            why.append("奖励丰厚")
        if self.playstyle == "稳":
            return -danger * 10 + reward * 0.3, why, danger
        # 爽：在敢接的范围内挑奖励最高的；超出范围的大幅扣分
        if danger <= self.risk:
            s = reward * 3 + danger
            why.append("打得过，冲")
        else:
            s = reward - (danger - self.risk) * 12
            why.append("现在接有点悬")
        return s, why, danger

    # ------------------------------------------------ 三选一

    @staticmethod
    def infer_kind(banner: str, cards: list[dict]) -> str:
        if "挑战" in banner or any(fuzzy(c.get("name", ""), CHALLENGE_DANGER, 0.6) for c in cards):
            return "challenge"
        if "遗" in banner or "溃" in banner:
            return "relic"
        if "主动" in banner:
            return "active"
        if "被动" in banner:
            return "passive"
        if "属性" in banner:
            return "attribute"
        votes = Counter()
        for c in cards:
            n = c.get("name", "")
            for kind in ("active", "passive", "relic"):
                if n and fuzzy(n, TABLES[kind]):
                    exact = re.sub(r"[^一-鿿A-Z]", "", n) in TABLES[kind]
                    votes[kind] += 2 if exact else 1
        return votes.most_common(1)[0][0] if votes else "other"

    def pick(self, kind: str, cards: list[dict]) -> int:
        """给每张卡打分、写理由（c['score'], c['why']），返回推荐第几张。"""
        pool = {"active": TABLES["active"], "passive": TABLES["passive"], "relic": TABLES["relic"],
                "challenge": CHALLENGE_DANGER}.get(kind, {})
        for c in cards:
            name = fuzzy(c.get("name", ""), pool, 0.6 if kind == "challenge" else 0.5) if pool else None
            c["matched"] = name or c.get("name", "")
            if kind == "challenge":
                s, why, c["danger"] = self.score_challenge(c.get("name", ""), c.get("desc", ""))
            elif kind == "active" and name:
                s, why = self.score_active(name)
            elif kind == "passive" and name:
                s, why = self.score_passive(name)
            else:
                s, why = self.score_other(kind, name, c.get("desc", ""), c.get("rarity", ""))
            bonus, tip = self._ai_bonus(kind, name or "", c.get("desc", ""))
            if bonus:
                s += bonus
                why.insert(0, tip)
            c["score"] = round(s, 1)
            c["why"] = "，".join(dict.fromkeys(why)) or "没什么特别"
        return max(range(len(cards)), key=lambda i: cards[i]["score"])

    # ------------------------------------------------ 出装 / 商店

    def equip_wishlist(self, owned: list[str]) -> list[str]:
        ai = [e for e in (self.ai_plan or {}).get("equip_order", []) if e in EQUIP_INFO]
        order = list(dict.fromkeys(ai + EQUIP_PRIORITY[self.style]))
        return [e for e in order if e not in owned]

    def shop_advice(self, skulls: int, gold: int, free_skill: int, free_equip: int, bag_items: int,
                    owned: list[str]) -> list[str]:
        """波次之间该干什么，给人看的建议。"""
        tips = []
        if bag_items:
            tips.append(f"背包里有 {bag_items} 件东西（技能书 / 锻造器），双击用掉")
        price = {"A": 30, "B": 10, "C": 5, "D": 1}
        grade = next((g for g in "ABCD" if skulls >= price[g]), None)
        if grade:
            kind = "主动" if free_skill > 0 else "被动"
            tips.append(f"骷髅币 {skulls}：买 {grade} 级{kind}书" + (f"（主动格还空 {free_skill} 个）" if free_skill else "（主动格满了）"))
        wish = self.equip_wishlist(owned)
        if free_equip and wish:
            nxt = wish[0]
            info = EQUIP_INFO.get(nxt, "")
            if gold >= 2500:
                tips.append(f"金币 {gold}：买「{nxt}」{('—' + info) if info else ''}")
            else:
                tips.append(f"攒钱买「{nxt}」，还差 {2500 - gold}")
        elif not free_equip:
            tips.append("装备格满了")
        left = gold - (2500 if free_equip and wish and gold >= 2500 else 0)
        if left >= 450 and (not free_equip or not wish or gold < 2500 or left >= 950):
            tips.append(f"剩下的金币买属性锻造器（约 {max(left // 450 - 1, 1)} 个）")
        if self.playstyle == "爽":
            letters = {v: k for k, v in DANGER_RANK.items()}
            tips.append(f"挑战：敢接危险 ≤ {letters.get(self.risk, 'B')} 的，挑奖励大的")
        else:
            tips.append("挑战：只接最安全的")
        tips.append("准备好了点「开始冒险」")
        return tips
