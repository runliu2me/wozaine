#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读写 .env 文件，供管理页面改配置用。不做任何加密——这个文件本身权限就是 600，
只有运行服务的用户能读，跟 API key 直接明文放在环境变量里的风险是一个量级，不重复造轮子。
"""

import os

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

# (环境变量名, 展示用标签, 是否敏感/要打码)
ADMIN_FIELDS = [
    ("DEEPSEEK_API_KEY", "DeepSeek API Key", True),
    ("DEEPSEEK_MODEL", "DeepSeek 模型", False),
    ("MINIMAX_API_KEY", "MiniMax API Key", True),
    ("MINIMAX_VOICE_ID", "当前使用的声音 ID", False),
    ("MINIMAX_TTS_MODEL", "MiniMax TTS 模型", False),
    ("DASHSCOPE_API_KEY", "阿里云百炼 API Key（语音识别，主）", True),
    ("TENCENT_SECRET_ID", "腾讯云 SecretId（语音识别，备用）", True),
    ("TENCENT_SECRET_KEY", "腾讯云 SecretKey（语音识别，备用）", True),
    ("COSYVOICE_VOICE_ID", "阿里 CosyVoice 复刻音色 ID（语音合成，备用）", False),
]
FIELD_KEYS = {f[0] for f in ADMIN_FIELDS}


def load():
    values = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                values[k] = v
    return values


def save(values):
    lines = [f"{k}={v}" for k, v in values.items()]
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(ENV_PATH, 0o600)


def mask(value):
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def describe():
    """给管理页面用：每个字段的当前状态（敏感字段只给打码预览，不回传明文）"""
    env = load()
    fields = []
    for key, label, sensitive in ADMIN_FIELDS:
        val = env.get(key, "")
        fields.append({
            "key": key,
            "label": label,
            "sensitive": sensitive,
            "configured": bool(val),
            "preview": mask(val) if sensitive else val,
        })
    return fields


def apply_updates(updates):
    """updates: {key: new_value}。空字符串表示"不改"，跳过。返回是否真的有改动"""
    env = load()
    changed = False
    for key, val in updates.items():
        if key not in FIELD_KEYS:
            continue
        if not isinstance(val, str):
            raise ValueError(f"{key} 必须是字符串")
        val = val.strip()
        # 不允许换行、等号开头之类能往 .env 里多塞一行配置的值（防止借管理页注入 ALERT_COMMAND 等）
        if "\n" in val or "\r" in val or "\x00" in val:
            raise ValueError(f"{key} 里不能有换行")
        if val:
            env[key] = val
            changed = True
    if changed:
        save(env)
    return changed
