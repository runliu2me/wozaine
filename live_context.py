#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每一轮对话前拼进提示词的"此时此刻的真实情况"：当前时间、孩子和老人所在城市的实时天气。

模型自己不知道现在几点、什么天气，问它就只会编（实测：晚上 11 点半它说下午 3 点半）。
所以这些事实每轮都从真实来源取，塞进 system prompt，让它"照着念"而不是"猜"。

天气用 Open-Meteo（免费、无需 key），结果缓存 20 分钟；查不到就明确写"没查到"，让模型说不知道，而不是编一个。
"""
import datetime
import json
import os
import threading
import time
import urllib.request

import family

# 城市在 family.json 里配置；孩子和老人同城就只查一个
_E, _C = family.ELDER_CITY, family.CHILD_CITY
if family.SAME_CITY:
    PLACES = [(_E["name"], _E["lat"], _E["lon"], f"你和妈妈都在{_E['name']}")]
else:
    PLACES = [
        (_C["name"], _C["lat"], _C["lon"], f"你现在在{_C['name']}"),
        (_E["name"], _E["lat"], _E["lon"], f"妈妈在{_E['name']}"),
    ]
CACHE_SECONDS = 20 * 60
TZ = datetime.timezone(datetime.timedelta(hours=family.TZ_HOURS))

_cache = {"at": 0.0, "text": None}
_lock = threading.Lock()

# WMO weather code → 大白话
_WMO = {
    0: "晴", 1: "大体晴", 2: "多云", 3: "阴",
    45: "有雾", 48: "有雾", 51: "毛毛雨", 53: "小雨", 55: "小雨",
    56: "冻雨", 57: "冻雨", 61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "冻雨", 71: "小雪", 73: "中雪", 75: "大雪", 77: "小雪",
    80: "阵雨", 81: "阵雨", 82: "大阵雨", 85: "阵雪", 86: "大阵雪",
    95: "雷阵雨", 96: "雷阵雨带冰雹", 99: "雷阵雨带冰雹",
}
_WEEKDAY = "一二三四五六日"


def _describe(code):
    return _WMO.get(int(code), "天气一般")


def _fetch_weather(name, lat, lon):
    url = ("https://api.open-meteo.com/v1/forecast?"
           f"latitude={lat}&longitude={lon}"
           "&current=temperature_2m,weather_code,wind_speed_10m,relative_humidity_2m"
           "&daily=temperature_2m_max,temperature_2m_min,weather_code"
           "&timezone=Asia%2FShanghai&forecast_days=2")
    with urllib.request.urlopen(url, timeout=6) as resp:
        d = json.loads(resp.read().decode("utf-8"))
    cur = d["current"]
    daily = d["daily"]
    today = (f"{name}现在{_describe(cur['weather_code'])}，{round(cur['temperature_2m'])}度，"
             f"今天{round(daily['temperature_2m_min'][0])}到{round(daily['temperature_2m_max'][0])}度")
    if int(cur.get("wind_speed_10m", 0)) >= 25:
        today += "，风挺大"
    if int(cur.get("relative_humidity_2m", 0)) >= 85:
        today += "，挺潮"
    if len(daily["weather_code"]) > 1:
        today += (f"；明天{_describe(daily['weather_code'][1])}，"
                  f"{round(daily['temperature_2m_min'][1])}到{round(daily['temperature_2m_max'][1])}度")
    return today


def _weather_lines():
    lines = []
    for name, lat, lon, note in PLACES:
        try:
            lines.append(f"- {_fetch_weather(name, lat, lon)}（{note}）")
        except Exception as exc:
            lines.append(f"- {name}的天气这会儿没查到（{type(exc).__name__}）：别编，就说你没看天气预报")
    return lines


def _time_lines():
    now = datetime.datetime.now(TZ)
    h = now.hour
    if h < 5:
        part = "深夜"
    elif h < 9:
        part = "早上"
    elif h < 12:
        part = "上午"
    elif h < 14:
        part = "中午"
    elif h < 18:
        part = "下午"
    elif h < 20:
        part = "傍晚"
    else:
        part = "晚上"
    return [f"- 现在是 {now.year}年{now.month}月{now.day}日 星期{_WEEKDAY[now.weekday()]}，"
            f"{part} {now.strftime('%H:%M')}"]


SCHEDULE_PATH = os.path.join(os.environ.get("COMPANION_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")), "schedule.json")


def schedule_block():
    """孩子的日程（data/schedule.json，可选），由 sync_schedule.py 从日历同步过来。
    妈妈问"你忙什么""什么时候回来"，就照实说，不编行程。没有这个文件就什么都不加。"""
    try:
        with open(SCHEDULE_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return ""
    items = d.get("items") or []
    if not items:
        return ""
    today = datetime.datetime.now(TZ).strftime("%Y-%m-%d")
    home = family.ELDER_CITY["name"]
    lines = [f"## 你（{family.CHILD_NAME}）的日程（{d.get('range', '')}，同步于 {d.get('synced_at', '')}；今天 {today}）",
             f"- 妈妈问你最近忙什么、哪天回{home}、在哪儿，就照这里说；没有的事别编。不要主动把日程念给她听，她问到了才说，一次说一两件就够。",
             f"- 里面有\"{home}\"的就是回家看她；高铁/航班条目说明那天在路上。"]
    for it in items:
        when = it.get("date", "")
        if it.get("time"):
            when += " " + it["time"]
        loc = f"（{it['location']}）" if it.get("location") else ""
        lines.append(f"- {when}：{it.get('summary', '')}{loc}")
    return "\n" + "\n".join(lines) + "\n"


def current_context():
    """返回拼进 system prompt 的一段文字。时间每次都是实时的；天气最多 20 分钟旧。"""
    with _lock:
        if _cache["text"] is None or time.time() - _cache["at"] > CACHE_SECONDS:
            _cache["text"] = "\n".join(_weather_lines())
            _cache["at"] = time.time()
        weather = _cache["text"]
    lines = ["## 此时此刻的真实情况（每次对话都是最新的，说到时间、日期、天气一律以这里为准，不许自己猜）"]
    lines += _time_lines()
    lines.append(weather)
    sb = schedule_block()
    if sb:
        lines.append(sb)
    where = "" if family.SAME_CITY else f"她问你那边就说{_C['name']}的，说她那边就说{_E['name']}的。"
    lines.append("- 妈妈问几点、今天几号、什么天气，就按上面的直接回答；" + where +
                 "上面没写的事实（比如新闻、别的城市的天气、股票）你就是不知道，老实说“这个我没看”，绝对不能编。")
    return "\n".join(lines)


if __name__ == "__main__":
    print(current_context())
