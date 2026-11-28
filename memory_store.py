#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""持久记忆：妈妈这条对话线的长期事实 + 最近对话记录。

单用户场景，不做多租户设计。事实列表由 deepseek_client 在每轮结束后
提炼追加；这里只管读写存储，不碰模型调用。
"""

import json
import os
import re
import threading
import time

from platform_compat import file_lock  # 跨进程文件锁（桥接、管理页、实时页、每晚任务都会写这些文件）

DATA_DIR = os.environ.get("COMPANION_DATA_DIR",
                           os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
FACTS_PATH = os.path.join(DATA_DIR, "facts.json")
HISTORY_PATH = os.path.join(DATA_DIR, "history.jsonl")

_LOCK = threading.Lock()


def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _write_json_atomic(path, obj, indent=2):
    """写临时文件后 os.replace 原子替换，防止中途进程崩溃写空数据文件，并保留原文件权限。"""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    orig_mode = None
    try:
        orig_mode = os.stat(path).st_mode & 0o777
    except OSError:
        pass
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=indent)
            f.flush()
        if orig_mode is not None:
            try:
                os.chmod(tmp, orig_mode)
            except OSError:
                pass
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise


def load_facts():
    _ensure_dir()
    if not os.path.exists(FACTS_PATH):
        return []
    with open(FACTS_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def add_facts(new_facts):
    """new_facts: list[str]，去重追加，带时间戳"""
    if not new_facts:
        return
    with _LOCK, file_lock(FACTS_PATH):
        facts = load_facts()
        existing_text = {item["text"] for item in facts}
        for text in new_facts:
            text = text.strip()
            if not text or text in existing_text:
                continue
            facts.append({"text": text, "added_at": time.strftime("%Y-%m-%d %H:%M:%S")})
            existing_text.add(text)
        _ensure_dir()
        _write_json_atomic(FACTS_PATH, facts, indent=2)


def delete_fact_at(index):
    """按下标删一条事实，主要给管理页面用。下标越界就什么都不做"""
    with _LOCK, file_lock(FACTS_PATH):
        facts = load_facts()
        if 0 <= index < len(facts):
            facts.pop(index)
            _ensure_dir()
            _write_json_atomic(FACTS_PATH, facts, indent=2)


def append_history(role, text, audio=None, raw=None):
    """audio: 这句话对应的录音/合成语音，相对 DATA_DIR 的路径（如 audio/2026-09-16/210501_user.mp3）
    妈妈的话（role=user）入库前按错字本纠正：text 存纠正后的，asr_raw 存识别原文，fixes 记改了哪几处（可撤销）。
    raw: 调用方已经纠正过 text 时，把识别原文传进来。"""
    _ensure_dir()
    item = {"role": role, "text": text, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    if role == "user" and text:
        try:
            import typo_book
            source = raw if raw is not None else text
            fixed, fixes = typo_book.correct(source)
            if fixes:
                item.update(text=fixed, asr_raw=source, fixes=fixes)
        except Exception as exc:  # 错字本出任何问题都不能丢妈妈的话
            import sys
            sys.stderr.write(f"[typo] 入库纠正失败：{exc}\n")
    if audio:
        item["audio"] = audio
    with _LOCK, file_lock(HISTORY_PATH):
        with open(HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def rewrite_history(fn):
    """整份对话记录读出来交给 fn(items) 改，fn 返回 True 才写回（先写临时文件再替换，持锁期间别的进程追加会等着）。"""
    _ensure_dir()
    with _LOCK, file_lock(HISTORY_PATH):
        items = list(_iter_history())
        if not fn(items):
            return False
        tmp = HISTORY_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
        os.replace(tmp, HISTORY_PATH)
        return True


AUDIO_DIR = os.path.join(DATA_DIR, "audio")


def save_audio(pcm_bytes, sample_rate, role):
    """把一段 16bit 单声道 PCM 存成 mp3（32kbps，语音够用、体积小），按天分目录。
    返回相对 DATA_DIR 的路径；失败返回 None（不影响对话）"""
    if not pcm_bytes:
        return None
    import subprocess
    day = time.strftime("%Y-%m-%d")
    folder = os.path.join(AUDIO_DIR, day)
    os.makedirs(folder, exist_ok=True)
    name = f"{time.strftime('%H%M%S')}_{int(time.time() * 1000) % 1000:03d}_{role}.mp3"
    full = os.path.join(folder, name)
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "s16le", "-ar", str(sample_rate), "-ac", "1", "-i", "pipe:0",
             "-codec:a", "libmp3lame", "-b:a", "32k", full],
            input=pcm_bytes, capture_output=True, timeout=60,
        )
        if proc.returncode != 0 or not os.path.exists(full):
            return None
    except Exception:
        return None
    return f"audio/{day}/{name}"


def audio_path(rel):
    """把 history 里的 audio 相对路径解析成绝对路径；越界/不存在返回 None"""
    if not rel or not rel.startswith("audio/"):
        return None
    full = os.path.abspath(os.path.join(DATA_DIR, rel))
    if not full.startswith(os.path.abspath(AUDIO_DIR) + os.sep) or not os.path.isfile(full):
        return None
    return full


def recent_history(limit=12):
    """最近 N 条消息，用于喂给模型做短期上下文"""
    _ensure_dir()
    if not os.path.exists(HISTORY_PATH):
        return []
    with open(HISTORY_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    out = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _iter_history():
    _ensure_dir()
    if not os.path.exists(HISTORY_PATH):
        return
    with open(HISTORY_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def iter_history_raw():
    """全部对话（含 asr_raw / fixes 字段），给错字本用"""
    return _iter_history()


def history_dates():
    """有过对话的日期列表（新→旧），带当天条数"""
    counts = {}
    for item in _iter_history():
        day = (item.get("ts") or "")[:10]
        if day:
            counts[day] = counts.get(day, 0) + 1
    return [{"date": d, "count": counts[d]} for d in sorted(counts, reverse=True)]


def history_for_date(date):
    """某一天（YYYY-MM-DD）的全部对话，按时间顺序"""
    return [item for item in _iter_history() if (item.get("ts") or "").startswith(date)]


def update_fact_at(index, text):
    """改一条已记住的事（管理页面用）"""
    text = text.strip()
    if not text:
        return
    with _LOCK, file_lock(FACTS_PATH):
        facts = load_facts()
        if 0 <= index < len(facts):
            facts[index]["text"] = text
            facts[index]["edited_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _ensure_dir()
            _write_json_atomic(FACTS_PATH, facts, indent=2)


def replace_facts(texts, backup=True):
    """整体替换记忆列表（整理/去琐碎用）。默认先把旧文件备份成 facts.json.bak-<时间>"""
    with _LOCK, file_lock(FACTS_PATH):
        _ensure_dir()
        if backup and os.path.exists(FACTS_PATH):
            import shutil
            shutil.copy2(FACTS_PATH, FACTS_PATH + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        facts = [{"text": t.strip(), "added_at": now} for t in texts if t and t.strip()]
        _write_json_atomic(FACTS_PATH, facts, indent=2)
        return facts


# ---------------- 每日报告 ----------------
REPORTS_DIR = os.path.join(DATA_DIR, "reports")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _report_path(date):
    if not isinstance(date, str) or not _DATE_RE.match(date):
        return None
    return os.path.join(REPORTS_DIR, f"{date}.json")


def save_report(date, text, turns):
    path = _report_path(date)
    if not path:
        raise ValueError(f"非法日期格式: {date!r}")
    os.makedirs(REPORTS_DIR, exist_ok=True)
    payload = {"date": date, "text": text, "turns": turns,
               "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    with _LOCK, file_lock(path):
        _write_json_atomic(path, payload, indent=2)
    return payload


def load_report(date):
    p = _report_path(date)
    if not p or not os.path.exists(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return None


def list_reports():
    if not os.path.isdir(REPORTS_DIR):
        return []
    out = []
    for name in sorted(os.listdir(REPORTS_DIR), reverse=True):
        if name.endswith(".json"):
            rep = load_report(name[:-5])
            if rep:
                out.append({"date": rep["date"], "turns": rep.get("turns", 0),
                            "generated_at": rep.get("generated_at", ""),
                            "preview": (rep.get("text") or "")[:60]})
    return out
