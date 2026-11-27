#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit x64 DLL dependencies of the ISH Python runtime in a PyInstaller onedir folder."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import sys
from pathlib import Path
from typing import Any

import pefile

IMAGE_FILE_MACHINE_AMD64 = 0x8664
LOAD_WITH_ALTERED_SEARCH_PATH = 0x00000008
LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800
REQUIRED_APP_LOCAL = {"vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"}


def _imports(path: Path) -> list[str]:
    try:
        image = pefile.PE(str(path), fast_load=False)
        machine = image.FILE_HEADER.Machine
        imports: list[str] = []
        for attr in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
            for entry in getattr(image, attr, ()) or ():
                name = entry.dll.split(b"\0", 1)[0].decode("ascii", errors="replace")
                if name:
                    imports.append(name)
        image.close()
    except Exception as exc:
        raise RuntimeError(f"Не удалось прочитать PE-зависимости {path.name}: {exc}") from exc
    if machine != IMAGE_FILE_MACHINE_AMD64:
        raise RuntimeError(f"{path.name}: требуется x64 PE, найдено 0x{machine:04X}")
    return list(dict.fromkeys(imports))


def _load_system_module(name: str) -> tuple[bool, int]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    load = kernel32.LoadLibraryExW
    load.argtypes = (ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32)
    load.restype = ctypes.c_void_p
    free = kernel32.FreeLibrary
    free.argtypes = (ctypes.c_void_p,)
    free.restype = ctypes.c_int
    handle = load(name, None, LOAD_LIBRARY_SEARCH_SYSTEM32)
    if not handle:
        return False, ctypes.get_last_error()
    free(handle)
    return True, 0


def verify_dependency_tree(app_dir: str | Path) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("Проверка загрузчика DLL выполняется только на Windows.")
    root = Path(app_dir).resolve()
    exe = root / "ISH.exe"
    runtime = root / "_internal"
    python_dll = runtime / "python311.dll"
    if not exe.is_file() or not python_dll.is_file():
        raise RuntimeError("Ожидается onedir-папка с ISH.exe и _internal/python311.dll.")
    image = pefile.PE(str(exe), fast_load=True)
    machine = image.FILE_HEADER.Machine
    image.close()
    if machine != IMAGE_FILE_MACHINE_AMD64:
        raise RuntimeError("ISH.exe должен быть x64.")

    dll_map: dict[str, Path] = {}
    for path in sorted(
        runtime.rglob("*.dll"),
        key=lambda item: (len(item.relative_to(runtime).parts), str(item).casefold()),
    ):
        _imports(path)
        dll_map.setdefault(path.name.casefold(), path)
    for required in REQUIRED_APP_LOCAL | {"python311.dll"}:
        if required not in dll_map:
            raise RuntimeError(f"В _internal отсутствует обязательная DLL: {required}")

    directory_cookie = os.add_dll_directory(str(runtime))
    try:
        visited: set[str] = {"python311.dll"}
        system_checked: dict[str, tuple[bool, int]] = {}
        graph = ["[APP] python311.dll"]
        failures: list[str] = []

        def walk(name: str, chain: tuple[str, ...]) -> None:
            key = name.casefold()
            if key in visited:
                return
            visited.add(key)
            local = dll_map.get(key)
            if local:
                graph.append("[APP] " + " -> ".join(chain + (name,)))
                for dependency in _imports(local):
                    walk(dependency, chain + (name,))
                return
            if key not in system_checked:
                system_checked[key] = _load_system_module(name)
            ok, error = system_checked[key]
            if ok:
                graph.append("[OS ] " + " -> ".join(chain + (name,)))
            else:
                failures.append(
                    "Не разрешена зависимость " + " -> ".join(chain + (name,))
                    + f" (Windows error {error})"
                )

        for dependency in _imports(python_dll):
            walk(dependency, ("python311.dll",))
        if failures:
            raise RuntimeError(
                "Дерево DLL не разрешено:\n" + "\n".join("  " + item for item in failures)
            )

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        load = kernel32.LoadLibraryExW
        load.argtypes = (ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32)
        load.restype = ctypes.c_void_p
        free = kernel32.FreeLibrary
        free.argtypes = (ctypes.c_void_p,)
        free.restype = ctypes.c_int
        loaded = load(str(python_dll), None, LOAD_WITH_ALTERED_SEARCH_PATH)
        if not loaded:
            raise RuntimeError(
                f"Windows не смогла загрузить _internal/python311.dll (ошибка {ctypes.get_last_error()})."
            )
        free(loaded)
        return {
            "python311_sha256": hashlib.sha256(python_dll.read_bytes()).hexdigest(),
            "dll_count": len(dll_map),
            "dependency_count": len(graph) - 1,
            "system_build": sys.getwindowsversion().build,
            "graph": graph,
        }
    finally:
        directory_cookie.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app_dir", help="путь к папке dist/ISH")
    args = parser.parse_args()
    try:
        result = verify_dependency_tree(args.app_dir)
    except Exception as exc:
        print(f"ОШИБКА PE/DLL-аудита: {exc}", file=sys.stderr)
        return 1
    print(f"PE/DLL audit: OK (Windows build {result['system_build']})")
    print(f"python311.dll SHA-256: {result['python311_sha256']}")
    print(f"App-local DLL files: {result['dll_count']}")
    print(f"Resolved dependency edges: {result['dependency_count']}")
    for line in result["graph"]:
        print("  " + line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
