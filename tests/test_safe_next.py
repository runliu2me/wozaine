#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""登录跳转白名单 + 会话签名：防开放重定向，会话 HMAC 可测。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 测试用密钥与隔离目录（不读写真实 .env / data/users.json）
import tempfile

_ISO_DIR = tempfile.TemporaryDirectory()
os.environ["ADMIN_SESSION_SECRET"] = "test-secret-for-unit"
os.environ["ADMIN_PASSWORD_HASH"] = ""
os.environ["COMPANION_DATA_DIR"] = _ISO_DIR.name

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
    # load_users 无 hash 时 admin 不在用户表；测签名格式
    parts = tok.split(".")
    assert len(parts) == 3
    user, exp, sig = parts
    assert user == "admin"
    assert web_auth._sig(user + "|" + exp) == sig
    assert web_auth.session_user(f"{exp}.{web_auth._sig(exp)}") is None  # 拒绝不含密码指纹的老 2 段格式
    print("PASS make_session shape")

    # 密码变更立即失效该账号会话，且不影响其他家人账号
    import hashlib
    import json

    def _fake_hash(pw, salt_hex):
        h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), 200_000).hex()
        return f"pbkdf2$200000${salt_hex}${h}"

    with tempfile.TemporaryDirectory() as td:
        orig_hash = web_auth.ADMIN_PASSWORD_HASH
        orig_users = web_auth.USERS_PATH
        try:
            web_auth.ADMIN_PASSWORD_HASH = _fake_hash("admin-pass-1", "11" * 16)
            web_auth.USERS_PATH = os.path.join(td, "users.json")
            with open(web_auth.USERS_PATH, "w", encoding="utf-8") as uf:
                json.dump({"xiaohong": {"name": "小红", "role": "family", "hash": _fake_hash("fam-pass-1", "22" * 16)}}, uf)

            admin_tok = web_auth.make_session("admin")
            fam_tok = web_auth.make_session("xiaohong")
            assert web_auth.session_user(admin_tok) == "admin"
            assert web_auth.session_user(fam_tok) == "xiaohong"

            # 管理员改密码：管理员旧会话立刻失效，家人会话不受影响
            web_auth.ADMIN_PASSWORD_HASH = _fake_hash("admin-pass-2", "33" * 16)
            assert web_auth.session_user(admin_tok) is None
            assert web_auth.session_user(fam_tok) == "xiaohong"

            # 家人改密码：该家人旧会话立刻失效，管理员新会话不受影响
            admin_tok2 = web_auth.make_session("admin")
            with open(web_auth.USERS_PATH, "w", encoding="utf-8") as uf:
                json.dump({"xiaohong": {"name": "小红", "role": "family", "hash": _fake_hash("fam-pass-2", "44" * 16)}}, uf)
            assert web_auth.session_user(fam_tok) is None
            assert web_auth.session_user(admin_tok2) == "admin"
            # 登录校验与改密并发窗口：传旧哈希签发的会话不得命中新密码
            old_h = _fake_hash("fam-pass-1", "22" * 16)
            stale_race_tok = web_auth.make_session("xiaohong", password_hash=old_h)
            assert web_auth.session_user(stale_race_tok) is None
        finally:
            web_auth.ADMIN_PASSWORD_HASH = orig_hash
            web_auth.USERS_PATH = orig_users
    print("PASS password change revokes only target user sessions")

    # 家人角色禁止访问 /api/admin/health（避免泄露设备 MAC 与待升级固件）
    assert ("GET", "/api/admin/health") in web_auth.FAMILY_DENY
    assert ("GET", "/api/admin/config") in web_auth.FAMILY_DENY
    assert ("GET", "/api/balance") in web_auth.FAMILY_DENY
    print("PASS family role denies /api/admin/health")

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
