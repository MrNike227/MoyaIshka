# -*- coding: utf-8 -*-
"""Replaces a running packaged executable after the main process exits."""
from __future__ import annotations
import os
import shutil
import sys
import time
from pathlib import Path


def apply_update(staged_path: str, target_path: str, process_id: str) -> int:
    staged = Path(staged_path).resolve()
    target = Path(target_path).resolve()
    if not staged.is_file() or staged.suffix.lower() != ".exe":
        return 2
    if target.suffix.lower() != ".exe" or target == staged:
        return 3
    try:
        pid = int(process_id)
    except ValueError:
        return 4
    # Poll for the GUI process to exit. The helper is a second instance of the same app.
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        SYNCHRONIZE = 0x00100000
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            kernel32.WaitForSingleObject(handle, 120000)
            kernel32.CloseHandle(handle)
    else:
        return 5
    time.sleep(1.0)
    backup = target.with_name(target.stem + ".previous.exe")
    try:
        if target.exists():
            shutil.copy2(target, backup)
        os.replace(staged, target)
        os.startfile(str(target))  # type: ignore[attr-defined]
        return 0
    except Exception:
        if backup.exists():
            try:
                shutil.copy2(backup, target)
            except Exception:
                pass
        return 6


if __name__ == "__main__":
    raise SystemExit(apply_update(*sys.argv[1:4]))
