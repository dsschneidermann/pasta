"""Capture HMR reload-time errors to a log file.

The hmr reloader (reactivity.hmr) only PRINTS a module's re-exec error and then
swallows it (ErrorFilter -> sys.excepthook), and errors raised inside the async
reload effect go to the event loop's exception handler - so a failed hot reload
leaves no record a tool can read. This module tees those sinks to hmr_debug.log in
the repo root, making reload failures - notably the load-ordering races a multi-file
change can trigger - diagnosable after the fact rather than only visible as console
output.

Wired in by an import at the end of server.py so it hot-loads with the server.
Import-time side effect only, and idempotent: safe to import (and re-import) repeatedly.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from datetime import datetime
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent.parent / "hmr_debug.log"


def _log(header: str, text: str) -> None:
    stamp = datetime.now().isoformat(timespec="milliseconds")
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"\n===== {stamp} {header} =====\n{text.rstrip()}\n")


def _fmt(exc_type, exc_value, tb) -> str:
    return "".join(traceback.format_exception(exc_type, exc_value, tb))


# --- sys.excepthook (what ErrorFilter.__exit__ calls) ------------------------
if not getattr(sys, "_hmr_dbg_excepthook_orig", None):
    sys._hmr_dbg_excepthook_orig = sys.excepthook  # type: ignore[attr-defined]
_excepthook_orig = sys._hmr_dbg_excepthook_orig  # type: ignore[attr-defined]


def _excepthook(exc_type, exc_value, tb):
    _log("sys.excepthook", _fmt(exc_type, exc_value, tb))
    _excepthook_orig(exc_type, exc_value, tb)


sys.excepthook = _excepthook

# --- sys.unraisablehook ------------------------------------------------------
if not getattr(sys, "_hmr_dbg_unraisable_orig", None):
    sys._hmr_dbg_unraisable_orig = sys.unraisablehook  # type: ignore[attr-defined]
_unraisable_orig = sys._hmr_dbg_unraisable_orig  # type: ignore[attr-defined]


def _unraisablehook(args):
    tail = _fmt(args.exc_type, args.exc_value, args.exc_traceback)
    _log("sys.unraisablehook", f"{args.err_msg or ''}\n{tail}")
    _unraisable_orig(args)


sys.unraisablehook = _unraisablehook

# --- asyncio loop exception handler (best-effort) ----------------------------
try:
    _loop = asyncio.get_running_loop()
except RuntimeError:
    _loop = None

if _loop is not None and not getattr(_loop, "_hmr_dbg_installed", False):
    _loop_orig = _loop.get_exception_handler()

    def _loop_handler(loop, context):
        exc = context.get("exception")
        msg = context.get("message", "")
        if exc is not None:
            _log("asyncio", f"{msg}\n" + _fmt(type(exc), exc, exc.__traceback__))
        else:
            _log("asyncio", str(context))
        if _loop_orig is not None:
            _loop_orig(loop, context)
        else:
            loop.default_exception_handler(context)

    _loop.set_exception_handler(_loop_handler)
    _loop._hmr_dbg_installed = True  # type: ignore[attr-defined]

_log("armed", f"HMR reload-error capture armed (excepthook + unraisable + asyncio); log at {LOG_PATH}")
