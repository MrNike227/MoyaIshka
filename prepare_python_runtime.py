#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bundle and validate the x64 Microsoft VC++ / Universal CRT DLL runtime."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Iterable

IMAGE_FILE_MACHINE_AMD64 = 0x8664
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
    "ucrtbase.dll",
)
RUNTIME_DLL_GLOBS = ("api-ms-win-crt-*.dll",)
REQUIRED_RUNTIME_DLLS = {
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "msvcp140.dll",
    "ucrtbase.dll",
    "api-ms-win-crt-runtime-l1-1-0.dll",
}


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


def collect_runtime_dlls(search_roots: Iterable[str | Path], destination: str | Path) -> list[Path]:
    """Copy matching x64 VC runtime DLLs; fail on missing or invalid components."""
    roots: list[Path] = []
    for value in search_roots:
        root = Path(value).expanduser()
        if root.is_dir():
            resolved = root.resolve()
            if resolved not in roots:
                roots.append(resolved)

    # Case-insensitive keys prevent duplicate entries on Windows.
    found: dict[str, Path] = {}
    for name in RUNTIME_DLL_NAMES:
        key = name.casefold()
        for root in roots:
            candidates = (root / name, root / "DLLs" / name)
            match = next((candidate for candidate in candidates if candidate.is_file()), None)
            if match is not None:
                found[key] = match
                break

    # Include every CRT API-set forwarder available from the selected x64 runtime.
    for root in roots:
        for pattern in RUNTIME_DLL_GLOBS:
            for candidate in sorted(root.glob(pattern)):
                found.setdefault(candidate.name.casefold(), candidate)

    missing = sorted(REQUIRED_RUNTIME_DLLS.difference(found))
    if missing:
        searched = "\n".join(f"  {root}" for root in roots) or "  (папки не найдены)"
        raise RuntimeError(
            "Не найден обязательный Microsoft Visual C++ / Universal CRT runtime: "
            + ", ".join(missing)
            + ". Нужен официальный 64-bit Python 3.11 и его x64 runtime DLL. "
            + "Проверены папки:\n"
            + searched
        )

    # Do not silently package a 32-bit or malformed DLL into the x64 EXE.
    for dll_path in found.values():
        try:
            machine = read_pe_machine(dll_path)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Некорректная runtime DLL {dll_path}: {exc}") from exc
        if machine != IMAGE_FILE_MACHINE_AMD64:
            raise RuntimeError(
                f"Неверная архитектура runtime DLL {dll_path.name}: "
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
    executable_dir = Path(sys.executable).resolve().parent
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    program_files_x86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    ucrt_redist = program_files_x86 / "Windows Kits" / "10" / "Redist" / "ucrt" / "DLLs" / "x64"

    # Prefer the exact x64 runtime used by the build interpreter; use SDK / System32 as fallback.
    roots = (
        ucrt_redist,
        executable_dir,
        Path(sys.base_prefix),
        Path(sys.prefix),
        system_root / "System32",
    )
    destination = Path(__file__).resolve().parent / "build_runtime" / "python_runtime"
    try:
        copied = collect_runtime_dlls(roots, destination)
    except Exception as exc:
        print(f"ОШИБКА подготовки Python DLL runtime: {exc}", file=sys.stderr)
        return 1

    print(f"Проверены и подготовлены x64 Python runtime DLL: {len(copied)}")
    for path in copied:
        print(f"  {path.name}")
    print(f"Каталог для PyInstaller: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
