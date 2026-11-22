"""AI 部分（Claude，用本机 Claude Code 的订阅登录，不要 API Key）：本局路线、看画面答疑、聊天。

游戏资料（game_data.md）放在系统提示词里，连续调用能命中缓存。
"""
from __future__ import annotations

import asyncio
import base64
import io
import os
import re
from pathlib import Path

os.environ.pop("ANTHROPIC_API_KEY", None)  # 必须在 SDK 启动前去掉，否则改走 API Key 计费

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query  # noqa: E402

from brain import EQUIP_INFO, TABLES, base_skill  # noqa: E402

ROOT = Path(__file__).resolve().parent
SHOP_EQUIPS = ["无尽之刃", "狂战斧", "饮血剑", "破败王者之刃", "穿甲弓", "灭世之帽", "溶解之杖", "痛苦面具", "日炎斗篷",
               "狂徒铠甲", "振奋铠甲", "圣杖之证", "巫师手套", "女神之泪", "钢铁之心", "力量之戒", "迅捷之戒",
               "生命之戒", "圣者的项链", "忍者刀鞘", "抗魔斗篷"]

INTRO = """你是玩家的《地下城竞技场》（Dungeon Brawls）游戏助手。玩家单人玩狂欢模式，目标是玩得爽：清屏快、伤害爆炸、奖励拿满，
但也别一直暴毙。游戏：一波波刷怪，怪物数量超过上限（压力值满）就输；波次之间在商店用骷髅币买技能书（D/C/B/A 级，随机三选一）、
用金币买装备（6 格）和属性锻造器（随机三选一属性）；有些波次要选挑战（危险系数 D~SSS，越危险奖励越好）。
战斗由游戏自带的自动战斗完成，主动技能要开自动释放。

回答要求：说人话，短句，玩家边打边看；名字照抄游戏里的写法；不确定就说不确定，不要编造。
后面附了从游戏文件提取的完整资料。"""


def _system_prompt() -> str:
    try:
        data = (ROOT / "game_data.md").read_text(encoding="utf-8")
    except Exception:
        data = "（缺 game_data.md：运行 build_game_data.py）"
    return f"{INTRO}\n\n<game_data>\n{data}\n</game_data>"


SYSTEM = _system_prompt()


async def _ask(text: str, image_png: bytes | None, schema: dict | None, model: str, effort: str):
    content = []
    if image_png:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                     "data": base64.b64encode(image_png).decode()}})
    content.append({"type": "text", "text": text})

    async def messages():
        yield {"type": "user", "message": {"role": "user", "content": content}, "parent_tool_use_id": None}

    opts = ClaudeAgentOptions(model=model, effort=effort, tools=[], max_turns=2, setting_sources=[],
                              system_prompt=SYSTEM, cwd=str(ROOT), extra_args={"no-session-persistence": None},
                              output_format={"type": "json_schema", "schema": schema} if schema else None)
    result = None
    async for msg in query(prompt=messages(), options=opts):
        if isinstance(msg, ResultMessage):
            if msg.is_error:
                raise RuntimeError(msg.result or "Claude 返回错误")
            result = msg.structured_output if schema else msg.result
    if result is None:
        raise RuntimeError("没有拿到回答")
    return result


def ask(text: str, image_png: bytes | None = None, schema: dict | None = None,
        model: str = "claude-sonnet-5-5", effort: str = "low"):
    return asyncio.run(_ask(text, image_png, schema, model, effort))


def png_bytes(img, max_w: int = 1600) -> bytes:
    if img.width > max_w:
        img = img.resize((max_w, round(img.height * max_w / img.width)))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _clean(s: str) -> str:
    """去掉偶尔漏出来的 XML / Markdown 痕迹（上一版军师出现过 </hero><parameter …>）。"""
    s = re.sub(r"</?[a-zA-Z_][^>]*>", "", str(s))
    s = re.sub(r"\*\*|^#+\s*", "", s, flags=re.M)
    return s.strip()


# ---------------------------------------------------------------- 本局路线

ACTIVE_NAMES = sorted({base_skill(n) for n in TABLES["active"]})
PASSIVE_NAMES = sorted(TABLES["passive"])

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "一句话：这把怎么玩最爽，不超过 40 字"},
        "how_to_play": {"type": "array", "items": {"type": "string"}, "maxItems": 4, "description": "操作要点，每条不超过 30 字"},
        "core_actives": {"type": "array", "items": {"type": "string", "enum": ACTIVE_NAMES}, "maxItems": 5,
                         "description": "优先拿的主动技能（不带品级字母）"},
        "core_passives": {"type": "array", "items": {"type": "string", "enum": PASSIVE_NAMES}, "maxItems": 5},
        "equip_order": {"type": "array", "items": {"type": "string", "enum": SHOP_EQUIPS}, "maxItems": 6,
                        "description": "出装顺序"},
        "relic_focus": {"type": "array", "items": {"type": "string"}, "maxItems": 5,
                        "description": "遗物优先看的关键词或遗物名，如「法术伤害」「灭世之帽」"},
        "challenge_tip": {"type": "string", "description": "挑战怎么选，一句话"},
    },
    "required": ["summary", "how_to_play", "core_actives", "core_passives", "equip_order", "relic_focus", "challenge_tip"],
    "additionalProperties": False,
}


def plan_run(hero: str, style: str, playstyle: str, skills: list[str], model: str, effort: str) -> dict:
    h = TABLES["hero"].get(hero) or {}
    prompt = (f"英雄：{hero or '未知'}（特性：{h.get('info', '未知')}；成长 力量+{h.get('STRadd')} 敏捷+{h.get('AGIadd')} "
              f"耐力+{h.get('STAadd')}）\n当前判断的流派：{style}；玩家想要的风格：{playstyle}\n已学技能：{'、'.join(skills) or '无'}\n\n"
              "给这把出一条最爽的路线：优先拿哪些主动 / 被动技能、出装顺序、遗物看什么、挑战怎么选。"
              "技能和装备名字只能从给定选项里选。")
    plan = ask(prompt, schema=PLAN_SCHEMA, model=model, effort=effort)
    for k in ("summary", "challenge_tip"):
        plan[k] = _clean(plan.get(k, ""))
    plan["how_to_play"] = [_clean(x) for x in plan.get("how_to_play", [])]
    plan["equip_order"] = [e for e in plan.get("equip_order", []) if e in EQUIP_INFO or e in SHOP_EQUIPS]
    return plan


# ---------------------------------------------------------------- 选英雄后：几个流派任选

OPTION = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "流派名，4~8 个字，如「雷电清屏流」"},
        "why_fun": {"type": "string", "description": "为什么爽，一句话，不超过 30 字"},
        "difficulty": {"type": "string", "enum": ["简单", "中等", "难"]},
        "core_actives": PLAN_SCHEMA["properties"]["core_actives"],
        "core_passives": PLAN_SCHEMA["properties"]["core_passives"],
        "equip_order": PLAN_SCHEMA["properties"]["equip_order"],
        "relic_focus": PLAN_SCHEMA["properties"]["relic_focus"],
        "how_to_play": {"type": "array", "items": {"type": "string"}, "maxItems": 3, "description": "操作要点，每条不超过 25 字"},
        "challenge_tip": {"type": "string", "description": "挑战怎么选，一句话"},
    },
    "required": ["name", "why_fun", "difficulty", "core_actives", "core_passives", "equip_order", "relic_focus",
                 "how_to_play", "challenge_tip"],
    "additionalProperties": False,
}
OPTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "hero_tip": {"type": "string", "description": "这个英雄的特点和自带技能怎么用，一两句话，不超过 50 字"},
        "options": {"type": "array", "items": OPTION, "minItems": 3, "maxItems": 3,
                    "description": "3 个明显不同的流派，第一个是最推荐的"},
    },
    "required": ["hero_tip", "options"],
    "additionalProperties": False,
}
PLAN_DIR = ROOT / "data" / "plans"


def cached_options(hero: str) -> dict | None:
    import json
    f = PLAN_DIR / f"{hero}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


def build_options(hero: str, playstyle: str, model: str, effort: str) -> dict:
    """先看英雄自带技能和成长，再给 3 个流派。结果按英雄缓存，下次秒出。"""
    import json
    h = TABLES["hero"].get(hero) or {}
    prompt = (f"玩家选了英雄「{hero}」。\n"
              f"英雄特性：{h.get('info', '未知')}\n"
              f"自带{h.get('skill_kind', '')}技能「{h.get('skill', '')}」：{h.get('skill_info', '')}\n"
              f"初始：生命 {h.get('hp')}、法力 {h.get('mp')}、攻击 {h.get('attack')}、攻速 {h.get('attackSpeed')}、护甲 {h.get('Armor')}\n"
              f"每级成长：力量 +{h.get('STRadd')}（加攻击）、敏捷 +{h.get('AGIadd')}（加攻速移速）、耐力 +{h.get('STAadd')}（加生命）\n"
              f"玩家想要的风格：{playstyle}（爽 = 清屏快、伤害爆炸；稳 = 不容易死）\n\n"
              "先吃透这个英雄的自带技能和成长，再给 3 个明显不同、都能玩得爽的流派（第一个最推荐）。"
              "每个流派写清：优先拿哪些主动 / 被动技能、出装顺序、遗物看什么、怎么操作、挑战怎么选。"
              "技能和装备名字只能从给定选项里选。")
    data = ask(prompt, schema=OPTIONS_SCHEMA, model=model, effort=effort)
    data["hero_tip"] = _clean(data.get("hero_tip", ""))
    for o in data.get("options", []):
        for k in ("name", "why_fun", "challenge_tip"):
            o[k] = _clean(o.get(k, ""))
        o["how_to_play"] = [_clean(x) for x in o.get("how_to_play", [])]
        o["summary"] = f"{o['name']}：{o['why_fun']}"
    data["chosen"] = 0
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    (PLAN_DIR / f"{hero}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def save_choice(hero: str, data: dict):
    import json
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    (PLAN_DIR / f"{hero}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- 看画面 / 聊天

def answer(question: str, image_png: bytes | None, context: str, model: str, effort: str) -> str:
    prompt = (f"【本局情况】\n{context}\n\n玩家的问题：{question}\n"
              + ("附上的是游戏当前画面。" if image_png else "")
              + "\n用 6 行以内回答，每行一个要点，不要用标题和粗体。")
    return _clean(ask(prompt, image_png, model=model, effort=effort))
