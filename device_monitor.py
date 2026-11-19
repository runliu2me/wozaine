#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""老人那台设备的离线告警（systemd timer 每 15 分钟跑一次，见 systemd/device-monitor.timer）。

设备放在老人家里，没人会盯着它。这里做三件事：
  1. 桥接服务本身连不上 → 告警
  2. 老人的设备超过 OFFLINE_AFTER 秒没和服务器说过话 → 告警（之后每 24 小时提醒一次）
  3. 恢复上线 → 告一声"恢复了"
另外把桥接服务攒下的服务级故障（识别/合成欠费、电量低等）取走发出去。

告警发到哪里（.env 里配，二选一，都不配就只写日志）：
  ALERT_WEBHOOK_URL + ALERT_WEBHOOK_KIND：群机器人 webhook，KIND 取 wecom（企业微信）/ feishu（飞书）/
      dingtalk（钉钉）/ json（POST {"text": ...}，适合自建服务、Bark 等）
  ALERT_COMMAND：任意命令，告警文字从标准输入传进去，比如 "mail -s 我在呢 you@example.com"
状态存在 data/device_monitor.json，避免重复轰炸。
手动跑：venv/bin/python3 device_monitor.py
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

import family

# .env 由 systemd 的 EnvironmentFile 读进来；手动跑时也读一下
_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_ENV):
    for _line in open(_ENV, encoding="utf-8"):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("COMPANION_DATA_DIR", os.path.join(BASE_DIR, "data"))
STATE_PATH = os.path.join(DATA_DIR, "device_monitor.json")

BRIDGE_STATUS_URL = os.environ.get("XIAOZHI_STATUS_URL", "http://127.0.0.1:3096/xiaozhi/ota/status")
CTL_TOKEN = os.environ.get("XIAOZHI_CTL_TOKEN", "")
ELDER_DEVICE_ID = os.environ.get("XIAOZHI_ELDER_DEVICE_ID", "").lower()   # 老人那台设备的 MAC（小写，冒号分隔）
OFFLINE_AFTER = int(os.environ.get("XIAOZHI_OFFLINE_AFTER", str(2 * 3600)))   # 2 小时没联系就算离线
REALERT_EVERY = 24 * 3600
ALERT_WEBHOOK_URL = os.environ.get("ALERT_WEBHOOK_URL", "")
ALERT_WEBHOOK_KIND = os.environ.get("ALERT_WEBHOOK_KIND", "wecom").lower()
ALERT_COMMAND = os.environ.get("ALERT_COMMAND", "")


def _load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


def _webhook_body(text):
    if ALERT_WEBHOOK_KIND == "feishu":
        return {"msg_type": "text", "content": {"text": text}}
    if ALERT_WEBHOOK_KIND in ("wecom", "dingtalk"):
        return {"msgtype": "text", "text": {"content": text}}
    return {"text": text}


def notify(text):
    print("[alert]", text)
    ok = False
    if ALERT_WEBHOOK_URL:
        try:
            req = urllib.request.Request(ALERT_WEBHOOK_URL, data=json.dumps(_webhook_body(text)).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                ok = 200 <= resp.status < 300
        except Exception as exc:
            print("webhook 发送失败:", exc)
    if ALERT_COMMAND:
        try:
            r = subprocess.run(ALERT_COMMAND, shell=True, input=text, capture_output=True, text=True, timeout=60)
            ok = ok or r.returncode == 0
            if r.returncode != 0:
                print("ALERT_COMMAND 失败:", (r.stderr or r.stdout)[:300])
        except Exception as exc:
            print("ALERT_COMMAND 异常:", exc)
    if not (ALERT_WEBHOOK_URL or ALERT_COMMAND):
        print("没配 ALERT_WEBHOOK_URL / ALERT_COMMAND，告警只写日志")
    return ok


def fetch_status():
    req = urllib.request.Request(BRIDGE_STATUS_URL, headers={"X-Ctl-Token": CTL_TOKEN})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


BALANCE_ALERT_BELOW = float(os.environ.get("DEEPSEEK_BALANCE_ALERT_BELOW", "30"))


def check_balance(state, now):
    """DeepSeek 余额快用完时提前提醒（一天最多一次）。没钱了设备只会说"网络不好"，妈妈不会知道是欠费。"""
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key or now - state.get("balance_checked_at", 0) < 24 * 3600:
        return
    try:
        req = urllib.request.Request("https://api.deepseek.com/user/balance", headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        total = float(((data.get("balance_infos") or [{}])[0]).get("total_balance", 0) or 0)
        state["balance_checked_at"] = now
        print(f"DeepSeek 余额：{total:.2f}")
        if total < BALANCE_ALERT_BELOW:
            notify(f"[我在呢] DeepSeek 余额只剩 {total:.0f} 元，低于 {BALANCE_ALERT_BELOW:.0f} 元，请尽快充值，否则设备会没法对话。")
    except Exception as exc:
        print("余额检查失败：", exc)


def main():
    now = time.time()
    state = _load_state()
    stamp = time.strftime("%m-%d %H:%M")
    check_balance(state, now)

    # 1) 桥接服务
    try:
        status = fetch_status()
        bridge_ok = True
    except Exception as exc:
        status = None
        bridge_ok = False
        print("bridge status failed:", exc)

    if not bridge_ok:
        last = state.get("bridge_alert_at", 0)
        if now - last > REALERT_EVERY:
            if notify(f"[我在呢] {stamp} 家里服务器上的桥接服务连不上了（设备将无法对话）。"
                      f"请检查服务器上的 xiaozhi-bridge 服务。"):
                state["bridge_alert_at"] = now
        _save_state(state)
        return
    if state.pop("bridge_alert_at", None):
        notify(f"[我在呢] {stamp} 桥接服务已恢复。")

    # 1.5) 设备崩溃重启：固件 2.8.0 起开机会上报复位原因和重启前最后在干什么
    boots = (status.get("boots") or {}).get(ELDER_DEVICE_ID) or []
    seen = float(state.get("last_boot_ts", 0))
    crash_lines = []
    for b in boots:
        if b.get("ts", 0) <= seen:
            continue
        if b.get("reason") in ("panic", "task_wdt", "int_wdt", "other_wdt", "brownout"):
            crash_lines.append(f"{time.strftime('%m-%d %H:%M', time.localtime(b['ts']))} {b['reason']}，重启前：{b.get('last_action') or '?'}，运行了 {b.get('uptime_before')} 秒")
    if boots:
        state["last_boot_ts"] = max(b.get("ts", 0) for b in boots)
    if crash_lines:
        notify("[我在呢] 设备崩溃重启：\n" + "\n".join(crash_lines[-5:]))

    # 1.6) 桥接记下的服务级告警（欠费等）：没发过的发出去
    sent = set(state.get("alerts_sent", []))
    for a in status.get("alerts") or []:
        k = f"{a.get('key')}@{int(a.get('ts', 0))}"
        if k in sent:
            continue
        if notify(f"[我在呢] {a.get('text')}"):
            sent.add(k)
    state["alerts_sent"] = sorted(sent)[-50:]

    # 2) 老人的设备
    online = any(d.get("device_id") == ELDER_DEVICE_ID for d in status.get("devices", []))
    last_seen = float((status.get("last_seen") or {}).get(ELDER_DEVICE_ID) or 0)
    # 桥接刚重启时 last_seen 为空：用上次记录的值，避免误报
    if last_seen == 0:
        last_seen = float(state.get("last_seen", 0))
    if online:
        last_seen = now
    state["last_seen"] = last_seen
    state["online"] = online

    offline_for = now - last_seen if last_seen else None
    is_offline = (not online) and (offline_for is None or offline_for > OFFLINE_AFTER)

    if is_offline:
        last_alert = state.get("device_alert_at", 0)
        if now - last_alert > REALERT_EVERY:
            hours = int(offline_for // 3600) if offline_for is not None else "?"
            if notify(f"[我在呢] {stamp} {family.ELDER_LABEL}的设备已经 {hours} 小时没有连上服务器了。"
                      f"可能是家里断网/停电，或设备被关机。设备联网后会自动恢复；"
                      f"如果长时间不恢复，请打电话让家里人看一眼设备是否亮着、路由器是否正常。"):
                state["device_alert_at"] = now
    else:
        if state.pop("device_alert_at", None):
            notify(f"[我在呢] {stamp} {family.ELDER_LABEL}的设备已重新连上服务器。")

    _save_state(state)
    print(f"bridge ok; elder device online={online}, last_seen={time.strftime('%m-%d %H:%M', time.localtime(last_seen)) if last_seen else '-'}")


if __name__ == "__main__":
    sys.exit(main())
