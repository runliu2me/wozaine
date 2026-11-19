#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MiniMax T2A：把回复文字合成语音。

克隆音色到位前，用 MINIMAX_VOICE_ID 指定一个系统占位音色；
克隆完成后只需要把 MINIMAX_VOICE_ID 换成克隆出来的 voice_id，代码不用动。

接口地址因账号所在站点而异（api.minimax.chat / api.minimaxi.com 等），报 invalid api key 时用 MINIMAX_API_URL 换一个。
"""

import binascii
import os

import requests

API_URL = os.environ.get("MINIMAX_API_URL", "https://api.minimax.chat/v1/t2a_v2")
API_KEY = os.environ.get("MINIMAX_API_KEY", "")
GROUP_ID = os.environ.get("MINIMAX_GROUP_ID", "")
VOICE_ID = os.environ.get("MINIMAX_VOICE_ID", "male-qn-qingse")  # 占位系统音色
MODEL = os.environ.get("MINIMAX_TTS_MODEL", "speech-2.6-hd")
# 语气参数：想要更有精神可以设 MINIMAX_TTS_EMOTION=happy
TTS_EMOTION = os.environ.get("MINIMAX_TTS_EMOTION", "")   # happy/neutral/sad/... 或空字符串=不带
TTS_SPEED = float(os.environ.get("MINIMAX_TTS_SPEED", "1.0"))
TTS_PITCH = int(os.environ.get("MINIMAX_TTS_PITCH", "0"))
TIMEOUT = 30


def synthesize_with_voice(text, voice_id):
    """返回 mp3 音频的 bytes，指定具体 voice_id（供保活脚本碰不同候选音色用）"""
    if not API_KEY:
        raise RuntimeError("MINIMAX_API_KEY 未配置")
    params = {"GroupId": GROUP_ID} if GROUP_ID else {}
    resp = requests.post(
        API_URL,
        params=params,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "text": text,
            "stream": False,
            "output_format": "hex",
            "voice_setting": {
                "voice_id": voice_id,
                "speed": TTS_SPEED,
                "vol": 1,
                "pitch": TTS_PITCH,
                # MINIMAX_TTS_EMOTION 非空时才带情绪参数（比如 happy）
                **({"emotion": TTS_EMOTION} if TTS_EMOTION else {}),
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1,
            },
        },
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    base_resp = data.get("base_resp", {})
    if base_resp.get("status_code") not in (0, None):
        raise RuntimeError(f"MiniMax TTS 报错: {base_resp}")
    hex_audio = data["data"]["audio"]
    return binascii.unhexlify(hex_audio)


def synthesize(text):
    """返回 mp3 音频的 bytes，用配置里的默认 voice_id（正式对话走这个）"""
    return synthesize_with_voice(text, VOICE_ID)
