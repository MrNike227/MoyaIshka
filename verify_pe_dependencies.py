#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit the real PE dependency tree of python311.dll inside a PyInstaller EXE."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

import pefile
from PyInstaller.archive.readers import CArchiveReader

IMAGE_FILE_MACHINE_AMD64 = 0x8664
LOAD_WITH_ALTERED_SEARCH_PATH = 0x00000008
LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800
REQUIRED_APP_LOCAL = {"vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"}


def _basename(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1].casefold()


def _imports(data: bytes, display_name: str) -> list[str]:
    try:
        image = pefile.PE(data=data, fast_load=False)
        machine = image.FILE_HEADER.Machine
        imports: list[str] = []
        for attr in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
            for entry in getattr(image, attr, ()) or ():
                name = entry.dll.split(b"\0", 1)[0].decode("ascii", errors="replace")
                if name:
                    imports.append(name)
        image.close()
    except Exception as exc:
        raise RuntimeError(f"Не удалось прочитать PE-зависимости {display_name}: {exc}") from exc
    if machine != IMAGE_FILE_MACHINE_AMD64:
        raise RuntimeError(f"{display_name}: требуется x64 PE (0x8664), найдено 0x{machine:04X}")
    return list(dict.fromkeys(imports))


def _assert_exe_x64(exe_path: Path) -> None:
    try:
        image = pefile.PE(str(exe_path), fast_load=True)
        machine = image.FILE_HEADER.Machine
        image.close()
    except Exception as exc:
        raise RuntimeError(f"Не удалось прочитать PE-заголовок EXE {exe_path}: {exc}") from exc
    if machine != IMAGE_FILE_MACHINE_AMD64:
        raise RuntimeError(f"{exe_path.name}: EXE должен быть x64 (0x8664), найдено 0x{machine:04X}")


def _load_system_module(name: str) -> tuple[bool, int]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    load_library = kernel32.LoadLibraryExW
    load_library.argtypes = (ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32)
    load_library.restype = ctypes.c_void_p
    free_library = kernel32.FreeLibrary
    free_library.argtypes = (ctypes.c_void_p,)
    free_library.restype = ctypes.c_int
    handle = load_library(name, None, LOAD_LIBRARY_SEARCH_SYSTEM32)
    if not handle:
        return False, ctypes.get_last_error()
    free_library(handle)
    return True, 0


def _extract_native_dlls(exe_path: Path, destination: Path) -> tuple[dict[str, tuple[Path, bytes, str]], int]:
    archive = CArchiveReader(str(exe_path))
    root_dlls: dict[str, tuple[Path, bytes, str]] = {}
    total_dlls = 0
    duplicates: list[str] = []
    for archive_name in archive.toc:
        normalised = archive_name.replace("\\", "/")
        if PurePosixPath(normalised).suffix.casefold() != ".dll":
            continue
        total_dlls += 1
        parts = PurePosixPath(normalised).parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise RuntimeError(f"Недопустимый путь DLL в EXE-архиве: {archive_name}")
        data = archive.extract(archive_name)
        output = destination.joinpath(*parts)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
        # Bare DLL imports are resolved from _MEIPASS, not arbitrary subfolders.
        if output.parent != destination:
            continue
        key = _basename(archive_name)
        if key in root_dlls:
            _, old_data, old_archive_name = root_dlls[key]
            if hashlib.sha256(old_data).digest() != hashlib.sha256(data).digest():
                duplicates.append(f"{key}: {old_archive_name} / {archive_name}")
            continue
        root_dlls[key] = (output, data, archive_name)
    if duplicates:
        raise RuntimeError("В корне EXE обнаружены конфликтующие одноимённые DLL:\n  " + "\n  ".join(duplicates))
    return root_dlls, total_dlls


def verify_dependency_tree(exe_path: str | Path) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("Проверка загрузчика DLL выполняется только на Windows.")
    exe = Path(exe_path).resolve()
    if not exe.is_file():
        raise FileNotFoundError(exe)
    _assert_exe_x64(exe)

    with tempfile.TemporaryDirectory(prefix="moya_pe_audit_") as temp:
        temp_dir = Path(temp)
        native, total_dlls = _extract_native_dlls(exe, temp_dir)
        python_entry = native.get("python311.dll")
        if python_entry is None:
            raise RuntimeError("В корне PyInstaller-архива отсутствует python311.dll.")
        for required in sorted(REQUIRED_APP_LOCAL):
            if required not in native:
                raise RuntimeError(f"Обязательная app-local VC runtime DLL отсутствует в EXE: {required}")

        dll_directory = os.add_dll_directory(str(temp_dir))
        try:
            visited: set[str] = {"python311.dll"}
            system_checked: dict[str, tuple[bool, int]] = {}
            graph_lines = ["[APP] python311.dll"]
            failures: list[str] = []

            def walk(module_name: str, chain: tuple[str, ...]) -> None:
                key = module_name.casefold()
                if key in visited:
                    return
                visited.add(key)
                bundled = native.get(key)
                if bundled is not None:
                    _, data, archive_name = bundled
                    imports = _imports(data, archive_name)
                    graph_lines.append("[APP] " + " -> ".join(chain + (module_name,)))
                    for dependency in imports:
                        walk(dependency, chain + (module_name,))
                    return

                if key not in system_checked:
                    system_checked[key] = _load_system_module(module_name)
                ok, error_code = system_checked[key]
                if ok:
                    graph_lines.append("[OS ] " + " -> ".join(chain + (module_name,)))
                else:
                    failures.append(
                        "Не разрешена системная/API-set зависимость "
                        + " -> ".join(chain + (module_name,))
                        + f" (Windows error {error_code})"
                    )

            for dependency in _imports(python_entry[1], python_entry[2]):
                walk(dependency, ("python311.dll",))
            if failures:
                raise RuntimeError(
                    "Проверка дерева DLL python311.dll завершилась ошибкой:\n"
                    + "\n".join("  " + item for item in failures)
                    + "\n\nПроверено:\n"
                    + "\n".join("  " + item for item in graph_lines)
                )

            # Perform the actual load using PyInstaller's LOAD_WITH_ALTERED_SEARCH_PATH flag.
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            load_library = kernel32.LoadLibraryExW
            load_library.argtypes = (ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32)
            load_library.restype = ctypes.c_void_p
            free_library = kernel32.FreeLibrary
            free_library.argtypes = (ctypes.c_void_p,)
            free_library.restype = ctypes.c_int
            handle = load_library(str(python_entry[0]), None, LOAD_WITH_ALTERED_SEARCH_PATH)
            if not handle:
                error_code = ctypes.get_last_error()
                raise RuntimeError(
                    f"Windows не смогла загрузить извлечённый python311.dll "
                    f"(ошибка {error_code}). Проверь дерево импортов выше."
                )
            free_library(handle)

            return {
                "python311_sha256": hashlib.sha256(python_entry[1]).hexdigest(),
                "native_dlls": total_dlls,
                "dependencies": len(graph_lines) - 1,
                "system_build": sys.getwindowsversion().build,
                "graph": graph_lines,
            }
        finally:
            dll_directory.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exe", help="путь к собранному PyInstaller EXE")
    args = parser.parse_args()
    try:
        result = verify_dependency_tree(args.exe)
    except Exception as exc:
        print(f"ОШИБКА PE/DLL-аудита: {exc}", file=sys.stderr)
        return 1
    print(f"PE/DLL audit: OK (Windows build {result['system_build']})")
    print(f"python311.dll SHA-256: {result['python311_sha256']}")
    print(f"DLL files in EXE: {result['native_dlls']}")
    print(f"Resolved dependency edges: {result['dependencies']}")
    for line in result["graph"]:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
