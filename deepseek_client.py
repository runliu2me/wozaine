#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepSeek 对话大脑：生成回复 + 从对话里提炼要长期记住的事实。

TODO: DEEPSEEK_MODEL 的具体 model id（V4-Flash 上线后的正式名字）
需要在拿到 key、看一眼官方控制台/文档后确认，先用环境变量兜底。
"""

import json
import os
import urllib.parse

import requests

import family

API_URL = os.environ.get("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
TIMEOUT = 30


def is_deepseek_official_api(url=None):
    """当前对话接口是否为 DeepSeek 官方地址（仅官方地址支持 /user/balance 余额查询）。"""
    target = (API_URL if url is None else url).strip()
    if not target:
        return True
    try:
        host = (urllib.parse.urlsplit(target).hostname or "").lower()
    except ValueError:
        return False
    return host == "api.deepseek.com"


def _parse_extra_body(raw):
    """解析 DEEPSEEK_EXTRA_BODY（JSON 对象），用于透传兼容模型的额外参数（如关闭思考模式）。"""
    text = (raw or "").strip()
    if len(text) >= 2 and text[0] == text[-1] == "'":
        text = text[1:-1].strip()
    if not text:
        return {}
    try:
        obj = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"DEEPSEEK_EXTRA_BODY 不是合法 JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError('DEEPSEEK_EXTRA_BODY 必须是 JSON 对象（例如 {"thinking":{"type":"disabled"}}）')
    return obj


EXTRA_BODY = _parse_extra_body(os.environ.get("DEEPSEEK_EXTRA_BODY", ""))


def _build_chat_payload(messages, temperature=0.7, model=None, stream=False):
    payload = dict(EXTRA_BODY)
    payload.update({
        "model": model or MODEL,
        "messages": messages,
        "temperature": temperature,
    })
    if stream:
        payload["stream"] = True
    else:
        payload.pop("stream", None)
    return payload


def _chat(messages, temperature=0.7, model=None, timeout=None):
    if not API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")
    resp = requests.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        json=_build_chat_payload(messages, temperature=temperature, model=model, stream=False),
        timeout=timeout or TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


def build_system_prompt(persona_text, facts):
    lines = [persona_text.strip(), "", "## 关于对方，你已经知道的事（不要生硬复述，自然带入）"]
    if facts:
        for item in facts:
            lines.append(f"- {item['text']}")
    else:
        lines.append("（暂时还不了解太多，多问多听）")
    # 她讲过的人生故事（概括），每轮都带着：知道聊过什么、该接着问什么
    try:
        import story
        kb = story.known_block()
        if kb:
            lines += [kb]
    except Exception as exc:
        lines += [f"（故事背景读取失败：{exc}）"]
    # 错字本：哪些词常被识别错（比如句尾喊人名被听成别的词，入库时还没纠正）
    try:
        import typo_book
        hint = typo_book.prompt_hint()
        if hint:
            lines += ["", hint]
    except Exception:
        pass
    # 真实的时间和天气：模型自己不知道，不给它就只会编
    try:
        import live_context
        lines += ["", live_context.current_context()]
    except Exception as exc:
        lines += ["", f"## 此时此刻的真实情况：没取到（{exc}）。问到时间、天气就老实说不清楚，不许编。"]
    return "\n".join(lines)


def _build_messages(persona_text, facts, recent_turns, user_text):
    system_prompt = build_system_prompt(persona_text, facts)
    messages = [{"role": "system", "content": system_prompt}]
    for turn in recent_turns:
        role = "assistant" if turn["role"] == "assistant" else "user"
        messages.append({"role": role, "content": turn["text"]})
    messages.append({"role": "user", "content": user_text})
    return messages


def _enforce_style(text):
    """提示词管不住模型偶尔冒出来的"您"，这里再兜底强制替换一次"""
    return text.replace("您", "你")


def generate_reply(persona_text, facts, recent_turns, user_text):
    reply = _chat(_build_messages(persona_text, facts, recent_turns, user_text))
    return _enforce_style(reply)


def generate_reply_stream(persona_text, facts, recent_turns, user_text):
    """流式生成回复，逐个文本片段 yield 出来，用于边生成边合成语音"""
    if not API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")
    messages = _build_messages(persona_text, facts, recent_turns, user_text)
    resp = requests.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        json=_build_chat_payload(messages, temperature=0.7, stream=True),
        timeout=TIMEOUT,
        stream=True,
    )
    resp.raise_for_status()
    for line in resp.iter_lines():
        if not line:
            continue
        line = line.decode("utf-8")
        if not line.startswith("data: "):
            continue
        payload = line[len("data: "):]
        if payload.strip() == "[DONE]":
            break
        obj = json.loads(payload)
        if obj.get("error"):
            raise RuntimeError(f"模型流式返回报错: {obj['error']}")
        choices = obj.get("choices") or []
        if not choices:
            continue
        delta = (choices[0].get("delta") or {}).get("content")
        if delta:
            yield _enforce_style(delta)


MEMORY_CRITERIA = (
    "什么值得记（一个月以后聊天时还用得上的、关于妈妈这个人的稳定信息）：\n"
    "- 她的性格、习惯、喜好和忌口（爱吃什么、不吃什么、喜欢聊什么、忌讳什么）\n"
    "- 长期的身体情况和变化趋势（哪里老疼、睡眠规律的变化、新出现且持续的毛病）\n"
    "- 她生活里的人和关系（阿姨、老朋友、邻居、亲戚：叫什么、和她什么关系、常做什么）\n"
    "- 固定的日常安排和地点（每周训练、常去的地方、作息）\n"
    "- 反复提起、放不下的事；重要的人生经历和往事（老家、年轻时的工作、家里的旧事）\n"
    "- 情绪的长期变化（比如这段时间明显低落/开朗了），不是某一天的心情\n"
    "什么不值得记（一律不要写）：\n"
    "- 当天的天气、当天吃了什么、今天下没下楼、今天累不累这类只对当天有意义的事\n"
    "- 一次性的情绪和客套：想我、关心我路上安全、让我早点回来、说谢谢\n"
    "- 她对我（儿子/AI）的要求和反应；我说了什么\n"
    "- 太笼统的话（\"愿意聊天\"\"住了很多年\"）；从一句话猜出来的不确定的事\n"
    "写法：主语用\"妈妈\"，不要用\"对方\"；一条一句话、具体、可核对；宁可不记，不要凑数。"
)


def _elder(text):
    """提示词模板里默认写的是「妈妈 / 母亲 / 妈 / 她 / 儿子」；family.json 换了称呼就跟着换。
    只用在模板上，不要套到老人的原话上。"""
    return family.adapt_prompt(text)


def _typo_hint():
    try:
        import typo_book
        h = typo_book.prompt_hint()
        return ("\n\n" + h + "\n因为识别错字而产生的错误信息（比如把人名听错，就以为多了一个人）不要记。") if h else ""
    except Exception:
        return ""


def extract_facts(user_text, reply_text):
    """从这一轮对话里提炼值得长期记住的新信息，没有就返回空列表。
    会把已记住的事一并给模型看，避免重复、避免记琐碎的东西"""
    try:
        import memory_store
        existing = [f["text"] for f in memory_store.load_facts()][-60:]
    except Exception:
        existing = []
    prompt = (
        _elder("下面是妈妈和儿子（AI 扮演）的一轮对话。判断妈妈这句话里有没有值得长期记住的新信息。\n\n"
               + MEMORY_CRITERIA)
        + _typo_hint() +
        "\n\n已经记住的（有相同或相近的就不要再记）：\n"
        + ("\n".join("- " + t for t in existing) if existing else "（暂无）") +
        f"\n\n{family.ELDER_LABEL}说：{user_text}\n{family.CHILD_ROLE}回复：{reply_text}\n\n"
        "如果有值得记的新信息，每条一行、不要编号、不要多余的话；绝大多数对话都没有，那就只回复\"无\"。"
    )
    try:
        result = _chat([{"role": "user", "content": prompt}], temperature=0.1)
    except Exception:
        return []
    if not result or result.strip().strip("。") in ("无", ""):
        return []
    out = []
    for line in result.splitlines():
        line = line.strip().lstrip("-•* ").strip()
        if line and line not in ("无", "无。") and len(line) <= 60:
            out.append(line)
    return out


def consolidate_facts(facts):
    """把已有记忆整体清理一遍：删掉琐碎/过期/重复的，合并同类项，统一用\"妈妈\"做主语。
    返回整理后的列表（字符串）。给 daily_report 每晚跑一次用。"""
    if not facts:
        return []
    prompt = (
        _elder("下面是一份关于妈妈的长期记忆列表，是聊天时自动记下来的，里面有很多琐碎、重复、过期的条目。"
               "请按下面的标准整理成一份干净的列表：\n\n" + MEMORY_CRITERIA +
               "\n\n整理规则：不符合\"值得记\"的删掉；意思相近的合并成一条；主语统一为\"妈妈\"；"
               "保留具体信息（人名、地点、频率）；输出每条一行、不要编号、不要解释。") + _typo_hint() + "\n\n当前列表：\n"
        + "\n".join("- " + t for t in facts)
    )
    result = _chat([{"role": "user", "content": prompt}], temperature=0.1)
    out = []
    for line in result.splitlines():
        line = line.strip().lstrip("-•* ").strip()
        if line and len(line) <= 80:
            out.append(line)
    return out


def summarize_day(date, turns):
    """把一天的对话整理成给儿子看的日报（turns: [{"role","text","ts"}...]）"""
    lines = []
    for t in turns:
        who = family.ELDER_LABEL if t.get("role") == "user" else f"AI({family.CHILD_NAME})"
        lines.append(f"[{(t.get('ts') or '')[11:16]}] {who}：{t.get('text', '')}")
    transcript = "\n".join(lines)
    prompt = (
        f"下面是 {date} {family.ELDER_LABEL}和 AI 陪聊设备（用{family.CHILD_ROLE}{family.CHILD_NAME}的声音和身份）的全部对话记录。"
        f"请写一份给{family.CHILD_ROLE}{family.CHILD_NAME}看的当日简报，用中文、口语化、要短（全文控制在 150 字以内，每段一两句话，"
        "不要逐句复述，不要客套开头），分成四段，每段用一行小标题：\n"
        "1. 今天聊了什么（一两句概括）\n"
        f"2. {family.ELDER_LABEL}的状态（情绪、身体、吃饭睡觉；没提到就写没提到）\n"
        "3. 值得注意（反复提起、担心的、要家人跟进的；没有就写「无」）\n"
        "4. 明天可以关心的话题（一两条，具体）\n\n"
        + _typo_hint().replace("不要记", "不要写进简报") +
        f"\n\n对话记录：\n{transcript}"
    )
    return _chat([{"role": "user", "content": prompt}], temperature=0.4)
