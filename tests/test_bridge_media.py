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


def test_ensure_opuslib_ctl_argtypes_mock():
    import ctypes
    from types import SimpleNamespace

    enc_api = SimpleNamespace(EncoderPointer=object(), libopus_ctl=SimpleNamespace(argtypes=None))
    dec_api = SimpleNamespace(DecoderPointer=object(), libopus_ctl=SimpleNamespace(argtypes=None))
    bridge_media.ensure_opuslib_ctl_argtypes(enc_api=enc_api, dec_api=dec_api)
    assert enc_api.libopus_ctl.argtypes == (enc_api.EncoderPointer, ctypes.c_int)
    assert dec_api.libopus_ctl.argtypes == (dec_api.DecoderPointer, ctypes.c_int)
    # 幂等：已有签名不覆盖
    sentinel = (object(),)
    enc_api.libopus_ctl.argtypes = sentinel
    bridge_media.ensure_opuslib_ctl_argtypes(enc_api=enc_api, dec_api=dec_api)
    assert enc_api.libopus_ctl.argtypes == sentinel


def test_opus_encoder_ctl_real():
    import platform_compat

    platform_compat.ensure_opus_dll()
    try:
        import opuslib
    except Exception:
        return False
    bridge_media.ensure_opuslib_ctl_argtypes()
    for rate, bitrate in ((16000, 48000), (24000, 64000)):
        enc = opuslib.Encoder(rate, 1, opuslib.APPLICATION_VOIP)
        enc.bitrate = bitrate
        enc.complexity = 10
        assert enc.bitrate == bitrate
        assert enc.complexity == 10
    return True


if __name__ == "__main__":
    test_resample_passthrough()
    print("PASS resample passthrough")
    test_resample_changes_length()
    print("PASS resample upsample")
    test_ensure_opuslib_ctl_argtypes_mock()
    print("PASS opuslib ctl argtypes mock")
    if test_opus_encoder_ctl_real():
        print("PASS opuslib ctl real")
    else:
        print("SKIP opuslib ctl real (opuslib unavailable)")
    print("全部通过")
