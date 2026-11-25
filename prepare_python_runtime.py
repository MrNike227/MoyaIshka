#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Copy the Microsoft Visual C++ runtime beside the frozen Python DLL.

PyInstaller --onefile extracts these files into the same temporary directory
as python311.dll. This makes its non-system runtime dependencies available to
LoadLibrary even on Windows machines without the VC++ Redistributable installed.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Iterable

RUNTIME_DLL_NAMES = (
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "vcruntime140_threads.dll",
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll",
    "msvcp140_codecvt_ids.dll",
    "concrt140.dll",
    "vcomp140.dll",
)
REQUIRED_RUNTIME_DLLS = {"vcruntime140.dll"}


def collect_runtime_dlls(search_roots: Iterable[str | Path], destination: str | Path) -> list[Path]:
    """Copy available app-local VC runtime DLLs; fail if the core runtime is absent."""
    roots: list[Path] = []
    for value in search_roots:
        root = Path(value).expanduser()
        if root.is_dir():
            resolved = root.resolve()
            if resolved not in roots:
                roots.append(resolved)

    found: dict[str, Path] = {}
    for name in RUNTIME_DLL_NAMES:
        for root in roots:
            candidate = root / name
            if candidate.is_file():
                found[name] = candidate
                break
            dlls_candidate = root / "DLLs" / name
            if dlls_candidate.is_file():
                found[name] = dlls_candidate
                break

    missing = sorted(REQUIRED_RUNTIME_DLLS.difference(found))
    if missing:
        searched = "\n".join(f"  {root}" for root in roots) or "  (папки не найдены)"
        raise RuntimeError(
            "Не найден обязательный Microsoft Visual C++ Runtime: "
            + ", ".join(missing)
            + ". Установи/используй официальный 64-bit Python 3.11 с runtime DLL. "
            + "Проверены папки:\n"
            + searched
        )

    target = Path(destination).expanduser().resolve()
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    for name, source in found.items():
        output = target / name
        shutil.copy2(source, output)
        copied.append(output)
    return copied


def main() -> int:
    # Windows runners may use cp1252 for Python stdout even after chcp 65001.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    executable_dir = Path(sys.executable).resolve().parent
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    # Prefer the current system VC runtime over the older app-local copy that
    # may ship with a Python installation; this avoids runtime version conflicts.
    roots = (system_root / "System32", Path(sys.base_prefix), Path(sys.prefix), executable_dir)
    destination = Path(__file__).resolve().parent / "build_runtime" / "python_runtime"
    try:
        copied = collect_runtime_dlls(roots, destination)
    except Exception as exc:
        print(f"ОШИБКА подготовки Python DLL runtime: {exc}", file=sys.stderr)
        return 1

    print(f"Python runtime DLL подготовлены: {len(copied)}")
    for path in copied:
        print(f"  {path.name}")
    print(f"Каталог для PyInstaller: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
