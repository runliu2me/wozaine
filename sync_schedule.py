#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""（可选）把孩子的日历同步成 data/schedule.json，AI 每轮对话都会带着它（见 live_context.schedule_block），
妈妈问"你最近忙什么""哪天回来"时就能照实说，不编行程。

用法：在 .env 里设 SCHEDULE_ICS_URL=<日历的 iCal/ICS 订阅地址>（飞书、钉钉、Google、Outlook、iCloud 日历都能导出
这种"私密订阅链接"），然后启用 systemd/schedule-sync.timer（每小时一次），或手动跑：
    venv/bin/python3 sync_schedule.py
不想接日历也可以手写 data/schedule.json，格式：
    {"range": "说明文字", "synced_at": "2026-01-01 08:00",
     "items": [{"date": "2026-01-05", "time": "09:30", "summary": "去北京出差", "location": "北京"}]}
（date 可以写成 "2026-01-05~2026-01-07" 表示多天。）

限制：这是个很小的 ICS 解析器，不展开重复日程（RRULE），只取单次日程；时区按 family.json 的时区换算。
"""
import datetime
import json
import os
import re
import sys
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import family  # noqa: E402

_ENV = os.path.join(BASE_DIR, ".env")
if os.path.exists(_ENV):
    for _line in open(_ENV, encoding="utf-8"):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

DATA_DIR = os.environ.get("COMPANION_DATA_DIR", os.path.join(BASE_DIR, "data"))
OUT = os.path.join(DATA_DIR, "schedule.json")
ICS_URL = os.environ.get("SCHEDULE_ICS_URL", "")
TZ = datetime.timezone(datetime.timedelta(hours=family.TZ_HOURS))


def _unfold(text):
    return re.sub(r"\r?\n[ \t]", "", text).splitlines()


def _unescape(v):
    return v.replace("\\n", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip()


def _parse_dt(prop, value):
    """返回 (date, 'HH:MM' 或 '')。带 Z 的按 UTC 换算到本地时区；带 TZID 或不带的当作本地时间。"""
    if "VALUE=DATE" in prop or re.fullmatch(r"\d{8}", value):
        return datetime.date(int(value[:4]), int(value[4:6]), int(value[6:8])), ""
    m = re.fullmatch(r"(\d{8})T(\d{2})(\d{2})(\d{2})?(Z?)", value)
    if not m:
        return None, ""
    d = datetime.datetime.strptime(m.group(1) + m.group(2) + m.group(3), "%Y%m%d%H%M")
    if m.group(5) == "Z":
        d = d.replace(tzinfo=datetime.timezone.utc).astimezone(TZ)
    return d.date(), d.strftime("%H:%M")


def parse_ics(text):
    events, cur = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            prop, value = line.split(":", 1)
            name = prop.split(";", 1)[0].upper()
            if name in ("DTSTART", "DTEND"):
                cur[name] = _parse_dt(prop, value)
            elif name in ("SUMMARY", "LOCATION", "STATUS"):
                cur[name] = _unescape(value)
    return events


def main():
    if not ICS_URL:
        print("没设 SCHEDULE_ICS_URL，跳过（可以手写 data/schedule.json，格式见文件开头说明）")
        return 0
    today = datetime.datetime.now(TZ).date()
    start = (today.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
    end = (today.replace(day=1) + datetime.timedelta(days=62)).replace(day=1)
    with urllib.request.urlopen(ICS_URL, timeout=60) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    items = []
    for e in parse_ics(text):
        d, t = e.get("DTSTART", (None, ""))
        if not d or not (start <= d < end) or e.get("STATUS") == "CANCELLED":
            continue
        summary = e.get("SUMMARY", "")
        if not summary:
            continue
        date = d.isoformat()
        ed, _ = e.get("DTEND", (None, ""))
        if not t and ed and ed > d + datetime.timedelta(days=1):   # 全天多日：DTEND 是第二天（不含）
            date = f"{date}~{(ed - datetime.timedelta(days=1)).isoformat()}"
        items.append({"date": date, "time": t, "summary": summary, "location": e.get("LOCATION", "")})
    items.sort(key=lambda x: (x["date"], x["time"]))
    out = {"range": f"{start} ~ {end - datetime.timedelta(days=1)}",
           "synced_at": datetime.datetime.now(TZ).strftime("%Y-%m-%d %H:%M"), "items": items}
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print(f"{len(items)} 条日程写入 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
