#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_media 纯函数冒烟（不依赖 ffmpeg/opus 真转码）。"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bridge_media


def test_resample_passthrough():
    pcm = b"\x00\x01" * 16
    assert bridge_media.resample_pcm_16k(b"", 24000) == b""
    assert bridge_media.resample_pcm_16k(pcm, 16000) == pcm


def test_resample_changes_length():
    pcm = b"\x00\x00" * 160  # 160 samples @16k
    out = bridge_media.resample_pcm_16k(pcm, 24000)
    assert len(out) > len(pcm)
    assert len(out) % 2 == 0


if __name__ == "__main__":
    test_resample_passthrough()
    print("PASS resample passthrough")
    test_resample_changes_length()
    print("PASS resample upsample")
    print("全部通过")
