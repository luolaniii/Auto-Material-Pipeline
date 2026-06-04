# -*- coding: utf-8 -*-
"""
崩溃诊断：打包成 --windowed exe 后没有控制台，任何未捕获异常都会静默闪退。
这个模块在程序最早期安装全套钩子，把异常/段错误写到磁盘日志，方便定位。

安装内容：
  1. 若 stdout/stderr 为 None（windowed 模式），重定向到日志文件，避免 print 抛错。
  2. faulthandler -> 捕获 C 层崩溃（段错误、栈溢出）的 Python 栈。
  3. sys.excepthook -> 主线程未捕获异常。
  4. threading.excepthook -> 子线程未捕获异常。
"""

from __future__ import annotations

import datetime
import faulthandler
import os
import sys
import threading
import traceback
from pathlib import Path

_LOG_FILE = None
_LOG_HANDLE = None


def _resolve_log_dir() -> Path:
    """优先 exe/脚本同级 logs/；不可写则回退到 %TEMP%/UEPipelineGUI。"""
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "logs")
    candidates.append(Path(__file__).resolve().parent.parent / "logs")
    candidates.append(Path(os.environ.get("TEMP", ".")) / "UEPipelineGUI" / "logs")

    for c in candidates:
        try:
            c.mkdir(parents=True, exist_ok=True)
            probe = c / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return c
        except Exception:
            continue
    return Path(".")


def log_path() -> "Path | None":
    return _LOG_FILE


def _write(msg: str) -> None:
    try:
        if _LOG_HANDLE:
            _LOG_HANDLE.write(msg)
            _LOG_HANDLE.flush()
    except Exception:
        pass


def install() -> "Path | None":
    """安装所有钩子，返回日志文件路径。"""
    global _LOG_FILE, _LOG_HANDLE

    log_dir = _resolve_log_dir()
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    _LOG_FILE = log_dir / f"session_{stamp}.log"

    try:
        _LOG_HANDLE = open(_LOG_FILE, "w", encoding="utf-8", buffering=1)
    except Exception:
        _LOG_HANDLE = None
        return None

    # 1. windowed 模式下 stdout/stderr 可能为 None，重定向避免 print 崩溃
    if sys.stdout is None:
        sys.stdout = _LOG_HANDLE
    if sys.stderr is None:
        sys.stderr = _LOG_HANDLE

    _write(f"=== UE Pipeline GUI session {stamp} ===\n")
    _write(f"frozen={getattr(sys, 'frozen', False)} exe={sys.executable}\n")
    _write(f"python={sys.version}\n\n")

    # 2. C 层崩溃（段错误/栈溢出）
    try:
        faulthandler.enable(file=_LOG_HANDLE, all_threads=True)
    except Exception:
        pass

    # 3. 主线程未捕获异常
    def _excepthook(exc_type, exc_value, exc_tb):
        _write("\n!!! UNCAUGHT EXCEPTION (main thread) !!!\n")
        _write("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
        _write("\n")

    sys.excepthook = _excepthook

    # 4. 子线程未捕获异常（Python 3.8+）
    def _thread_excepthook(args):
        _write("\n!!! UNCAUGHT EXCEPTION (thread: %s) !!!\n" % getattr(args, "thread", None))
        _write("".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
        _write("\n")

    try:
        threading.excepthook = _thread_excepthook
    except Exception:
        pass

    return _LOG_FILE
