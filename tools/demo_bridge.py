#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""假的「设备桥接」，配合 tools/demo_data.py 使用：没有设备也能看到管理台在线时的样子。

    python3 tools/demo_data.py /tmp/wozaine-demo
    python3 tools/demo_bridge.py [--scene 场景]
    COMPANION_DATA_DIR=/tmp/wozaine-demo python3 server.py

场景（--scene）：
    ok        在线、电量 76%、半小时前聊过（默认）
    talking   正在对话
    charging  在充电
    low       电量 15%、没在充电，并带一条电量告警
    offline   离线，最后在线是 3 小时前
    alert     在线，但语音合成两路都失败（演示服务级告警）

它只实现管理台会调用的几个控制口（状态、音量、重启、结束对话），只在本机监听，不连任何外部服务。
"""
import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEVICE_ID = "aa:bb:cc:dd:ee:01"   # 虚构
STARTED = time.time()
STATE = {"volume": 85}


def _status(scene):
    now = time.time()
    dev = {
        "device_id": DEVICE_ID, "state": "listening", "listen_mode": "auto",
        "conversation_active": False, "tx_rate": 24000,
        "connected_at": now - 5 * 3600 - 12 * 60, "last_activity": now - 31 * 60,
        "volume": STATE["volume"], "battery": {"level": 76, "charging": False},
    }
    alerts = []
    if scene == "talking":
        dev.update(state="ai_speaking", conversation_active=True, last_activity=now - 4)
    elif scene == "charging":
        dev["battery"] = {"level": 54, "charging": True}
    elif scene == "low":
        dev["battery"] = {"level": 15, "charging": False}
        alerts.append({"ts": now - 20 * 60, "key": "device_battery",
                       "text": "设备电量只剩 15%，没在充电，已经提醒她接充电线"})
    elif scene == "alert":
        alerts.append({"ts": now - 8 * 60, "key": "tts_down",
                       "text": "MiniMax 和阿里 CosyVoice 语音合成都失败，设备发不出声音了！"})
    devices = [] if scene == "offline" else [dev]
    last_seen = {DEVICE_ID: now - 3 * 3600 - 7 * 60 if scene == "offline" else now - 20}
    return {"devices": devices, "staged_firmware": {}, "last_seen": last_seen,
            "require_device_token": True, "boots": {}, "alerts": alerts, "story_asked": []}


def make_handler(scene):
    class H(BaseHTTPRequestHandler):
        def _json(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.split("?", 1)[0].rstrip("/") == "/xiaozhi/ota/status":
                self._json(200, _status(scene))
            else:
                self._json(404, {"error": "demo bridge: not found"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            path = self.path.split("?", 1)[0]
            if scene == "offline":
                self._json(409, {"ok": False, "error": "设备不在线"})
                return
            if path.endswith("/volume"):
                STATE["volume"] = max(0, min(100, int(body.get("volume", STATE["volume"]))))
                self._json(200, {"ok": True, "volume": STATE["volume"]})
                return
            self._json(200, {"ok": True})

        def log_message(self, fmt, *args):
            sys.stderr.write("[demo-bridge] " + (fmt % args) + "\n")
    return H


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scene", default="ok", choices=["ok", "talking", "charging", "low", "offline", "alert"])
    ap.add_argument("--port", type=int, default=3096, help="和 server.py 的 XIAOZHI_CTL_BASE 对上（默认 3096）")
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(a.scene))
    print(f"演示桥接：http://127.0.0.1:{a.port}  场景={a.scene}（Ctrl+C 退出）")
    srv.serve_forever()


if __name__ == "__main__":
    main()
