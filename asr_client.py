#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""语音识别：阿里云百炼 Qwen3-ASR-Flash 为主，腾讯云一句话识别兜底。

选型经验（用一位老人的 15 段原声实测，同一批录音）：
  - 阿里 qwen3-asr-flash：人名几乎全对（讯飞方言大模型常写错），1.1–1.9 秒，按秒计费；
  - 腾讯 16k_zh + 热词：和讯飞差不多，0.6–1.3 秒，按次计费；
  - 腾讯 16k_zh_dialect（多方言）反而最差——老人多半说的是带口音的普通话，不是纯方言。
  - 讯飞欠费时报 11201 "licc failed"，曾因告警规则没认出来，连续三天没识别也没人知道——所以失败一定要告警。
所以主用阿里，阿里出错（欠费/超时/网络）就当场改用腾讯，这一句照样能听懂；
两家都失败才抛异常，交给上层放兜底语音。主线路失败一次就通过 ALERT_HOOK 告警（上层 24 小时去重），
不能再让"悄悄降级"拖好几天。

接口：transcribe_pcm(16k/16bit/单声道 PCM) -> 文字。
"""

import base64
import fcntl
import hashlib
import hmac
import io
import json
import os
import sys
import time
import wave

import requests

import family

DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
# 百炼控制台给的业务空间地址（…/api/v1）；没配就用公共地址
DASHSCOPE_BASE_URL = os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/api/v1").rstrip("/")
DASHSCOPE_ASR_MODEL = os.environ.get("DASHSCOPE_ASR_MODEL", "qwen3-asr-flash")
# 给识别模型的背景提示：说话人和常出现的词。阿里拿它当上下文，腾讯取其中的热词
ASR_CONTEXT = os.environ.get("ASR_CONTEXT", family.ASR_CONTEXT)   # 识别提示：在 family.json 里改

TENCENT_SECRET_ID = os.environ.get("TENCENT_SECRET_ID", "")
TENCENT_SECRET_KEY = os.environ.get("TENCENT_SECRET_KEY", "")
TENCENT_REGION = os.environ.get("TENCENT_ASR_REGION", "ap-shanghai")
TENCENT_ENGINE = os.environ.get("TENCENT_ASR_ENGINE", "16k_zh")
TENCENT_HOTWORDS = os.environ.get("TENCENT_ASR_HOTWORDS", family.ASR_HOTWORDS)

SAMPLE_RATE = 16000

# 识别用量自己记账（管理页「维护」显示）：阿里百炼的 API Key 查不了账户余额，只能靠这里估算花了多少。
# 桥接、管理页、实时页三个进程都会写，用 flock 串行化
DATA_DIR = os.environ.get("COMPANION_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
USAGE_PATH = os.path.join(DATA_DIR, "asr_usage.json")
USAGE_KEEP_DAYS = 120
# qwen3-asr-flash 北京地域单价（元/秒），2026-10 查的官方价；只用来估算
ALIYUN_PRICE_PER_SEC = float(os.environ.get("ALIYUN_ASR_PRICE_PER_SEC", "0.00022"))

# 上层（xiaozhi_bridge）挂上 raise_alert：fn(key, text)。识别模块自己不知道怎么通知管理员。
ALERT_HOOK = None


def _alert(key, text):
    sys.stderr.write(f"[asr] {key}: {text}\n")
    if ALERT_HOOK:
        try:
            ALERT_HOOK(key, text)
        except Exception:
            pass


def _record(field, seconds=0.0):
    """记一笔：field = aliyun / tencent（识别成功，累计句数和秒数）或 aliyun_fail / all_fail（只计次数）。
    记账失败绝不能影响识别本身。"""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(USAGE_PATH + ".lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                with open(USAGE_PATH, encoding="utf-8") as f:
                    usage = json.load(f)
            except (OSError, ValueError):
                usage = {}
            day = usage.setdefault(time.strftime("%Y-%m-%d"), {})
            item = day.setdefault(field, {"n": 0, "sec": 0.0})
            item["n"] += 1
            item["sec"] = round(item["sec"] + seconds, 1)
            for old in sorted(usage)[:-USAGE_KEEP_DAYS]:
                del usage[old]
            tmp = USAGE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(usage, f, ensure_ascii=False)
            os.replace(tmp, USAGE_PATH)
    except Exception as exc:
        sys.stderr.write(f"[asr] usage record failed: {exc}\n")


def usage_summary():
    """今天和本月的识别用量 + 阿里费用估算。"""
    try:
        with open(USAGE_PATH, encoding="utf-8") as f:
            usage = json.load(f)
    except (OSError, ValueError):
        usage = {}
    today, month = time.strftime("%Y-%m-%d"), time.strftime("%Y-%m")

    def total(days):
        out = {}
        for d in days:
            for field, item in usage.get(d, {}).items():
                acc = out.setdefault(field, {"n": 0, "sec": 0.0})
                acc["n"] += item.get("n", 0)
                acc["sec"] = round(acc["sec"] + item.get("sec", 0.0), 1)
        ali = out.get("aliyun", {}).get("sec", 0.0)
        out["aliyun_cost"] = round(ali * ALIYUN_PRICE_PER_SEC, 2)
        return out

    return {"today": total([today]), "month": total([d for d in usage if d.startswith(month)]),
            "price_per_sec": ALIYUN_PRICE_PER_SEC}


def _pcm_to_wav(pcm_bytes):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm_bytes)
    return buf.getvalue()


def _typo_hotwords():
    """错字本里管理员确认过的词（人名、地名），每句识别都带给识别模型。失败就当没有。"""
    try:
        import typo_book
        return typo_book.hotwords()
    except Exception:
        return []


def _aliyun_context():
    words = [w for w in _typo_hotwords() if w not in ASR_CONTEXT]
    return ASR_CONTEXT + ("家里人和常提的地方：" + "、".join(words) + "。" if words else "")


def _tencent_hotwords():
    have = [x.split("|")[0] for x in TENCENT_HOTWORDS.split(",") if x]
    extra = [f"{w}|10" for w in _typo_hotwords() if w not in have and len(w) <= 30]
    return ",".join([x for x in TENCENT_HOTWORDS.split(",") if x] + extra[:120])


def _aliyun(pcm_bytes, timeout):
    if not DASHSCOPE_API_KEY:
        raise RuntimeError("阿里云百炼 DASHSCOPE_API_KEY 未配置")
    audio = "data:audio/wav;base64," + base64.b64encode(_pcm_to_wav(pcm_bytes)).decode()
    body = {
        "model": DASHSCOPE_ASR_MODEL,
        "input": {"messages": [
            {"role": "system", "content": [{"text": _aliyun_context()}]},
            {"role": "user", "content": [{"audio": audio}]},
        ]},
        "parameters": {"asr_options": {"enable_itn": False, "language": "zh"}},
    }
    resp = requests.post(
        DASHSCOPE_BASE_URL + "/services/aigc/multimodal-generation/generation",
        json=body, timeout=timeout,
        headers={"Authorization": "Bearer " + DASHSCOPE_API_KEY},
    )
    if resp.status_code != 200:
        try:
            err = resp.json()
            detail = f"{err.get('code')}: {err.get('message')}"
        except ValueError:
            detail = resp.text[:200]
        raise RuntimeError(f"阿里云识别报错 HTTP {resp.status_code} {detail}")
    content = resp.json()["output"]["choices"][0]["message"]["content"]
    return "".join(part.get("text", "") for part in content).strip()


def _tencent_call(service, action, version, payload, region="", timeout=10):
    """腾讯云 API 3.0（TC3-HMAC-SHA256 签名）。返回 Response 字典，带 Error 时抛 RuntimeError。"""
    if not (TENCENT_SECRET_ID and TENCENT_SECRET_KEY):
        raise RuntimeError("腾讯云 TENCENT_SECRET_ID/TENCENT_SECRET_KEY 未配置")
    host = f"{service}.tencentcloudapi.com"
    body = json.dumps(payload)
    ts = int(time.time())
    date = time.strftime("%Y-%m-%d", time.gmtime(ts))
    canonical = ("POST\n/\n\ncontent-type:application/json; charset=utf-8\nhost:%s\n\ncontent-type;host\n%s"
                 % (host, hashlib.sha256(body.encode()).hexdigest()))
    scope = f"{date}/{service}/tc3_request"
    to_sign = f"TC3-HMAC-SHA256\n{ts}\n{scope}\n" + hashlib.sha256(canonical.encode()).hexdigest()

    def _h(key, msg):
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    signing_key = _h(_h(_h(("TC3" + TENCENT_SECRET_KEY).encode(), date), service), "tc3_request")
    signature = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {
        "Authorization": f"TC3-HMAC-SHA256 Credential={TENCENT_SECRET_ID}/{scope}, "
                         f"SignedHeaders=content-type;host, Signature={signature}",
        "Content-Type": "application/json; charset=utf-8", "Host": host,
        "X-TC-Action": action, "X-TC-Timestamp": str(ts), "X-TC-Version": version,
    }
    if region:
        headers["X-TC-Region"] = region
    resp = requests.post("https://" + host, data=body.encode(), headers=headers, timeout=timeout)
    data = resp.json().get("Response", {})
    if "Error" in data:
        raise RuntimeError(f"腾讯云报错 {data['Error'].get('Code')}: {data['Error'].get('Message')}")
    return data


def _tencent(pcm_bytes, timeout):
    params = {
        "EngSerViceType": TENCENT_ENGINE, "SourceType": 1, "VoiceFormat": "pcm",
        "Data": base64.b64encode(pcm_bytes).decode(), "DataLen": len(pcm_bytes),
    }
    hw = _tencent_hotwords()
    if hw:
        params["HotwordList"] = hw
    data = _tencent_call("asr", "SentenceRecognition", "2019-06-14", params, region=TENCENT_REGION, timeout=timeout)
    return (data.get("Result") or "").strip()


def tencent_balance():
    """腾讯云账户可用余额（元）。接口返回的是分。"""
    data = _tencent_call("billing", "DescribeAccountBalance", "2018-07-09", {})
    return round(data.get("Balance", 0) / 100, 2)


def transcribe_pcm(pcm_bytes, timeout=15):
    """pcm_bytes: 16k/16bit/单声道 PCM 原始字节。返回识别出的文字。两家都失败才抛 RuntimeError。"""
    seconds = len(pcm_bytes) / (SAMPLE_RATE * 2)
    try:
        text = _aliyun(pcm_bytes, timeout)
        _record("aliyun", seconds)
        return text
    except Exception as exc:
        primary_err = exc
    _record("aliyun_fail")
    _alert("asr_aliyun", f"阿里云语音识别失败，已临时改用腾讯云：{primary_err}"
                         "（欠费请到阿里云费用中心充值：https://billing-cost.console.aliyun.com/）")
    try:
        text = _tencent(pcm_bytes, timeout)
        _record("tencent", seconds)
        return text
    except Exception as exc:
        _record("all_fail")
        raise RuntimeError(f"阿里云和腾讯云识别都失败：{primary_err}；{exc}") from exc


if __name__ == "__main__":
    # 自检：python3 asr_client.py some.wav|.mp3 [aliyun|tencent]
    import subprocess
    path = sys.argv[1]
    pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", path, "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"],
                         capture_output=True, check=True).stdout
    which = sys.argv[2] if len(sys.argv) > 2 else ""
    fn = {"aliyun": _aliyun, "tencent": _tencent}.get(which, lambda p, t: transcribe_pcm(p, t))
    t0 = time.time()
    print(fn(pcm, 15), f"({time.time() - t0:.1f}s)")
