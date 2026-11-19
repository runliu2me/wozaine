#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全双工语音陪伴：麦克风常开，AI 说话时也在监听，能被打断。

打断要同时做四件事（业内公认，漏一个都是半成品）：
1. 立刻停止播放（发 interrupt 消息给前端，前端清空播放队列）
2. 取消还没生成完的 LLM 回复（cancel_event 让 generate_reply_stream 的消费循环提前退出）
3. 取消还没合成完的语音（同一个 cancel_event 挡住后续的 TTS 调用）
4. 把被打断前的对话如实记入历史（被打断的这轮不写入记忆，只记录新的一轮）

协议：
  客户端 -> 服务器：二进制帧，16kHz/16bit/单声道 PCM，每帧固定 vad.FRAME_BYTES 字节
  服务器 -> 客户端：JSON 文本消息
    {"type": "asr_text", "text": "..."}
    {"type": "audio_chunk", "seq": N, "text": "...", "audio_base64": "...", "audio_mime": "audio/mpeg"}
    {"type": "interrupt"}
    {"type": "done", "reply_text": "..."}
    {"type": "error", "error": "..."}
"""

import asyncio
import base64
import json
import os
import queue
import sys
import threading
import traceback

import websockets

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import deepseek_client
import family
import tts_client
import memory_store
import asr_client
from vad import StreamVAD, FRAME_BYTES

LISTEN_HOST = os.environ.get("REALTIME_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("REALTIME_PORT", "3095"))
PERSONA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "persona.md")
ALLOWED_LOGIN = os.environ.get("ALLOWED_TAILSCALE_LOGIN", "")
ALLOW_LOCAL = os.environ.get("ALLOW_LOCAL", "1") == "1"

MAX_UTTERANCE_FRAMES = 900  # ~28.8s 硬上限（只防异常无限攒，不该在正常唠嗑里被打到）
# 断句/打断的迟滞时长由 vad.StreamVAD 的 onset_frames/offset_frames 控制，这里不重复定义
PREROLL_FRAMES = 6  # 说话判定生效前保留的滚动缓冲，避免开头被吃掉


def load_persona():
    return family.read_persona()


def _find_sentence_end(buf):
    for i, ch in enumerate(buf):
        if ch in "。！？\n":
            return i
    return None


class Session:
    def __init__(self, ws, loop):
        self.ws = ws
        self.loop = loop
        # StreamVAD 内部已经用 onset/offset 帧数做了迟滞判定（speaking 是"已经确认"的状态，
        # 不是逐帧的原始概率）。这里只在 speaking 状态"翻转"的那一帧动作，不要再叠加第二层
        # 计数器去重复等一段静音/人声——那样等于把延迟翻倍，之前就是因为这个 bug 导致断句判断
        # 迟迟不触发。
        self.vad = StreamVAD()
        self.state = "listening"  # listening -> user_speaking -> processing -> ai_speaking -> listening
        self.frame_buf = []
        self.preroll = []
        self.out_queue = asyncio.Queue()
        self.cancel_event = threading.Event()
        self.send_lock = asyncio.Lock()

    async def send(self, obj):
        async with self.send_lock:
            await self.ws.send(json.dumps(obj, ensure_ascii=False))

    async def handle_frame(self, frame):
        if len(frame) != FRAME_BYTES:
            return  # 忽略残帧，前端按固定帧长发送
        changed, speaking = self.vad.process_frame(frame)

        if self.state in ("processing", "ai_speaking"):
            if changed and speaking:
                self.cancel_event.set()
                # 打断那一刻，可能已经有上一轮合成好、还没发出去的语音包排在队列里
                # （worker 线程 emit 得早，sender_loop 还没来得及 await 发送）——
                # 不清空的话，打断信号发出去之后，浏览器还会再收到一句"迟到"的旧回复。
                while not self.out_queue.empty():
                    try:
                        self.out_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                await self.send({"type": "interrupt"})
                self.state = "user_speaking"
                self.frame_buf = list(self.preroll) + [frame]
            return

        if self.state == "listening":
            self.preroll.append(frame)
            if len(self.preroll) > PREROLL_FRAMES:
                self.preroll.pop(0)
            if changed and speaking:
                self.state = "user_speaking"
                self.frame_buf = list(self.preroll) + [frame]
            return

        if self.state == "user_speaking":
            self.frame_buf.append(frame)
            if (changed and not speaking) or len(self.frame_buf) >= MAX_UTTERANCE_FRAMES:
                pcm = b"".join(self.frame_buf)
                self.frame_buf = []
                self.state = "processing"
                self._start_turn(pcm)
            return

    def _start_turn(self, pcm):
        cancel_event = threading.Event()
        self.cancel_event = cancel_event
        threading.Thread(target=self._worker, args=(pcm, cancel_event), daemon=True).start()

    def _emit(self, obj):
        self.loop.call_soon_threadsafe(self.out_queue.put_nowait, obj)

    def _set_state(self, state):
        self.loop.call_soon_threadsafe(setattr, self, "state", state)

    def _worker(self, pcm, cancel_event):
        try:
            user_text = asr_client.transcribe_pcm(pcm)
        except Exception as exc:
            self._emit({"type": "error", "error": f"识别出错: {exc}"})
            self._set_state("listening")
            return
        user_text = user_text.strip()
        asr_raw = user_text
        try:
            import typo_book
            user_text = typo_book.correct_text(asr_raw)   # 错字本
        except Exception:
            traceback.print_exc()
        if not user_text:
            self._set_state("listening")
            return
        self._emit({"type": "asr_text", "text": user_text})

        persona_text = load_persona()
        facts = memory_store.load_facts()
        recent_turns = memory_store.recent_history(limit=12)

        self._set_state("ai_speaking")

        # 文字生成和语音合成分成两个线程：合成一句话要等 MiniMax 网络请求，
        # 这段时间不该让 DeepSeek 那边也跟着停摆——用一个队列接力，
        # produce_text 只管尽快把整段回复读完、切好句子丢进队列，
        # 合成慢不影响它往前读。
        sentence_queue = queue.Queue()
        full_reply = {"text": ""}

        def produce_text():
            buf = ""
            try:
                for delta in deepseek_client.generate_reply_stream(persona_text, facts, recent_turns, user_text):
                    if cancel_event.is_set():
                        break
                    buf += delta
                    full_reply["text"] += delta
                    while True:
                        idx = _find_sentence_end(buf)
                        if idx is None:
                            break
                        sentence_queue.put(buf[:idx + 1].strip())
                        buf = buf[idx + 1:]
                if not cancel_event.is_set() and buf.strip():
                    sentence_queue.put(buf.strip())
            except Exception as exc:
                sentence_queue.put(("__error__", str(exc)))
            finally:
                sentence_queue.put(None)

        threading.Thread(target=produce_text, daemon=True).start()

        seq = 0
        try:
            while True:
                item = sentence_queue.get()
                if item is None:
                    break
                if isinstance(item, tuple) and item[0] == "__error__":
                    self._emit({"type": "error", "error": item[1]})
                    break
                if cancel_event.is_set() or not item:
                    continue
                audio = tts_client.synthesize(item)
                if cancel_event.is_set():
                    break
                self._emit({
                    "type": "audio_chunk", "seq": seq, "text": item,
                    "audio_base64": base64.b64encode(audio).decode("ascii"),
                    "audio_mime": "audio/mpeg",
                })
                seq += 1
        except Exception as exc:
            traceback.print_exc()
            self._emit({"type": "error", "error": str(exc)})

        if not cancel_event.is_set():
            reply_text = full_reply["text"].strip()
            memory_store.append_history("user", user_text, raw=asr_raw)
            memory_store.append_history("assistant", reply_text)

            def _extract_facts_bg():
                try:
                    new_facts = deepseek_client.extract_facts(user_text, reply_text)
                    memory_store.add_facts(new_facts)
                except Exception:
                    traceback.print_exc()
            threading.Thread(target=_extract_facts_bg, daemon=True).start()

            self._emit({"type": "done", "reply_text": reply_text})
            self._set_state("listening")
        # 被打断的分支：state 已经在 handle_frame 里改成 user_speaking 了，这里不要覆盖回 listening

    async def sender_loop(self):
        while True:
            obj = await self.out_queue.get()
            await self.send(obj)


def _authorized(ws):
    headers = ws.request.headers
    login = headers.get("Tailscale-User-Login", "")
    if login and ALLOWED_LOGIN and login == ALLOWED_LOGIN:
        return True
    if ALLOW_LOCAL and not headers.get("X-Forwarded-For"):
        return True
    return False


async def handle_connection(ws):
    if not _authorized(ws):
        await ws.close(code=4403, reason="unauthorized")
        return
    loop = asyncio.get_event_loop()
    session = Session(ws, loop)
    sender_task = asyncio.create_task(session.sender_loop())
    try:
        async for message in ws:
            if isinstance(message, (bytes, bytearray)):
                await session.handle_frame(bytes(message))
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        sender_task.cancel()


async def main():
    async with websockets.serve(handle_connection, LISTEN_HOST, LISTEN_PORT, max_size=None):
        print(f"realtime companion ws: ws://{LISTEN_HOST}:{LISTEN_PORT}")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
