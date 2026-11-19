#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模拟一台小智设备跟桥接说话：hello → listen start(auto) → 流 Opus 音频 → 收 stt/tts/二进制帧。
供 run_scenarios.py 用，也可以单独跑：venv/bin/python3 tests/device_sim.py ws://127.0.0.1:4097 token mac
"""
import asyncio
import json
import subprocess
import time

import opuslib
import websockets

SR = 16000
FRAME_MS = 60
FRAME_SAMPLES = SR * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2


def mp3_to_pcm16k(path):
    return subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ar", str(SR), "-ac", "1", "-f", "s16le", "pipe:1"],
                          capture_output=True, check=True).stdout


class DeviceSim:
    def __init__(self, url, token, device_id, board="zhengchen-cam", version="2.8.4"):
        self.url, self.token, self.device_id, self.board, self.version = url, token, device_id, board, version
        self.ws = None
        self.enc = opuslib.Encoder(SR, 1, opuslib.APPLICATION_VOIP)
        self.msgs = []          # 收到的 JSON（按序）
        self.frames = []        # (t, nbytes) 收到的二进制帧
        self.events = asyncio.Queue()
        self._rx = None
        self.closed = None

    async def connect(self, send_hello=True):
        headers = {"Device-Id": self.device_id, "Client-Id": "sim-" + self.device_id.replace(":", ""), "Protocol-Version": "1"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        self.ws = await websockets.connect(self.url, additional_headers=headers, max_size=None)
        self._rx = asyncio.create_task(self._reader())
        if send_hello:
            await self.send_json({"type": "hello", "version": 1, "transport": "websocket",
                                  "audio_params": {"format": "opus", "sample_rate": SR, "channels": 1, "frame_duration": FRAME_MS}})
            m = await self.wait_json(lambda m: m.get("type") == "hello", 5)
            assert m, "no server hello"
            return m

    async def _reader(self):
        try:
            async for msg in self.ws:
                if isinstance(msg, (bytes, bytearray)):
                    self.frames.append((time.time(), len(msg)))
                    await self.events.put(("bin", len(msg)))
                else:
                    try:
                        d = json.loads(msg)
                    except Exception:
                        continue
                    d["_t"] = time.time()
                    self.msgs.append(d)
                    await self.events.put(("json", d))
        except websockets.exceptions.ConnectionClosed as e:
            self.closed = e
            await self.events.put(("closed", e))

    async def send_json(self, obj):
        await self.ws.send(json.dumps(obj))

    async def listen_start(self, mode="auto"):
        await self.send_json({"type": "listen", "state": "start", "mode": mode})

    async def listen_stop(self):
        await self.send_json({"type": "listen", "state": "stop"})

    async def wait_json(self, pred, timeout):
        """等一条满足 pred 的 JSON（先查已收到的，再等新的）"""
        for m in self.msgs:
            if pred(m) and not m.get("_seen"):
                m["_seen"] = True
                return m
        end = time.time() + timeout
        while time.time() < end:
            try:
                kind, d = await asyncio.wait_for(self.events.get(), max(0.05, end - time.time()))
            except asyncio.TimeoutError:
                return None
            if kind == "json" and pred(d) and not d.get("_seen"):
                d["_seen"] = True
                return d
            if kind == "closed":
                return None
        return None

    async def drain(self, seconds):
        await asyncio.sleep(seconds)

    async def stream_pcm(self, pcm, realtime=True):
        """按 60ms 一帧实时发送（像真设备一样）"""
        n = 0
        for i in range(0, len(pcm), FRAME_BYTES):
            chunk = pcm[i:i + FRAME_BYTES]
            if len(chunk) < FRAME_BYTES:
                chunk += b"\x00" * (FRAME_BYTES - len(chunk))
            await self.ws.send(self.enc.encode(chunk, FRAME_SAMPLES))
            n += 1
            if realtime:
                await asyncio.sleep(FRAME_MS / 1000)
        return n

    async def stream_silence(self, seconds):
        await self.stream_pcm(b"\x00" * int(SR * 2 * seconds))

    def frames_after(self, t):
        return [f for f in self.frames if f[0] > t]

    async def close(self):
        try:
            await self.ws.close()
        except Exception:
            pass
        if self._rx:
            self._rx.cancel()
