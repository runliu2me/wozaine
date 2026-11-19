#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网页静态检查：CSP 不放行内联，所以页面里不能有内联脚本、内联样式、onclick 一类的内联事件；
也不能再出现拍照、相册这类功能入口（开源版不含摄像头功能）。"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")


def main():
    files = [f for f in glob.glob(os.path.join(WEB, "*")) if f.endswith((".html", ".js", ".css"))]
    assert files
    bad = []
    for f in files:
        text = open(f, encoding="utf-8").read()
        name = os.path.basename(f)
        if name.endswith((".html", ".js")):
            if re.search(r"""\sstyle\s*=\s*["']""", text):
                bad.append(f"{name}: 内联 style 属性（CSP style-src 不放行）")
            if re.search(r"""\son[a-z]+\s*=\s*["']""", text):
                bad.append(f"{name}: 内联事件属性（onclick 等）")
        if name.endswith(".html"):
            for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", text, re.S):
                if "src=" not in m.group(1) or m.group(2).strip():
                    bad.append(f"{name}: 内联 <script>")
            if re.search(r"<style\b", text):
                bad.append(f"{name}: 内联 <style>")
        if re.search(r"snapshot|/api/album|/api/device/capture|拍一张|相册", text):
            bad.append(f"{name}: 还有拍照/相册相关内容")
    for b in bad:
        print("FAIL", b)
    assert not bad
    print("PASS 网页没有内联脚本/样式/事件，也没有拍照入口（%d 个文件）" % len(files))


if __name__ == "__main__":
    sys.exit(main())
