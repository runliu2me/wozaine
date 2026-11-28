#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""family 配置去硬编码离线单测：验证默认妈妈/儿子完全向后兼容，切换爸爸/女儿无称谓残留。"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 先将家庭档案与数据目录切到隔离临时路径，避免首次导入时读取仓库里的真实配置
_ISO = tempfile.TemporaryDirectory()
_INIT_FAMILY = os.path.join(_ISO.name, "init_family.json")
with open(_INIT_FAMILY, "w", encoding="utf-8") as _f:
    json.dump({"elder_name": "王秀英", "child_name": "小明"}, _f, ensure_ascii=False)
os.environ["COMPANION_FAMILY"] = _INIT_FAMILY
os.environ["COMPANION_DATA_DIR"] = _ISO.name


def _reload_with_family(cfg: dict):
    import family
    import book
    import deepseek_client
    import live_context
    import phone_brain
    import story
    import typo_book

    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as f:
        json.dump(cfg, f, ensure_ascii=False)
        path = f.name
    old_env = os.environ.get("COMPANION_FAMILY")
    try:
        os.environ["COMPANION_FAMILY"] = path
        importlib.reload(family)
        importlib.reload(live_context)
        importlib.reload(deepseek_client)
        importlib.reload(story)
        importlib.reload(book)
        importlib.reload(typo_book)
        importlib.reload(phone_brain)
        return family, live_context, deepseek_client, story, book, typo_book, phone_brain
    finally:
        if old_env is None:
            os.environ.pop("COMPANION_FAMILY", None)
        else:
            os.environ["COMPANION_FAMILY"] = old_env
        try:
            os.remove(path)
        except OSError:
            pass


def test_default_mom_and_son_backward_compatible():
    family, live_context, deepseek_client, story, book, typo_book, phone_brain = _reload_with_family({
        "elder_name": "王秀英",
        "elder_brief": "成都人，七十多岁",
        "elder_birth": "1950 年代",
        "child_name": "小明",
        "child_nickname": "明明",
        "book_title": "妈，你慢慢说",
        "elder_label": "妈妈",
        "elder_pronoun": "她",
    })
    assert family.ELDER_CALL == "妈"
    assert family.ELDER_FORMAL == "母亲"
    assert family.CHILD_ROLE == "儿子"
    assert family.GREETINGS[0] == "妈，我在呢。有什么事儿吗？"
    assert family.PROACTIVE_GREETINGS == ["妈，你在吗？", "妈，在吗？"]
    assert family.LOW_BATTERY_TEXT == "妈，我快没电了，帮我接上充电线，给我充充电吧。"
    assert family.FALLBACK_TEXT == "妈，我这边网络有点不好，你等一会儿再拍一下按钮找我。"
    assert book.SUBTITLE == "王秀英口述 · 她的儿子小明整理"


def test_dad_and_daughter_zero_leakage():
    family, live_context, deepseek_client, story, book, typo_book, phone_brain = _reload_with_family({
        "elder_name": "李建国",
        "elder_brief": "南京人，七十多岁",
        "elder_birth": "1950 年代",
        "child_name": "小红",
        "child_nickname": "红红",
        "child_role": "女儿",
        "book_title": "爸，你慢慢说",
        "elder_label": "爸爸",
        "elder_city": {"name": "南京", "lat": 32.06, "lon": 118.79},
        "child_city": {"name": "上海", "lat": 31.23, "lon": 121.47},
    })
    assert family.ELDER_CALL == "爸"
    assert family.ELDER_FORMAL == "父亲"
    assert family.ELDER_PRONOUN == "他"
    assert family.CHILD_ROLE == "女儿"
    assert all(g.startswith("爸，我在呢。") for g in family.GREETINGS)
    assert family.PROACTIVE_GREETINGS == ["爸，你在吗？", "爸，在吗？"]
    assert "爸" in family.LOW_BATTERY_TEXT and "妈" not in family.LOW_BATTERY_TEXT
    assert "爸" in family.FALLBACK_TEXT and "妈" not in family.FALLBACK_TEXT
    assert book.SUBTITLE == "李建国口述 · 他的女儿小红整理"

    # 离线测 live_context（含城市备注与日程块）与 typo_book.prompt_hint()
    orig_wl = live_context._weather_lines
    orig_sched = live_context.SCHEDULE_PATH
    with tempfile.TemporaryDirectory() as td:
        sched_path = os.path.join(td, "schedule.json")
        with open(sched_path, "w", encoding="utf-8") as sf:
            json.dump({"range": "本周", "synced_at": "10:00", "items": [{"date": "2026-10-02", "summary": "回南京"}]}, sf, ensure_ascii=False)
        live_context.SCHEDULE_PATH = sched_path
        live_context._weather_lines = lambda: [f"- {name}晴（{note}）" for name, _lat, _lon, note in live_context.PLACES]
        live_context._cache.update(at=0.0, text=None)
        try:
            ctx = live_context.current_context()
        finally:
            live_context._weather_lines = orig_wl
            live_context.SCHEDULE_PATH = orig_sched

        orig_tb_data = (typo_book.DATA_DIR, typo_book.BOOK_PATH)
        typo_book.DATA_DIR = td
        typo_book.BOOK_PATH = os.path.join(td, "typo_book.json")
        typo_book._CACHE.update(mtime=None, book=None, compiled=None)
        try:
            with open(typo_book.BOOK_PATH, "w", encoding="utf-8") as tf:
                json.dump({"terms": [{"id": "t1", "right": "小红", "wrong": ["小宏"], "maybe": [], "note": "女儿"}]}, tf, ensure_ascii=False)
            typo_hint = typo_book.prompt_hint()
        finally:
            typo_book.DATA_DIR, typo_book.BOOK_PATH = orig_tb_data
            typo_book._CACHE.update(mtime=None, book=None, compiled=None)

    # 各模块静态提示词模板与实际运行时上下文均不应残留「妈 / 母亲 / 儿子 / 她」
    samples = [
        ctx,
        typo_hint,
        deepseek_client._elder(deepseek_client.MEMORY_CRITERIA),
        story.prompt_block({"text": "小时候最爱吃什么？", "chapter": "童年"}, proactive=True, turn=1),
        story.retell_block({"chapter": "童年", "retell": ["你小时候住河边，对吧？"]}, turn=1),
        family.adapt_prompt(story.EXTRACT_PROMPT),
        family.adapt_prompt(book.CHAPTER_PROMPT),
        family.adapt_prompt(typo_book.JUDGE_PROMPT),
        family.adapt_prompt(phone_brain.PHONE_SUFFIX),
    ]
    for s in samples:
        for bad in ("妈妈", "母亲", "妈", "儿子", "她"):
            assert bad not in s, f"found leaked {bad!r} in:\n{s[:240]}"


def test_adapt_prompt_single_pass_no_double_replace():
    # 若 elder_label 含「妈」（如「阿妈」），单趟替换不应变成「阿阿妈」
    family, *_ = _reload_with_family({
        "elder_label": "阿妈",
        "elder_call": "阿妈",
        "child_role": "女儿",
    })
    out = family.adapt_prompt("妈妈说：妈，儿子回来了。")
    assert out == "阿妈说：阿妈，女儿回来了。", repr(out)


def test_fallback_cache_invalidates_when_text_changes():
    import bridge_media
    import platform_compat

    platform_compat.ensure_opus_dll()
    try:
        import opuslib
    except Exception:
        return
    assert opuslib is not None

    orig_conv = bridge_media.mp3_to_pcm_mono
    calls = []
    try:
        bridge_media.mp3_to_pcm_mono = lambda mp3_bytes, rate, gain_db: b"\x00\x00" * (rate * 60 // 1000)
        with tempfile.TemporaryDirectory() as td:
            c1 = bridge_media.FallbackOpusCache(td, "妈，网络不好", lambda t: (calls.append(t), b"mp3")[1], 48000, 64000, 60)
            assert len(c1.frames(16000, 960, 0)) == 1
            assert calls == ["妈，网络不好"]

            # 相同文案命中磁盘缓存，不再调 TTS
            c2 = bridge_media.FallbackOpusCache(td, "妈，网络不好", lambda t: (calls.append(t), b"mp3")[1], 48000, 64000, 60)
            assert len(c2.frames(16000, 960, 0)) == 1
            assert calls == ["妈，网络不好"]

            # 改称谓后文案变了，应自动重新合成而不是复用旧「妈」缓存
            c3 = bridge_media.FallbackOpusCache(td, "爸，网络不好", lambda t: (calls.append(t), b"mp3")[1], 48000, 64000, 60)
            assert len(c3.frames(16000, 960, 0)) == 1
            assert calls == ["妈，网络不好", "爸，网络不好"]
    finally:
        bridge_media.mp3_to_pcm_mono = orig_conv


if __name__ == "__main__":
    test_default_mom_and_son_backward_compatible()
    print("PASS default mom/son backward compatible")
    test_dad_and_daughter_zero_leakage()
    print("PASS dad/daughter zero leakage (incl. live_context & typo_book)")
    test_adapt_prompt_single_pass_no_double_replace()
    print("PASS adapt_prompt single-pass safety")
    test_fallback_cache_invalidates_when_text_changes()
    print("PASS fallback cache invalidates on text change")
    print("全部通过")
