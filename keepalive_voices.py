#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MiniMax 克隆音色 7 天不用会被自动清掉，这个脚本定期"碰"一下每个 voice_id 防止被删。

要保活哪些音色：.env 里的 MINIMAX_VOICE_IDS（逗号分隔），没填就用 MINIMAX_VOICE_ID。
定下最终用哪个声音之后，把用不上的从列表里删掉，不然会一直白白保活没用的音色。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import minimax_client

VOICE_IDS = [v.strip() for v in (os.environ.get("MINIMAX_VOICE_IDS") or os.environ.get("MINIMAX_VOICE_ID", "")).split(",")
             if v.strip() and not v.strip().startswith("male-") and not v.strip().startswith("female-")]  # 系统音色不用保活


def main():
    for voice_id in VOICE_IDS:
        try:
            minimax_client.synthesize_with_voice("。", voice_id)
            print(f"{voice_id}: 保活成功")
        except Exception as exc:
            print(f"{voice_id}: 保活失败 - {exc}")


if __name__ == "__main__":
    main()
