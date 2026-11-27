#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""家庭档案：这台机器陪的是谁、用谁的身份说话、各自在哪个城市。

所有「因人而异」的信息都集中在 family.json（不进 git），代码里不再写死任何人名地名。
第一次部署：cp family.example.json family.json，然后照着改。
人怎么说话、家里有哪些人这类细节，写在 persona.md（同样从 persona.example.md 复制）。
"""
import json
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PATH = os.environ.get("COMPANION_FAMILY", os.path.join(BASE_DIR, "family.json"))
EXAMPLE_PATH = os.path.join(BASE_DIR, "family.example.json")

DEFAULTS = {
    "elder_name": "王秀英",          # 老人的全名（只在故事书里用）
    "elder_brief": "七十多岁",        # 一句话介绍老人，给模型做背景，如「成都人，七十多岁，说四川话」
    "elder_birth": "1950 年代",       # 大概出生年代，写书用
    "child_name": "小明",             # AI 扮演的那个孩子的名字（声音也是克隆他的）
    "child_nickname": "明明",         # 系统里对这个 AI 的昵称（管理台、提示词里用）
    "child_role": "儿子",             # AI 扮演的角色（儿子 / 女儿 / 孩子，用于提示词、日报和书的副标题）
    "elder_city": {"name": "北京", "lat": 39.90, "lon": 116.40},   # 老人所在城市（报天气）
    "child_city": {"name": "北京", "lat": 39.90, "lon": 116.40},   # 孩子所在城市；和老人同城就填一样的
    "book_title": "妈，你慢慢说",     # 故事书书名
    "elder_label": "妈妈",            # 管理台和提示词里的称呼（可改成「爸爸」「姥姥」等）
    "elder_call": "",                # 口头呼唤称呼（如「妈」「爸」；不填就按 elder_label 推导）
    "elder_formal": "",              # 书面称呼（如「母亲」「父亲」；不填就按 elder_label 推导）
    "elder_pronoun": "",             # 管理台和提示词里怎么指代老人：她 / 他；不填就按称呼猜（爸、爷、公、伯、叔、舅 → 他）
    "asr_context": "说话人是一位老人，口音可能比较重。常说的词：妈妈、吃饭、回来。",  # 给语音识别的提示
    "asr_hotwords": "",              # 腾讯云识别热词，格式「词|权重,词|权重」，如「小明|11」
    "timezone_offset_hours": 8,      # 时区（北京时间 = 8）
}


def _default_elder_call(label):
    if label in ("妈妈", "老妈"):
        return "妈"
    if label in ("爸爸", "老爸"):
        return "爸"
    return label


def _default_elder_formal(label):
    if label in ("妈妈", "老妈"):
        return "母亲"
    if label in ("爸爸", "老爸"):
        return "父亲"
    return label


def _default_greetings(call):
    return [
        f"{call}，我在呢。有什么事儿吗？",
        f"{call}，我在呢。想我了？",
        f"{call}，我在呢。今天怎么样？",
        f"{call}，我在呢。吃饭了没？",
        f"{call}，我在呢。刚忙完，你说。",
        f"{call}，我在呢。怎么啦？",
        f"{call}，我在呢。你说，我听着。",
        f"{call}，我在呢。今天感觉怎么样？",
        f"{call}，我在呢。慢慢说，不急。",
        f"{call}，我在呢。今天出去转了没？",
    ]


def _default_proactive_greetings(call):
    return [
        f"{call}，你在吗？",
        f"{call}，在吗？",
    ]


def _load():
    data = dict(DEFAULTS)
    for p in (PATH,) if os.path.exists(PATH) else (EXAMPLE_PATH,):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data.update({k: v for k, v in json.load(f).items() if not k.startswith("_")})
        except Exception as exc:  # 档案写坏了也不能让设备哑掉，用默认值顶上
            print(f"[family] 读取 {p} 失败，用默认值：{exc}")
    return data


P = _load()
ELDER_NAME = P["elder_name"]
ELDER_BRIEF = P["elder_brief"]
ELDER_BIRTH = P["elder_birth"]
CHILD_NAME = P["child_name"]
CHILD_NICK = P["child_nickname"]
CHILD_ROLE = P.get("child_role") or "儿子"
ELDER_CITY = P["elder_city"]
CHILD_CITY = P["child_city"]
BOOK_TITLE = P["book_title"]
ELDER_LABEL = P.get("elder_label") or "妈妈"
ELDER_CALL = P.get("elder_call") or _default_elder_call(ELDER_LABEL)
ELDER_FORMAL = P.get("elder_formal") or _default_elder_formal(ELDER_LABEL)
ELDER_PRONOUN = P.get("elder_pronoun") or ("他" if any(c in ELDER_LABEL for c in "爸爷公伯叔舅") else "她")
ASR_CONTEXT = P["asr_context"]
ASR_HOTWORDS = P["asr_hotwords"]
TZ_HOURS = P["timezone_offset_hours"]
SAME_CITY = ELDER_CITY.get("name") == CHILD_CITY.get("name")

GREETINGS = [str(x).strip() for x in (P.get("greetings") or []) if str(x).strip()] or _default_greetings(ELDER_CALL)
PROACTIVE_GREETINGS = (
    [str(x).strip() for x in (P.get("proactive_greetings") or []) if str(x).strip()]
    or _default_proactive_greetings(ELDER_CALL)
)
LOW_BATTERY_TEXT = str(P.get("low_battery_text") or "").strip() or f"{ELDER_CALL}，我快没电了，帮我接上充电线，给我充充电吧。"
FALLBACK_TEXT = str(P.get("fallback_text") or "").strip() or f"{ELDER_CALL}，我这边网络有点不好，你等一会儿再拍一下按钮找我。"

_PROMPT_TOKEN_RE = re.compile(r"妈妈|母亲|妈|她|儿子")


def adapt_prompt(text):
    """把静态提示词模板里的默认称谓（妈妈/母亲/妈/她/儿子）单趟替换成当前家庭配置。
    只用于静态模板，严禁套在老人的原话上。"""
    mapping = {
        "妈妈": ELDER_LABEL,
        "母亲": ELDER_FORMAL,
        "妈": ELDER_CALL,
        "她": ELDER_PRONOUN,
        "儿子": CHILD_ROLE,
    }
    return _PROMPT_TOKEN_RE.sub(lambda m: mapping[m.group(0)], text)


PERSONA_PATH = os.path.join(BASE_DIR, "persona.md")
PERSONA_EXAMPLE_PATH = os.path.join(BASE_DIR, "persona.example.md")


def read_persona():
    """人设：persona.md；还没建就先用模板顶上（模板里有【方括号】占位，正式用之前一定要改）。"""
    for p in (PERSONA_PATH, PERSONA_EXAMPLE_PATH):
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read()
    return ""
