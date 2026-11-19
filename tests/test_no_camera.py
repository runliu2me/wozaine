#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开源版不含任何拍照功能：服务端、网页、固件补丁里都不许再出现拍照/上传照片的代码。

设备板子上可能带摄像头（我们用的征辰 Minicam 就有），但补丁不初始化它、不注册拍照工具、
也不处理服务器下发的拍照指令。这条测试防止以后有人顺手又加回来。
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CODE_PATTERNS = [r"snapshot", r"/ota/capture", r"request_snapshot", r"\bSNAPSHOT_", r"/api/album"]
# 固件补丁只看新增的行（上游原有代码作为上下文出现不算）
PATCH_PATTERNS = [r'"camera"', r"InitializeCamera", r"GetCamera\(\)\s*override", r"self\.camera\.", r"Esp32Camera",
                  r"SetPreviewEnabled", r"snapshot"]


def main():
    bad = []
    files = glob.glob(os.path.join(ROOT, "*.py")) + glob.glob(os.path.join(ROOT, "tools", "*.py")) + \
        glob.glob(os.path.join(ROOT, "web", "*.js")) + glob.glob(os.path.join(ROOT, "web", "*.html"))
    for f in files:
        for i, line in enumerate(open(f, encoding="utf-8"), 1):
            for p in CODE_PATTERNS:
                if re.search(p, line):
                    bad.append(f"{os.path.relpath(f, ROOT)}:{i}: {line.strip()[:80]}")
    for f in glob.glob(os.path.join(ROOT, "firmware", "*.patch")):
        for i, line in enumerate(open(f, encoding="utf-8"), 1):
            if not line.startswith("+") or line.startswith("+++"):
                continue
            for p in PATCH_PATTERNS:
                if re.search(p, line):
                    bad.append(f"{os.path.relpath(f, ROOT)}:{i}: {line.strip()[:80]}")
    for b in bad:
        print("FAIL", b)
    assert not bad, "开源版不应包含拍照功能"
    print(f"PASS 没有拍照/照片上传代码（检查了 {len(files)} 个源文件和固件补丁）")


if __name__ == "__main__":
    sys.exit(main())
