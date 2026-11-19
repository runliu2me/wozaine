#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Silero VAD 包装：不自己发明语音活动检测，用业界标准的预训练模型。

单纯"有没有声音"不够用——研究表明纯阈值判断打断，89% 是误触发（咳嗽、
杂音、AI 听到自己的回声）。这里在 Silero 的逐帧概率之上加一个"最短持续
帧数"门槛：概率超阈值要连续撑够 N 帧才算数，防止一声杂音就误判。

直接用 onnxruntime 跑 models/silero_vad.onnx，不再依赖 torch/torchaudio/silero-vad 包：
小服务器（比如 2 核 4G 云主机），torch 一个包就 400MB 内存，而且 CPU/CUDA 版本一配错就起不来。
推理逻辑照抄 silero_vad.utils_vad.OnnxWrapper（v5 模型：512 样本一帧 + 前一帧末尾 64 样本做上下文，
状态张量 (2,1,128)）。
"""

import os

import numpy as np
import onnxruntime

SAMPLE_RATE = 16000
FRAME_SAMPLES = 512  # Silero VAD v5 要求 16k 采样率下固定 512 样本一帧，约 32ms
FRAME_BYTES = FRAME_SAMPLES * 2  # 16bit
CONTEXT_SAMPLES = 64
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "silero_vad.onnx")

_SESSION = None


def _session():
    """整个进程共用一个 onnx session（线程安全，只读）；每个 StreamVAD 自己保存状态。"""
    global _SESSION
    if _SESSION is None:
        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        _SESSION = onnxruntime.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"], sess_options=opts)
    return _SESSION


class StreamVAD:
    def __init__(self, speech_threshold=0.5, onset_frames=10, offset_frames=20):
        """onset_frames≈320ms 连续人声才算"开始说话"；offset_frames≈640ms 连续静音才算"说完了" """
        self.session = _session()
        self.threshold = speech_threshold
        self.onset_frames = onset_frames
        self.offset_frames = offset_frames
        self.reset()

    def reset(self):
        """新的一句开始前清状态（模型只加载一次）"""
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, CONTEXT_SAMPLES), dtype=np.float32)
        self._voiced_run = 0
        self._silence_run = 0
        self.is_speaking = False

    def _prob(self, samples):
        x = np.concatenate([self._context, samples.reshape(1, -1)], axis=1)
        out, state = self.session.run(None, {
            "input": x.astype(np.float32),
            "state": self._state,
            "sr": np.array(SAMPLE_RATE, dtype=np.int64),
        })
        self._state = state
        self._context = x[:, -CONTEXT_SAMPLES:]
        return float(out.reshape(-1)[0])

    def process_frame(self, pcm_int16_bytes):
        """pcm_int16_bytes: 恰好 FRAME_BYTES 字节的 16bit PCM。返回 (状态是否翻转, 当前是否在说话)"""
        samples = np.frombuffer(pcm_int16_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        prob = self._prob(samples)
        changed = False
        if prob >= self.threshold:
            self._voiced_run += 1
            self._silence_run = 0
            if not self.is_speaking and self._voiced_run >= self.onset_frames:
                self.is_speaking = True
                changed = True
        else:
            self._silence_run += 1
            self._voiced_run = 0
            if self.is_speaking and self._silence_run >= self.offset_frames:
                self.is_speaking = False
                changed = True
        return changed, self.is_speaking
