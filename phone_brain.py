#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""电话线路的"AI 大脑"：一个 OpenAI 兼容的 /v1/chat/completions 接口。

阿里云智能联络中心 / 腾讯云联络中心的语音智能体负责接电话、识别、断句、合成；它们把每一轮对话
以 OpenAI 协议 POST 到这里，这里套上和桌上设备完全一样的人设、长期记忆、真实时间天气，
用 DeepSeek 生成回复，并把对话记进管理台的记录（来源标 phone）。

鉴权：Authorization: Bearer <PHONE_API_KEY>（.env）。支持 stream=true（SSE）和普通 JSON。
"""
import json
import os
import threading
import time
import traceback
import uuid

import deepseek_client
import family
import memory_store

PHONE_API_KEY = os.environ.get("PHONE_API_KEY", "")
PERSONA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "persona.md")

PHONE_SUFFIX = """

## 现在的情形（电话）
- 你现在是在**电话**里和妈妈说话：她打你的手机，你在忙没接到，电话自动转给了你。
- 开头先自然地说一句"妈，我在呢"之类，别解释什么"转接""AI"。她要是问"你怎么没接"，就说"刚才手上有事，现在能说了"。
- 电话里听不清是常事：她说的话识别出来有偏差就顺着大意接，别反复让她重复。
- 每次只说一两句，说完停下来等她；电话里长篇大论她跟不上。
- 她说"挂了""不说了""再见"，就好好道别，让她挂。
"""


def _persona():
    return family.read_persona() + PHONE_SUFFIX


def _convert(messages):
    """把厂商发来的 OpenAI 格式消息拆成：历史轮次 + 最后一句用户话。厂商自己的 system 提示忽略，用我们的人设。"""
    turns = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, list):  # 多模态格式：只取文本
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if role in ("user", "assistant") and content:
            turns.append({"role": role, "text": str(content)})
    user_text = ""
    if turns and turns[-1]["role"] == "user":
        user_text = turns.pop()["text"]
    return turns, user_text


def _sse(handler, chunk_obj):
    handler.wfile.write(("data: " + json.dumps(chunk_obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
    handler.wfile.flush()


def handle(handler):
    """在 server.py 的 do_POST 里调用。handler 是 BaseHTTPRequestHandler。"""
    auth = handler.headers.get("Authorization", "")
    if not PHONE_API_KEY or auth != f"Bearer {PHONE_API_KEY}":
        handler._send_json(401, {"error": {"message": "unauthorized", "type": "invalid_request_error"}})
        return
    body = handler._read_json_body(max_size=256 * 1024) or {}
    messages = body.get("messages") or []
    stream = bool(body.get("stream"))
    model_name = body.get("model") or "runrun-phone"
    turns, user_text = _convert(messages)
    if not user_text:
        user_text = "（妈妈刚接通，还没说话）"
    # 电话里的短期上下文：厂商发来的这通电话的历史 + 我们记录里最近几句（设备上聊过的也算）
    recent = memory_store.recent_history(limit=8) + turns[-10:]
    facts = memory_store.load_facts()
    persona = _persona()
    rid = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())
    full = []

    try:
        gen = deepseek_client.generate_reply_stream(persona, facts, recent, user_text)
        if stream:
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
            handler.send_header("Cache-Control", "no-cache")
            handler.send_header("Connection", "close")
            handler.end_headers()
            _sse(handler, {"id": rid, "object": "chat.completion.chunk", "created": created, "model": model_name,
                           "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]})
            for delta in gen:
                full.append(delta)
                _sse(handler, {"id": rid, "object": "chat.completion.chunk", "created": created, "model": model_name,
                               "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}]})
            _sse(handler, {"id": rid, "object": "chat.completion.chunk", "created": created, "model": model_name,
                           "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
            handler.wfile.write(b"data: [DONE]\n\n")
            handler.wfile.flush()
        else:
            for delta in gen:
                full.append(delta)
            text = "".join(full)
            handler._send_json(200, {"id": rid, "object": "chat.completion", "created": created, "model": model_name,
                                     "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                                     "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})
    except Exception as exc:
        traceback.print_exc()
        if not full:
            try:
                handler._send_json(500, {"error": {"message": str(exc)[:200], "type": "server_error"}})
            except Exception:
                pass
        return

    reply = "".join(full).strip()
    if user_text and not user_text.startswith("（") and reply:
        memory_store.append_history("user", "[电话] " + user_text)
        memory_store.append_history("assistant", reply)

        def _bg():
            try:
                memory_store.add_facts(deepseek_client.extract_facts(user_text, reply))
            except Exception:
                traceback.print_exc()
        threading.Thread(target=_bg, daemon=True).start()
