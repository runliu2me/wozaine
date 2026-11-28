#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桥接服务的场景测试：起一个独立的"预演"桥接（另一组端口、临时数据目录），用模拟设备把
正常对话、打断、掉线、识别/模型/合成出故障、鉴权、OTA 等场景全跑一遍，
不碰生产。会真实调用语音识别/DeepSeek/MiniMax（几分钱）；需要 data/audio 里至少有一段真实录音。

    venv/bin/python3 tests/run_scenarios.py            # 全部
    venv/bin/python3 tests/run_scenarios.py normal auth # 只跑指定场景
"""
import asyncio
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from device_sim import DeviceSim, mp3_to_pcm16k, SR  # noqa: E402

OTA = 4096; WS = 4097; IC = 4098
TMP = os.path.join(tempfile.gettempdir(), "xz-scenarios")
DEV = "aa:bb:cc:dd:ee:01"
TOKEN = "test-device-token"
CTL = "test-ctl-token"
BRIDGE = None
RESULTS = []


def env_for(faults=""):
    env = {}
    env_file = os.path.join(ROOT, ".env")
    if os.path.exists(env_file):
        for line in open(env_file, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k] = v
    env.update(os.environ)
    env.update({
        "XIAOZHI_OTA_HOST": "127.0.0.1", "XIAOZHI_OTA_PORT": str(OTA),
        "XIAOZHI_WS_HOST": "127.0.0.1", "XIAOZHI_WS_PORT": str(WS),
        "XIAOZHI_INTERCOM_HOST": "127.0.0.1", "XIAOZHI_INTERCOM_PORT": str(IC),
        "XIAOZHI_PUBLIC_WS_URL": f"ws://127.0.0.1:{WS}/xiaozhi/ws",
        "COMPANION_DATA_DIR": os.path.join(TMP, "data"),
        "XIAOZHI_DEVICE_TOKEN": TOKEN, "XIAOZHI_REQUIRE_DEVICE_TOKEN": "1",
        "XIAOZHI_ALLOWED_DEVICE_IDS": DEV, "XIAOZHI_CTL_TOKEN": CTL,
        "XIAOZHI_TURN_DEADLINE": "15", "XIAOZHI_IDLE_SECONDS": "8",
        "XIAOZHI_TEST_FAULTS": faults,
    })
    return env


def start_bridge(faults=""):
    global BRIDGE
    stop_bridge()
    log = open(os.path.join(TMP, "bridge.log"), "ab")
    BRIDGE = subprocess.Popen([sys.executable, "-u", os.path.join(ROOT, "xiaozhi_bridge.py")],
                              env=env_for(faults), stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{OTA}/xiaozhi/ota/files/nope", timeout=1)
        except urllib.error.HTTPError:
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("bridge did not start")


def stop_bridge():
    global BRIDGE
    if BRIDGE and BRIDGE.poll() is None:
        BRIDGE.terminate()
        try:
            BRIDGE.wait(5)
        except subprocess.TimeoutExpired:
            BRIDGE.kill()
    BRIDGE = None


def bridge_alive():
    return BRIDGE is not None and BRIDGE.poll() is None


def http(path, data=None, headers=None, method=None, timeout=10):
    req = urllib.request.Request(f"http://127.0.0.1:{OTA}{path}", data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (("  -- " + detail) if detail else ""), flush=True)


def user_pcm():
    files = sorted(glob.glob(os.path.join(ROOT, "data/audio/*/*_user.mp3")))
    assert files, "need a real user recording under data/audio"
    return mp3_to_pcm16k(files[-1])


async def new_dev(token=TOKEN, dev=DEV):
    d = DeviceSim(f"ws://127.0.0.1:{WS}", token, dev)
    await d.connect()
    return d


# ---------------- 场景 ----------------
async def sc_normal():
    """拍一下 → 问候 → 说一句 → 识别+回复 → 回到聆听 → 拍一下结束"""
    d = await new_dev()
    t0 = time.time()
    await d.listen_start("auto")
    m = await d.wait_json(lambda m: m.get("type") == "tts" and m.get("state") == "sentence_start", 15)
    record("normal.greeting", bool(m) and m["text"].startswith("妈，我在呢"), str(m and m.get("text")))
    stop = await d.wait_json(lambda m: m.get("type") == "tts" and m.get("state") == "stop", 20)
    record("normal.greeting_audio", bool(stop) and len(d.frames_after(t0)) > 10, f"frames={len(d.frames_after(t0))}")
    # 帧节奏：不能一口气全发（设备缓冲只有 20 帧）
    fr = d.frames_after(t0)
    span = fr[-1][0] - fr[0][0] if len(fr) > 1 else 0
    record("normal.frame_pacing", span >= len(fr) * 0.06 * 0.7, f"{len(fr)} frames over {span:.1f}s")
    pcm = user_pcm()
    t1 = time.time()
    await d.stream_pcm(pcm)
    await d.stream_silence(1.8)
    stt = await d.wait_json(lambda m: m.get("type") == "stt", 20)
    record("normal.stt", bool(stt) and len(stt.get("text", "")) > 0, str(stt and stt.get("text")))
    reply = await d.wait_json(lambda m: m.get("type") == "tts" and m.get("state") == "sentence_start", 40)
    record("normal.reply", bool(reply), str(reply and reply.get("text")))
    stop = await d.wait_json(lambda m: m.get("type") == "tts" and m.get("state") == "stop", 60)
    record("normal.reply_done", bool(stop), f"turn took {time.time()-t1:.1f}s")
    # 结束
    await d.listen_stop()
    await d.drain(1)
    st = json.loads(http("/xiaozhi/ota/status", headers={"X-Ctl-Token": CTL})[1])
    s = st["devices"][0]
    record("normal.stop_resets_state", s["state"] == "listening" and s["conversation_active"] is False, f"state={s['state']} active={s['conversation_active']}")
    await d.close()


async def sc_tap_stop_during_reply():
    """回复放到一半拍一下：1 秒内不能再有音频帧"""
    d = await new_dev()
    await d.listen_start("auto")
    await d.wait_json(lambda m: m.get("type") == "tts" and m.get("state") == "sentence_start", 15)
    await d.drain(0.6)
    await d.listen_stop()
    t = time.time() + 1.0
    await d.drain(3)
    late = [f for f in d.frames if f[0] > t]
    record("tapstop.no_audio_after_stop", len(late) == 0, f"{len(late)} late frames")
    st = json.loads(http("/xiaozhi/ota/status", headers={"X-Ctl-Token": CTL})[1])["devices"][0]
    record("tapstop.state", st["state"] == "listening" and not st["conversation_active"], st["state"])
    await d.close()


async def sc_idle_end():
    """对话中一直没人说话：IDLE 秒后服务器发 conversation/end"""
    d = await new_dev()
    await d.listen_start("auto")
    await d.wait_json(lambda m: m.get("type") == "tts" and m.get("state") == "stop", 25)
    t0 = time.time()
    silence = asyncio.create_task(d.stream_silence(14))
    m = await d.wait_json(lambda m: m.get("type") == "conversation" and m.get("state") == "end", 20)
    silence.cancel()
    record("idle.end_sent", bool(m) and 4 <= (m["_t"] - t0) <= 16, f"after {m and round(m['_t']-t0,1)}s")
    await d.close()


async def sc_proactive():
    d = await new_dev()
    await d.send_json({"type": "companion", "event": "proactive"})
    await d.listen_start("auto")
    m = await d.wait_json(lambda m: m.get("type") == "tts" and m.get("state") == "sentence_start", 15)
    record("proactive.greeting", bool(m) and m["text"].startswith("妈，你在吗"), str(m and m.get("text")))
    await d.listen_stop(); await d.close()


async def sc_disconnect_mid_reply():
    """回复中途设备断线：桥接不能挂、会话要清掉、下一台能正常连"""
    d = await new_dev()
    await d.listen_start("auto")
    await d.wait_json(lambda m: m.get("type") == "tts" and m.get("state") == "sentence_start", 15)
    try:
        d.ws.transport.abort()  # 粗暴断开（像拔电/断网）
    except Exception:
        await d.ws.close(1001)
    await asyncio.sleep(2)
    st = json.loads(http("/xiaozhi/ota/status", headers={"X-Ctl-Token": CTL})[1])
    record("disconnect.session_removed", len(st["devices"]) == 0, f"{len(st['devices'])} sessions")
    d2 = await new_dev()
    await d2.listen_start("auto")
    m = await d2.wait_json(lambda m: m.get("type") == "tts" and m.get("state") == "sentence_start", 15)
    record("disconnect.reconnect_ok", bool(m))
    await d2.listen_stop(); await d2.close()
    record("disconnect.bridge_alive", bridge_alive())


async def sc_duplicate_device():
    d1 = await new_dev()
    d2 = await new_dev()
    await asyncio.sleep(0.5)
    st = json.loads(http("/xiaozhi/ota/status", headers={"X-Ctl-Token": CTL})[1])
    record("dup.single_registry_entry", len(st["devices"]) == 1, f"{len(st['devices'])}")
    await d2.listen_start("auto")
    m = await d2.wait_json(lambda m: m.get("type") == "tts" and m.get("state") == "sentence_start", 15)
    record("dup.newest_session_works", bool(m))
    await d2.listen_stop(); await d1.close(); await d2.close()


async def sc_auth():
    for name, tok, dev, code in [("auth.no_token", "", DEV, 4001), ("auth.bad_token", "wrong", DEV, 4001), ("auth.unknown_device", TOKEN, "00:11:22:33:44:55", 4003)]:
        d = DeviceSim(f"ws://127.0.0.1:{WS}", tok, dev)
        try:
            await d.connect(send_hello=False)
            await asyncio.sleep(1)
            ok = d.closed is not None and d.closed.code == code
            record(name, ok, f"closed={d.closed and d.closed.code}")
        except Exception as e:
            record(name, False, repr(e))
        await d.close()


async def sc_ota_endpoint():
    body = json.dumps({"board": {"type": "zhengchen-cam"}, "application": {"version": "2.0.0", "reset_reason": "panic", "last_action": "boot", "uptime_before_reset": 0}}).encode()
    code, out = http("/xiaozhi/ota/", body, {"Content-Type": "application/json", "Device-Id": DEV}, "POST")
    r = json.loads(out)
    record("ota.known_device_gets_token", code == 200 and r["websocket"]["token"] == TOKEN)
    code, out = http("/xiaozhi/ota/", body, {"Content-Type": "application/json", "Device-Id": "00:11:22:33:44:55"}, "POST")
    record("ota.stranger_no_token", code == 200 and json.loads(out)["websocket"]["token"] == "")
    code, out = http("/xiaozhi/ota/", b"{not json", {"Content-Type": "application/json"}, "POST")
    record("ota.malformed_body_no_crash", code in (200, 400) and bridge_alive(), f"code={code}")
    code, out = http("/xiaozhi/ota/", b"{}", {"Content-Type": "application/json"}, "POST")
    record("ota.empty_body_ok", code == 200)
    st = json.loads(http("/xiaozhi/ota/status", headers={"X-Ctl-Token": CTL})[1])
    b = st["boots"].get(DEV, [])
    record("ota.crash_recorded", any(x["reason"] == "panic" for x in b), f"{len(b)} boots")
    record("ota.status_needs_token", http("/xiaozhi/ota/status")[0] == 403)
    record("ota.files_no_traversal", http("/xiaozhi/ota/files/../../.env")[0] in (404, 400))


async def sc_fault(kind):
    """asr/llm/tts 出故障：妈妈要听到兜底那句话，状态要在 deadline 内复位"""
    d = await new_dev()
    await d.listen_start("auto")
    await d.wait_json(lambda m: m.get("type") == "tts" and m.get("state") == "stop", 25)
    t0 = time.time()
    await d.stream_pcm(user_pcm())
    await d.stream_silence(1.8)
    fb = await d.wait_json(lambda m: m.get("type") == "tts" and m.get("state") == "sentence_start" and "网络" in m.get("text", ""), 40)
    record(f"fault.{kind}.fallback_spoken", bool(fb), str(fb and fb.get("text")))
    stop = await d.wait_json(lambda m: m.get("type") == "tts" and m.get("state") == "stop", 40)
    record(f"fault.{kind}.turn_ends", bool(stop), f"{time.time()-t0:.1f}s")
    await asyncio.sleep(1)
    st = json.loads(http("/xiaozhi/ota/status", headers={"X-Ctl-Token": CTL})[1])["devices"][0]
    record(f"fault.{kind}.state_recovered", st["state"] in ("user_speaking", "listening"), st["state"])
    await d.listen_stop(); await d.close()


async def sc_asr_hang():
    """识别卡死：deadline(15s) 内强制复位，之后还能正常对话"""
    d = await new_dev()
    await d.listen_start("auto")
    await d.wait_json(lambda m: m.get("type") == "tts" and m.get("state") == "stop", 25)
    t0 = time.time()
    await d.stream_pcm(user_pcm()); await d.stream_silence(1.8)
    stop = await d.wait_json(lambda m: m.get("type") == "tts" and m.get("state") == "stop", 30)
    record("fault.asr_hang.reset_within_deadline", bool(stop) and (stop["_t"] - t0) < 25, f"{stop and round(stop['_t']-t0,1)}s")
    st = json.loads(http("/xiaozhi/ota/status", headers={"X-Ctl-Token": CTL})[1])["devices"][0]
    record("fault.asr_hang.state_recovered", st["state"] in ("user_speaking", "listening"), st["state"])
    await d.listen_stop(); await d.close()


async def sc_edge():
    d = await new_dev()
    # hello 之前的音频不能引起异常
    for _ in range(20):
        await d.listen_start("auto"); await d.listen_stop()
    await asyncio.sleep(0.5)
    record("edge.rapid_toggle_alive", bridge_alive())
    # 超长说话：30s 上限自动断句
    await d.listen_start("auto")
    await d.wait_json(lambda m: m.get("type") == "tts" and m.get("state") == "stop", 25)
    pcm = user_pcm()
    long = (pcm * 12)[: SR * 2 * 36]
    t0 = time.time()
    task = asyncio.create_task(d.stream_pcm(long))
    stt = await d.wait_json(lambda m: m.get("type") == "stt", 45)
    task.cancel()
    record("edge.max_utterance_cap", bool(stt) and (stt["_t"] - t0) < 40, f"{stt and round(stt['_t']-t0,1)}s")
    await d.listen_stop(); await d.close()
    # keepalive
    d = await new_dev()
    m = await d.wait_json(lambda m: m.get("type") == "ping", 65)
    record("edge.keepalive_ping", bool(m))
    await d.close()
    # 二进制帧在 hello 前
    d = DeviceSim(f"ws://127.0.0.1:{WS}", TOKEN, DEV)
    await d.connect(send_hello=False)
    await d.ws.send(b"\x00" * 100)
    await asyncio.sleep(0.5)
    record("edge.binary_before_hello", bridge_alive() and d.closed is None)
    await d.close()


async def sc_smoke():
    """不花钱的代码级自检：把每一轮对话真正会跑到的纯函数都调一遍（story / 提示词拼装 / 日程），
    任何 NameError/AttributeError 都在这里炸，不要等老人说话时才炸。"""
    import importlib
    os.environ["COMPANION_DATA_DIR"] = os.path.join(TMP, "data")
    for m in ("story", "live_context", "deepseek_client", "memory_store", "xiaozhi_bridge", "server", "daily_report", "phone_brain"):
        try:
            importlib.import_module(m)
            record(f"smoke.import.{m}", True)
        except Exception as e:
            record(f"smoke.import.{m}", False, repr(e)[:160])
    try:
        import story, deepseek_client, live_context
        q = story.pick_question()
        ok = bool(q) and len(story.prompt_block(q, proactive=True, turn=1)) > 100 and len(story.prompt_block(q, proactive=False, turn=2)) > 100
        record("smoke.story.prompt_block", ok)
        rb = story.retell_block({"id": "x", "retell": ["a", "b", "c"], "chapter": "小时候", "question": "q"}, turn=2)
        record("smoke.story.retell_block", "第 2 轮" in rb)
        record("smoke.story.known_block", isinstance(story.known_block(), str))
        sp = deepseek_client.build_system_prompt("人设", [{"text": "妈妈喜欢吃芒果"}])
        record("smoke.build_system_prompt", "此时此刻" in sp and "妈妈喜欢吃芒果" in sp)
        record("smoke.schedule_block", isinstance(live_context.schedule_block(), str))
        record("smoke.story.status", isinstance(story.status().get("questions"), list))
    except Exception as e:
        record("smoke.EXCEPTION", False, repr(e)[:200])


SCENARIOS = {
    "smoke": ("", sc_smoke),
    "auth": ("", sc_auth), "ota": ("", sc_ota_endpoint),
    "normal": ("", sc_normal), "tapstop": ("", sc_tap_stop_during_reply), "idle": ("", sc_idle_end),
    "proactive": ("", sc_proactive), "disconnect": ("", sc_disconnect_mid_reply), "dup": ("", sc_duplicate_device),
    "edge": ("", sc_edge),
    "fault_asr": ("asr=error", lambda: sc_fault("asr_error")), "fault_llm": ("llm=error", lambda: sc_fault("llm_error")),
    "fault_tts": ("tts=error", lambda: sc_fault("tts_error")), "fault_asr_hang": ("asr=hang", sc_asr_hang),
}


def main():
    names = sys.argv[1:] or list(SCENARIOS)
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(os.path.join(TMP, "data"), exist_ok=True)
    if os.path.isdir(os.path.join(ROOT, "data", "fallback")):
        shutil.copytree(os.path.join(ROOT, "data", "fallback"), os.path.join(TMP, "data", "fallback"), dirs_exist_ok=True)
    if os.path.isfile(os.path.join(ROOT, "data", "facts.json")):
        shutil.copy(os.path.join(ROOT, "data", "facts.json"), os.path.join(TMP, "data", "facts.json"))
    current_faults = None
    try:
        for n in names:
            faults, fn = SCENARIOS[n]
            if faults != current_faults or not bridge_alive():
                start_bridge(faults); current_faults = faults
                time.sleep(1.5)
            print(f"=== {n} (faults={faults or '-'})", flush=True)
            try:
                asyncio.run(asyncio.wait_for(fn(), 180))
            except Exception as e:
                record(n + ".EXCEPTION", False, repr(e)[:200])
            if not bridge_alive():
                record(n + ".bridge_crashed", False, "bridge process died")
    finally:
        stop_bridge()
    print("\n==== SUMMARY ====")
    bad = [r for r in RESULTS if not r[1]]
    print(f"{len(RESULTS) - len(bad)} passed, {len(bad)} failed")
    for r in bad:
        print("  FAIL", r[0], r[2])
    print(f"bridge log: {TMP}/bridge.log")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
