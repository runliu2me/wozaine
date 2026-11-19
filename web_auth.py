#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""管理台登录与会话：密码哈希、Cookie 会话、登录锁、跳转白名单、安全响应头。

从 server.py 抽出，方便单测；行为与抽出前保持一致。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
import urllib.parse

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")
ADMIN_SESSION_SECRET = os.environ.get("ADMIN_SESSION_SECRET", "")
SESSION_COOKIE = "wozaine_session"
LEGACY_SESSION_COOKIE = "mom_session"  # 开源前私有部署用过的名字，读时兼容
SESSION_DAYS = 180
# 不登录也能访问的：登录页及其资源、致敬页、图标；manifest 浏览器取的时候不带 cookie，里面也没有隐私
PUBLIC_PATHS = {"/login", "/api/login", "/_auth", "/favicon.ico", "/login.css", "/login.js", "/thanks", "/thanks.css", "/thanks.js",
                "/manifest.webmanifest"}

DATA_DIR = os.environ.get(
    "COMPANION_DATA_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"),
)
USERS_PATH = os.path.join(DATA_DIR, "users.json")

# role=admin 全能；role=family 能看/对讲/出题/改记忆，不能动维护
FAMILY_DENY = {
    ("GET", "/api/admin/config"),
    ("POST", "/api/admin/config"),
    ("POST", "/api/admin/persona"),
    ("POST", "/api/admin/restart"),
    ("POST", "/api/device/reboot"),
    ("POST", "/api/book/render"),
    ("GET", "/api/balance"),
}

CSP = (
    "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
    "style-src 'self'; script-src 'self'; "
    "connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)

# 浏览器权限：只有对讲要用麦克风（同源）；摄像头、定位等一律不给——这个项目不拍照、不定位
PERMISSIONS_POLICY = "camera=(), microphone=(self), geolocation=(), payment=(), usb=(), display-capture=()"

_LOGIN_FAILS = {}
_LOGIN_LOCK = threading.Lock()


def safe_next_path(raw):
    """登录后跳转只允许站内相对路径，防止 //evil 一类开放重定向。"""
    if not raw:
        return "/home"
    path = urllib.parse.unquote(str(raw)).strip()
    if not path.startswith("/") or path.startswith("//") or "\\" in path or "\x00" in path:
        return "/home"
    if ":" in path.split("?", 1)[0]:
        return "/home"
    if path.startswith("/login") or path.startswith("/api/"):
        return "/home"
    return path


def verify_password(password, stored):
    try:
        algo, iters, salt, digest = stored.split("$")
        if algo != "pbkdf2":
            return False
        calc = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iters)
        ).hex()
        return hmac.compare_digest(calc, digest)
    except Exception:
        return False


def load_users():
    users = {}
    if ADMIN_PASSWORD_HASH:
        users[ADMIN_USER] = {
            "name": os.environ.get("ADMIN_DISPLAY_NAME", "管理员"),
            "role": "admin",
            "hash": ADMIN_PASSWORD_HASH,
        }
    try:
        with open(USERS_PATH, "r", encoding="utf-8") as f:
            for k, v in json.load(f).items():
                if k != ADMIN_USER and v.get("hash"):
                    users[k] = v
    except Exception:
        pass
    return users


def _sig(msg):
    return hmac.new(ADMIN_SESSION_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()[:40]


def make_session(user):
    exp = str(int(time.time()) + SESSION_DAYS * 86400)
    return f"{user}.{exp}.{_sig(user + '|' + exp)}"


def session_user(value):
    """返回登录的用户名；无效返回 None。兼容老格式（exp.sig = 管理员）。"""
    try:
        parts = value.split(".")
        if len(parts) == 2:
            exp, sig = parts
            ok = hmac.compare_digest(sig, _sig(exp)) and int(exp) > time.time()
            return ADMIN_USER if ok else None
        user, exp, sig = parts
        if (
            hmac.compare_digest(sig, _sig(user + "|" + exp))
            and int(exp) > time.time()
            and user in load_users()
        ):
            return user
    except Exception:
        pass
    return None


def session_valid(value):
    return session_user(value) is not None


def login_blocked(ip):
    now = time.time()
    with _LOGIN_LOCK:
        for k in list(_LOGIN_FAILS):
            _LOGIN_FAILS[k] = [t for t in _LOGIN_FAILS[k] if now - t < 3600]
            if not _LOGIN_FAILS[k]:
                del _LOGIN_FAILS[k]
        mine = [t for t in _LOGIN_FAILS.get(ip, []) if now - t < 900]
        return len(mine) >= 5


def login_fail(ip):
    with _LOGIN_LOCK:
        _LOGIN_FAILS.setdefault(ip, []).append(time.time())


def login_clear(ip):
    with _LOGIN_LOCK:
        _LOGIN_FAILS.pop(ip, None)


def cookie_value(headers_cookie, name):
    raw = headers_cookie or ""
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return ""


def session_cookie_value(headers_cookie):
    return cookie_value(headers_cookie, SESSION_COOKIE) or cookie_value(
        headers_cookie, LEGACY_SESSION_COOKIE
    )


def origin_ok(origin, host_header):
    """浏览器跨站 POST 防护：有 Origin 时必须和 Host 同站。"""
    if not origin:
        return True
    try:
        host = (host_header or "").split(":")[0].lower()
        oh = urllib.parse.urlparse(origin).hostname or ""
        return oh.lower() == host
    except Exception:
        return False


def apply_security_headers(send_header):
    """send_header 是 BaseHTTPRequestHandler.send_header 绑定方法。"""
    send_header("X-Content-Type-Options", "nosniff")
    send_header("X-Frame-Options", "DENY")
    send_header("Referrer-Policy", "same-origin")
    send_header("Content-Security-Policy", CSP)
    send_header("Permissions-Policy", PERMISSIONS_POLICY)


def set_session_cookies(send_header, user):
    send_header(
        "Set-Cookie",
        f"{SESSION_COOKIE}={make_session(user)}; Max-Age={SESSION_DAYS * 86400}; "
        f"Path=/; HttpOnly; Secure; SameSite=Lax",
    )
    send_header(
        "Set-Cookie",
        f"{LEGACY_SESSION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax",
    )


def clear_session_cookies(send_header):
    send_header(
        "Set-Cookie",
        f"{SESSION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax",
    )
    send_header(
        "Set-Cookie",
        f"{LEGACY_SESSION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax",
    )
