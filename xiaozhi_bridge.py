#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小智AI协议桥接：让 xiaozhi-esp32 官方固件（不用改固件源码）直接对接咱们自己的
讯飞识别 + DeepSeek对话 + MiniMax克隆语音这套后端，不用重新发明设备端的音频/网络栈。

协议是设备固件（main/protocols/websocket_protocol.cc、main/ota.cc）里读出来的，两层：
1. OTA 接口（HTTP POST）：设备开机先访问这个，上报设备信息，我们回一段 JSON，
   告诉它该连哪个 WebSocket 地址。
2. WebSocket 音频通道：设备连上后先发一句 JSON "hello" 握手，我们必须在10秒内
   回一句 "hello" 确认，之后设备开始发 **Opus 编码**的语音二进制帧（60ms/帧，16kHz单声道），
   我们回复也是同样格式的 Opus 二进制帧。这个编码格式是固件写死的，改不了，
   所以这层"翻译"（Opus解码给讯飞、Opus编码送回设备）是必须做的，不是我们选的。

断句/打断逻辑**不用**我们自己的 VAD——设备按钮按下/松开时，固件会主动发一条
{"type":"listen","state":"start"/"stop"} 的信号（main/protocols/protocol.cc），
这是一句话到底从哪开始到哪结束的权威信号，比我们自己在服务器这边用 Silero VAD 去猜
可靠得多（猜错了就是断句、吞字）。打断也是同一个机制：设备说话的时候用户又按下按钮，
固件会自己 AbortSpeaking() 再发一条新的 "listen start"，我们收到"正在处理/正在说话时
又来了一条 start"就当成打断处理。
"""

import asyncio
import collections
import json
import os
import queue
import random
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import opuslib
import websockets

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import deepseek_client
import family
import tts_client
import memory_store
import asr_client
import vad
import story
import typo_book
import device_gate
import bridge_media

OTA_HOST = os.environ.get("XIAOZHI_OTA_HOST", "127.0.0.1")
OTA_PORT = int(os.environ.get("XIAOZHI_OTA_PORT", "3096"))
WS_HOST = os.environ.get("XIAOZHI_WS_HOST", "127.0.0.1")
WS_PORT = int(os.environ.get("XIAOZHI_WS_PORT", "3097"))
# 设备最终要能连到的公网 WebSocket 地址——这个要走 ten 服务器转发，不是内网地址
PUBLIC_WS_URL = os.environ.get("XIAOZHI_PUBLIC_WS_URL", "wss://CHANGE_ME/xiaozhi/ws")
DEVICE_TOKEN = device_gate.DEVICE_TOKEN  # 设备口令：OTA 下发，WS 带 Authorization: Bearer
# 设为 1 才真正拒绝没带口令的设备（设备在开机做 OTA 检查时会拿到口令，所以先下发、确认两台都带上了再打开）
REQUIRE_DEVICE_TOKEN = device_gate.REQUIRE_DEVICE_TOKEN
# 只有这些设备（MAC）能拿到设备口令、能连 WebSocket；其他人 POST OTA 接口只会拿到时间和地址，连不上
ALLOWED_DEVICE_IDS = device_gate.ALLOWED_DEVICE_IDS
# 电量提醒：设备报电量低于 LOW_BATTERY_PCT 且没在充电 → AI 开口"妈，我快没电了…"；之后每 30 分钟最多提醒一次，
# 低于 CRIT 时每 10 分钟；插上电就安静。电量通过固件的 get_device_status 每 2 分钟查一次。
LOW_BATTERY_PCT = int(os.environ.get("XIAOZHI_LOW_BATTERY_PCT", "20"))
CRIT_BATTERY_PCT = int(os.environ.get("XIAOZHI_CRIT_BATTERY_PCT", "10"))
LOW_BATTERY_TEXT = "妈，我快没电了，帮我接上充电线，给我充充电吧。"
TURN_DEADLINE_SECONDS = int(os.environ.get("XIAOZHI_TURN_DEADLINE", "90"))  # 一轮（识别+生成+合成+播放）最多这么久，超了强制复位，防止设备永远"处理中"

# ---- 测试用故障注入（只在 XIAOZHI_TEST_FAULTS 设了才生效，生产环境永远为空）----
# 形如 "asr=hang,llm=error,tts=slow"；见 tests/run_scenarios.py
_TEST_FAULTS = dict(kv.split("=", 1) for kv in os.environ.get("XIAOZHI_TEST_FAULTS", "").split(",") if "=" in kv)


def _test_fault(name):
    mode = _TEST_FAULTS.get(name)
    if not mode:
        return
    sys.stderr.write(f"[test-fault] {name}={mode}\n")
    if mode == "error":
        raise RuntimeError(f"test fault: {name}")
    if mode == "hang":
        time.sleep(3600)
    if mode == "slow":
        time.sleep(20)
FALLBACK_TEXT = "妈，我这边网络有点不好，你等一会儿再拍一下按钮找我。"
LAST_SEEN = {}  # device_id -> 最近一次收到设备消息的时间（给离线告警用）
BOOTS = collections.defaultdict(lambda: collections.deque(maxlen=30))
_STORY_ASKED_TODAY = collections.deque(maxlen=50)   # 今天各场对话里问过的故事题/核实的故事（daily_report 取走做提取）
_PROACTIVE_TODAY = {"date": "", "n": 0, "retell": 0}   # 今天主动问候了几次、其中几次是"核实"
ALERTS = collections.deque(maxlen=20)   # 需要通知管理员的服务级故障（欠费等），device_monitor 每 15 分钟取走并发告警
_ALERT_LAST = {}


def raise_alert(key, text, cooldown=24 * 3600):
    """记一条告警（同一类 24 小时内只记一次）。"""
    now = time.time()
    if now - _ALERT_LAST.get(key, 0) < cooldown:
        return
    _ALERT_LAST[key] = now
    ALERTS.append({"ts": now, "key": key, "text": text})
    sys.stderr.write(f"[alert] {key}: {text}\n")  # device_id -> 最近 30 次开机 {ts, reason, last_action, uptime}


asr_client.ALERT_HOOK = raise_alert  # 阿里云主线路失败（已改用腾讯）也要通知管理员
tts_client.ALERT_HOOK = raise_alert  # MiniMax 失败（已改用 CosyVoice）也要通知管理员

PERSONA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "persona.md")
PUBLIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")  # OtaHandler.do_GET 对外提供的文件
DATA_DIR = os.environ.get("COMPANION_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
OTA_STAGED_PATH = os.path.join(DATA_DIR, "ota_staged.json")  # 管理页面"推送固件"写的：{board_type: {version, file}}
# 管理页面（server.py）调用控制接口用的共享密钥。OTA 端口经 nginx 对公网开放，
# 所以 /status /say /reboot 这几个必须验 token，只有本机的 server.py 知道
CTL_TOKEN = device_gate.CTL_TOKEN
# 公网可下载文件的根地址（固件包放 public/ 下）：由 WS 地址推出来
PUBLIC_FILES_BASE = PUBLIC_WS_URL.replace("wss://", "https://").replace("ws://", "http://").rsplit("/xiaozhi/ws", 1)[0] + "/xiaozhi/ota/files"

# 在线设备登记：device_id(MAC) -> DeviceSession。WS 连接在 asyncio 线程里，OTA/控制接口在
# http.server 线程里，跨线程一律走 MAIN_LOOP.call_soon_threadsafe
SESSIONS = {}
MAIN_LOOP = None

# 互动：对讲
INTERCOM_HOST = os.environ.get("XIAOZHI_INTERCOM_HOST", "127.0.0.1")
INTERCOM_PORT = int(os.environ.get("XIAOZHI_INTERCOM_PORT", "3098"))  # 手机端对讲 WebSocket（tailscale serve :8459）
# 半双工：按住说话时设备喇叭在放我的声音，麦克风又收进去传回来 = 自己听自己，很炸。
# 所以开麦期间（以及松开后的这一小段尾巴里，设备喇叭还有没播完的我的声音）不再把设备的声音回传给自己。
INTERCOM_HANGOVER = float(os.environ.get("XIAOZHI_INTERCOM_HANGOVER", "0.6"))  # 单位秒
ALLOWED_LOGIN = os.environ.get("ALLOWED_TAILSCALE_LOGIN", "")


def _today():
    return time.strftime("%Y-%m-%d")


def _resample_16k(pcm_bytes, to_rate):
    return bridge_media.resample_pcm_16k(pcm_bytes, to_rate)


def load_staged_firmware():
    try:
        with open(OTA_STAGED_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _version_tuple(v):
    out = []
    for part in str(v or "0").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)

SAMPLE_RATE = 16000  # 上行（设备麦克风→语音识别）固定 16k
OPUS_FRAME_MS = 60
OPUS_FRAME_SAMPLES = SAMPLE_RATE * OPUS_FRAME_MS // 1000  # 960

# 下行（TTS→设备）采样率按设备定：设备用 hello 里我们报的 sample_rate 建解码器，
# 报成和它喇叭一样的采样率就不用在设备上重采样。按 WebSocket 头里的 Device-Id（MAC）区分。
DEFAULT_TX_SAMPLE_RATE = 16000
# .env 里 XIAOZHI_TX_SAMPLE_RATES=MAC=采样率,MAC=采样率，比如喇叭是 24k 的板子填 aa:bb:cc:dd:ee:ff=24000。
# 不填也能用（16k，设备自己重采样），填对了音质更好、设备更省力。
DEVICE_TX_SAMPLE_RATES = {k.strip().lower(): int(v) for k, v in
                          (x.split("=", 1) for x in os.environ.get("XIAOZHI_TX_SAMPLE_RATES", "").split(",") if "=" in x)}
MAX_UTTERANCE_FRAMES = int(30 * 1000 / OPUS_FRAME_MS)  # ~30s 硬上限，防止按钮卡住之类的异常情况

# auto 模式（拍一下连续对话）的服务器端断句参数。VAD 帧 = 32ms
VAD_ONSET_FRAMES = 8          # 连续 ~256ms 有人声才算开口
VAD_OFFSET_FRAMES = 40        # 连续 ~1.3s 静音才算说完——老人说话中间停顿多，宁可等一等
VAD_PREROLL_FRAMES = 20       # 开口前预留 ~640ms，避免吞掉第一个字
MAX_UTTERANCE_VAD_FRAMES = int(30 * 1000 / 32)
CONVERSATION_IDLE_SECONDS = int(os.environ.get("XIAOZHI_IDLE_SECONDS", "30"))  # 这么久没人说话就结束对话，设备回待命（90 秒太长，老人会觉得它一直在听）

# 下行音质/流畅度
OPUS_BITRATE = 48000      # 16k 单声道语音，48kbps 基本透明（libopus 默认会低很多）
OPUS_BITRATE_24K = 64000  # 24k 下行用
TTS_GAIN_DB = float(os.environ.get("XIAOZHI_TTS_GAIN_DB", "0"))  # 下发给设备前整体抬多少分贝
SEND_LEAD_FRAMES = 8      # 每段语音开头先抢发 8 帧（~480ms）垫底，之后匀速；设备缓冲上限 20 帧

# 拍一下之后 AI 先开口的招呼语，随机挑一句（风格跟 persona.md 一致：叫"妈"，不用"您"）
GREETINGS = [
    # 产品就叫"在呢"：每一句开场白第一句都是"妈，我在呢"，后面再接一句别的
    "妈，我在呢。有什么事儿吗？",
    "妈，我在呢。想我了？",
    "妈，我在呢。今天怎么样？",
    "妈，我在呢。吃饭了没？",
    "妈，我在呢。刚忙完，你说。",
    "妈，我在呢。怎么啦？",
    "妈，我在呢。你说，我听着。",
    "妈，我在呢。今天感觉怎么样？",
    "妈，我在呢。慢慢说，不急。",
    "妈，我在呢。今天出去转了没？",
]


# 设备白天主动喊人（固件里的"主动问候"，白天 10–21 点随机 6–7 次）：开场白用这一组
# 主动问候= AI 带着一个好奇心来找她：只喊一句"妈，你在吗？"，她一应声，
# 就接"在啊，有件事想问你……"直接进入故事问题（见 story.prompt_block(proactive=True)）
PROACTIVE_GREETINGS = [
    "妈，你在吗？",
    "妈，在吗？",
]


def load_persona():
    return family.read_persona()


def _find_sentence_end(buf):
    for i, ch in enumerate(buf):
        if ch in "。！？\n":
            return i
    return None


# ================================================================================
# OTA 接口：纯 stdlib http.server，跟 server.py 的风格一致
# ================================================================================
class OtaHandler(BaseHTTPRequestHandler):
    timeout = 30  # 慢客户端/半开连接不能无限占着线程
    server_version = "xiaozhi-ota/0.1"

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return b""
        return self.rfile.read(min(length, 256 * 1024))

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ctl_authorized(self):
        return device_gate.ctl_auth_ok(self.headers.get("X-Ctl-Token", ""))

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/xiaozhi/ota/volume":
            if not self._ctl_authorized():
                self._json(403, {"error": "forbidden"})
                return
            session = next(iter(SESSIONS.values()), None)
            if session is None or MAIN_LOOP is None:
                self._json(409, {"error": "设备不在线"})
                return
            body = {}
            try:
                body = json.loads(self._read_body() or b"{}")
            except Exception:
                pass
            try:
                if body.get("volume") is not None:
                    v = asyncio.run_coroutine_threadsafe(session.set_volume(body["volume"]), MAIN_LOOP).result(timeout=10)
                else:
                    v = asyncio.run_coroutine_threadsafe(session.get_volume(), MAIN_LOOP).result(timeout=10)
                self._json(200, {"ok": True, "volume": v})
            except Exception as exc:
                self._json(504, {"error": f"设备没有回应：{exc}"})
            return
        if path == "/xiaozhi/ota/say" or path == "/xiaozhi/ota/reboot" or path == "/xiaozhi/ota/end":
            if not self._ctl_authorized():
                self._json(403, {"error": "forbidden"})
                return
            try:
                req = json.loads(self._read_body().decode("utf-8") or "{}")
            except Exception:
                req = {}
            device_id = (req.get("device_id") or "").lower()
            session = SESSIONS.get(device_id) if device_id else (next(iter(SESSIONS.values()), None))
            if session is None or MAIN_LOOP is None:
                self._json(409, {"error": "设备不在线"})
                return
            if path.endswith("/say"):
                text = (req.get("text") or "").strip()
                if not text:
                    self._json(400, {"error": "text 为空"})
                    return
                fut = asyncio.run_coroutine_threadsafe(session.push_speech(text), MAIN_LOOP)
                ok, msg = fut.result(timeout=5)
                self._json(200 if ok else 409, {"ok": ok, "message": msg})
                return
            if path.endswith("/reboot"):
                fut = asyncio.run_coroutine_threadsafe(session.send_json({"type": "system", "command": "reboot"}), MAIN_LOOP)
                fut.result(timeout=5)
                self._json(200, {"ok": True})
                return
            fut = asyncio.run_coroutine_threadsafe(session.send_json({"type": "conversation", "state": "end"}), MAIN_LOOP)
            fut.result(timeout=5)
            self._json(200, {"ok": True})
            return

        if path != "/xiaozhi/ota":
            self.send_response(404)
            self.end_headers()
            return

        # 设备开机上报：{"application":{"version":...},"board":{"type":...},...}
        # 我们用它判断有没有给这块板子准备好的新固件
        info = {}
        try:
            info = json.loads(self._read_body().decode("utf-8") or "{}")
        except Exception:
            info = {}
        board_type = (info.get("board") or {}).get("type") or ""
        current_version = (info.get("application") or {}).get("version") or ""
        device_mac = (self.headers.get("Device-Id") or "").lower()

        resp = {
            "server_time": {
                "timestamp": int(time.time() * 1000),
                "timezone_offset": int(family.TZ_HOURS * 60),
            },
            "websocket": {
                "url": PUBLIC_WS_URL,
                # 设备口令只发给白名单里的设备；陌生请求拿不到口令，也就连不上语音通道
                "token": device_gate.ota_device_token(device_mac),
                "version": 1,
            },
        }
        app_info = info.get("application") or {}
        reset_reason = app_info.get("reset_reason") or ""
        last_action = app_info.get("last_action") or ""
        uptime_before = app_info.get("uptime_before_reset")
        sys.stderr.write(f"[ota] device {device_mac} board={board_type} version={current_version}"
                         f" reset={reset_reason} last_action={last_action!r} uptime_before={uptime_before}\n")
        if device_mac and reset_reason:
            BOOTS[device_mac].append({"ts": time.time(), "reason": reset_reason, "last_action": last_action,
                                      "uptime_before": uptime_before, "version": current_version})
        staged = load_staged_firmware().get(board_type) if board_type else None
        if staged and _version_tuple(staged.get("version")) > _version_tuple(current_version):
            resp["firmware"] = {
                "version": staged["version"],
                "url": f"{PUBLIC_FILES_BASE}/{staged['file']}",
            }
            sys.stderr.write(f"[ota] offering firmware {staged['version']} to {board_type} {device_mac} (current {current_version})\n")
        self._json(200, resp)

    def do_GET(self):
        # 顺便当个小文件服务器：https://你的域名/xiaozhi/ota/files/<文件名>
        # 用来把语音对比样本之类的东西发给家人听。
        # 只允许 public/ 目录下的纯文件名，杜绝路径穿越
        if self.path.split("?", 1)[0].rstrip("/") == "/xiaozhi/ota/status":
            if not self._ctl_authorized():
                self._json(403, {"error": "forbidden"})
                return
            devices = []
            for did, s in list(SESSIONS.items()):
                devices.append({
                    "device_id": did, "state": s.state, "listen_mode": s.listen_mode,
                    "conversation_active": s.conversation_active, "tx_rate": s.tx_rate,
                    "connected_at": s.connected_at, "last_activity": s.last_activity, "volume": s.volume, "battery": s.battery,
                })
            self._json(200, {"devices": devices, "staged_firmware": load_staged_firmware(),
                             "last_seen": dict(LAST_SEEN), "require_device_token": REQUIRE_DEVICE_TOKEN,
                             "boots": {k: list(v) for k, v in BOOTS.items()}, "alerts": list(ALERTS),
                             "story_asked": list(_STORY_ASKED_TODAY)})
            return
        prefix = "/xiaozhi/ota/files/"
        if not self.path.startswith(prefix):
            self.send_response(404)
            self.end_headers()
            return
        name = os.path.basename(self.path[len(prefix):].split("?", 1)[0])
        full = os.path.join(PUBLIC_DIR, name)
        if not name or not os.path.isfile(full):
            self.send_response(404)
            self.end_headers()
            return
        ctype = {"mp3": "audio/mpeg", "png": "image/png", "jpg": "image/jpeg", "wav": "audio/wav",
                 "html": "text/html; charset=utf-8"}.get(name.rsplit(".", 1)[-1].lower(), "application/octet-stream")
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write("[ota] %s - %s\n" % (self.address_string(), fmt % args))


def run_ota_server():
    server = ThreadingHTTPServer((OTA_HOST, OTA_PORT), OtaHandler)
    print(f"xiaozhi OTA server: http://{OTA_HOST}:{OTA_PORT}/xiaozhi/ota")
    server.serve_forever()


# ================================================================================
# WebSocket 音频通道
# ================================================================================
class DeviceSession:
    def __init__(self, ws, loop):
        self.ws = ws
        self.loop = loop
        self.state = "listening"  # listening -> user_speaking -> processing -> ai_speaking -> listening
        self.frame_buf = []  # 一段话期间收到的解码后 PCM（60ms/960样本 一段）
        self.out_queue = asyncio.Queue()
        self.cancel_event = threading.Event()
        self.send_lock = asyncio.Lock()

        # 两种断句方式：
        #  manual —— Waveshare 那台：按住说话/松手结束，设备发 listen start/stop 定边界
        #  auto   —— 拍一下进入连续对话的板子，设备一直流音频，由这里用 VAD 断句，
        #            回复完设备自动回到聆听；服务器先打招呼；长时间没人说话就结束对话
        self.listen_mode = "manual"
        self.conversation_active = False
        self.vad = None
        self.vad_buf = bytearray()
        self.vad_in_speech = False
        self.preroll = collections.deque(maxlen=VAD_PREROLL_FRAMES)
        self.last_activity = time.time()
        self.connected_at = time.time()
        self.turn_started_at = 0.0
        self.token_ok = False
        self.proactive_next = False  # 设备说下一次对话是它主动发起的 → 用"妈，你在吗？"开场
        self.mcp_seq = 0
        self.mcp_pending = {}        # id -> asyncio.Future（设备回复 MCP 结果）
        self.volume = None           # 最近一次查到的喇叭音量（0-100）
        self.battery = None          # {"level": 0-100, "charging": bool}
        self.low_battery_said_at = 0.0
        self.story_q = None          # 这场对话里要问的故事问题（StoryWorth 式口述传记，见 story.py）
        self.story_after_turns = 1   # 妈妈说了几句之后才把故事题给模型
        self.proactive_story = False
        self.user_turns = 0

        # 互动：对讲（手机端 WebSocket）
        self.intercom_ws = None        # 手机端连接；不为 None 时设备麦克风声音直接转发给手机，不做识别
        self.intercom_talking = False  # 手机端开麦中（设备处于 Speaking，在播手机传来的声音）
        self._intercom_buf = bytearray()

        self.opus_decoder = opuslib.Decoder(SAMPLE_RATE, 1)
        # 下行采样率按设备定（见 DEVICE_TX_SAMPLE_RATES）
        device_id = ""
        try:
            device_id = (ws.request.headers.get("Device-Id") or "").strip().lower()
        except Exception:
            pass
        self.device_id = device_id
        self.tx_rate = DEVICE_TX_SAMPLE_RATES.get(device_id, DEFAULT_TX_SAMPLE_RATE)
        self.tx_frame_samples = self.tx_rate * OPUS_FRAME_MS // 1000
        self.opus_encoder = opuslib.Encoder(self.tx_rate, 1, opuslib.APPLICATION_VOIP)
        # 默认码率偏低（听感发闷/偶尔有沙沙声），单声道语音 48k(16kHz)/64k(24kHz) 已接近透明；复杂度拉满
        self.opus_encoder.bitrate = OPUS_BITRATE if self.tx_rate <= 16000 else OPUS_BITRATE_24K
        self.opus_encoder.complexity = 10

    async def mcp_call(self, name, arguments=None, timeout=8):
        """调用固件内置的 MCP 工具（self.get_device_status / self.audio_speaker.set_volume ...）"""
        self.mcp_seq += 1
        rid = self.mcp_seq
        fut = self.loop.create_future()
        self.mcp_pending[rid] = fut
        try:
            await self.send_json({"type": "mcp", "payload": {"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                                                             "params": {"name": name, "arguments": arguments or {}}}})
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.mcp_pending.pop(rid, None)

    def handle_mcp_reply(self, payload):
        rid = payload.get("id")
        fut = self.mcp_pending.get(rid)
        if fut is None or fut.done():
            return
        if "error" in payload:
            fut.set_exception(RuntimeError(str(payload["error"])[:200]))
        else:
            fut.set_result(payload.get("result"))

    async def get_device_status(self):
        r = await self.mcp_call("self.get_device_status")
        # 固件把状态 JSON 塞在 content[0].text 里
        text = ((r or {}).get("content") or [{}])[0].get("text", "")
        st = json.loads(text)
        try:
            self.volume = int((st.get("audio_speaker") or {}).get("volume"))
        except Exception:
            pass
        b = st.get("battery")
        if isinstance(b, dict) and b.get("level") is not None:
            self.battery = {"level": int(b["level"]), "charging": bool(b.get("charging"))}
        return st

    async def get_volume(self):
        try:
            await self.get_device_status()
        except Exception:
            pass
        return self.volume

    async def battery_watch(self):
        """每 2 分钟问一次电量；快没电就让 AI 开口提醒（只在设备空闲、她没在说话时）"""
        await asyncio.sleep(20)
        while True:
            try:
                await self.get_device_status()
                b = self.battery
                if b and not b["charging"] and b["level"] <= LOW_BATTERY_PCT:
                    interval = 600 if b["level"] <= CRIT_BATTERY_PCT else 1800
                    if time.time() - self.low_battery_said_at > interval and self.state in ("listening", "user_speaking") and not self.vad_in_speech:
                        ok, _ = await self.push_speech(LOW_BATTERY_TEXT)
                        if ok:
                            self.low_battery_said_at = time.time()
                            sys.stderr.write(f"[battery] {self.device_id}: {b['level']}% not charging -> reminded mom\n")
                            if b["level"] <= CRIT_BATTERY_PCT:
                                raise_alert("device_battery", f"{family.ELDER_LABEL}的设备电量只剩 {b['level']}%，没在充电，已经提醒{family.ELDER_PRONOUN}接充电线", cooldown=3600)
            except websockets.exceptions.ConnectionClosed:
                return
            except Exception:
                pass
            await asyncio.sleep(120)

    async def set_volume(self, volume):
        volume = max(0, min(100, int(volume)))
        await self.mcp_call("self.audio_speaker.set_volume", {"volume": volume})
        self.volume = volume
        return volume

    async def intercom_start(self, ws):
        self.intercom_ws = ws
        self.intercom_talking = False
        self._talk_ended_at = 0.0
        await self.send_json({"type": "system", "command": "start_intercom"})

    async def intercom_stop(self):
        if self.intercom_talking:
            await self.intercom_talk(False)
        self.intercom_ws = None
        await self.send_json({"type": "system", "command": "stop_intercom"})

    async def intercom_talk(self, on):
        """手机端开麦/闭麦。开麦时给设备发 tts start 让它进入播放态（Realtime 模式下麦克风照常开着）"""
        if on and not self.intercom_talking:
            self.intercom_talking = True
            self.cancel_event.set()  # 打断正在放的任何东西
            while not self.out_queue.empty():
                try:
                    self.out_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            await self.send_json({"type": "tts", "state": "start"})
        elif not on and self.intercom_talking:
            self.intercom_talking = False
            self._talk_ended_at = time.time()   # 开始计尾巴，见 INTERCOM_HANGOVER
            await self.send_json({"type": "tts", "state": "stop"})

    def intercom_audio(self, pcm16k):
        """手机端麦克风 PCM(16k) → 设备。走 out_queue，由 sender_loop 按节奏发"""
        if not self.intercom_talking:
            return
        pcm = _resample_16k(pcm16k, self.tx_rate)
        self._intercom_buf = getattr(self, "_intercom_buf", bytearray()) + pcm
        frame_bytes = self.tx_frame_samples * 2
        while len(self._intercom_buf) >= frame_bytes:
            chunk = bytes(self._intercom_buf[:frame_bytes])
            del self._intercom_buf[:frame_bytes]
            self.out_queue.put_nowait({"__kind__": "binary", "data": self.opus_encoder.encode(chunk, self.tx_frame_samples)})

    async def send_json(self, obj):
        async with self.send_lock:
            await self.ws.send(json.dumps(obj, ensure_ascii=False))

    async def send_binary(self, data):
        async with self.send_lock:
            await self.ws.send(data)

    async def handle_opus_frame(self, opus_bytes):
        try:
            pcm = self.opus_decoder.decode(opus_bytes, OPUS_FRAME_SAMPLES)
        except Exception:
            return  # 坏包直接丢，不值得为一帧解码失败中断整个会话
        if self.intercom_ws is not None:
            # 对讲/监听中：设备的声音原样转给手机（16k PCM），不做识别。
            # 半双工：我正按着说话（或刚松开，设备还在播我最后一段）时，不往回传——
            # 否则我自己的声音绕一圈回到耳朵里，又响又炸。
            if self.intercom_talking:
                return
            if time.time() - getattr(self, "_talk_ended_at", 0.0) < INTERCOM_HANGOVER:
                return
            try:
                await self.intercom_ws.send(pcm)
            except Exception:
                pass
            return
        if self.state != "user_speaking":
            return  # 不在"按钮按着"/"聆听中"的窗口内，这帧音频不属于任何一句话，丢掉
        if self.listen_mode == "auto":
            self._feed_vad(pcm)
            return
        self.frame_buf.append(pcm)
        if len(self.frame_buf) >= MAX_UTTERANCE_FRAMES:
            # 按钮卡住之类的异常保护，正常按住说话不会碰到这个上限
            self._finalize_utterance()

    def _feed_vad(self, pcm):
        """auto 模式：Silero VAD 断句。VAD 按 32ms/512 样本一帧吃，opus 是 60ms 一帧，先攒再切"""
        self.vad_buf += pcm
        while len(self.vad_buf) >= vad.FRAME_BYTES:
            chunk = bytes(self.vad_buf[:vad.FRAME_BYTES])
            del self.vad_buf[:vad.FRAME_BYTES]
            changed, speaking = self.vad.process_frame(chunk)
            if speaking:
                if not self.vad_in_speech:
                    # 刚开口：把开口前的预留帧一起算进去，别把第一个字吞了
                    self.vad_in_speech = True
                    self.frame_buf = list(self.preroll)
                    self.preroll.clear()
                self.frame_buf.append(chunk)
                self.last_activity = time.time()
                if len(self.frame_buf) >= MAX_UTTERANCE_VAD_FRAMES:
                    self._finalize_utterance()
                    return
            else:
                if self.vad_in_speech and changed:
                    # 说完了（静音持续够长）→ 这一句结束
                    self._finalize_utterance()
                    return
                self.preroll.append(chunk)

    def _reset_vad(self):
        if self.vad is None:
            self.vad = vad.StreamVAD(onset_frames=VAD_ONSET_FRAMES, offset_frames=VAD_OFFSET_FRAMES)
        else:
            self.vad.reset()
        self.vad_buf = bytearray()
        self.vad_in_speech = False
        self.preroll.clear()
        self.frame_buf = []

    async def handle_listen_event(self, data):
        state = data.get("state")
        if state == "start":
            mode = data.get("mode", "manual")
            self.listen_mode = "auto" if mode == "auto" else "manual"
            was_busy = self.state in ("processing", "ai_speaking")
            if was_busy:
                # 设备正在处理/说话的时候又来了一条 start —— 这是用户按钮打断，
                # 固件那边已经 AbortSpeaking() 了，我们这边要同步取消还没发完的内容
                self.cancel_event.set()
                while not self.out_queue.empty():
                    try:
                        self.out_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
            self.last_activity = time.time()
            if mode == "realtime":
                # 对讲模式（管理页发起）：设备麦克风常开，声音直接转发手机，不识别、不打招呼
                self.listen_mode = "realtime"
                self.conversation_active = False
                self.state = "user_speaking"
                return
            if self.listen_mode == "auto":
                self._reset_vad()
                if not self.conversation_active:
                    # 拍一下刚进入对话：先由 AI 打招呼，说完设备会自动回到聆听
                    self.conversation_active = True
                    self._start_greeting()
                    return
                self.state = "user_speaking"
                return
            self.frame_buf = []
            self.state = "user_speaking"
        elif state == "stop":
            if self.listen_mode == "auto":
                # 用户拍一下结束对话（或固件收到 conversation/end 后回到待命）
                self.conversation_active = False
                if self.state in ("processing", "ai_speaking"):
                    self.cancel_event.set()
                # 已经排进发送队列、还没发出去的音频帧也要清掉（场景测试抓到：拍停之后还会漏 18 帧过去）
                while not self.out_queue.empty():
                    try:
                        self.out_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                self._reset_vad()
                self.state = "listening"
                return
            if self.state == "user_speaking":
                self._finalize_utterance()

    def _finalize_utterance(self):
        pcm = b"".join(self.frame_buf)
        self.frame_buf = []
        self.state = "processing"
        self._start_turn(pcm)

    def _start_turn(self, pcm):
        cancel_event = threading.Event()
        self.cancel_event = cancel_event
        self.turn_started_at = time.time()
        threading.Thread(target=self._worker, args=(pcm, cancel_event), daemon=True).start()

    def _emit(self, obj):
        self.loop.call_soon_threadsafe(self.out_queue.put_nowait, obj)

    def _set_state(self, state):
        self.loop.call_soon_threadsafe(setattr, self, "state", state)

    def _rearm_after_turn(self):
        """一轮结束后回到哪个状态（在 worker 线程里调）：
        manual 模式回到 listening 等下一次按键；auto 模式对话还在继续，重新武装 VAD 等她开口"""
        def _do():
            if self.listen_mode == "auto" and self.conversation_active:
                self._reset_vad()
                self.state = "user_speaking"
            else:
                self.state = "listening"
        self.loop.call_soon_threadsafe(_do)

    def _start_greeting(self):
        """拍一下进入对话：由 AI 先开口打招呼。设备主动问候时改用"妈，你在吗？"这一组"""
        self.user_turns = 0
        self.proactive_story = bool(self.proactive_next)   # 主动来找她的：她一应声就进入故事
        self.story_after_turns = 1
        self.story_entry = None   # "核实"模式：这场要讲给她听的那段故事
        today = time.strftime("%Y-%m-%d")
        if _PROACTIVE_TODAY["date"] != today:
            _PROACTIVE_TODAY.update(date=today, n=0, retell=0)
        try:
            retell = None
            if self.proactive_next:
                _PROACTIVE_TODAY["n"] += 1
                # 一天几次主动问候里挑 1-2 次做"核实与反馈"：第 2 次固定核实，之后每次 30% 机会，最多 2 次
                n = _PROACTIVE_TODAY["n"]
                if _PROACTIVE_TODAY["retell"] < 2 and (n == 2 or (n > 2 and random.random() < 0.3)):
                    retell = story.pick_retell()
            if retell:
                self.story_entry = retell
                _PROACTIVE_TODAY["retell"] += 1
                _STORY_ASKED_TODAY.append({"date": today, "kind": "retell", "entry_id": retell["id"], "text": retell.get("question", "")})
                sys.stderr.write(f"[story] retell/verify: {retell.get('question', '')}\n")
            else:
                self.story_q = story.pick_question()
                if self.story_q:
                    _STORY_ASKED_TODAY.append({"date": today, "kind": "ask", "text": self.story_q["text"]})
                    sys.stderr.write(f"[story] ask: {self.story_q['text']}\n")
        except Exception:
            traceback.print_exc()
            self.story_q = None
        if self.proactive_next:
            self.proactive_next = False
            sys.stderr.write(f"[ws] {self.device_id}: proactive check-in\n")
            self._start_speak(random.choice(PROACTIVE_GREETINGS))
            return
        self._start_speak(random.choice(GREETINGS))

    def _start_speak(self, text):
        cancel_event = threading.Event()
        self.cancel_event = cancel_event
        self.state = "processing"
        self.turn_started_at = time.time()
        threading.Thread(target=self._speak_worker, args=(text, cancel_event), daemon=True).start()

    async def push_speech(self, text):
        """管理页面"主动对妈妈说一句话"：设备空闲时直接把这句话用孩子的克隆声音放出去。
        放完设备会进入聆听（auto 模式），妈妈可以接着回，30 秒没人说话自动收。"""
        if self.state not in ("listening", "user_speaking"):
            return False, f"设备正忙（{self.state}），稍后再试"
        if self.vad_in_speech:
            return False, f"{family.ELDER_LABEL}正在说话，稍后再试"
        self.listen_mode = "auto"
        self.conversation_active = True
        self.last_activity = time.time()
        self._start_speak(text)
        return True, "已发送"

    def _speak_fallback(self, cancel_event, tts_started):
        """某一环（识别/DeepSeek/MiniMax）出错时，别让妈妈那边只是一片沉默：
        用提前合成好的一句话告诉她"网络不好，等会儿再来"。合成结果缓存在磁盘上，
        所以哪怕 MiniMax 本身挂了也能说出来。"""
        try:
            frames = _fallback_frames(self.tx_rate, self.tx_frame_samples, self.opus_encoder)
            if not frames or cancel_event.is_set():
                return
            if not tts_started:
                self._emit({"__kind__": "json", "type": "tts", "state": "start"})
            self._emit({"__kind__": "json", "type": "tts", "state": "sentence_start", "text": FALLBACK_TEXT})
            for f in frames:
                if cancel_event.is_set():
                    return
                self._emit({"__kind__": "binary", "data": f})
        except Exception:
            traceback.print_exc()

    def _speak_worker(self, text, cancel_event):
        try:
            self._set_state("ai_speaking")
            self._emit({"__kind__": "json", "type": "tts", "state": "start"})
            _test_fault("tts")
            mp3_audio = tts_client.synthesize(text)
            if cancel_event.is_set():
                return
            pcm16k = _mp3_to_pcm_mono(mp3_audio, self.tx_rate)
            self._emit({"__kind__": "json", "type": "tts", "state": "sentence_start", "text": text})
            for opus_frame in self._encode_opus_frames(pcm16k):
                if cancel_event.is_set():
                    return
                self._emit({"__kind__": "binary", "data": opus_frame})
            memory_store.append_history("assistant", text,
                                        audio=memory_store.save_audio(pcm16k, self.tx_rate, "assistant"))
        except Exception as exc:
            traceback.print_exc()
            _note_upstream_error(exc)
            self._emit({"__kind__": "error", "error": str(exc)})
            self._speak_fallback(cancel_event, tts_started=True)
        finally:
            if not cancel_event.is_set():
                self._emit({"__kind__": "json", "type": "tts", "state": "stop"})
                self._rearm_after_turn()

    async def idle_watchdog(self):
        """auto 模式：她很久没说话就把对话收掉，设备回到待命（省电、也免得一直开着麦）。
        另外盯着每一轮的时长：哪一环卡死了（识别不回、生成慢、合成挂）就强制复位，
        不然设备会永远停在"处理中"，之后她按按钮都没反应。"""
        while True:
            await asyncio.sleep(5)
            try:
                now = time.time()
                if (self.state in ("processing", "ai_speaking") and self.turn_started_at
                        and now - self.turn_started_at > TURN_DEADLINE_SECONDS):
                    sys.stderr.write(f"[watchdog] {self.device_id}: turn stuck in {self.state} for "
                                     f"{int(now - self.turn_started_at)}s, resetting\n")
                    self.cancel_event.set()
                    self.turn_started_at = 0.0
                    await self.send_json({"type": "tts", "state": "stop"})
                    if self.listen_mode == "auto" and self.conversation_active:
                        self._reset_vad()
                        self.state = "user_speaking"
                        self.last_activity = now
                    else:
                        self.state = "listening"
                if (self.listen_mode == "auto" and self.conversation_active
                        and self.state == "user_speaking"
                        and now - self.last_activity > CONVERSATION_IDLE_SECONDS):
                    self.conversation_active = False
                    self.state = "listening"
                    await self.send_json({"type": "conversation", "state": "end"})
            except websockets.exceptions.ConnectionClosed:
                return
            except Exception:
                traceback.print_exc()

    def _encode_opus_frames(self, pcm_mono_bytes):
        """把整段 PCM（采样率 = self.tx_rate）切成 60ms 的 Opus 帧列表"""
        frame_bytes = self.tx_frame_samples * 2
        frames = []
        for i in range(0, len(pcm_mono_bytes), frame_bytes):
            chunk = pcm_mono_bytes[i:i + frame_bytes]
            if len(chunk) < frame_bytes:
                chunk = chunk + b"\x00" * (frame_bytes - len(chunk))
            frames.append(self.opus_encoder.encode(chunk, self.tx_frame_samples))
        return frames

    def _worker(self, pcm, cancel_event):
        try:
            _test_fault("asr")
            user_text = asr_client.transcribe_pcm(pcm)
        except Exception as exc:
            sys.stderr.write(f"[asr] {self.device_id}: 识别失败: {exc}\n")
            _note_upstream_error(exc)
            self._emit({"__kind__": "error", "error": f"识别出错: {exc}"})
            self._speak_fallback(cancel_event, tts_started=False)
            self._emit({"__kind__": "json", "type": "tts", "state": "stop"})
            self._rearm_after_turn()
            return
        user_text = user_text.strip()
        if not user_text:
            self._rearm_after_turn()
            return
        # 错字本：入库和给模型的都是纠正后的文字，识别原文另存（append_history 的 raw）
        asr_raw = user_text
        try:
            user_text = typo_book.correct_text(asr_raw)
        except Exception:
            traceback.print_exc()
        self._emit({"__kind__": "json", "type": "stt", "text": user_text})
        # 把妈妈这句话的原声存下来（管理页记录里可以回放），并且**马上**记进对话记录：
        # 不能等回复放完才一起记：她中途拍一下（取消），这句话就丢了
        user_audio = memory_store.save_audio(pcm, SAMPLE_RATE, "user")
        memory_store.append_history("user", user_text, audio=user_audio, raw=asr_raw)
        try:
            self._worker_reply(pcm, cancel_event, user_text, user_audio)
        except Exception as exc:
            # 这里以外的任何代码错误都会让线程悄悄死掉——她说的话既没回、也没记，状态卡在 processing。
            # 兜底：先把她的话记下来，再放兜底语音，再复位。
            traceback.print_exc()
            _note_upstream_error(exc)
            self._set_state("ai_speaking")
            self._emit({"__kind__": "json", "type": "tts", "state": "start"})
            self._speak_fallback(cancel_event, tts_started=True)
            self._emit({"__kind__": "json", "type": "tts", "state": "stop"})
            self._rearm_after_turn()

    def _worker_reply(self, pcm, cancel_event, user_text, user_audio):
        reply_pcm = bytearray()

        self.user_turns += 1
        # 主动唤醒的对话：先聊两句眼前的事，第三句起才进入"故事"；拍一下发起的：第二句起
        if self.story_entry:
            extra = story.retell_block(self.story_entry, turn=self.user_turns)
        elif self.user_turns >= self.story_after_turns:
            extra = story.prompt_block(self.story_q, proactive=self.proactive_story, turn=self.user_turns)
        else:
            extra = ""
        persona_text = load_persona() + extra
        facts = memory_store.load_facts()
        recent_turns = memory_store.recent_history(limit=12)

        self._set_state("ai_speaking")
        self._emit({"__kind__": "json", "type": "tts", "state": "start"})

        sentence_queue = queue.Queue()
        full_reply = {"text": ""}

        def produce_text():
            buf = ""
            try:
                _test_fault("llm")
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

        try:
            while True:
                item = sentence_queue.get()
                if item is None:
                    break
                if isinstance(item, tuple) and item[0] == "__error__":
                    _note_upstream_error(RuntimeError(item[1]))
                    self._emit({"__kind__": "error", "error": item[1]})
                    if not reply_pcm:
                        self._speak_fallback(cancel_event, tts_started=True)
                    break
                if cancel_event.is_set() or not item:
                    continue
                _test_fault("tts")
                mp3_audio = tts_client.synthesize(item)
                if cancel_event.is_set():
                    break
                pcm16k = _mp3_to_pcm_mono(mp3_audio, self.tx_rate)
                reply_pcm += pcm16k
                # 设备固件用这个消息把回复文字显示在屏幕上（不是我们之前瞎起的
                # stt_reply_text，那个固件根本不认，见 main/application.cc 里
                # 对 type=="tts" state=="sentence_start" 的处理）
                self._emit({"__kind__": "json", "type": "tts", "state": "sentence_start", "text": item})
                for opus_frame in self._encode_opus_frames(pcm16k):
                    if cancel_event.is_set():
                        break
                    self._emit({"__kind__": "binary", "data": opus_frame})
        except Exception as exc:
            traceback.print_exc()
            _note_upstream_error(exc)
            self._emit({"__kind__": "error", "error": str(exc)})
            if not reply_pcm:
                self._speak_fallback(cancel_event, tts_started=True)

        reply_text = full_reply["text"].strip()
        if cancel_event.is_set() and reply_text:
            # 她中途拍停了：回复没放完也记下来（标注），记录才完整
            memory_store.append_history("assistant", "[被打断] " + reply_text)
        if not cancel_event.is_set():
            reply_audio = memory_store.save_audio(bytes(reply_pcm), self.tx_rate, "assistant")
            memory_store.append_history("assistant", reply_text, audio=reply_audio)

            def _extract_facts_bg():
                try:
                    new_facts = deepseek_client.extract_facts(user_text, reply_text)
                    memory_store.add_facts(new_facts)
                except Exception:
                    traceback.print_exc()
            threading.Thread(target=_extract_facts_bg, daemon=True).start()

            self._emit({"__kind__": "json", "type": "tts", "state": "stop"})
            self._rearm_after_turn()

    async def keepalive_loop(self):
        """设备固件 120 秒收不到服务器任何消息就把通道当成失效、断开重连（每 2 分钟一次 TLS 握手，
        白白折腾）。这里每 50 秒发一条空消息让它知道服务器还在。"""
        while True:
            await asyncio.sleep(50)
            try:
                await self.send_json({"type": "ping"})
            except websockets.exceptions.ConnectionClosed:
                return
            except Exception:
                traceback.print_exc()

    async def sender_loop(self):
        # 设备固件的音频解码缓冲区只有 MAX_DECODE_PACKETS_IN_QUEUE 帧（约1.2秒），
        # 满了就直接丢包（PushPacketToDecodeQueue 非阻塞模式）。这里必须按真实播放
        # 节奏（每帧60ms）限速发送，否则整段回复瞬间发完会把设备缓冲区挤爆，
        # 表现就是"说一半突然跳到下一句"。
        next_send_time = None
        frame_interval = OPUS_FRAME_MS / 1000
        while True:
            obj = await self.out_queue.get()
            kind = obj.get("__kind__")
            if kind == "binary":
                now = self.loop.time()
                lead = frame_interval * SEND_LEAD_FRAMES
                if next_send_time is None or next_send_time < now - lead:
                    # 新一段语音开始：允许先一口气发 SEND_LEAD_FRAMES 帧垫在设备缓冲里，
                    # 之后再按 60ms 一帧匀速发——始终领先播放约半秒，网络抖一下也不断音；
                    # 领先量远小于设备 20 帧的缓冲上限，不会挤爆
                    next_send_time = now - lead
                if next_send_time > now:
                    await asyncio.sleep(next_send_time - now)
                await self.send_binary(obj["data"])
                next_send_time += frame_interval
            elif kind == "error":
                await self.send_json({"type": "error", "message": obj["error"]})
            else:
                payload = {k: v for k, v in obj.items() if k != "__kind__"}
                await self.send_json(payload)


def _mp3_to_pcm_mono(mp3_bytes, rate):
    return bridge_media.mp3_to_pcm_mono(mp3_bytes, rate, TTS_GAIN_DB)


_FALLBACK = None
_FALLBACK_LOCK = threading.Lock()


def _fallback_cache():
    global _FALLBACK
    with _FALLBACK_LOCK:
        if _FALLBACK is None:
            _FALLBACK = bridge_media.FallbackOpusCache(
                DATA_DIR, FALLBACK_TEXT, tts_client.synthesize,
                OPUS_BITRATE, OPUS_BITRATE_24K, OPUS_FRAME_MS,
            )
        return _FALLBACK


def _note_upstream_error(exc):
    """把厂商报错翻译成告警：欠费/额度这类必须让管理员马上知道"""
    msg = str(exc)
    low = msg.lower()
    if "合成都失败" in msg:
        raise_alert("tts_down", "MiniMax 和阿里 CosyVoice 语音合成都失败，设备发不出声音了！" + msg[:300])
    elif "识别都失败" in msg:
        # 教训：旧规则只认个别错误码时，主备都挂了也可能没人知道。
        # 现在不猜错误码：主备两家识别都失败就告警（主线路单独失败由 asr_client 自己告警）
        raise_alert("asr_down", "阿里云和腾讯云语音识别都失败，设备听不懂老人说话了！" + msg[:300])
    elif "insufficient balance" in low or "1008" in msg:
        raise_alert("minimax_balance", "MiniMax（克隆声音）账户余额不足，设备现在发不出声音！请马上充值：platform.minimaxi.com")
    elif "402" in msg or "Insufficient" in msg:
        raise_alert("deepseek_balance", "DeepSeek 余额不足，设备无法生成回复，请马上充值")


def _fallback_frames(rate, frame_samples, _encoder_unused):
    return _fallback_cache().frames(rate, frame_samples, TTS_GAIN_DB)


def _prepare_fallbacks():
    for rate in sorted(set([DEFAULT_TX_SAMPLE_RATE] + list(DEVICE_TX_SAMPLE_RATES.values()))):
        n = len(_fallback_frames(rate, rate * OPUS_FRAME_MS // 1000, None))
        print(f"[fallback] {rate} Hz: {n} frames ready")


async def _fetch_volume_quiet(session):
    try:
        v = await session.get_volume()
        sys.stderr.write(f"[ws] {session.device_id}: speaker volume = {v}\n")
    except Exception as exc:
        sys.stderr.write(f"[ws] volume query failed: {exc}\n")


async def handle_connection(ws):
    loop = asyncio.get_event_loop()
    session = DeviceSession(ws, loop)
    # 设备口令：OTA 响应里下发，设备存在 NVS、连 WS 时带上
    try:
        auth = (ws.request.headers.get("Authorization") or "").strip()
    except Exception:
        auth = ""
    ok, token_ok, why = device_gate.ws_accept(session.device_id, auth)
    session.token_ok = token_ok
    if not ok:
        sys.stderr.write(f"[ws] device {session.device_id or '?'} rejected ({why})\n")
        code = 4003 if why == "device not in allowlist" else 4001
        await ws.close(code=code, reason=why[:60] or "unauthorized")
        return
    if DEVICE_TOKEN and not session.token_ok:
        # REQUIRE 已在 device_gate.ws_accept 里拒绝；走到这里说明尚未强制口令
        sys.stderr.write(
            f"[ws] device {session.device_id or '?'} connected WITHOUT valid token (not enforced yet)\n"
        )
    if session.device_id:
        LAST_SEEN[session.device_id] = time.time()
    sender_task = asyncio.create_task(session.sender_loop())
    watchdog_task = asyncio.create_task(session.idle_watchdog())
    keepalive_task = asyncio.create_task(session.keepalive_loop())
    battery_task = asyncio.create_task(session.battery_watch())
    hello_received = False
    reg_key = session.device_id or f"anon-{id(session):x}"
    SESSIONS[reg_key] = session
    try:
        async for message in ws:
            if session.device_id:
                LAST_SEEN[session.device_id] = time.time()
            if isinstance(message, (bytes, bytearray)):
                if not hello_received:
                    continue  # 握手完成前不处理音频
                await session.handle_opus_frame(bytes(message))
            else:
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    continue
                msg_type = data.get("type")
                if msg_type == "hello" and not hello_received:
                    hello_received = True
                    await session.send_json({
                        "type": "hello",
                        "transport": "websocket",
                        "session_id": os.urandom(8).hex(),
                        "audio_params": {
                            "sample_rate": session.tx_rate,   # 设备按这个建解码器
                            "frame_duration": OPUS_FRAME_MS,
                        },
                    })
                    print(f"[ws] device hello ok, audio_params from device: {data.get('audio_params')}")
                    asyncio.get_event_loop().call_later(2.0, lambda: asyncio.ensure_future(_fetch_volume_quiet(session)))
                elif msg_type == "listen" and hello_received:
                    await session.handle_listen_event(data)
                elif msg_type == "companion" and data.get("event") == "proactive":
                    session.proactive_next = True
                elif msg_type == "mcp" and isinstance(data.get("payload"), dict):
                    session.handle_mcp_reply(data["payload"])
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        session.cancel_event.set()  # 设备断了，别让后台线程继续给一个死连接生成回复
        sender_task.cancel()
        watchdog_task.cancel()
        keepalive_task.cancel()
        battery_task.cancel()
        if SESSIONS.get(reg_key) is session:
            del SESSIONS[reg_key]


async def handle_intercom(ws):
    """手机端"互动"页的对讲 WebSocket（经 tailscale serve 暴露，只有本人的设备能连）。
    协议：手机发 JSON {"type":"start"} 开始（设备麦克风声音以 16k PCM 二进制帧回传）；
    {"type":"talk","on":true/false} 开麦/闭麦；开麦时手机发 16k PCM 二进制帧 → 播给设备；
    关闭连接 = 结束对讲。"""
    try:
        login = ws.request.headers.get("Tailscale-User-Login", "")
    except Exception:
        login = ""
    if ALLOWED_LOGIN and login and login != ALLOWED_LOGIN:
        await ws.close(code=4403, reason="forbidden")
        return
    session = None
    try:
        async for message in ws:
            if isinstance(message, (bytes, bytearray)):
                if session is not None:
                    session.intercom_audio(bytes(message))
                continue
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue
            t = data.get("type")
            if t == "start":
                session = SESSIONS.get((data.get("device_id") or "").lower()) or next(iter(SESSIONS.values()), None)
                if session is None:
                    await ws.send(json.dumps({"type": "error", "message": "设备不在线"}))
                    continue
                if session.intercom_ws is not None and session.intercom_ws is not ws:
                    await ws.send(json.dumps({"type": "error", "message": "已经有一个对讲在进行"}))
                    continue
                await session.intercom_start(ws)
                await ws.send(json.dumps({"type": "started", "device_id": session.device_id, "state": session.state}))
            elif t == "talk" and session is not None:
                await session.intercom_talk(bool(data.get("on")))
                await ws.send(json.dumps({"type": "talk", "on": session.intercom_talking}))
            elif t == "stop":
                break
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        if session is not None and session.intercom_ws is ws:
            try:
                await session.intercom_stop()
            except Exception:
                pass


def _sd_notify(msg):
    """给 systemd 发心跳（Type=notify + WatchdogSec）。没在 systemd 下跑就什么都不做。"""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    try:
        import socket
        if addr.startswith("@"):
            addr = "\0" + addr[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(msg.encode())
    except Exception:
        pass


async def _heartbeat(ota_thread):
    """事件循环还活着、OTA 线程还活着才喂狗；任何一个死了就让 systemd 把整个进程拉起来。"""
    _sd_notify("READY=1")
    while True:
        await asyncio.sleep(20)
        if not ota_thread.is_alive():
            sys.stderr.write("[heartbeat] OTA server thread died, exiting for systemd restart\n")
            os._exit(1)
        _sd_notify("WATCHDOG=1")


async def run_ws_server(ota_thread):
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()
    # max_size：一帧 Opus 只有几百字节；不设上限的话一个坏客户端一帧就能把内存吃光
    async with websockets.serve(handle_connection, WS_HOST, WS_PORT, max_size=1024 * 1024), \
            websockets.serve(handle_intercom, INTERCOM_HOST, INTERCOM_PORT, max_size=1024 * 1024):
        print(f"xiaozhi WS server: ws://{WS_HOST}:{WS_PORT}; intercom: ws://{INTERCOM_HOST}:{INTERCOM_PORT}")
        asyncio.create_task(_heartbeat(ota_thread))
        await asyncio.Future()


def main():
    ota_thread = threading.Thread(target=run_ota_server, daemon=True)
    ota_thread.start()
    threading.Thread(target=_prepare_fallbacks, daemon=True).start()
    asyncio.run(run_ws_server(ota_thread))


if __name__ == "__main__":
    main()
