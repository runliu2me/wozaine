#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""登录跳转白名单 + 会话签名：防开放重定向，会话 HMAC 可测。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 测试用密钥（不读写真实 .env）
os.environ["ADMIN_SESSION_SECRET"] = "test-secret-for-unit"
os.environ["ADMIN_PASSWORD_HASH"] = ""

import importlib

import web_auth

importlib.reload(web_auth)


def main():
    f = web_auth.safe_next_path
    cases = [
        ("/home", "/home"),
        ("/log", "/log"),
        ("/live?x=1", "/live?x=1"),
        ("//evil.example", "/home"),
        ("/\\evil", "/home"),
        ("https://evil", "/home"),
        ("/login", "/home"),
        ("/api/me", "/home"),
        ("", "/home"),
        (None, "/home"),
    ]
    failed = 0
    for raw, want in cases:
        got = f(raw)
        ok = got == want
        print(("PASS" if ok else "FAIL"), "next", repr(raw), "->", repr(got))
        failed += not ok

    # origin
    assert web_auth.origin_ok("", "a.com") is True
    assert web_auth.origin_ok("https://a.com", "a.com") is True
    assert web_auth.origin_ok("https://evil.com", "a.com") is False
    print("PASS origin_ok")

    # session roundtrip
    tok = web_auth.make_session("admin")
    # load_users 无 hash 时 admin 不在用户表；老格式兼容测签名本身
    parts = tok.split(".")
    assert len(parts) == 3
    user, exp, sig = parts
    assert user == "admin"
    assert web_auth._sig(user + "|" + exp) == sig
    print("PASS make_session shape")

    assert "/login.css" in web_auth.PUBLIC_PATHS
    assert "/login.js" in web_auth.PUBLIC_PATHS
    assert "/thanks" in web_auth.PUBLIC_PATHS
    assert "/thanks.css" in web_auth.PUBLIC_PATHS
    assert "/manifest.webmanifest" in web_auth.PUBLIC_PATHS
    assert "/app.js" not in web_auth.PUBLIC_PATHS and "/index.html" not in web_auth.PUBLIC_PATHS
    print("PASS public login assets")

    # 安全响应头：脚本和样式都不放行内联；浏览器权限里摄像头一律不给
    sent = {}
    web_auth.apply_security_headers(lambda k, v: sent.__setitem__(k, v))
    csp = sent["Content-Security-Policy"]
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp, csp
    assert "frame-ancestors 'none'" in csp
    assert "camera=()" in sent["Permissions-Policy"]
    assert sent["X-Content-Type-Options"] == "nosniff"
    print("PASS security headers")

    if failed:
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
