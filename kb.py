"""攻略知识库：每个英雄的技能玩法、3 个常见流派（含长线规划和自动战斗参数）、技能 / 装备 / 地图说明。

数据在 knowledge/*.json（自己写的攻略，不是游戏文件）；名字必须和游戏数值表 tables.json 里的写法一致。
检查格式：  .venv\\Scripts\\python.exe kb.py --check [文件…]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KDIR = ROOT / "knowledge"

# 商店装备页前几行（按位置买，所以出装只能从这里选）
SHOP_EQUIPS = ["无尽之刃", "狂战斧", "饮血剑", "破败王者之刃", "穿甲弓", "灭世之帽", "溶解之杖", "痛苦面具", "日炎斗篷",
               "狂徒铠甲", "振奋铠甲", "圣杖之证", "巫师手套", "女神之泪", "钢铁之心", "力量之戒", "迅捷之戒",
               "生命之戒", "圣者的项链", "忍者刀鞘", "抗魔斗篷"]

# 自动战斗时英雄技能 / 主动技能怎么放
AUTO_RULES = {
    "passive": "被动，不用按",
    "on_cd": "身边有怪就放，冷却好了就放",
    "aoe_cluster": "等怪聚成一堆（≥min_enemies 只）再朝怪堆放",
    "dash_to_cluster": "位移技能：朝怪最密的方向放，冲进去打",
    "buff_fight": "增益 / 变身：怪多或打 BOSS 时开",
    "heal_low": "血量低于 hp_below 时放",
    "summon": "召唤物：冷却好了就放",
    "charge": "按住蓄力 hold 秒再松开，朝怪堆",
    "escape": "保命位移 / 无敌：血低或被围时朝人少的方向放",
    "boss": "留给 BOSS / 精英",
    "manual": "需要玩家自己操作（小游戏、弹窗选择等），自动玩不放",
}
ROLES = {"aoe", "single", "dot", "summon", "buff", "heal", "escape", "control", "utility", "execute"}
ENGAGE = {"dive", "kite", "hold"}
STYLES = {"magic", "physical", "summon"}
DIFFICULTY = {"简单", "中等", "难"}

_tables = None


def tables() -> dict:
    global _tables
    if _tables is None:
        _tables = json.loads((ROOT / "tables.json").read_text(encoding="utf-8"))
    return _tables


def base_skill(name: str) -> str:
    return re.sub(r"之[一二三四五]$", "", re.sub(r"^[DCBAST](?=[一-鿿])", "", name or ""))


def book_actives() -> set[str]:
    """能从技能书学到的主动技能（不含英雄专属）。"""
    md = (ROOT / "game_data.md").read_text(encoding="utf-8")
    sec = md.split("## 主动技能", 1)[1].split("## 英雄专属", 1)[0]
    return {m.group(1) for m in re.finditer(r"^- (.+?)（", sec, re.M)}


def all_actives() -> set[str]:
    return {base_skill(n) for n in tables()["active"]}


# ---------------------------------------------------------------- 读取

class KB:
    def __init__(self):
        self.heroes: dict[str, dict] = {}     # id → 英雄攻略
        self.by_name: dict[str, str] = {}     # 任一名字 → id
        self.skills: dict = {"active": {}, "passive": {}, "equip": {}}
        self.maps: dict = {}
        self.reload()

    def reload(self):
        self.heroes.clear()
        self.by_name.clear()
        for f in sorted(KDIR.glob("heroes_*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"{f.name} 读不了：{e}")
                continue
            for hid, h in data.items():
                self.heroes[hid] = h
                for n in h.get("names", []):
                    self.by_name[n] = hid
        for name, h in tables()["hero"].items():   # 游戏表里的别名也能查到
            hid = str(h.get("id"))
            if hid in self.heroes:
                self.by_name.setdefault(name, hid)
        for key, fname in (("skills", "skills.json"), ("maps", "maps.json")):
            try:
                setattr(self, key, json.loads((KDIR / fname).read_text(encoding="utf-8")))
            except Exception:
                pass

    def hero(self, name: str) -> dict | None:
        hid = self.by_name.get(name)
        return self.heroes.get(hid) if hid else None

    def active_note(self, name: str) -> dict:
        return self.skills.get("active", {}).get(base_skill(name), {})

    def passive_note(self, name: str) -> str:
        return self.skills.get("passive", {}).get(name, "")

    def equip_note(self, name: str) -> str:
        return self.skills.get("equip", {}).get(name, "")


# ---------------------------------------------------------------- 检查

def _str(errs, where, v, limit=None, required=True):
    if not isinstance(v, str) or (required and not v.strip()):
        errs.append(f"{where}：应为非空文字")
    elif limit and len(v) > limit:
        errs.append(f"{where}：超过 {limit} 字（{len(v)}）")


def check_heroes(data: dict) -> list[str]:
    T = tables()
    errs = []
    books = book_actives()
    passives, relics = set(T["passive"]), set(T["relic"])
    hero_names = set(T["hero"])
    for hid, h in data.items():
        w = f"英雄 {hid}"
        if not re.fullmatch(r"\d+", hid):
            errs.append(f"{w}：键应为英雄 id 数字")
        names = h.get("names") or []
        if not names or any(n not in hero_names for n in names):
            errs.append(f"{w}：names 必须是 tables.json 里的英雄名，得到 {names}")
        _str(errs, f"{w}.role", h.get("role"), 30)
        if h.get("range") not in ("melee", "ranged"):
            errs.append(f"{w}.range 应为 melee / ranged")
        sk = h.get("skill") or {}
        _str(errs, f"{w}.skill.name", sk.get("name"))
        if sk.get("kind") not in ("主动", "被动"):
            errs.append(f"{w}.skill.kind 应为 主动 / 被动")
        _str(errs, f"{w}.skill.how", sk.get("how"), 70)
        auto = sk.get("auto") or {}
        if auto.get("rule") not in AUTO_RULES:
            errs.append(f"{w}.skill.auto.rule 应为 {list(AUTO_RULES)}")
        for t in h.get("tips", []):
            _str(errs, f"{w}.tips", t, 32)
        builds = h.get("builds") or []
        if len(builds) != 3:
            errs.append(f"{w}：builds 要正好 3 个")
        for i, b in enumerate(builds):
            bw = f"{w}.builds[{i}]"
            _str(errs, f"{bw}.name", b.get("name"), 10)
            _str(errs, f"{bw}.tagline", b.get("tagline"), 32)
            if b.get("difficulty") not in DIFFICULTY:
                errs.append(f"{bw}.difficulty 应为 {DIFFICULTY}")
            if b.get("style") not in STYLES:
                errs.append(f"{bw}.style 应为 {STYLES}")
            for a in b.get("core_actives", []):
                if a not in books:
                    errs.append(f"{bw}.core_actives：「{a}」不是技能书能学到的主动技能")
            for p in b.get("core_passives", []):
                if p not in passives:
                    errs.append(f"{bw}.core_passives：「{p}」不在被动技能表")
            for e in b.get("equip_order", []):
                if e not in SHOP_EQUIPS:
                    errs.append(f"{bw}.equip_order：「{e}」不在商店装备列表")
            for r in b.get("relic_focus", []):
                if r not in relics:
                    errs.append(f"{bw}.relic_focus：「{r}」不在遗物表")
            if not (2 <= len(b.get("core_actives", [])) <= 5 and 2 <= len(b.get("core_passives", [])) <= 6
                    and 3 <= len(b.get("equip_order", [])) <= 6 and 2 <= len(b.get("relic_focus", [])) <= 6):
                errs.append(f"{bw}：核心主动 2~5、被动 2~6、出装 3~6、遗物 2~6 个")
            _str(errs, f"{bw}.stats", b.get("stats"), 20)
            plan = b.get("plan") or {}
            for k in ("early", "mid", "late"):
                _str(errs, f"{bw}.plan.{k}", plan.get(k), 45)
            hp = b.get("how_to_play") or []
            if not 2 <= len(hp) <= 4:
                errs.append(f"{bw}.how_to_play 要 2~4 条")
            for t in hp:
                _str(errs, f"{bw}.how_to_play", t, 32)
            _str(errs, f"{bw}.challenge_tip", b.get("challenge_tip"), 36)
            c = b.get("combat") or {}
            if c.get("engage") not in ENGAGE:
                errs.append(f"{bw}.combat.engage 应为 {ENGAGE}")
            d = c.get("distance")
            if not isinstance(d, (int, float)) or not 0 <= d <= 0.35:
                errs.append(f"{bw}.combat.distance 应为 0~0.35 的数")
    return errs


def check_skills(data: dict) -> list[str]:
    errs = []
    T = tables()
    acts = all_actives() | book_actives()
    for n, v in data.get("active", {}).items():
        if n not in acts:
            errs.append(f"active「{n}」不在主动技能表（去掉品级字母）")
        if v.get("role") not in ROLES:
            errs.append(f"active「{n}」.role 应为 {ROLES}")
        if v.get("auto") not in AUTO_RULES:
            errs.append(f"active「{n}」.auto 应为 {list(AUTO_RULES)}")
        _str(errs, f"active「{n}」.note", v.get("note"), 45)
    for n, v in data.get("passive", {}).items():
        if n not in T["passive"]:
            errs.append(f"passive「{n}」不在被动技能表")
        _str(errs, f"passive「{n}」", v, 36)
    for n, v in data.get("equip", {}).items():
        if n not in SHOP_EQUIPS:
            errs.append(f"equip「{n}」不在商店装备列表")
        _str(errs, f"equip「{n}」", v, 40)
    missing = book_actives() - set(data.get("active", {}))
    if missing:
        errs.append(f"还缺这些技能书主动技能：{sorted(missing)}")
    return errs


def main(argv):
    files = [Path(a) for a in argv if not a.startswith("--")] or sorted(KDIR.glob("*.json"))
    bad = 0
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"✗ {f.name}：JSON 格式错误 {e}")
            bad += 1
            continue
        if f.name.startswith("heroes_"):
            errs = check_heroes(data)
        elif f.name == "skills.json":
            errs = check_skills(data)
        else:
            errs = []
        for e in errs:
            print(f"✗ {f.name}：{e}")
        print(f"{'✓' if not errs else '✗'} {f.name}：{len(errs)} 个问题")
        bad += len(errs)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
