#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""asr_client 离线单测：仅配腾讯云直连不误告警、双路主备降级与记账（不调用任何云服务）。"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import asr_client


def _with_isolated_asr(fn):
    orig = (
        asr_client.DASHSCOPE_API_KEY,
        asr_client.TENCENT_SECRET_ID,
        asr_client.TENCENT_SECRET_KEY,
        asr_client.DATA_DIR,
        asr_client.USAGE_PATH,
        asr_client.ALERT_HOOK,
        asr_client._aliyun,
        asr_client._tencent,
    )
    with tempfile.TemporaryDirectory() as td:
        asr_client.DATA_DIR = td
        asr_client.USAGE_PATH = os.path.join(td, "asr_usage.json")
        try:
            fn()
        finally:
            (
                asr_client.DASHSCOPE_API_KEY,
                asr_client.TENCENT_SECRET_ID,
                asr_client.TENCENT_SECRET_KEY,
                asr_client.DATA_DIR,
                asr_client.USAGE_PATH,
                asr_client.ALERT_HOOK,
                asr_client._aliyun,
                asr_client._tencent,
            ) = orig


def test_tencent_only_skips_aliyun_alert():
    def _run():
        alerts = []
        called = []
        asr_client.DASHSCOPE_API_KEY = ""
        asr_client.TENCENT_SECRET_ID = "fake-id"
        asr_client.TENCENT_SECRET_KEY = "fake-key"
        asr_client.ALERT_HOOK = lambda k, t: alerts.append((k, t))
        asr_client._aliyun = lambda pcm, timeout: (_ for _ in ()).throw(AssertionError("should not call aliyun"))
        asr_client._tencent = lambda pcm, timeout: (called.append("tencent"), "妈，今天吃饺子")[1]

        text = asr_client.transcribe_pcm(b"\x00\x00" * 16000)
        assert text == "妈，今天吃饺子"
        assert called == ["tencent"]
        assert alerts == []
        today = asr_client.usage_summary()["today"]
        assert today["tencent"]["n"] == 1
        assert "aliyun_fail" not in today

    _with_isolated_asr(_run)


def test_aliyun_failure_falls_back_and_alerts():
    def _run():
        alerts = []
        asr_client.DASHSCOPE_API_KEY = "fake-dashscope"
        asr_client.TENCENT_SECRET_ID = "fake-id"
        asr_client.TENCENT_SECRET_KEY = "fake-key"
        asr_client.ALERT_HOOK = lambda k, t: alerts.append((k, t))
        asr_client._aliyun = lambda pcm, timeout: (_ for _ in ()).throw(RuntimeError("mock timeout"))
        asr_client._tencent = lambda pcm, timeout: "下午去公园散步"

        text = asr_client.transcribe_pcm(b"\x00\x00" * 16000)
        assert text == "下午去公园散步"
        assert len(alerts) == 1 and alerts[0][0] == "asr_aliyun"
        today = asr_client.usage_summary()["today"]
        assert today["aliyun_fail"]["n"] == 1
        assert today["tencent"]["n"] == 1

    _with_isolated_asr(_run)


def test_tencent_only_failure_records_all_fail():
    def _run():
        alerts = []
        asr_client.DASHSCOPE_API_KEY = ""
        asr_client.TENCENT_SECRET_ID = "fake-id"
        asr_client.TENCENT_SECRET_KEY = "fake-key"
        asr_client.ALERT_HOOK = lambda k, t: alerts.append((k, t))
        asr_client._tencent = lambda pcm, timeout: (_ for _ in ()).throw(RuntimeError("net down"))

        try:
            asr_client.transcribe_pcm(b"\x00\x00" * 16000)
        except RuntimeError as exc:
            assert "语音识别失败（仅配置腾讯云）" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")
        assert alerts == []
        today = asr_client.usage_summary()["today"]
        assert today["all_fail"]["n"] == 1
        assert "aliyun_fail" not in today

    _with_isolated_asr(_run)


if __name__ == "__main__":
    test_tencent_only_skips_aliyun_alert()
    print("PASS tencent-only skips aliyun alert")
    test_aliyun_failure_falls_back_and_alerts()
    print("PASS aliyun fallback and alert")
    test_tencent_only_failure_records_all_fail()
    print("PASS tencent-only failure records all_fail")
    print("全部通过")
