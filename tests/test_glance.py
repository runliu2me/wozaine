#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""首页表盘的设备状态：在线/离线/电量/告警，以及家人账号拿不到管理员才该看的东西。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import glance  # noqa: E402

NOW = 1_800_000_000.0
MAC = "aa:bb:cc:dd:ee:01"


def raw(devices=None, last_seen=None, alerts=None):
    return {"devices": devices if devices is not None else [], "last_seen": last_seen or {},
            "alerts": alerts or [], "boots": {MAC: [{"ts": NOW - 60, "reason": "panic"}]},
            "staged_firmware": {"board": {"version": "9.9.9"}}, "require_device_token": True,
            "story_asked": ["你小时候住哪儿？"]}


def dev(**kw):
    d = {"device_id": MAC, "state": "listening", "conversation_active": False, "connected_at": NOW - 3600,
         "last_activity": NOW - 60, "volume": 80, "listen_mode": "auto", "tx_rate": 24000,
         "battery": {"level": 76, "charging": False}}
    d.update(kw)
    return d


def main():
    # 在线：family 只拿到表盘字段
    g = glance.build(raw([dev()]), "family", elder_id=MAC, now=NOW)
    assert g["online"] and g["bridge_ok"]
    assert set(g["devices"][0]) == {"state", "conversation_active", "connected_at", "last_activity", "battery"}
    for k in ("staged_firmware", "require_device_token", "boots", "story_asked"):
        assert k not in g, k
    print("PASS family 只拿表盘字段")

    a = glance.build(raw([dev()]), "admin", elder_id=MAC, now=NOW)
    assert a["devices"][0]["device_id"] == MAC and a["staged_firmware"]
    assert "boots" not in a and "story_asked" not in a
    print("PASS admin 多拿设备号和固件，但不拿故事题和开机记录")

    # 离线：给出离线多久；桥接重启丢了 last_seen 时用 monitor 状态兜底
    g = glance.build(raw([], {MAC: NOW - 3 * 3600}), "family", elder_id=MAC, now=NOW)
    assert not g["online"] and g["offline_for"] == 3 * 3600
    g = glance.build(raw([]), "family", elder_id=MAC, monitor_state={"last_seen": NOW - 600}, now=NOW)
    assert g["offline_for"] == 600
    g = glance.build(raw([]), "family", elder_id=MAC, now=NOW)
    assert g["last_seen"] is None and "offline_for" not in g
    print("PASS 离线时长与兜底")

    # 指定了老人设备号：别的设备在线不算她在线
    other = dev(device_id="00:11:22:33:44:55")
    g = glance.build(raw([other]), "family", elder_id=MAC, now=NOW)
    assert not g["online"] and g["devices"] == []
    a = glance.build(raw([other]), "admin", elder_id=MAC, now=NOW)
    assert not a["online"] and a["devices"][0]["device_id"] == other["device_id"]
    print("PASS 只认老人那台")

    # 告警：家人只看到设备类原文；服务类合成一句「已通知管理员」；超过一天的不提
    alerts = [{"ts": NOW - 300, "key": "tts_down", "text": "MiniMax 欠费 sk-xxx"},
              {"ts": NOW - 200, "key": "asr_down", "text": "识别挂了"},
              {"ts": NOW - 100, "key": "device_battery", "text": "电量 15%"},
              {"ts": NOW - 2 * 86400, "key": "deepseek_balance", "text": "很久以前"}]
    low = dev(battery={"level": 15, "charging": False})
    g = glance.build(raw([low], alerts=alerts), "family", elder_id=MAC, now=NOW)
    texts = [x["text"] for x in g["alerts"]]
    assert "电量 15%" in texts and not any("sk-" in t or "识别挂了" in t for t in texts), texts
    assert sum(1 for x in g["alerts"] if x["kind"] == "service") == 1
    a = glance.build(raw([low], alerts=alerts), "admin", elder_id=MAC, now=NOW)
    assert len(a["alerts"]) == 3 and a["alerts"][0]["key"] == "device_battery"
    print("PASS 告警按角色裁剪、按时间排序、过期不提")

    # 电量告警过时（已在充电）就不挂着
    charging = dev(battery={"level": 15, "charging": True})
    g = glance.build(raw([charging], alerts=alerts[2:3]), "family", elder_id=MAC, now=NOW)
    assert g["alerts"] == []
    print("PASS 充上电后电量告警自动消失")

    # 桥接不通：家人看不到内部报错
    g = glance.build({"error": "<urlopen error [Errno 111] 127.0.0.1:3096>"}, "family", now=NOW)
    assert not g["bridge_ok"] and "127.0.0.1" not in g["error"]
    a = glance.build({"error": "<urlopen error [Errno 111] 127.0.0.1:3096>"}, "admin", now=NOW)
    assert "127.0.0.1" in a["error"]
    print("PASS 桥接报错只给管理员看原文")
    print("ALL PASS")


if __name__ == "__main__":
    main()
