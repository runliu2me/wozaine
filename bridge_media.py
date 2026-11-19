#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桥接侧媒体小工具：PCM 重采样、MP3→PCM、兜底语音 Opus 帧缓存。

从 xiaozhi_bridge.py 抽出，减少主文件体积；行为与抽出前一致。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading


def resample_pcm_16k(pcm_bytes, to_rate):
    """16k PCM → 设备下行采样率（线性插值，对讲够用）。"""
    if to_rate == 16000 or not pcm_bytes:
        return pcm_bytes
    import numpy as np

    x = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    n_out = int(len(x) * to_rate / 16000)
    idx = np.linspace(0, len(x) - 1, num=n_out)
    y = np.interp(idx, np.arange(len(x)), x)
    return np.clip(y, -32768, 32767).astype(np.int16).tobytes()


def mp3_to_pcm_mono(mp3_bytes, rate, gain_db):
    """TTS MP3 → 单声道 PCM；抬音量后再软限幅，避免削波。"""
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
            "-ar", str(rate), "-ac", "1",
            "-af", f"volume={gain_db}dB,alimiter=limit=0.89:level=false",
            "-f", "s16le", "pipe:1",
        ],
        input=mp3_bytes, capture_output=True, timeout=20,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 转码失败: {proc.stderr.decode('utf-8', 'ignore')[:300]}")
    return proc.stdout


class FallbackOpusCache:
    """兜底那句话的 Opus 帧。PCM 缓存在 data/fallback/<rate>.pcm。"""

    def __init__(self, data_dir, fallback_text, synthesize_mp3, opus_bitrate, opus_bitrate_24k, opus_frame_ms):
        self.data_dir = data_dir
        self.fallback_text = fallback_text
        self.synthesize_mp3 = synthesize_mp3
        self.opus_bitrate = opus_bitrate
        self.opus_bitrate_24k = opus_bitrate_24k
        self.opus_frame_ms = opus_frame_ms
        self._cache = {}
        self._lock = threading.Lock()

    def frames(self, rate, frame_samples, gain_db):
        import opuslib

        with self._lock:
            if rate in self._cache:
                return self._cache[rate]
            d = os.path.join(self.data_dir, "fallback")
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, f"{rate}.pcm")
            pcm = None
            if os.path.exists(path):
                with open(path, "rb") as f:
                    pcm = f.read()
            else:
                try:
                    pcm = mp3_to_pcm_mono(self.synthesize_mp3(self.fallback_text), rate, gain_db)
                    with open(path, "wb") as f:
                        f.write(pcm)
                except Exception as exc:
                    sys.stderr.write(f"[fallback] synth failed: {exc}\n")
                    return []
            enc = opuslib.Encoder(rate, 1, opuslib.APPLICATION_VOIP)
            enc.bitrate = self.opus_bitrate if rate <= 16000 else self.opus_bitrate_24k
            frame_bytes = frame_samples * 2
            frames = []
            for i in range(0, len(pcm), frame_bytes):
                chunk = pcm[i : i + frame_bytes]
                if len(chunk) < frame_bytes:
                    chunk = chunk + b"\x00" * (frame_bytes - len(chunk))
                frames.append(enc.encode(chunk, frame_samples))
            self._cache[rate] = frames
            return frames
