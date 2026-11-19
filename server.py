#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「我在呢」管理台：网页 + JSON API。

默认只监听 127.0.0.1，公网流量应经 nginx（HTTPS）反代进来。
生产环境必须设置 ADMIN_PASSWORD_HASH + ADMIN_SESSION_SECRET；
未设密码时仅放行本机直连（开发用），带 X-Forwarded-* 的代理请求一律拒绝。
"""

import base64
import secrets
import datetime
import json
import os
import queue
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import subprocess
import threading

import deepseek_client
import family
import tts_client
import memory_store
import asr_client
import env_config
import phone_brain
import story
import book
import typo_book
import web_auth
import glance

LISTEN_HOST = os.environ.get("LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "3093"))
WEB_ROOT = os.path.abspath(os.environ.get(
    "WEB_ROOT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")))
ALLOWED_LOGIN = os.environ.get("ALLOWED_TAILSCALE_LOGIN", "")
ALLOW_LOCAL = os.environ.get("ALLOW_LOCAL", "1") == "1"
PERSONA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "persona.md")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
DATA_DIR = os.environ.get("COMPANION_DATA_DIR", os.path.join(BASE_DIR, "data"))
OTA_STAGED_PATH = os.path.join(DATA_DIR, "ota_staged.json")
SERVICES = ["xiaozhi-bridge.service", "companion-realtime.service", "companion.service"]
# 管理页「重启服务」走一个只认上面三个服务名的 root 小脚本（deploy/install.sh 装到 /usr/local/sbin，
# 并在 /etc/sudoers.d 里只放行这一条）。它内部用 systemd-run 脱离本服务的 cgroup 再 systemctl restart。
RESTART_CMD = ["sudo", "-n", os.environ.get("RESTART_HELPER", "/usr/local/sbin/wozaine-restart")]
BRIDGE_CTL_BASE = os.environ.get("XIAOZHI_CTL_BASE", "http://127.0.0.1:3096")
CTL_TOKEN = os.environ.get("XIAOZHI_CTL_TOKEN", "")
PUBLIC_OTA_URL = os.environ.get("XIAOZHI_PUBLIC_OTA_URL", "https://YOUR_DOMAIN/xiaozhi/ota/")
MAX_BODY = 64 * 1024
MAX_AUDIO = 8 * 1024 * 1024


def webm_to_pcm16k(webm_bytes):
    """浏览器录的音频（webm/opus 等）转成讯飞要的 16k/16bit/单声道 PCM"""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-ar", "16000", "-ac", "1", "-f", "s16le", "pipe:1"],
        input=webm_bytes, capture_output=True, timeout=20,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 转码失败: {proc.stderr.decode('utf-8', 'ignore')[:300]}")
    return proc.stdout

MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
        ".mp3": "audio/mpeg", ".webmanifest": "application/manifest+json"}


def load_persona():
    return family.read_persona()


def _find_sentence_end(buf):
    for i, ch in enumerate(buf):
        if ch in "。！？\n":
            return i
    return None


# ---- 网页登录 / 会话（实现见 web_auth.py）----
ADMIN_USER = web_auth.ADMIN_USER
ADMIN_PASSWORD_HASH = web_auth.ADMIN_PASSWORD_HASH
ADMIN_SESSION_SECRET = web_auth.ADMIN_SESSION_SECRET
SESSION_COOKIE = web_auth.SESSION_COOKIE
LEGACY_SESSION_COOKIE = web_auth.LEGACY_SESSION_COOKIE
SESSION_DAYS = web_auth.SESSION_DAYS
PUBLIC_PATHS = web_auth.PUBLIC_PATHS
FAMILY_DENY = web_auth.FAMILY_DENY
USERS_PATH = web_auth.USERS_PATH
safe_next_path = web_auth.safe_next_path
verify_password = web_auth.verify_password
load_users = web_auth.load_users
make_session = web_auth.make_session
session_user = web_auth.session_user
session_valid = web_auth.session_valid


class Handler(BaseHTTPRequestHandler):
    server_version = "wozaine/0.1"

    def _cookie(self, name):
        return web_auth.cookie_value(self.headers.get("Cookie", ""), name)

    def _session_cookie_value(self):
        return web_auth.session_cookie_value(self.headers.get("Cookie", ""))

    def _security_headers(self):
        web_auth.apply_security_headers(self.send_header)

    def _check_origin(self):
        return web_auth.origin_ok(self.headers.get("Origin", ""), self.headers.get("Host", ""))

    def _user(self):
        if ADMIN_PASSWORD_HASH and ADMIN_SESSION_SECRET:
            u = session_user(self._session_cookie_value())
            return (u, load_users().get(u, {}).get("role", "family")) if u else (None, None)
        return ("local", "admin") if self._authorized() else (None, None)

    def _forbidden_for_role(self, method):
        u, role = self._user()
        if role == "admin":
            return False
        path = self.path.partition("?")[0]
        # 错字本只有管理员能看能改
        if (method, path) in FAMILY_DENY or path.startswith("/api/admin/ota") or path.startswith("/api/typo"):
            self._send_json(403, {"error": "这个操作只有管理员能做"})
            return True
        return False

    def _authorized(self):
        if ADMIN_PASSWORD_HASH and ADMIN_SESSION_SECRET:
            return session_valid(self._session_cookie_value())
        login = self.headers.get("Tailscale-User-Login", "")
        if login and ALLOWED_LOGIN and login == ALLOWED_LOGIN:
            return True
        # 没设密码时只放行本机直连；经过 nginx 等反向代理来的（带 X-Forwarded-For / X-Real-IP）一律不放行
        if ALLOW_LOCAL and not self.headers.get("X-Forwarded-For") and not self.headers.get("X-Real-IP"):
            return True
        return False

    def _deny(self):
        path = self.path.partition("?")[0]
        if path.startswith("/api/") or path == "/_auth":
            self._send_json(401, {"error": "未登录"})
            return
        self.send_response(302)
        self._security_headers()
        self.send_header("Location", "/login?next=" + urllib.parse.quote(safe_next_path(self.path)))
        self.end_headers()

    def _handle_login_post(self):
        body = self._read_json_body() or {}
        user = str(body.get("user") or "").strip()
        pw = str(body.get("password") or "")
        ip = self.headers.get("X-Real-IP") or self.client_address[0]
        time.sleep(0.4)  # 拖慢暴力猜密码
        if web_auth.login_blocked(ip):
            sys.stderr.write(f"[login] blocked {ip}\n")
            self._send_json(429, {"error": "错误次数太多，请 15 分钟后再试"})
            return
        acct = load_users().get(user)
        if not (acct and verify_password(pw, acct["hash"])):
            web_auth.login_fail(ip)
            sys.stderr.write(f"[login] failed from {ip}\n")
            self._send_json(401, {"error": "用户名或密码不对"})
            return
        web_auth.login_clear(ip)
        payload = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._security_headers()
        sys.stderr.write(f"[login] ok {user} from {ip}\n")
        web_auth.set_session_cookies(self.send_header, user)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._security_headers()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, path):
        rel = path.lstrip("/") or "index.html"
        full = os.path.abspath(os.path.join(WEB_ROOT, rel))
        if not full.startswith(WEB_ROOT + os.sep) or not os.path.isfile(full):   # 带上分隔符，防止 web 之外同名前缀的目录
            self.send_response(404)
            self.end_headers()
            return
        ext = os.path.splitext(full)[1]
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self._security_headers()
        if ext in (".js", ".css", ".svg"):
            self.send_header("Cache-Control", "public, max-age=3600")
        else:
            self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = urllib.parse.parse_qs(query)
        if path == "/login":
            if self._authorized():
                self.send_response(302); self.send_header("Location", "/home"); self.end_headers(); return
            self._send_static("/login.html")
            return
        if path == "/logout":
            self.send_response(302)
            self._security_headers()
            web_auth.clear_session_cookies(self.send_header)
            self.send_header("Location", "/login")
            self.end_headers()
            return
        if path == "/_auth":  # nginx auth_request：给 WebSocket 路径做登录校验
            if self._authorized():
                self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()
            else:
                self._send_json(401, {"error": "未登录"})
            return
        # 未登录可访问的只有 web_auth.PUBLIC_PATHS 里列的（登录资源、致敬页、图标、manifest）
        if path == "/thanks":
            self._send_static("/thanks.html")
            return
        if path in PUBLIC_PATHS and "." in path:
            self._send_static(path)
            return
        if not self._authorized():
            self._deny()
            return
        if self._forbidden_for_role(self.command):
            return
        if path == "/admin":
            self._send_static("/index.html")  # 老入口，现在首页就是管理页
            return
        if path == "/api/me":
            u, role = self._user()
            self._send_json(200, {
                "user": u, "role": role,
                "name": load_users().get(u, {}).get("name", u),
                "elder_label": getattr(family, "ELDER_LABEL", "妈妈"),
                "elder_pronoun": getattr(family, "ELDER_PRONOUN", "她"),
                "elder_name": getattr(family, "ELDER_NAME", ""),
                "child_nickname": getattr(family, "CHILD_NICK", ""),
                "book_title": getattr(family, "BOOK_TITLE", ""),
            })
            return
        if path == "/api/admin/config":
            self._handle_admin_config_get()
            return
        if path == "/api/facts":  # 「它记住的事」：家人也能看（/api/admin/config 里还有密钥配置，只给管理员）
            self._send_json(200, {"facts": memory_store.load_facts()})
            return
        if path == "/api/history/dates":
            self._send_json(200, {"dates": memory_store.history_dates()})
            return
        if path == "/api/history":
            date = (params.get("date") or [_today()])[0]
            self._send_json(200, {"date": date, "turns": memory_store.history_for_date(date),
                                  "report": memory_store.load_report(date)})
            return
        if path == "/api/reports":
            self._send_json(200, {"reports": memory_store.list_reports()})
            return
        if path == "/api/admin/health":
            self._handle_health()
            return
        if path == "/api/balance":
            self._send_json(200, _get_balances())
            return
        if path.startswith("/api/audio/"):
            # 对话录音回放：/api/audio/<date>/<file>.mp3 → data/audio/<date>/<file>.mp3
            full = memory_store.audio_path("audio/" + path[len("/api/audio/"):])
            if not full:
                self.send_response(404)
                self.end_headers()
                return
            with open(full, "rb") as f:
                body = f.read()
            self.send_response(200)
            self._security_headers()
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "private, max-age=86400")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/typo":
            try:
                self._send_json(200, typo_book.summary())
            except Exception as exc:
                traceback.print_exc()
                self._send_json(500, {"error": str(exc)})
            return
        if path == "/api/story":
            self._send_json(200, story.status())
            return
        if path == "/api/book":
            self._send_json(200, book.payload())
            return
        if path == "/book":
            self._send_static("/book.html")
            return
        if path.startswith("/vendor/"):
            self._send_static(path)
            return
        if path == "/api/story/export":
            text = story.compile_biography()
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="story-export.md"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/device/status":
            self._handle_device_status()
            return
        # 每个页签有自己的真实网址（/home /live /log /memory /ops），这样手机上
        # 下拉刷新、加书签、加到主屏幕都会停在当前页，而不是跳回首页
        if path.strip("/") in ("", "home", "live", "log", "memory", "story", "ops"):
            self._send_static("/index.html")
            return
        self._send_static(path)

    def _handle_admin_config_get(self):
        self._send_json(200, {
            "fields": env_config.describe(),
            "persona": load_persona(),
            "facts": memory_store.load_facts(),
            # 管理页「声音」下拉框里的候选：.env 里 MINIMAX_VOICE_IDS=id1,id2（不填就只有当前这个）
            "known_voices": [v.strip() for v in (os.environ.get("MINIMAX_VOICE_IDS") or os.environ.get("MINIMAX_VOICE_ID", "")).split(",") if v.strip()],
        })

    def do_POST(self):
        if self.path == "/api/login":
            self._handle_login_post()
            return
        if self.command == "POST" and not self._check_origin():
            self._send_json(403, {"error": "拒绝跨站请求"})
            return
        if self.path.startswith("/v1/chat/completions"):
            phone_brain.handle(self)   # 电话线路的大脑接口，自己用 Bearer 鉴权
            return
        if not self._authorized():
            self._deny()
            return
        if self._forbidden_for_role(self.command):
            return
        if self.path == "/api/asr":
            self._handle_asr()
            return
        if self.path == "/api/chat_stream":
            self._handle_chat_stream()
            return
        if self.path == "/api/admin/config":
            self._handle_admin_config_post()
            return
        if self.path == "/api/admin/persona":
            self._handle_admin_persona_post()
            return
        if self.path == "/api/admin/facts":
            self._handle_admin_facts_post()
            return
        if self.path == "/api/admin/facts/delete":
            self._handle_admin_facts_delete()
            return
        if self.path == "/api/admin/facts/update":
            self._handle_admin_facts_update()
            return
        if self.path == "/api/report/generate":
            self._handle_report_generate()
            return
        if self.path == "/api/admin/restart":
            self._handle_restart()
            return
        if self.path == "/api/device/say":
            self._handle_device_say()
            return
        if self.path == "/api/device/reboot":
            self._proxy_ctl_simple("/xiaozhi/ota/reboot")
            return
        if self.path == "/api/story/question":
            body = self._read_json_body() or {}
            text = str(body.get("text") or "").strip()
            if not text:
                self._send_json(400, {"error": "问题为空"}); return
            q = story.add_question(text, body.get("chapter"), pin=bool(body.get("pin")))
            self._send_json(200, {"ok": True, "question": q} if q else {"ok": False, "error": "已经有这个问题了"})
            return
        if self.path == "/api/story/question/update":
            body = self._read_json_body() or {}
            q = story.set_question(str(body.get("id") or ""), **{k: body[k] for k in ("pinned", "shelved", "answered", "text", "chapter") if k in body})
            self._send_json(200 if q else 404, {"ok": bool(q), "question": q})
            return
        if self.path.startswith("/api/typo/"):
            self._handle_typo_post()
            return
        if self.path == "/api/book/render":
            body = self._read_json_body() or {}
            n = book.render(force=bool(body.get("force")))
            self._send_json(200, {"ok": True, "rendered": n})
            return
        if self.path == "/api/story/rebuild":
            body = self._read_json_body() or {}
            date = body.get("date") or datetime.date.today().strftime("%Y-%m-%d")
            turns = memory_store.history_for_date(date)
            n = story.extract_day(date, turns, todays_q=body.get("question"))
            story.compile_biography()
            self._send_json(200, {"ok": True, "added": n})
            return
        if self.path == "/api/device/volume":
            body = self._read_json_body() or {}
            result = _bridge_ctl("POST", "/xiaozhi/ota/volume", {"volume": body.get("volume")} if body.get("volume") is not None else {})
            self._send_json(200 if result.get("ok") else 409, result)
            return
        if self.path == "/api/device/end":
            self._proxy_ctl_simple("/xiaozhi/ota/end")
            return
        if self.path.startswith("/api/admin/ota/stage"):
            self._handle_ota_stage()
            return
        if self.path != "/api/chat":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > MAX_BODY:
            self._send_json(400, {"error": "bad request body"})
            return
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
            user_text = body["text"].strip()
        except Exception:
            self._send_json(400, {"error": "invalid json"})
            return
        if not user_text:
            self._send_json(400, {"error": "empty text"})
            return

        try:
            persona_text = load_persona()
            facts = memory_store.load_facts()
            recent_turns = memory_store.recent_history(limit=12)

            reply_text = deepseek_client.generate_reply(persona_text, facts, recent_turns, user_text)

            memory_store.append_history("user", user_text)
            memory_store.append_history("assistant", reply_text)

            def _extract_facts_bg():
                try:
                    new_facts = deepseek_client.extract_facts(user_text, reply_text)
                    memory_store.add_facts(new_facts)
                except Exception:
                    traceback.print_exc()
            threading.Thread(target=_extract_facts_bg, daemon=True).start()

            audio_bytes = tts_client.synthesize(reply_text)
            audio_b64 = base64.b64encode(audio_bytes).decode("ascii")

            self._send_json(200, {
                "reply_text": reply_text,
                "audio_base64": audio_b64,
                "audio_mime": "audio/mpeg",
            })
        except Exception as exc:
            traceback.print_exc()
            self._send_json(500, {"error": str(exc)})

    def _write_ndjson(self, obj):
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        self.wfile.write(data)
        self.wfile.flush()

    def _handle_chat_stream(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > MAX_BODY:
            self._send_json(400, {"error": "bad request body"})
            return
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
            user_text = body["text"].strip()
        except Exception:
            self._send_json(400, {"error": "invalid json"})
            return
        if not user_text:
            self._send_json(400, {"error": "empty text"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.close_connection = True

        persona_text = load_persona()
        facts = memory_store.load_facts()
        recent_turns = memory_store.recent_history(limit=12)

        sentence_queue = queue.Queue()
        full_reply = {"text": ""}

        def producer():
            buf = ""
            try:
                for delta in deepseek_client.generate_reply_stream(persona_text, facts, recent_turns, user_text):
                    buf += delta
                    full_reply["text"] += delta
                    while True:
                        idx = _find_sentence_end(buf)
                        if idx is None:
                            break
                        sentence_queue.put(buf[:idx + 1])
                        buf = buf[idx + 1:]
                if buf.strip():
                    sentence_queue.put(buf)
            except Exception as exc:
                sentence_queue.put(("__error__", str(exc)))
            finally:
                sentence_queue.put(None)

        threading.Thread(target=producer, daemon=True).start()

        seq = 0
        try:
            while True:
                item = sentence_queue.get()
                if item is None:
                    break
                if isinstance(item, tuple) and item[0] == "__error__":
                    self._write_ndjson({"type": "error", "error": item[1]})
                    return
                sentence = item.strip()
                if not sentence:
                    continue
                audio_bytes = tts_client.synthesize(sentence)
                audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
                self._write_ndjson({
                    "type": "audio_chunk", "seq": seq, "text": sentence,
                    "audio_base64": audio_b64, "audio_mime": "audio/mpeg",
                })
                seq += 1
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            traceback.print_exc()
            try:
                self._write_ndjson({"type": "error", "error": str(exc)})
            except Exception:
                pass
            return

        reply_text = full_reply["text"].strip()
        memory_store.append_history("user", user_text)
        memory_store.append_history("assistant", reply_text)

        def _extract_facts_bg():
            try:
                new_facts = deepseek_client.extract_facts(user_text, reply_text)
                memory_store.add_facts(new_facts)
            except Exception:
                traceback.print_exc()
        threading.Thread(target=_extract_facts_bg, daemon=True).start()

        try:
            self._write_ndjson({"type": "done", "reply_text": reply_text})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _handle_asr(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > MAX_AUDIO:
            self._send_json(400, {"error": "bad audio body"})
            return
        raw_audio = self.rfile.read(length)
        try:
            pcm = webm_to_pcm16k(raw_audio)
            text = asr_client.transcribe_pcm(pcm)
            self._send_json(200, {"text": text})
        except Exception as exc:
            traceback.print_exc()
            self._send_json(500, {"error": str(exc)})

    def _read_json_body(self, max_size=MAX_BODY):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > max_size:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def _handle_admin_config_post(self):
        body = self._read_json_body()
        if body is None:
            self._send_json(400, {"error": "invalid json"})
            return
        updates = body.get("fields", {})
        if not isinstance(updates, dict):
            self._send_json(400, {"error": "fields must be an object"})
            return
        try:
            changed = env_config.apply_updates(updates)
        except Exception as exc:
            traceback.print_exc()
            self._send_json(500, {"error": str(exc)})
            return
        restarted = False
        if changed:
            # 这个重启命令要包含"重启我自己"（companion.service）——如果直接从本进程
            # subprocess.run 发，systemctl restart 会把本服务所在的 cgroup 一起杀掉，
            # 连带杀死这条重启命令本身，导致排在后面的 companion-realtime 根本没被重启到。
            # 用 systemd-run 开一个独立的临时单元发命令，不在我这个 cgroup 里，杀我杀不到它。
            def _restart_later():
                import time
                time.sleep(0.5)
                try:
                    subprocess.run(RESTART_CMD + ["xiaozhi-bridge.service", "companion-realtime.service", "companion.service"],
                                   check=True, timeout=15)
                except Exception:
                    traceback.print_exc()
            threading.Thread(target=_restart_later, daemon=True).start()
            restarted = True
        self._send_json(200, {"ok": True, "changed": changed, "restarting": restarted})

    def _handle_admin_persona_post(self):
        body = self._read_json_body(max_size=256 * 1024)
        if body is None or "content" not in body:
            self._send_json(400, {"error": "invalid json"})
            return
        try:
            with open(PERSONA_PATH, "w", encoding="utf-8") as f:
                f.write(body["content"])
        except Exception as exc:
            traceback.print_exc()
            self._send_json(500, {"error": str(exc)})
            return
        self._send_json(200, {"ok": True})

    def _handle_admin_facts_post(self):
        body = self._read_json_body()
        if body is None or not body.get("text", "").strip():
            self._send_json(400, {"error": "invalid json"})
            return
        try:
            memory_store.add_facts([body["text"].strip()])
        except Exception as exc:
            traceback.print_exc()
            self._send_json(500, {"error": str(exc)})
            return
        self._send_json(200, {"ok": True, "facts": memory_store.load_facts()})

    def _handle_admin_facts_delete(self):
        body = self._read_json_body()
        if body is None or "index" not in body:
            self._send_json(400, {"error": "invalid json"})
            return
        try:
            index = int(body["index"])
        except (TypeError, ValueError):
            self._send_json(400, {"error": "bad index"})
            return
        try:
            memory_store.delete_fact_at(index)
        except Exception as exc:
            traceback.print_exc()
            self._send_json(500, {"error": str(exc)})
            return
        self._send_json(200, {"ok": True, "facts": memory_store.load_facts()})

    # ---------------- 记忆编辑 ----------------
    def _handle_admin_facts_update(self):
        body = self._read_json_body()
        if body is None or "index" not in body or not str(body.get("text", "")).strip():
            self._send_json(400, {"error": "invalid json"})
            return
        try:
            memory_store.update_fact_at(int(body["index"]), str(body["text"]))
        except Exception as exc:
            traceback.print_exc()
            self._send_json(500, {"error": str(exc)})
            return
        self._send_json(200, {"ok": True, "facts": memory_store.load_facts()})

    # ---------------- 错字本 ----------------
    def _handle_typo_post(self):
        """管理员在管理台确认词、改写法类别、撤销。改完在后台按新词表重算（确认的错写几秒钟就改完；
        要看上下文的写法交给 DeepSeek，加词时先判一小批，其余每晚陆续判）。"""
        body = self._read_json_body() or {}
        action = self.path[len("/api/typo/"):]
        try:
            if action == "term":
                t = typo_book.add_term(body.get("right"), body.get("wrong") or [], body.get("note") or "")
                typo_book.run_async(judge=True, discover_new=True, budget=200, auto_budget=36)
                self._send_json(200, {"ok": True, "term": t})
            elif action == "variant":
                kind = body.get("kind")
                if kind not in ("wrong", "maybe", "rejected", "delete"):
                    self._send_json(400, {"error": "kind 不对"})
                    return
                typo_book.set_variant(body.get("tid"), str(body.get("variant") or ""), kind)
                if kind in ("rejected", "delete"):
                    threading.Thread(target=typo_book.undo_and_rerun, daemon=True).start()
                else:
                    typo_book.run_async(judge=(kind == "maybe"), discover_new=False, budget=200, auto_budget=24)
                self._send_json(200, {"ok": True})
            elif action == "term/delete":
                typo_book.delete_term(body.get("tid"))
                threading.Thread(target=typo_book.undo_and_rerun, daemon=True).start()
                self._send_json(200, {"ok": True})
            elif action == "veto":
                typo_book.veto(str(body.get("key") or ""), int(body.get("s")), str(body.get("from") or ""))
                typo_book.run_async(judge=False, discover_new=False)
                self._send_json(200, {"ok": True})
            elif action == "run":
                typo_book.run_async(judge=True, discover_new=True, budget=int(body.get("budget") or 300), auto_budget=36)
                self._send_json(200, {"ok": True})
            else:
                self._send_json(404, {"error": "没有这个操作"})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            traceback.print_exc()
            self._send_json(500, {"error": str(exc)})

    # ---------------- 每日报告 ----------------
    def _handle_report_generate(self):
        body = self._read_json_body() or {}
        date = str(body.get("date") or _today())
        turns = memory_store.history_for_date(date)
        if not turns:
            self._send_json(400, {"error": f"{date} 没有对话记录"})
            return
        try:
            text = deepseek_client.summarize_day(date, turns)
            report = memory_store.save_report(date, text, len(turns))
        except Exception as exc:
            traceback.print_exc()
            self._send_json(500, {"error": str(exc)})
            return
        self._send_json(200, {"ok": True, "report": report})

    # ---------------- 维护：服务状态 / 连通性 / 重启 ----------------
    def _handle_health(self):
        checks = {}
        for unit in SERVICES:
            try:
                r = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=5)
                checks[unit] = r.stdout.strip() or r.stderr.strip()
            except Exception as exc:
                checks[unit] = f"error: {exc}"
        # 公网入口（腾讯云 ten → frp → 本机）是否通
        t0 = time.time()
        try:
            req = urllib.request.Request(PUBLIC_OTA_URL, data=b"{}", method="POST",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                ok = resp.status == 200 and b"websocket" in resp.read()
            public = {"ok": ok, "ms": int((time.time() - t0) * 1000)}
        except Exception as exc:
            public = {"ok": False, "error": str(exc)[:120], "ms": int((time.time() - t0) * 1000)}
        # 桥接服务本机控制口
        bridge = _bridge_ctl("GET", "/xiaozhi/ota/status")
        self._send_json(200, {"services": checks, "public_entry": public,
                              "bridge": {"ok": "devices" in bridge, "error": bridge.get("error")},
                              "devices": bridge.get("devices", []),
                              "staged_firmware": bridge.get("staged_firmware", {}),
                              "server_time": time.strftime("%Y-%m-%d %H:%M:%S")})

    def _handle_device_status(self):
        # 首页表盘用：只给在线/电量/最后在线/告警，按角色裁剪（家人看不到开机记录、固件、服务告警原文）
        _, role = self._user()
        self._send_json(200, glance.build(_bridge_ctl("GET", "/xiaozhi/ota/status"), role,
                                          elder_id=ELDER_DEVICE_ID, monitor_state=_monitor_state()))

    def _handle_device_say(self):
        body = self._read_json_body() or {}
        text = str(body.get("text") or "").strip()
        if not text:
            self._send_json(400, {"error": "text 为空"})
            return
        result = _bridge_ctl("POST", "/xiaozhi/ota/say", {"text": text, "device_id": body.get("device_id", "")})
        # 说出去的话由桥接在真正放完后连同音频一起记入对话记录，这里不再重复记
        self._send_json(200 if result.get("ok") else 409, result)

    def _proxy_ctl_simple(self, ctl_path):
        body = self._read_json_body() or {}
        result = _bridge_ctl("POST", ctl_path, {"device_id": body.get("device_id", "")})
        self._send_json(200 if result.get("ok") else 409, result)

    def _handle_restart(self):
        body = self._read_json_body() or {}
        wanted = body.get("services") or []
        units = [u for u in wanted if u in SERVICES]
        if not units:
            self._send_json(400, {"error": "没有指定要重启的服务"})
            return
        # companion.service（我自己）必须排最后，且用 systemd-run 脱离本 cgroup（见 _handle_admin_config_post 的说明）
        units.sort(key=lambda u: u == "companion.service")

        def _later():
            time.sleep(0.5)
            try:
                subprocess.run(RESTART_CMD + units, check=True, timeout=15)
            except Exception:
                traceback.print_exc()
        threading.Thread(target=_later, daemon=True).start()
        self._send_json(200, {"ok": True, "restarting": units})

    # ---------------- OTA：把新固件放到公网文件目录，设备下次开机自检升级 ----------------
    def _handle_ota_stage(self):
        params = urllib.parse.parse_qs(self.path.partition("?")[2])
        board = (params.get("board") or [""])[0].strip()
        version = (params.get("version") or [""])[0].strip()
        if not board or not version or not all(ch.isalnum() or ch in "-_." for ch in board + version):
            self._send_json(400, {"error": "需要 board 和 version（只能是字母数字-_.）"})
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > 8 * 1024 * 1024:
            self._send_json(400, {"error": "固件文件大小不对"})
            return
        data = self.rfile.read(length)
        if data[:1] != b"\xe9":  # ESP 应用镜像 magic byte
            self._send_json(400, {"error": "这不像 ESP32 固件（xiaozhi.bin）"})
            return
        os.makedirs(PUBLIC_DIR, exist_ok=True)
        fname = f"fw_{board}_{version}_{secrets.token_hex(8)}.bin"   # 文件名带随机串：只有服务器下发的地址才能下载，猜不到
        with open(os.path.join(PUBLIC_DIR, fname), "wb") as f:
            f.write(data)
        staged = {}
        try:
            with open(OTA_STAGED_PATH, "r", encoding="utf-8") as f:
                staged = json.load(f)
        except Exception:
            staged = {}
        staged[board] = {"version": version, "file": fname, "size": len(data),
                         "staged_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        os.makedirs(os.path.dirname(OTA_STAGED_PATH), exist_ok=True)
        with open(OTA_STAGED_PATH, "w", encoding="utf-8") as f:
            json.dump(staged, f, ensure_ascii=False, indent=2)
        self._send_json(200, {"ok": True, "staged": staged})

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def _today():
    return datetime.date.today().strftime("%Y-%m-%d")


_balance_cache = {"at": 0, "data": None}


def _get_balances():
    """各家余额。DeepSeek、腾讯云有余额接口；MiniMax 只给控制台链接；阿里百炼的 API Key 查不了余额，
    给识别模块自己记的用量和费用估算。
    结果缓存 60 秒，页面每个角落都显示也不会把接口打爆。"""
    now = time.time()
    if _balance_cache["data"] and now - _balance_cache["at"] < 60:
        return _balance_cache["data"]
    out = {"deepseek": None, "minimax": {"console": "https://platform.minimaxi.com/user-center/basic-information/interface-key"},
           "aliyun": {"console": "https://billing-cost.console.aliyun.com/home"},
           "tencent": {"console": "https://console.cloud.tencent.com/expense/overview"}, "checked_at": time.strftime("%H:%M")}
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        try:
            req = urllib.request.Request("https://api.deepseek.com/user/balance", headers={"Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            info = (data.get("balance_infos") or [{}])[0]
            out["deepseek"] = {"currency": info.get("currency", "CNY"), "total": float(info.get("total_balance", 0) or 0),
                               "available": bool(data.get("is_available", True))}
        except Exception as exc:
            out["deepseek"] = {"error": str(exc)[:80]}
    try:
        out["tencent"]["total"] = asr_client.tencent_balance()
    except Exception as exc:
        out["tencent"]["error"] = str(exc)[:80]
    out["asr"] = asr_client.usage_summary()
    _balance_cache.update(at=now, data=out)
    return out


ELDER_DEVICE_ID = os.environ.get("XIAOZHI_ELDER_DEVICE_ID", "").lower()


def _monitor_state():
    """device_monitor.py 存的状态：桥接刚重启、内存里的「最后在线」丢了时，用它兜底。"""
    try:
        with open(os.path.join(DATA_DIR, "device_monitor.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _bridge_ctl(method, path, payload=None):
    """调桥接服务（xiaozhi_bridge.py）的本机控制口"""
    try:
        data = json.dumps(payload or {}).encode("utf-8") if method == "POST" else None
        req = urllib.request.Request(BRIDGE_CTL_BASE + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", "X-Ctl-Token": CTL_TOKEN})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception:
            return {"error": f"HTTP {exc.code}"}
    except Exception as exc:
        return {"error": str(exc)[:160]}


def main():
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    print(f"companion server: http://{LISTEN_HOST}:{LISTEN_PORT} (root {WEB_ROOT})")
    server.serve_forever()


if __name__ == "__main__":
    main()
