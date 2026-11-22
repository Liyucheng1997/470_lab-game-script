"""从游戏自带的多语言表里提取英雄、技能、遗物、卡牌、装备等资料，生成 game_data.md 给军师用。

游戏更新后重新运行一次：  .venv\\Scripts\\python.exe build_game_data.py
（只读游戏文件，不做任何修改）
"""
from __future__ import annotations

import re
import sys
from collections import OrderedDict
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "game_data.md"
TABLE = Path("TranslationOverride") / "多语言表_参考_请勿修改.xlsx"
TIERS = "DCBAS"


def find_game_dir() -> Path:
    """在各个 Steam 库里找「地下城竞技场」的安装目录。"""
    libs = []
    for steam in (Path(r"C:\Program Files (x86)\Steam"), Path(r"D:\Steam")):
        vdf = steam / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            libs += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="ignore"))]
    libs += [Path(f"{d}:\\SteamLibrary") for d in "CDEFGH"]
    for lib in libs:
        game = lib / "steamapps" / "common" / "地下城竞技场"
        if (game / TABLE).exists():
            return game
    raise SystemExit("没找到游戏目录，请把游戏安装目录作为参数传进来：python build_game_data.py <游戏目录>")


def clean(text) -> str:
    text = re.sub(r"</?color[^>]*>", "", str(text or ""))
    text = re.sub(r"\{\d+\}", "X", text)                 # 数值在游戏数据里，表里只有占位符
    text = re.sub(r"\s*\n\s*", " / ", text.strip())
    return re.sub(r"\s{2,}", " ", text)


def load_sheets(path: Path) -> dict[str, "OrderedDict[str, str]"]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheets = {}
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        hdr = next((i for i, r in enumerate(rows[:10]) if any(str(c).strip() in ("简体", "中文") for c in r if c)), None)
        if hdr is None:
            continue
        zh = next(j for j, c in enumerate(rows[hdr]) if c and str(c).strip() in ("简体", "中文"))
        data = OrderedDict()
        for r in rows[hdr + 1:]:
            if len(r) > zh and r[zh] not in (None, "") and r[zh - 1] not in (None, ""):
                data[str(r[zh - 1]).strip()] = clean(r[zh])
        sheets[ws.title.replace("language_", "")] = data
    return sheets


def tier_of(id_: str) -> str:
    m = re.search(r"_(\d+)$", id_)
    n = int(m.group(1)) // 100 if m else 0
    return TIERS[n] if n < len(TIERS) else ""


def pairs(names: dict, descs: dict, prefix: str, skip=lambda k: False):
    """名字表 + 描述表（id_m / id_info）配对。"""
    for k, name in names.items():
        if not k.startswith(prefix) or k.endswith(("_m", "_info", "_total")) or skip(k):
            continue
        desc = descs.get(k + "_m") or descs.get(k + "_info") or names.get(k + "_m") or names.get(k + "_info") or ""
        yield k, name, desc


def grouped(items, strip_tier_letter: bool):
    """同名技能有 D~S 多个品级，合并成一条。"""
    out = OrderedDict()
    for k, name, desc in items:
        base = re.sub(r"^[DCBAST](?=[^\x00-\x7f])", "", name) if strip_tier_letter else name
        base = re.sub(r"之[一二三四五]$", "", base)
        e = out.setdefault(base, {"desc": desc, "tiers": []})
        t = tier_of(k)
        if t and t not in e["tiers"]:
            e["tiers"].append(t)
        if not e["desc"]:
            e["desc"] = desc
    return out


def main():
    game = Path(sys.argv[1]) if len(sys.argv) > 1 else find_game_dir()
    s = load_sheets(game / TABLE)
    base, hero = s.get("base", {}), s.get("hero", {})
    L = ["# 地下城竞技场 游戏资料（从游戏文件自动提取）", "",
         "说明：X 表示具体数值（随品级 / 等级变化，表里没有）。技能品级从低到高 D < C < B < A < S。", ""]

    L += ["## 基础规则",
          f"- {base.get('str_m', '力量：增加攻击力；敏捷：增加攻速和移速；耐力：增加最大生命')}",
          "- 在限定时间内守住地下城，压力值不能超过 100%，超过即失败。",
          "- 地图：哥布林王国（难度 0~5，另有狂欢 1~3）、亡灵废墟（难度 1~3）。",
          "- 狂欢模式可以调刷怪数量、小怪掉落、金币、经验、敌人上限 / 攻击 / 血量 / 护甲成长、怪物词条等倍率（不能上排行榜），最适合单人爽玩。",
          "- 技能书（主动 / 被动，D~S 级）、属性锻造器、装备强化券、遗物、卡牌（局外养成，携带有上限）。", ""]

    L += ["## 英雄（名字：特性）"]
    for k, name, desc in pairs(hero, hero, "hero_", skip=lambda k: not re.fullmatch(r"hero_\d+", k)):
        L.append(f"- {name}：{desc}")
    L.append("")

    act, actinfo = s.get("active", {}), s.get("activeinfo", {})
    items = list(pairs(act, actinfo, "a_"))
    common = [(k, n, d) for k, n, d in items if int(re.search(r"\d+", k).group()) < 500]
    exclusive = [(k, n, d) for k, n, d in items if int(re.search(r"\d+", k).group()) >= 500]
    L += ["## 主动技能（技能书，名字（可出现的品级）：效果）"]
    for name, e in grouped(common, True).items():
        L.append(f"- {name}（{''.join(e['tiers'])}）：{e['desc']}")
    L += ["", "## 英雄专属 / 特殊主动技能"]
    for name, e in grouped(exclusive, False).items():
        L.append(f"- {name}：{e['desc']}")
    L.append("")

    ps, psinfo = s.get("passskill", {}), s.get("passskillinfo", {})
    L += ["## 被动技能（名字（品级）：效果）"]
    for name, e in grouped(pairs(ps, psinfo, "p_"), False).items():
        L.append(f"- {name}（{''.join(e['tiers'])}）：{e['desc']}")
    L.append("")

    def simple(title, sheet, prefix, skip=lambda k: False):
        data = s.get(sheet, {})
        rows = [(n, d) for _, n, d in pairs(data, data, prefix, skip)]
        seen, lines = set(), []
        for n, d in rows:
            if (n, d) in seen:
                continue
            seen.add((n, d))
            lines.append(f"- {n}：{d}" if d else f"- {n}")
        if lines:
            L.extend([f"## {title}"] + lines + [""])

    simple("遗物 / 道具", "item", "pickitem_")
    simple("装备", "equip", "equip_")
    simple("卡牌（局外）", "cards", "card_")
    simple("属性锻造", "forging", "forging_")
    simple("怪物词条（敌人身上的词缀）", "enemyEntries", "enemyEntries_")
    enemy = s.get("enemy", {})
    if enemy:
        L += ["## 敌人", "- " + "、".join(dict.fromkeys(v for k, v in enemy.items() if not k.endswith(("_m", "_info")))), ""]

    OUT.write_text("\n".join(L), encoding="utf-8")
    print(f"已生成 {OUT.name}：{len(L)} 行，{OUT.stat().st_size // 1024} KB（来自 {game}）")
    build_tables(game, hero)


# ---------------------------------------------------------------- 自动玩用的数值表

CONFIG_TABLES = ["activeSkill", "passsiveSkill", "remains", "equipment", "hero"]
TABLES_OUT = ROOT / "tables.json"


def _rows(raw: bytes) -> list[dict]:
    """游戏配置表：第 1 行字段名，第 2~4 行类型 / 默认值 / 中文说明，之后是数据。"""
    import io
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    keys = [str(k).strip() if k else "" for k in rows[0]]
    out = []
    for r in rows[4:]:
        if not r or r[0] in (None, "") or str(r[0]).startswith("*"):
            continue
        d = {k: (v if v is not None else "") for k, v in zip(keys, r) if k}
        out.append(d)
    return out


def build_tables(game: Path, lang_hero: dict | None = None):
    """从 resources.assets 里读游戏自己的配置表（技能伤害、品级、标签、装备属性、英雄成长）。"""
    import json
    import UnityPy
    env = UnityPy.load(str(game / "DungeonBrawl_Data" / "resources.assets"))
    raw = {}
    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        ta = obj.read()
        name = ta.m_Name.removesuffix(".xlsx")
        if name in CONFIG_TABLES:  # 只读这几张配置表
            data = ta.m_Script
            raw[name] = data if isinstance(data, (bytes, bytearray)) else data.encode("utf-8", "surrogateescape")
    t = {name: _rows(b) for name, b in raw.items()}

    def num(v, default=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    actives = {}
    for r in t.get("activeSkill", []):
        name = str(r.get("name", "")).strip()
        if not name:
            continue
        # 照策划表里的公式算：单次 = 基础 + 倍率×1000 属性，总伤害 = 单次 × (持续/间隔)，60 秒 DPS = 总伤害 × 60/冷却
        per_hit = num(r.get("damageBase")) + num(r.get("damageValue")) * 1000
        interval, duration, cd = num(r.get("interval")), num(r.get("duration")), max(num(r.get("cd")), 0.5)
        hits = max(int(duration / interval), 1) if interval > 0 and duration > 0 else 1
        total = per_hit * hits
        dps = total * 60 / cd
        actives[name] = {"mana_eff": round(total / num(r.get("cost"), 1), 1) if num(r.get("cost")) else 0,"quality": r.get("quality", ""), "attr": r.get("attribute", ""),
                         "tags": [s.strip() for s in str(r.get("tags", "")).split(",") if s.strip()],
                         "cost": num(r.get("cost")), "cd": num(r.get("cd")), "dps60": dps,
                         "info": clean(r.get("info", ""))}
    passives = {}
    for r in t.get("passsiveSkill", []):
        name = str(r.get("name", "")).strip()
        if name:
            passives[name] = {"quality": r.get("quality", ""), "attr": r.get("attribute", ""),
                              "tags": [s.strip() for s in str(r.get("tags", "")).split(",") if s.strip()],
                              "info": clean(r.get("info", ""))}
    relics = {str(r["name"]).strip(): {"grade": r.get("grade", ""), "info": clean(r.get("info", ""))}
              for r in t.get("remains", []) if str(r.get("name", "")).strip()}
    equips = {str(r["name"]).strip(): {"info": clean(r.get("info", "")), "attrs": str(r.get("attributeType", ""))}
              for r in t.get("equipment", []) if str(r.get("name", "")).strip()}
    # 英雄：配置表里的名字可能和游戏里显示的不一样（显示名在多语言表 hero_<id>），两个名字都能查到
    act_by_id = {str(r.get("id")).split(".")[0]: str(r.get("name", "")).strip() for r in t.get("activeSkill", [])}
    pas_by_id = {str(r.get("id")).split(".")[0]: str(r.get("name", "")).strip() for r in t.get("passsiveSkill", [])}
    lang_hero = lang_hero or {}
    heroes = {}
    for r in t.get("hero", []):
        name = str(r.get("name", "")).strip()
        if not name:
            continue
        hid = str(r.get("id", "")).split(".")[0]
        active = str(r.get("zhuDong", "")).lower() == "true"
        sid = str(r.get("skill", "")).split(".")[0]
        sname = (act_by_id if active else pas_by_id).get(sid, "")
        sinfo = (actives if active else passives).get(sname, {}).get("info", "")
        entry = {"id": hid, "name": name, "info": clean(r.get("info", "")),
                 "skill": sname, "skill_kind": "主动" if active else "被动", "skill_info": sinfo,
                 **{k: r.get(k) for k in ("hp", "mp", "attack", "attackSpeed", "Armor", "STR", "STRadd",
                                           "AGI", "AGIadd", "STA", "STAadd", "zhuDong")}}
        heroes[name] = entry
        shown = lang_hero.get(f"hero_{hid}")
        if shown and shown not in heroes:
            heroes[shown] = dict(entry, name=shown)
    TABLES_OUT.write_text(json.dumps({"active": actives, "passive": passives, "relic": relics,
                                      "equip": equips, "hero": heroes}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已生成 {TABLES_OUT.name}：主动 {len(actives)}、被动 {len(passives)}、遗物 {len(relics)}、装备 {len(equips)}、英雄 {len(heroes)}")


if __name__ == "__main__":
    main()
