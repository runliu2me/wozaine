#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""语音合成：MiniMax 克隆音色为主，阿里云百炼 CosyVoice 复刻音色兜底。

MiniMax 没钱了（或者出别的错）就用 CosyVoice，妈妈那边不能哑。
CosyVoice 音色用孩子 15 秒左右的录音复刻（cosyvoice-v3-plus，北京地域，每万字约 2 元）。
实测 v3-flash 不够像，v3-plus 声音更像，但慢——整句合成完才返回，
短句约 1.4 秒、两三句话的长段 4–6 秒（MiniMax 约 1 秒）。只做兜底，所以先不改成边合成边播。
MiniMax 失败一次就告警（上层 24 小时去重），两家都失败才抛异常，交给上层放兜底语音。

接口和 minimax_client.synthesize 一样：synthesize(text) -> mp3 bytes。
"""

import json
import os
import sys
import time
import uuid

import websocket  # websocket-client

import asr_client
import minimax_client

DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
# 语音合成只能走公共地址：百炼业务空间专属地址（DASHSCOPE_BASE_URL）不开放 TTS 模型
COSYVOICE_WS_URL = os.environ.get("COSYVOICE_WS_URL", "wss://dashscope.aliyuncs.com/api-ws/v1/inference")
COSYVOICE_MODEL = os.environ.get("COSYVOICE_MODEL", "cosyvoice-v3-plus")
COSYVOICE_VOICE_ID = os.environ.get("COSYVOICE_VOICE_ID", "")
COSYVOICE_RATE = float(os.environ.get("COSYVOICE_RATE", "1.0"))
TIMEOUT = 30

# 上层（xiaozhi_bridge）挂上 raise_alert：fn(key, text)
ALERT_HOOK = None


def _alert(key, text):
    sys.stderr.write(f"[tts] {key}: {text}\n")
    if ALERT_HOOK:
        try:
            ALERT_HOOK(key, text)
        except Exception:
            pass


def _cosyvoice(text):
    if not (DASHSCOPE_API_KEY and COSYVOICE_VOICE_ID):
        raise RuntimeError("CosyVoice 未配置（DASHSCOPE_API_KEY / COSYVOICE_VOICE_ID）")
    deadline = time.time() + TIMEOUT
    ws = websocket.create_connection(COSYVOICE_WS_URL, header=["Authorization: Bearer " + DASHSCOPE_API_KEY], timeout=TIMEOUT)
    try:
        task_id = uuid.uuid4().hex
        ws.send(json.dumps({
            "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
            "payload": {"task_group": "audio", "task": "tts", "function": "SpeechSynthesizer", "model": COSYVOICE_MODEL,
                        "parameters": {"text_type": "PlainText", "voice": COSYVOICE_VOICE_ID, "format": "mp3",
                                       "sample_rate": 24000, "rate": COSYVOICE_RATE},
                        "input": {}},
        }))
        audio = bytearray()
        while time.time() < deadline:
            opcode, data = ws.recv_data()
            if opcode == websocket.ABNF.OPCODE_BINARY:
                audio.extend(data)
                continue
            if opcode == websocket.ABNF.OPCODE_CLOSE:
                raise RuntimeError(f"CosyVoice 连接被关闭: {data[2:200].decode('utf-8', 'ignore')}")
            header = json.loads(data).get("header", {})
            event = header.get("event")
            if event == "task-started":
                for action, payload in (("continue-task", {"input": {"text": text}}), ("finish-task", {"input": {}})):
                    ws.send(json.dumps({"header": {"action": action, "task_id": task_id, "streaming": "duplex"}, "payload": payload}))
            elif event == "task-finished":
                if not audio:
                    raise RuntimeError("CosyVoice 没返回音频")
                return bytes(audio)
            elif event == "task-failed":
                raise RuntimeError(f"CosyVoice 报错 {header.get('error_code')}: {header.get('error_message')}")
        raise RuntimeError(f"CosyVoice 合成超时（{TIMEOUT}s）")
    finally:
        ws.close()


def synthesize(text):
    """返回 mp3 bytes。MiniMax 失败就改用 CosyVoice；两家都失败抛 RuntimeError。"""
    try:
        return minimax_client.synthesize(text)
    except Exception as exc:
        primary_err = exc
    _alert("tts_minimax", f"MiniMax 语音合成失败，已临时改用阿里 CosyVoice 复刻音色：{primary_err}"
                          "（欠费请充值：https://platform.minimaxi.com/user-center/payment/balance）")
    try:
        audio = _cosyvoice(text)
        asr_client._record("tts_cosyvoice")
        return audio
    except Exception as exc:
        asr_client._record("tts_all_fail")
        raise RuntimeError(f"MiniMax 和 CosyVoice 合成都失败：{primary_err}；{exc}") from exc


if __name__ == "__main__":
    # 自检：python3 tts_client.py "文字" out.mp3 [cosyvoice]
    t0 = time.time()
    audio = _cosyvoice(sys.argv[1]) if sys.argv[3:] == ["cosyvoice"] else synthesize(sys.argv[1])
    open(sys.argv[2], "wb").write(audio)
    print(len(audio), f"({time.time() - t0:.1f}s)")
