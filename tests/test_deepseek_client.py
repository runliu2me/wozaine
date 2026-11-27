#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deepseek_client 离线单测：扩展请求体解析与拼装（不调用任何云服务）。"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import deepseek_client


def test_parse_extra_body():
    assert deepseek_client._parse_extra_body("") == {}
    assert deepseek_client._parse_extra_body("   ") == {}
    assert deepseek_client._parse_extra_body('{"thinking":{"type":"disabled"}}') == {
        "thinking": {"type": "disabled"}
    }
    assert deepseek_client._parse_extra_body("""'{"thinking":{"type":"disabled"}}'""") == {
        "thinking": {"type": "disabled"}
    }
    for bad in ("not-json", "[1, 2]", '"str"', "123"):
        try:
            deepseek_client._parse_extra_body(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad!r}")


def test_build_chat_payload_preserves_core_fields():
    old_extra = deepseek_client.EXTRA_BODY
    old_model = deepseek_client.MODEL
    try:
        deepseek_client.MODEL = "glm-5.4"
        deepseek_client.EXTRA_BODY = {}
        msgs = [{"role": "user", "content": "妈，吃饭了吗？"}]
        p = deepseek_client._build_chat_payload(msgs, temperature=0.5)
        assert p == {"model": "glm-5.4", "messages": msgs, "temperature": 0.5}

        deepseek_client.EXTRA_BODY = {
            "thinking": {"type": "disabled"},
            "model": "hijacked",
            "stream": False,
        }
        p2 = deepseek_client._build_chat_payload(msgs, temperature=0.7, stream=True)
        assert p2["thinking"] == {"type": "disabled"}
        assert p2["model"] == "glm-5.4"
        assert p2["messages"] == msgs
        assert p2["temperature"] == 0.7
        assert p2["stream"] is True

        # EXTRA_BODY 里的 stream: True 不能把非流式 _chat 改成流式
        deepseek_client.EXTRA_BODY = {"thinking": {"type": "disabled"}, "stream": True}
        p3 = deepseek_client._build_chat_payload(msgs, temperature=0.1, stream=False)
        assert "stream" not in p3
        assert p3["thinking"] == {"type": "disabled"}
    finally:
        deepseek_client.EXTRA_BODY = old_extra
        deepseek_client.MODEL = old_model


def test_is_deepseek_official_api():
    assert deepseek_client.is_deepseek_official_api("") is True
    assert deepseek_client.is_deepseek_official_api("https://api.deepseek.com/chat/completions") is True
    assert deepseek_client.is_deepseek_official_api("https://api.deepseek.com/v1/chat/completions") is True
    assert deepseek_client.is_deepseek_official_api("https://open.bigmodel.cn/api/paas/v4/chat/completions") is False
    assert deepseek_client.is_deepseek_official_api("http://127.0.0.1:8000/v1/chat/completions") is False
    assert deepseek_client.is_deepseek_official_api("https://api.deepseek.com.example.com/v1/chat/completions") is False
    assert deepseek_client.is_deepseek_official_api("http://[::1") is False


def test_stream_raises_on_sse_error_event():
    class _FakeResp:
        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield b'data: {"error":{"message":"upstream overloaded","code":503}}'

    orig_post = deepseek_client.requests.post
    orig_key = deepseek_client.API_KEY
    try:
        deepseek_client.API_KEY = "fake-key"
        deepseek_client.requests.post = lambda *a, **kw: _FakeResp()
        try:
            list(deepseek_client.generate_reply_stream("人设", [], [], "你好"))
        except RuntimeError as exc:
            assert "upstream overloaded" in str(exc)
        else:
            raise AssertionError("expected RuntimeError on SSE error event")
    finally:
        deepseek_client.requests.post = orig_post
        deepseek_client.API_KEY = orig_key


if __name__ == "__main__":
    test_parse_extra_body()
    print("PASS parse extra body")
    test_build_chat_payload_preserves_core_fields()
    print("PASS build chat payload")
    test_is_deepseek_official_api()
    print("PASS is_deepseek_official_api")
    test_stream_raises_on_sse_error_event()
    print("PASS stream raises on SSE error event")
    print("全部通过")
