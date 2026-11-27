#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨平台兼容层（Linux / macOS / Windows）。

提供：
  - file_lock(path, timeout=None): POSIX 用 fcntl.flock，Windows 用线程锁 + msvcrt.locking
    （非截断打开，避免 WinError 13；默认阻塞等待，与 flock 一致；传 timeout 时统一超时语义）
  - ensure_opus_dll(): 自动定位 libopus 动态库（Windows 的 opus.dll / pyogg；macOS 的 Homebrew / MacPorts），
    让 opuslib 的 ctypes.util.find_library('opus') 直接可用，无需手动配置环境变量
  - check_local_port(host, port): 无 systemd 环境（Windows / macOS 本机运行）下的端口存活探测
  - can_systemd_restart(helper_path): 判断当前环境是否具备 systemd + sudo + 重启助手脚本
"""
from __future__ import annotations

import contextlib
import ctypes.util
import importlib.util
import os
import shutil
import socket
import sys
import threading
import time

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

if os.name == "nt":
    import msvcrt
else:
    msvcrt = None

_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_OPUS_DLL_DIRS: list[object] = []
_OPUS_FALLBACK_LIB: str | None = None
_FIND_LIBRARY_PATCHED = False


def _thread_lock_for(path: str) -> threading.Lock:
    key = os.path.abspath(path)
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[key] = lock
        return lock


@contextlib.contextmanager
def file_lock(path: str, timeout: float | None = None):
    """跨进程 + 跨线程排他文件锁。timeout=None 表示一直等待（与 POSIX flock 一致）。"""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    lock_path = path + ".lock"
    deadline = None if timeout is None else (time.monotonic() + timeout)

    if fcntl is not None:
        with open(lock_path, "a+b") as lk:
            if deadline is None:
                fcntl.flock(lk, fcntl.LOCK_EX)
            else:
                while True:
                    try:
                        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError(f"等待文件锁超时: {lock_path}")
                        time.sleep(0.01)
            try:
                yield
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)
        return

    # Windows: 先用进程内线程锁串行，再用 msvcrt.locking 做跨进程字节锁。
    # 用 a+b 打开避免截断被另一进程锁住的文件导致 PermissionError。
    tlock = _thread_lock_for(lock_path)
    if deadline is None:
        tlock.acquire()
    else:
        rem = max(0.0, deadline - time.monotonic())
        if not tlock.acquire(timeout=rem):
            raise TimeoutError(f"等待文件锁超时: {lock_path}")
    try:
        with open(lock_path, "a+b") as lk:
            lk.seek(0, os.SEEK_END)
            if lk.tell() == 0:
                try:
                    lk.write(b"0")
                    lk.flush()
                except OSError:
                    pass
            while True:
                lk.seek(0)
                try:
                    msvcrt.locking(lk.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if deadline is not None and time.monotonic() >= deadline:
                        raise TimeoutError(f"等待文件锁超时: {lock_path}")
                    time.sleep(0.01)
            try:
                yield
            finally:
                lk.seek(0)
                try:
                    msvcrt.locking(lk.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
    finally:
        tlock.release()


def _register_opus_fallback_path(dll_path: str) -> None:
    """当 Windows 仅找到别名 DLL（libopus-0.dll / libopus.dll）时，让 opuslib 的 find_library('opus') 能命中它。"""
    global _OPUS_FALLBACK_LIB, _FIND_LIBRARY_PATCHED
    _OPUS_FALLBACK_LIB = dll_path
    if _FIND_LIBRARY_PATCHED:
        return
    orig_find = ctypes.util.find_library

    def _patched_find_library(name: str):
        found = orig_find(name)
        if found is None and name == "opus" and _OPUS_FALLBACK_LIB:
            return _OPUS_FALLBACK_LIB
        return found

    ctypes.util.find_library = _patched_find_library
    _FIND_LIBRARY_PATCHED = True


def _iter_windows_opus_dirs(repo_dir: str) -> list[str]:
    dirs: list[str] = [repo_dir, os.path.join(repo_dir, "bin")]
    spec = importlib.util.find_spec("pyogg")
    if spec and spec.submodule_search_locations:
        for base in spec.submodule_search_locations:
            if not base or not os.path.isdir(base):
                continue
            dirs.append(base)
            for root, _sub, _files in os.walk(base):
                if root != base:
                    dirs.append(root)
    return dirs


def ensure_opus_dll() -> str | None:
    """Windows / macOS 下自动定位 libopus 动态库，使 opuslib 直接可用；其他平台返回 None。"""
    if os.name == "nt":
        existing = ctypes.util.find_library("opus")
        if existing:
            return existing

        repo_dir = os.path.dirname(os.path.abspath(__file__))
        candidates = _iter_windows_opus_dirs(repo_dir)

        # 优先匹配 opus.dll（ctypes.util.find_library('opus') 原生可识别），其次匹配别名 DLL
        for dll_name in ("opus.dll", "libopus-0.dll", "libopus.dll"):
            for d in candidates:
                if not d or not os.path.isdir(d):
                    continue
                dll_path = os.path.join(d, dll_name)
                if os.path.isfile(dll_path):
                    path_env = os.environ.get("PATH", "")
                    if d.lower() not in path_env.lower().split(os.pathsep):
                        os.environ["PATH"] = d + os.pathsep + path_env
                    if hasattr(os, "add_dll_directory"):
                        try:
                            _OPUS_DLL_DIRS.append(os.add_dll_directory(d))
                        except OSError:
                            pass
                    if dll_name != "opus.dll":
                        _register_opus_fallback_path(dll_path)
                    return dll_path
        return None

    if sys.platform == "darwin":
        existing = ctypes.util.find_library("opus")
        if existing:
            return existing

        for d in ("/opt/homebrew/lib", "/usr/local/lib", "/opt/local/lib"):
            lib_path = os.path.join(d, "libopus.dylib")
            if os.path.isfile(lib_path):
                cur = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
                parts = [p for p in cur.split(":") if p]
                if d not in parts:
                    # 保留 dyld 默认回退路径，避免覆盖后收窄其他动态库的查找范围
                    base = parts if parts else ["/usr/local/lib", "/usr/lib"]
                    os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join([d] + [p for p in base if p != d])
                return lib_path
        return None

    return None


def check_local_port(host: str, port: int, timeout: float = 0.5) -> bool:
    """探测本机 TCP 端口是否在监听。通配绑定地址（0.0.0.0 / ::）自动转为 127.0.0.1。"""
    if not port:
        return False
    probe_host = "127.0.0.1" if host in ("", "0.0.0.0", "::", "[::]") else host
    try:
        with socket.create_connection((probe_host, port), timeout=timeout):
            return True
    except OSError:
        return False


def can_systemd_restart(helper_path: str) -> bool:
    """判断当前系统是否支持通过 sudo + wozaine-restart 重启 systemd 服务。"""
    return (
        os.name != "nt"
        and shutil.which("systemctl") is not None
        and shutil.which("sudo") is not None
        and bool(helper_path)
        and os.path.isfile(helper_path)
    )
