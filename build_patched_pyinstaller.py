#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a PyInstaller 6.22.3 bootloader that preloads bundled VC runtime DLLs.

The stock one-file bootloader preloads ucrtbase.dll, but not the remaining
app-local VC runtime DLLs. This custom build preloads them from the extracted
_MEIPASS directory before LoadLibraryExW(python311.dll) is called.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PYINSTALLER_VERSION = "6.22.3"
PYINSTALLER_TAG = f"v{PYINSTALLER_VERSION}"
PYINSTALLER_REPOSITORY = "https://github.com/pyinstaller/pyinstaller.git"
BOOTLOADER_SOURCE_RELATIVE = Path("bootloader") / "src" / "pyi_dylib_python.c"
BOOTLOADER_ARCH = "Windows-64bit-intel"
PATCH_MARKER = "MOYA_PRELOAD_VC_RUNTIME_BEFORE_PYTHON_4_1_8_3"
SOURCE_ANCHOR = """    /* Look for python shared library relative to the top-level application directory.
     * Assuming that python shared library name contains only ASCII characters,
     * we can use it directly in the wide-char string formatting function,
     * without having to explicitly convert it from UTF8 to wide-char. */""";

PRELOAD_C_BLOCK = r'''
    /* MOYA_PRELOAD_VC_RUNTIME_BEFORE_PYTHON_4_1_8_3
     *
     * Explicitly load app-local Microsoft VC runtime DLLs before python311.dll.
     * Loading python311.dll directly can otherwise fail with ERROR_MOD_NOT_FOUND
     * on systems where the transitive VC runtime dependency has not been loaded.
     * ucrtbase.dll is preloaded by the upstream block above; load VCRUNTIME first,
     * followed by MSVCP and its optional companion libraries.
     */
    {
        static const wchar_t *runtime_dll_names[] = {
            L"vcruntime140.dll",
            L"vcruntime140_1.dll",
            L"vcruntime140_threads.dll",
            L"msvcp140.dll",
            L"msvcp140_1.dll",
            L"msvcp140_2.dll",
            L"msvcp140_atomic_wait.dll",
            L"msvcp140_codecvt_ids.dll",
            L"concrt140.dll",
            L"vcomp140.dll",
        };
        size_t runtime_dll_index;

        for (runtime_dll_index = 0;
             runtime_dll_index < sizeof(runtime_dll_names) / sizeof(runtime_dll_names[0]);
             runtime_dll_index++) {
            struct _stat runtime_stat;
            DWORD load_error;

            if (swprintf(dll_fullpath, PYI_PATH_MAX, L"%ls\\%ls", root_directory,
                         runtime_dll_names[runtime_dll_index]) >= PYI_PATH_MAX) {
                PYI_ERROR_W(L"Path of bundled VC runtime DLL exceeds buffer size (%d).\n", PYI_PATH_MAX);
                return -1;
            }

            /* Optional runtime DLLs are loaded only when present in the bundle. */
            if (_wstat(dll_fullpath, &runtime_stat) != 0) {
                continue;
            }

            PYI_DEBUG_W(L"DYLIB: attempting to pre-load bundled VC runtime DLL: %ls\n", dll_fullpath);
            if (LoadLibraryExW(dll_fullpath, NULL, LOAD_WITH_ALTERED_SEARCH_PATH) == NULL) {
                load_error = GetLastError();
                PYI_DEBUG_W(
                    L"DYLIB: could not pre-load bundled VC runtime DLL %ls (Windows error %lu).\n",
                    dll_fullpath, (unsigned long)load_error
                );
                (void)load_error;
            } else {
                PYI_DEBUG_W(L"DYLIB: pre-loaded bundled VC runtime DLL: %ls\n", dll_fullpath);
            }
        }
    }

'''
BOOTLOADER_FILES = ("run.exe", "runw.exe", "run_d.exe", "runw_d.exe")
MARKER_FILE = "moya_vc_runtime_preload_4.1.8.3.json"


def patch_bootloader_source(source_path: str | Path) -> bool:
    """Insert the app-local VC runtime preload block before Python DLL loading.

    Returns True when the source was changed; returns False when already patched.
    """
    path = Path(source_path)
    source = path.read_text(encoding="utf-8")
    if PATCH_MARKER in source:
        return False
    if source.count(SOURCE_ANCHOR) != 1:
        raise RuntimeError(
            "Не удалось безопасно найти точку загрузки Python DLL в исходниках "
            f"PyInstaller {PYINSTALLER_VERSION}: {path}"
        )
    patched = source.replace(SOURCE_ANCHOR, PRELOAD_C_BLOCK + SOURCE_ANCHOR, 1)
    path.write_text(patched, encoding="utf-8", newline="\n")
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def installed_bootloader_dir() -> Path:
    try:
        version = importlib.metadata.version("pyinstaller")
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError("PyInstaller не установлен. Сначала установи requirements.txt.") from exc
    if version != PYINSTALLER_VERSION:
        raise RuntimeError(f"Требуется PyInstaller {PYINSTALLER_VERSION}, найден {version}.")
    import PyInstaller
    return Path(PyInstaller.__file__).resolve().parent / "bootloader" / BOOTLOADER_ARCH


def _marker_path(destination: Path) -> Path:
    return destination / MARKER_FILE


def verify_installed_bootloader() -> bool:
    """Check that the installed windowed and console bootloaders match the marker."""
    destination = installed_bootloader_dir()
    marker_path = _marker_path(destination)
    if not marker_path.is_file():
        return False
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        hashes = marker.get("sha256", {})
        return all(
            (destination / name).is_file()
            and hashes.get(name) == _sha256(destination / name)
            for name in BOOTLOADER_FILES
        ) and marker.get("version") == PYINSTALLER_VERSION
    except (OSError, ValueError, TypeError):
        return False


def build_patched_bootloader() -> None:
    """Clone the pinned PyInstaller source, patch it, compile and install bootloaders."""
    if sys.platform != "win32":
        raise RuntimeError("Сборка кастомного PyInstaller bootloader выполняется на Windows.")
    if verify_installed_bootloader():
        print("Кастомный PyInstaller bootloader уже установлен и проверен.")
        return

    # Keep the Waf/MSVC source tree under a short path. Long paths in the
    # runner's default %LOCALAPPDATA%\\Temp can be transformed into relative
    # 8.3 paths by MSVC, and one source file may then fail with C1083.
    temp_parent = Path(
        os.environ.get("RUNNER_TEMP")
        or os.environ.get("TEMP")
        or tempfile.gettempdir()
    )
    temp_parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix="mpyi_", dir=str(temp_parent)))
    source_root = temp_root / "pyinstaller"
    try:
        print(f"Загрузка исходников PyInstaller {PYINSTALLER_TAG}...")
        subprocess.run(
            [
                "git", "clone", "--depth", "1", "--single-branch",
                "--branch", PYINSTALLER_TAG, PYINSTALLER_REPOSITORY, str(source_root),
            ],
            check=True,
        )

        source_file = source_root / BOOTLOADER_SOURCE_RELATIVE
        if not source_file.is_file():
            raise RuntimeError(f"Не найден исходный файл bootloader: {source_file}")
        patch_bootloader_source(source_file)

        print("Компиляция Windows x64 bootloader с предварительной загрузкой VC runtime...")
        build_env = dict(__import__("os").environ)
        build_env["PYTHONIOENCODING"] = "utf-8:backslashreplace"
        build_env["PYTHONUTF8"] = "1"
        subprocess.run(
            [sys.executable, "waf", "all"],
            cwd=source_root / "bootloader",
            check=True,
            env=build_env,
        )

        build_output = source_root / "PyInstaller" / "bootloader" / BOOTLOADER_ARCH
        destination = installed_bootloader_dir()
        destination.mkdir(parents=True, exist_ok=True)

        missing = [name for name in BOOTLOADER_FILES if not (build_output / name).is_file()]
        if missing:
            raise RuntimeError(
                "Waf не создал все варианты Windows bootloader: " + ", ".join(missing)
            )

        for name in BOOTLOADER_FILES:
            shutil.copy2(build_output / name, destination / name)

        marker = {
            "version": PYINSTALLER_VERSION,
            "patch": PATCH_MARKER,
            "source_tag": PYINSTALLER_TAG,
            "sha256": {name: _sha256(destination / name) for name in BOOTLOADER_FILES},
        }
        _marker_path(destination).write_text(
            json.dumps(marker, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if not verify_installed_bootloader():
            raise RuntimeError("Проверка установленного кастомного PyInstaller bootloader не пройдена.")
        print("Кастомный PyInstaller bootloader с предварительной загрузкой VC runtime установлен.")
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def configure_console_encoding() -> None:
    """Keep Russian diagnostics printable on Windows runners using cp1252."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


def main() -> int:
    configure_console_encoding()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify-installed",
        action="store_true",
        help="проверить уже собранный bootloader, не выполняя компиляцию",
    )
    args = parser.parse_args()
    if args.verify_installed:
        if not verify_installed_bootloader():
            print("ОШИБКА: кастомный PyInstaller bootloader не установлен или повреждён.", file=sys.stderr)
            return 1
        print("Кастомный PyInstaller bootloader проверен.")
        return 0
    try:
        build_patched_bootloader()
    except Exception as exc:
        print(f"ОШИБКА подготовки PyInstaller bootloader: {exc}", file=sys.stderr)
        print(
            "Проверь Git и компоненты Visual C++ Build Tools для Visual Studio 2017 или новее.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
