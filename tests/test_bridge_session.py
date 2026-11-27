#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""xiaozhi_bridge DeviceSession 离线状态机回归测试（不调用任何云服务）。"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import xiaozhi_bridge


def _make_session(loop):
    ws = SimpleNamespace(request=SimpleNamespace(headers={"Device-Id": "aa:bb:cc:dd:ee:ff"}))
    s = xiaozhi_bridge.DeviceSession(ws, loop)
    s._reset_vad = lambda: None
    return s


def test_story_entry_initialized():
    loop = asyncio.new_event_loop()
    try:
        s = _make_session(loop)
        assert hasattr(s, "story_entry") and s.story_entry is None
    finally:
        loop.close()


def test_realtime_without_intercom_uses_auto_vad():
    async def _run():
        s = _make_session(asyncio.get_running_loop())
        greeted = []
        s._start_greeting = lambda: greeted.append(True)

        # AEC 设备（无手机对讲）发起 mode="realtime"：应进入 auto 连续对话并触发开场白
        await s.handle_listen_event({"type": "listen", "state": "start", "mode": "realtime"})
        assert s.listen_mode == "auto"
        assert s.conversation_active is True
        assert len(greeted) == 1

        # 同一对话里再次收到 listen start（播完一轮回到聆听）：进入收音，不重复打招呼
        await s.handle_listen_event({"type": "listen", "state": "start", "mode": "realtime"})
        assert s.listen_mode == "auto"
        assert s.state == "user_speaking"
        assert len(greeted) == 1

    asyncio.run(_run())


def test_realtime_with_intercom_stays_intercom():
    async def _run():
        s = _make_session(asyncio.get_running_loop())
        greeted = []
        s._start_greeting = lambda: greeted.append(True)
        s.intercom_ws = object()

        await s.handle_listen_event({"type": "listen", "state": "start", "mode": "realtime"})
        assert s.listen_mode == "realtime"
        assert s.conversation_active is False
        assert s.state == "user_speaking"
        assert len(greeted) == 0

    asyncio.run(_run())


def test_intercom_stop_suppresses_late_realtime_and_tail_frames():
    async def _run():
        s = _make_session(asyncio.get_running_loop())
        sent = []

        async def _fake_send(obj):
            sent.append(obj)

        s.send_json = _fake_send
        greeted = []
        finalized = []
        s._start_greeting = lambda: greeted.append(True)
        s._finalize_utterance = lambda: finalized.append(True)

        await s.intercom_start(object())
        await s.handle_listen_event({"type": "listen", "state": "start", "mode": "realtime"})
        assert s.listen_mode == "realtime" and s.state == "user_speaking"

        # 挂断对讲：应立即退出收音态，清空缓冲
        await s.intercom_stop()
        assert s.intercom_ws is None
        assert s.state == "listening"
        assert s.frame_buf == []

        # 刚挂断瞬间设备延迟回包 realtime/start：不应误触发 AI 开场白
        await s.handle_listen_event({"type": "listen", "state": "start", "mode": "realtime"})
        assert len(greeted) == 0

        # 挂断后设备发来尾随 listen/stop：不应误触发 ASR 识别
        await s.handle_listen_event({"type": "listen", "state": "stop"})
        assert len(finalized) == 0

    asyncio.run(_run())


def test_intercom_stop_resets_active_conversation_and_cancels():
    async def _run():
        s = _make_session(asyncio.get_running_loop())
        s.send_json = lambda obj: asyncio.sleep(0)
        greeted = []
        s._start_greeting = lambda: greeted.append(True)

        # 模拟老人在对话中时家人点开对讲又秒挂断（设备尚未回包 realtime/start）
        s.conversation_active = True
        s.listen_mode = "auto"
        s.state = "ai_speaking"
        s.vad_in_speech = True
        s.out_queue.put_nowait({"__kind__": "binary", "data": b"leftover"})

        await s.intercom_start(object())
        await s.intercom_stop()

        assert s.conversation_active is False
        assert s.cancel_event.is_set() is True
        assert s.vad_in_speech is False
        assert s.out_queue.empty() is True
        assert s.state == "listening"

        # 过防抖窗口后老人重新唤醒，应正常触发开场白
        s._intercom_stopped_at = 0.0
        await s.handle_listen_event({"type": "listen", "state": "start", "mode": "realtime"})
        assert len(greeted) == 1

    asyncio.run(_run())


def test_cancelled_asr_worker_does_not_enter_ai_speaking():
    import threading

    loop = asyncio.new_event_loop()
    orig_asr = xiaozhi_bridge.asr_client.transcribe_pcm
    try:
        s = _make_session(loop)
        emitted = []
        s._emit = lambda obj: emitted.append(obj)
        s._set_state = lambda st: setattr(s, "state", st)
        cancel_ev = threading.Event()

        def _slow_asr(pcm):
            cancel_ev.set()  # 模拟识别返回前对讲挂断/打断触发取消
            return "妈听到了"

        xiaozhi_bridge.asr_client.transcribe_pcm = _slow_asr
        s.state = "listening"
        s._worker(b"\x00\x00" * 1600, cancel_ev)
        assert s.state == "listening"
        assert emitted == []
    finally:
        xiaozhi_bridge.asr_client.transcribe_pcm = orig_asr
        loop.close()


def test_push_speech_resets_stale_story_entry():
    async def _run():
        s = _make_session(asyncio.get_running_loop())
        spoken = []
        s._start_speak = lambda text: spoken.append(text)
        s.conversation_active = False
        s.story_entry = {"id": "old-story", "question": "旧故事"}
        s.user_turns = 5

        ok, _ = await s.push_speech("妈，明天降温记得加衣服")
        assert ok is True
        assert s.story_entry is None
        assert s.user_turns == 0
        assert spoken == ["妈，明天降温记得加衣服"]

    asyncio.run(_run())


def test_tencent_only_asr_failure_triggers_asr_down_alert():
    orig_alerts = list(xiaozhi_bridge.ALERTS)
    orig_last = dict(xiaozhi_bridge._ALERT_LAST)
    try:
        xiaozhi_bridge.ALERTS.clear()
        xiaozhi_bridge._ALERT_LAST.clear()
        xiaozhi_bridge._note_upstream_error(RuntimeError("语音识别失败（仅配置腾讯云）：timeout"))
        keys = [a["key"] for a in xiaozhi_bridge.ALERTS]
        assert "asr_down" in keys
    finally:
        xiaozhi_bridge.ALERTS.clear()
        xiaozhi_bridge.ALERTS.extend(orig_alerts)
        xiaozhi_bridge._ALERT_LAST.clear()
        xiaozhi_bridge._ALERT_LAST.update(orig_last)


if __name__ == "__main__":
    test_story_entry_initialized()
    print("PASS story_entry initialized")
    test_realtime_without_intercom_uses_auto_vad()
    print("PASS realtime without intercom -> auto VAD")
    test_realtime_with_intercom_stays_intercom()
    print("PASS realtime with intercom -> intercom")
    test_intercom_stop_suppresses_late_realtime_and_tail_frames()
    print("PASS intercom stop suppresses late realtime/tail frames")
    test_intercom_stop_resets_active_conversation_and_cancels()
    print("PASS intercom stop resets active conversation and cancels")
    test_cancelled_asr_worker_does_not_enter_ai_speaking()
    print("PASS cancelled ASR worker does not enter ai_speaking")
    test_push_speech_resets_stale_story_entry()
    print("PASS push_speech resets stale story_entry")
    test_tencent_only_asr_failure_triggers_asr_down_alert()
    print("PASS tencent-only asr failure triggers asr_down")
    print("全部通过")
