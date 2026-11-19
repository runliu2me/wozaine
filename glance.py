#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""管理台「一眼看」的设备状态：把桥接服务的原始状态整理成首页表盘要的那几样，并按角色裁剪。

表盘只回答三件事：在不在线（离线多久了）、电量多少、有没有需要人管的事。
原始状态里还有开机记录、待升级固件、今天问过的故事题、设备是否强制令牌等，
这些对家人账号没有用，也不该给，只有管理员拿得到。

纯函数，不读网络；server.py 负责把桥接状态和离线告警的本地状态传进来。
"""
import time

# 告警分两类：和老人那台设备直接相关的（家人也该知道），和服务、账户相关的（只给管理员）
DEVICE_ALERT_KEYS = {"device_battery"}
ALERT_WINDOW = 24 * 3600          # 首页只提最近一天的事，旧的不再吵

_DEVICE_FIELDS_FAMILY = ("state", "conversation_active", "connected_at", "last_activity", "battery")
_DEVICE_FIELDS_ADMIN = _DEVICE_FIELDS_FAMILY + ("device_id", "listen_mode", "tx_rate", "volume")


def _pick(d, fields):
    return {k: d.get(k) for k in fields if k in d}


def _elder_device(devices, elder_id):
    if elder_id:
        for d in devices:
            if str(d.get("device_id", "")).lower() == elder_id:
                return d
        return None
    return devices[0] if devices else None


def build(status, role, elder_id="", monitor_state=None, now=None):
    """status：桥接 /xiaozhi/ota/status 的返回（可能是 {"error": ...}）。
    role：admin / family。elder_id：老人那台设备的 MAC（小写，可空）。
    monitor_state：device_monitor.py 存的状态（桥接重启后 last_seen 会丢，用它兜底）。"""
    now = now or time.time()
    elder_id = (elder_id or "").lower()
    admin = role == "admin"
    out = {"bridge_ok": "error" not in status, "server_time": now}
    if not out["bridge_ok"]:
        # 原始报错可能带内部地址，只给管理员看
        out["error"] = status.get("error") if admin else "服务暂时连不上"
        out["devices"] = []
        out["alerts"] = []
        return out

    devices = status.get("devices") or []
    fields = _DEVICE_FIELDS_ADMIN if admin else _DEVICE_FIELDS_FAMILY
    elder = _elder_device(devices, elder_id)
    ordered = ([elder] if elder else []) + [d for d in devices if d is not elder]
    # 家人只看老人那台；管理员看全部（老人那台排第一）
    out["devices"] = [_pick(d, fields) for d in (ordered if admin else ([elder] if elder else []))]
    out["online"] = elder is not None

    seen = status.get("last_seen") or {}
    if elder_id:
        last_seen = seen.get(elder_id)
    else:
        last_seen = max(seen.values()) if seen else None
    if not last_seen and monitor_state:
        last_seen = monitor_state.get("last_seen") or None
    out["last_seen"] = now if elder else (float(last_seen) if last_seen else None)
    if not elder and out["last_seen"]:
        out["offline_for"] = max(0, int(now - out["last_seen"]))

    alerts = []
    for a in status.get("alerts") or []:
        ts = float(a.get("ts") or 0)
        if now - ts > ALERT_WINDOW:
            continue
        key = str(a.get("key") or "")
        is_device = key in DEVICE_ALERT_KEYS
        if is_device:
            # 电量告警已经过时（现在在充电或电量回来了）就不再挂着
            b = (elder or {}).get("battery") or {}
            if b and (b.get("charging") or (b.get("level") or 0) > 20):
                continue
        if admin or is_device:
            alerts.append({"ts": ts, "key": key, "text": str(a.get("text") or ""),
                           "kind": "device" if is_device else "service"})
        elif not any(x["key"] == "service" for x in alerts):
            # 家人看不到原文（多半是欠费、接口失败），只知道「有事，管理员会处理」
            alerts.append({"ts": ts, "key": "service", "text": "语音服务出了点状况，已经通知管理员",
                           "kind": "service"})
    alerts.sort(key=lambda x: -x["ts"])
    out["alerts"] = alerts

    if admin:
        out["staged_firmware"] = status.get("staged_firmware") or {}
        out["require_device_token"] = status.get("require_device_token")
    return out
