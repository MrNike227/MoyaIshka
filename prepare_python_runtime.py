#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bundle only app-local Microsoft VC runtime DLLs required by 64-bit CPython.

Windows 10 and later provide the Universal CRT as an OS component. Do not copy
ucrtbase.dll or api-ms-win-crt-* API-set forwarders from the build machine:
Windows 10/11 always uses its system UCRT, even if an app-local copy is present.
"""
from __future__ import annotations

import os
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
    """Copy one consistent x64 VC runtime set from Python or Visual Studio redist."""
    roots = _normalise_roots(search_roots)
    selected_root: Path | None = None
    selected_files: dict[str, Path] = {}

    for root in roots:
        candidates = (root, root / "DLLs")
        for candidate_root in candidates:
            current = {
                name.casefold(): candidate_root / name
                for name in REQUIRED_RUNTIME_DLLS
                if (candidate_root / name).is_file()
            }
            if len(current) == len(REQUIRED_RUNTIME_DLLS):
                selected_root = candidate_root
                selected_files = current
                break
        if selected_root is not None:
            break

    if selected_root is None:
        searched = "\n".join(f"  {root}" for root in roots) or "  (папки не найдены)"
        raise RuntimeError(
            "Не найден полный набор Microsoft VC Runtime x64: "
            + ", ".join(REQUIRED_RUNTIME_DLLS)
            + ". Проверены Python и каталоги официального Visual Studio VC Redistributable. "
            + "System32 намеренно не используется как источник файлов для упаковки.\n"
            + "Проверенные каталоги:\n" + searched
        )

    for name in OPTIONAL_RUNTIME_DLLS:
        candidate = selected_root / name
        if candidate.is_file():
            selected_files[name.casefold()] = candidate

    for dll_path in selected_files.values():
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
    for source in selected_files.values():
        output = target / source.name
        shutil.copy2(source, output)
        copied.append(output)
    return copied


def discover_visual_c_runtime_roots() -> list[Path]:
    """Find redistributable DLL folders shipped with installed Visual Studio."""
    roots: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        try:
            candidate = path.resolve()
        except OSError:
            return
        key = os.path.normcase(str(candidate))
        if candidate.is_dir() and key not in seen:
            roots.append(candidate)
            seen.add(key)

    for variable in ("VCToolsRedistDir", "VSINSTALLDIR"):
        value = os.environ.get(variable, "").strip()
        if not value:
            continue
        base = Path(value)
        if variable == "VCToolsRedistDir":
            for candidate in base.glob("x64/Microsoft.VC*.CRT"):
                add(candidate)
        else:
            for candidate in base.glob("VC/Redist/MSVC/*/x64/Microsoft.VC*.CRT"):
                add(candidate)

    program_roots = {
        Path(value)
        for value in (
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
        )
        if value
    }
    for program_root in program_roots:
        for year in ("2022", "2019", "2017"):
            visual_studio = program_root / "Microsoft Visual Studio" / year
            if not visual_studio.is_dir():
                continue
            for edition in visual_studio.iterdir():
                redist = edition / "VC" / "Redist" / "MSVC"
                if not redist.is_dir():
                    continue
                for candidate in redist.glob("*/x64/Microsoft.VC*.CRT"):
                    add(candidate)
    return roots


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    roots = [
        Path(sys.executable).resolve().parent,
        Path(sys.base_prefix),
        Path(sys.prefix),
        *discover_visual_c_runtime_roots(),
    ]
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
