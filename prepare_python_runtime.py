#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bundle only app-local Microsoft VC runtime DLLs required by 64-bit CPython.

Windows 10 and later provide the Universal CRT as an OS component. Do not copy
ucrtbase.dll or api-ms-win-crt-* API-set forwarders from the build machine:
Windows 10/11 always uses its system UCRT, even if an app-local copy is present.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Iterable

IMAGE_FILE_MACHINE_AMD64 = 0x8664
REQUIRED_RUNTIME_DLLS = (
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "msvcp140.dll",
)
OPTIONAL_RUNTIME_DLLS = (
    "vcruntime140_threads.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll",
    "msvcp140_codecvt_ids.dll",
    "concrt140.dll",
    "vcomp140.dll",
)
RUNTIME_DLL_NAMES = REQUIRED_RUNTIME_DLLS + OPTIONAL_RUNTIME_DLLS


def read_pe_machine(path: str | Path) -> int:
    """Return the PE machine type, rejecting files that are not valid PE images."""
    dll_path = Path(path)
    with dll_path.open("rb") as handle:
        dos_header = handle.read(64)
        if len(dos_header) < 64 or dos_header[:2] != b"MZ":
            raise ValueError(f"{dll_path.name}: отсутствует корректный DOS/PE заголовок")
        pe_offset = int.from_bytes(dos_header[0x3C:0x40], "little")
        if pe_offset < 64 or pe_offset > 64 * 1024 * 1024:
            raise ValueError(f"{dll_path.name}: некорректное смещение PE-заголовка")
        handle.seek(pe_offset)
        pe_header = handle.read(6)
    if len(pe_header) != 6 or pe_header[:4] != b"PE\0\0":
        raise ValueError(f"{dll_path.name}: файл не является корректной Windows DLL")
    return int.from_bytes(pe_header[4:6], "little")


def _normalise_roots(search_roots: Iterable[str | Path]) -> list[Path]:
    roots: list[Path] = []
    for value in search_roots:
        root = Path(value).expanduser()
        if root.is_dir():
            resolved = root.resolve()
            if resolved not in roots:
                roots.append(resolved)
    return roots


def collect_runtime_dlls(search_roots: Iterable[str | Path], destination: str | Path) -> list[Path]:
    """Copy x64 VC runtime from the Python toolchain; never bundle Windows UCRT."""
    roots = _normalise_roots(search_roots)
    found: dict[str, Path] = {}
    for name in RUNTIME_DLL_NAMES:
        match = None
        for root in roots:
            candidates = (root / name, root / "DLLs" / name)
            match = next((candidate for candidate in candidates if candidate.is_file()), None)
            if match is not None:
                break
        if match is not None:
            found[name.casefold()] = match

    missing = [name for name in REQUIRED_RUNTIME_DLLS if name.casefold() not in found]
    if missing:
        searched = "\n".join(f"  {root}" for root in roots) or "  (папки не найдены)"
        raise RuntimeError(
            "В сборке 64-bit Python не найден обязательный Microsoft VC Runtime: "
            + ", ".join(missing)
            + ". Нужен официальный 64-bit Python 3.11 вместе с VC runtime. "
            + "Системные папки Windows намеренно не используются как источник приватных VC DLL.\n"
            + "Проверены папки:\n" + searched
        )

    for dll_path in found.values():
        try:
            machine = read_pe_machine(dll_path)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Некорректная VC runtime DLL {dll_path}: {exc}") from exc
        if machine != IMAGE_FILE_MACHINE_AMD64:
            raise RuntimeError(
                f"Неверная архитектура VC runtime DLL {dll_path.name}: "
                f"PE machine=0x{machine:04X}, требуется x64 (0x8664)"
            )

    target = Path(destination).expanduser().resolve()
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for source in found.values():
        output = target / source.name
        shutil.copy2(source, output)
        copied.append(output)
    return copied


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    roots = (
        Path(sys.executable).resolve().parent,
        Path(sys.base_prefix),
        Path(sys.prefix),
    )
    destination = Path(__file__).resolve().parent / "build_runtime" / "python_runtime"
    try:
        copied = collect_runtime_dlls(roots, destination)
    except Exception as exc:
        print(f"ОШИБКА подготовки Microsoft VC runtime: {exc}", file=sys.stderr)
        return 1
    print(f"Подготовлены x64 Microsoft VC runtime DLL: {len(copied)}")
    for path in copied:
        print(f"  {path.name}")
    print("UCRT/API-set DLL не копируются: Windows 10/11 предоставляет их как системный компонент.")
    print(f"Каталог для PyInstaller: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
