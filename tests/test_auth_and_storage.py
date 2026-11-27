#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安全与原子存储离线单测：phone_brain 鉴权、memory_store 原子写与日期路径校验、admin_password 原子写。"""
from __future__ import annotations

import os
import stat
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 隔离数据与家庭档案目录，避免在本机已有部署目录跑单测时读取真实数据
_ISOLATED_DIR = tempfile.TemporaryDirectory()
os.environ["COMPANION_DATA_DIR"] = _ISOLATED_DIR.name

import admin_password
import memory_store
import phone_brain


def test_memory_store_atomic_facts_and_reports():
    orig = (memory_store.DATA_DIR, memory_store.FACTS_PATH, memory_store.REPORTS_DIR)
    with tempfile.TemporaryDirectory() as td:
        memory_store.DATA_DIR = td
        memory_store.FACTS_PATH = os.path.join(td, "facts.json")
        memory_store.REPORTS_DIR = os.path.join(td, "reports")
        try:
            memory_store.add_facts(["喜欢喝茉莉花茶", "每周三去公园打太极"])
            facts = memory_store.load_facts()
            assert [f["text"] for f in facts] == ["喜欢喝茉莉花茶", "每周三去公园打太极"]

            # 模拟原有 0600 隐私权限：原子替换后仍应保持 0600，不被放宽为 0644
            if os.name != "nt":
                os.chmod(memory_store.FACTS_PATH, 0o600)
            memory_store.update_fact_at(0, "喜欢喝淡一点的茉莉花茶")
            assert memory_store.load_facts()[0]["text"] == "喜欢喝淡一点的茉莉花茶"
            if os.name != "nt":
                assert stat.S_IMODE(os.stat(memory_store.FACTS_PATH).st_mode) == 0o600

            memory_store.delete_fact_at(1)
            assert len(memory_store.load_facts()) == 1

            memory_store.replace_facts(["膝盖阴天会酸", "爱吃白菜猪肉饺子"], backup=True)
            assert [f["text"] for f in memory_store.load_facts()] == ["膝盖阴天会酸", "爱吃白菜猪肉饺子"]
            assert any(name.startswith("facts.json.bak-") for name in os.listdir(td))

            rep = memory_store.save_report("2026-10-01", "今天心情不错。", 6)
            assert rep["date"] == "2026-10-01"
            assert memory_store.load_report("2026-10-01")["text"] == "今天心情不错。"

            # 路径穿越防护：非法日期不得读到上级目录文件（如 users.json / ota_staged.json）
            with open(os.path.join(td, "users.json"), "w", encoding="utf-8") as f:
                f.write('{"leak": true}')
            for bad_date in ("../users", "..\\users", "2026-10-01/../users", "2026/10/01", "bad"):
                assert memory_store.load_report(bad_date) is None, f"should reject {bad_date!r}"
                try:
                    memory_store.save_report(bad_date, "x", 1)
                except ValueError:
                    pass
                else:
                    raise AssertionError(f"expected ValueError for {bad_date!r}")

            # 不应遗留 .tmp 临时文件
            leftover = [n for n in os.listdir(td) if n.endswith(".tmp")]
            leftover_rep = [n for n in os.listdir(memory_store.REPORTS_DIR) if n.endswith(".tmp")]
            assert not leftover and not leftover_rep, (leftover, leftover_rep)
        finally:
            memory_store.DATA_DIR, memory_store.FACTS_PATH, memory_store.REPORTS_DIR = orig


def test_admin_password_set_family_validation_and_atomic():
    with tempfile.TemporaryDirectory() as td:
        old = os.environ.get("COMPANION_DATA_DIR")
        os.environ["COMPANION_DATA_DIR"] = td
        try:
            for bad_user in ("", "a.b", "bad user"):
                try:
                    admin_password.set_family(bad_user, "family", "测试", "12345678")
                except ValueError:
                    pass
                else:
                    raise AssertionError(f"expected ValueError for {bad_user!r}")
            admin_password.set_family("xiaohong", "family", "小红", "12345678")
            upath = os.path.join(td, "users.json")
            assert os.path.isfile(upath)
            if os.name != "nt":
                assert stat.S_IMODE(os.stat(upath).st_mode) == 0o600
            assert not [n for n in os.listdir(td) if n.endswith(".tmp")]
        finally:
            if old is None:
                os.environ.pop("COMPANION_DATA_DIR", None)
            else:
                os.environ["COMPANION_DATA_DIR"] = old


def test_phone_brain_auth_and_default_model():
    sent = []

    class _FakeHandler:
        def __init__(self, auth_header, body=None):
            self.headers = {"Authorization": auth_header}
            self._body = body or {}

        def _send_json(self, code, obj):
            sent.append((code, obj))

        def _read_json_body(self, max_size=0):
            return self._body

    orig_key = phone_brain.PHONE_API_KEY
    orig_stream = phone_brain.deepseek_client.generate_reply_stream
    orig_persona = phone_brain._persona
    orig_recent = phone_brain.memory_store.recent_history
    orig_facts = phone_brain.memory_store.load_facts
    try:
        phone_brain.PHONE_API_KEY = "secret-phone-token"
        phone_brain._persona = lambda: "测试人设"
        phone_brain.memory_store.recent_history = lambda limit=8: []
        phone_brain.memory_store.load_facts = lambda: []

        phone_brain.handle(_FakeHandler("Bearer wrong-token"))
        assert sent[-1][0] == 401

        phone_brain.handle(_FakeHandler(""))
        assert sent[-1][0] == 401

        phone_brain.deepseek_client.generate_reply_stream = lambda *a, **kw: iter(["爸，我在呢。"])
        phone_brain.handle(_FakeHandler("Bearer secret-phone-token", {"messages": [], "stream": False}))
        code, body = sent[-1]
        assert code == 200
        assert body["model"] == "wozaine-phone"
        assert "runrun" not in body["model"]
    finally:
        phone_brain.PHONE_API_KEY = orig_key
        phone_brain.deepseek_client.generate_reply_stream = orig_stream
        phone_brain._persona = orig_persona
        phone_brain.memory_store.recent_history = orig_recent
        phone_brain.memory_store.load_facts = orig_facts


if __name__ == "__main__":
    test_memory_store_atomic_facts_and_reports()
    print("PASS memory_store atomic writes & path traversal guard")
    test_admin_password_set_family_validation_and_atomic()
    print("PASS admin_password set_family validation & atomic write")
    test_phone_brain_auth_and_default_model()
    print("PASS phone_brain auth and default model")
    print("全部通过")
