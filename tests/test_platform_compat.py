#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""platform_compat 离线单测（文件锁、本地端口探测、systemd 重启能力判断、Opus 动态库探测）。"""
from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import platform_compat


def test_file_lock_mutual_exclusion():
    with tempfile.TemporaryDirectory() as td:
        target = os.path.join(td, "sub", "counter.txt")
        errors = []

        def worker():
            try:
                for _ in range(25):
                    with platform_compat.file_lock(target):
                        val = int(open(target, encoding="utf-8").read()) if os.path.exists(target) else 0
                        with open(target, "w", encoding="utf-8") as f:
                            f.write(str(val + 1))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"unexpected errors: {errors}"
        assert int(open(target, encoding="utf-8").read()) == 100
        assert os.path.exists(target + ".lock")


def test_check_local_port_wildcard_normalization():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    try:
        for host in ("127.0.0.1", "0.0.0.0", "", "::", "[::]"):
            assert platform_compat.check_local_port(host, port) is True, f"failed for host={host!r}"
        assert platform_compat.check_local_port("127.0.0.1", 0) is False
    finally:
        srv.close()

    # 关闭后应返回 False
    assert platform_compat.check_local_port("127.0.0.1", port, timeout=0.2) is False


def test_can_systemd_restart():
    with tempfile.TemporaryDirectory() as td:
        missing = os.path.join(td, "no-such-helper")
        assert platform_compat.can_systemd_restart(missing) is False
        assert platform_compat.can_systemd_restart("") is False

        helper = os.path.join(td, "wozaine-restart")
        with open(helper, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n")

        orig_which = shutil.which
        orig_os_name = platform_compat.os.name
        try:
            shutil.which = lambda name: f"/usr/bin/{name}" if name in ("systemctl", "sudo") else None
            if orig_os_name != "nt":
                assert platform_compat.can_systemd_restart(helper) is True

            shutil.which = lambda name: "/usr/bin/sudo" if name == "sudo" else None
            assert platform_compat.can_systemd_restart(helper) is False

            shutil.which = lambda name: "/usr/bin/systemctl" if name == "systemctl" else None
            assert platform_compat.can_systemd_restart(helper) is False
        finally:
            shutil.which = orig_which


def test_file_lock_timeout():
    with tempfile.TemporaryDirectory() as td:
        target = os.path.join(td, "contended.txt")
        entered = threading.Event()
        release = threading.Event()

        def holder():
            with platform_compat.file_lock(target):
                entered.set()
                release.wait(5)

        t = threading.Thread(target=holder)
        t.start()
        assert entered.wait(2)
        try:
            try:
                with platform_compat.file_lock(target, timeout=0.05):
                    raise AssertionError("should have timed out")
            except TimeoutError:
                pass
        finally:
            release.set()
            t.join()


def test_ensure_opus_dll_safe():
    res = platform_compat.ensure_opus_dll()
    assert res is None or isinstance(res, str)


if __name__ == "__main__":
    test_file_lock_mutual_exclusion()
    print("PASS file_lock mutual exclusion")
    test_file_lock_timeout()
    print("PASS file_lock timeout")
    test_check_local_port_wildcard_normalization()
    print("PASS check_local_port wildcard normalization")
    test_can_systemd_restart()
    print("PASS can_systemd_restart")
    test_ensure_opus_dll_safe()
    print("PASS ensure_opus_dll safe")
    print("全部通过")
