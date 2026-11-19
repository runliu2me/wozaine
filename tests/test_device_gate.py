#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设备准入：白名单与口令。"""
import importlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["XIAOZHI_DEVICE_TOKEN"] = "devtok"
os.environ["XIAOZHI_CTL_TOKEN"] = "ctltok"
os.environ["XIAOZHI_ALLOWED_DEVICE_IDS"] = "aa:bb:cc:dd:ee:ff"
os.environ["XIAOZHI_REQUIRE_DEVICE_TOKEN"] = "1"

import device_gate

importlib.reload(device_gate)


def main():
    assert device_gate.mac_allowed("aa:bb:cc:dd:ee:ff")
    assert not device_gate.mac_allowed("00:11:22:33:44:55")
    print("PASS mac_allowed")

    assert device_gate.ota_device_token("aa:bb:cc:dd:ee:ff") == "devtok"
    assert device_gate.ota_device_token("00:11:22:33:44:55") == ""
    print("PASS ota_device_token")

    ok, tok, why = device_gate.ws_accept("aa:bb:cc:dd:ee:ff", "Bearer devtok")
    assert ok and tok and why == ""
    ok, tok, why = device_gate.ws_accept("aa:bb:cc:dd:ee:ff", "Bearer wrong")
    assert not ok and why == "bad device token"
    ok, tok, why = device_gate.ws_accept("00:11:22:33:44:55", "Bearer devtok")
    assert not ok and why == "device not in allowlist"
    print("PASS ws_accept")

    assert device_gate.ctl_auth_ok("ctltok")
    assert not device_gate.ctl_auth_ok("nope")
    assert not device_gate.ctl_auth_ok("")
    print("PASS ctl")
    print("全部通过")


if __name__ == "__main__":
    main()
