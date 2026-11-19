#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设备准入：MAC 白名单、设备口令、管理口令。

从 xiaozhi_bridge.py 抽出，逻辑保持不变，方便单测与文档对齐。
"""
from __future__ import annotations

import hmac
import os

DEVICE_TOKEN = os.environ.get("XIAOZHI_DEVICE_TOKEN", "")
REQUIRE_DEVICE_TOKEN = os.environ.get("XIAOZHI_REQUIRE_DEVICE_TOKEN", "0") == "1"
ALLOWED_DEVICE_IDS = {
    x.strip().lower()
    for x in os.environ.get("XIAOZHI_ALLOWED_DEVICE_IDS", "").split(",")
    if x.strip()
}
CTL_TOKEN = os.environ.get("XIAOZHI_CTL_TOKEN", "")


def mac_allowed(device_id: str) -> bool:
    """未配置白名单时放行（方便第一台设备连上）；配置了则必须命中。"""
    if not ALLOWED_DEVICE_IDS:
        return True
    return (device_id or "").lower() in ALLOWED_DEVICE_IDS


def ota_device_token(device_mac: str) -> str:
    """OTA 回复里下发给设备的口令；不在白名单则给空串，设备连不上 WS。"""
    if not DEVICE_TOKEN:
        return ""
    if not ALLOWED_DEVICE_IDS or (device_mac or "").lower() in ALLOWED_DEVICE_IDS:
        return DEVICE_TOKEN
    return ""


def bearer_ok(auth_header: str, expected: str) -> bool:
    if not expected:
        return False
    return hmac.compare_digest((auth_header or "").encode(), ("Bearer " + expected).encode())


def ws_accept(device_id: str, auth_header: str) -> tuple[bool, bool, str]:
    """返回 (接受连接, token_ok, 拒绝原因)。

    token_ok 表示口令对得上；未开 REQUIRE 时口令错仍可接受（仅打日志）。
    """
    if not mac_allowed(device_id):
        return False, False, "device not in allowlist"
    token_ok = bearer_ok(auth_header, DEVICE_TOKEN) if DEVICE_TOKEN else False
    if DEVICE_TOKEN and not token_ok and REQUIRE_DEVICE_TOKEN:
        return False, False, "bad device token"
    return True, token_ok, ""


def ctl_auth_ok(header_token: str) -> bool:
    return bool(CTL_TOKEN) and hmac.compare_digest((header_token or "").encode(), CTL_TOKEN.encode())
