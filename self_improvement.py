# -*- coding: utf-8 -*-
"""Reviewable unified-diff workflow for controlled self-modification."""
from __future__ import annotations
import re
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

EDITABLE_SUFFIXES = {".py"}


def project_dir() -> Path:
    configured = os.environ.get("MOYA_PROJECT_DIR", "").strip()
    return Path(configured).resolve() if configured else Path(__file__).resolve().parent


def editable_project_files() -> list[str]:
    root = project_dir()
    result = []
    for path in root.rglob("*.py"):
        if any(part in {"__pycache__", ".venv", "venv", "tests"} for part in path.parts):
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if rel in {"updater_helper.py"}:
            continue
        result.append(rel)
    return sorted(result)


def _normal(line: str) -> str:
    return line.rstrip("\r\n")


def apply_unified_diff(original: str, patch_text: str, expected_path: str) -> str:
    """Apply a single-file unified diff, checking every context/deletion line."""
    patch_lines = patch_text.splitlines(keepends=True)
    headers = []
    first_hunk = None
    for i, line in enumerate(patch_lines):
        if line.startswith("--- ") or line.startswith("+++ "):
            headers.append(line[4:].strip().split("\t", 1)[0])
        if line.startswith("@@ "):
            first_hunk = i
            break
    if len(headers) < 2 or first_hunk is None:
        raise ValueError("Не найден заголовок единого diff или блок изменений @@.")
    allowed = {f"a/{expected_path}", f"b/{expected_path}", expected_path}
    if headers[0] not in allowed or headers[1] not in allowed:
        raise ValueError("Diff пытается изменить другой файл. Разрешён только выбранный файл.")

    source_lines = original.splitlines(keepends=True)
    output: list[str] = []
    source_index = 0
    i = first_hunk
    hunk_count = 0
    while i < len(patch_lines):
        line = patch_lines[i]
        if not line.startswith("@@ "):
            i += 1
            continue
        match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if not match:
            raise ValueError(f"Непонятный заголовок блока изменений: {line.strip()}")
        old_start = int(match.group(1))
        target_index = max(0, old_start - 1)
        if target_index < source_index:
            raise ValueError("Блоки diff идут не по порядку.")
        output.extend(source_lines[source_index:target_index])
        source_index = target_index
        i += 1
        while i < len(patch_lines) and not patch_lines[i].startswith("@@ "):
            item = patch_lines[i]
            if item.startswith("\\ No newline at end of file"):
                i += 1
                continue
            if item.startswith(("--- ", "+++ ", "diff ", "index ")):
                raise ValueError("Разрешён diff только для одного файла.")
            if not item or item[0] not in " +-":
                raise ValueError(f"Некорректная строка в diff: {item[:80]!r}")
            marker, content = item[0], item[1:]
            if marker in " -":
                if source_index >= len(source_lines):
                    raise ValueError("Diff не совпадает с текущим файлом (ожидалась строка, но файл закончился).")
                if _normal(source_lines[source_index]) != _normal(content):
                    raise ValueError(
                        "Diff не совпадает с текущим содержимым файла. "
                        f"Ожидалось: {_normal(content)[:100]!r}; найдено: {_normal(source_lines[source_index])[:100]!r}"
                    )
                if marker == " ":
                    output.append(source_lines[source_index])
                source_index += 1
            elif marker == "+":
                output.append(content)
            i += 1
        hunk_count += 1
    if hunk_count == 0:
        raise ValueError("В diff нет применимых изменений.")
    output.extend(source_lines[source_index:])
    return "".join(output)


def apply_patch_with_backup(relative_path: str, patch_text: str, run_tests: bool = True) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() not in EDITABLE_SUFFIXES:
        raise ValueError("Разрешено изменять только Python-файлы проекта.")
    root = project_dir()
    target = (root / relative).resolve()
    if root.resolve() not in target.parents or not target.is_file():
        raise ValueError("Выбранный файл отсутствует или находится вне папки проекта.")
    original = target.read_text(encoding="utf-8")
    updated = apply_unified_diff(original, patch_text, relative.as_posix())
    compile(updated, str(target), "exec")  # Syntax check before writing.

    appdata = os.environ.get("APPDATA")
    backup_root = (Path(appdata) / "MoyaIshka" if appdata else Path.home() / ".moya_ishka") / "self_improve_backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup = backup_root / f"{stamp}_{relative.name}.bak"
    shutil.copy2(target, backup)
    temporary = target.with_suffix(target.suffix + ".new")
    temporary.write_text(updated, encoding="utf-8", newline="")
    temporary.replace(target)

    if run_tests:
        tests_dir = root / "tests"
        if tests_dir.exists():
            if getattr(sys, "frozen", False):
                if shutil.which("py"):
                    test_command = ["py", "-3", "-m", "unittest", "discover", "-s", "tests", "-v"]
                elif shutil.which("python"):
                    test_command = ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]
                else:
                    test_command = None
            else:
                test_command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
            if test_command is not None:
                try:
                    completed = subprocess.run(
                        test_command, cwd=str(root), capture_output=True, text=True, timeout=120,
                    )
                except Exception as exc:
                    shutil.copy2(backup, target)
                    raise RuntimeError(f"Не удалось завершить автотесты; исходная версия восстановлена: {exc}") from exc
                if completed.returncode != 0:
                    shutil.copy2(backup, target)
                    detail = (completed.stdout + "\n" + completed.stderr)[-5000:]
                    raise RuntimeError("Автотесты не прошли; исходная версия файла восстановлена.\n" + detail)
    return backup
