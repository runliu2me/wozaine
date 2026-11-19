#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设置/修改管理页面的登录密码（写入本机 .env 的 ADMIN_PASSWORD_HASH；没有会话密钥就顺便生成一个）。
用法：venv/bin/python3 admin_password.py            # 交互输入
      venv/bin/python3 admin_password.py 新密码      # 直接给
改完重启 companion.service 生效；已登录的手机不受影响（会话密钥不变）。"""
import getpass
import json
import hashlib
import os
import secrets
import sys

ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def set_family(user, role, name, pw):
    data_dir = os.environ.get("COMPANION_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
    path = os.path.join(data_dir, "users.json")
    users = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, 200_000).hex()
    users[user] = {"name": name or user, "role": role, "hash": f"pbkdf2$200000${salt.hex()}${h}"}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=1)
    os.chmod(path, 0o600)
    print(f"账号 {user}（{role}）已写入 {path}，立即生效")


def main():
    # 家人账号：admin_password.py --user xiaohong --role family --name 小红 [密码]
    if "--user" in sys.argv:
        a = sys.argv
        user = a[a.index("--user") + 1]
        role = a[a.index("--role") + 1] if "--role" in a else "family"
        name = a[a.index("--name") + 1] if "--name" in a else user
        rest = [x for i, x in enumerate(a[1:], 1) if not x.startswith("--") and a[i - 1] not in ("--user", "--role", "--name")]
        pw = rest[0] if rest else getpass.getpass("密码：")
        if len(pw) < 8:
            print("密码至少 8 位"); return 1
        set_family(user, role, name, pw)
        return 0
    pw = sys.argv[1] if len(sys.argv) > 1 else getpass.getpass("新密码：")
    if len(pw) < 8:
        print("密码至少 8 位"); return 1
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, 200_000).hex()
    stored = f"pbkdf2$200000${salt.hex()}${h}"
    lines = open(ENV, encoding="utf-8").read().splitlines() if os.path.exists(ENV) else []
    out = [l for l in lines if not l.startswith("ADMIN_PASSWORD_HASH=") and l.strip() != "ADMIN_SESSION_SECRET="]
    out.append(f"ADMIN_PASSWORD_HASH={stored}")
    if not any(l.startswith("ADMIN_SESSION_SECRET=") for l in out):
        out.append(f"ADMIN_SESSION_SECRET={secrets.token_hex(32)}")
    with open(ENV, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(ENV, 0o600)
    print("已写入 .env；执行 sudo systemctl restart companion.service 生效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
