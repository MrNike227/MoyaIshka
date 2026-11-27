# -*- coding: utf-8 -*-
"""Reviewable unified-diff workflow for controlled self-modification."""
from __future__ import annotations
import ast
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

EDITABLE_SUFFIXES = {".py"}


MAX_MODEL_CONTEXT_CHARS = 90_000
MAX_SELF_IMPROVEMENT_CONTEXT_CHARS = 22_000
MAX_SINGLE_DEFINITION_CHARS = 36_000

_PROMPT_STOP_WORDS = {
    "добавь", "добавить", "сделай", "сделать", "измени", "изменить", "улучши",
    "улучшить", "исправь", "исправить", "создай", "создать", "удали", "удалить",
    "нужно", "надо", "пожалуйста", "можно", "чтобы", "который", "которая",
    "которые", "этот", "эта", "это", "для", "при", "после", "перед", "между",
    "через", "приложение", "программа", "и", "в", "на", "по", "с", "из", "к",
    "the", "and", "for", "with", "from", "that", "this", "please", "make",
    "add", "change", "update", "fix", "create", "into", "when", "then",
}


def build_model_source_context(
    source: str,
    prompt: str,
    max_chars: int = MAX_MODEL_CONTEXT_CHARS,
) -> str:
    """Bound model input for large files while preserving original line numbers."""
    if not isinstance(source, str) or not source:
        raise ValueError("Файл пустой или не содержит текст.")
    if max_chars < 2_000:
        raise ValueError("Лимит контекста слишком мал.")
    if len(source) <= max_chars:
        return source

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(
            "Большой файл содержит синтаксическую ошибку. Исправь её перед саморазвитием."
        ) from exc

    lines = source.splitlines(keepends=True)
    prompt_words = {
        word for word in re.findall(r"[^\W_]{3,}", prompt.casefold())
        if word not in _PROMPT_STOP_WORDS
    }
    terms = set(prompt_words)
    terms.update(word[:5] for word in prompt_words if len(word) >= 5)
    general_review = any(
        marker in prompt.casefold()
        for marker in ("автоматический анализ", "анализ кода", "технический аудит", "code review")
    )
    if general_review:
        # A project-wide review should not collapse to the one function whose
        # name happens to match a review keyword. Use the bounded symbol index.
        terms.clear()
    symbol_rows: list[tuple[int, str]] = []
    candidates: list[tuple[int, int, int, str, str]] = []
    ui_request = any(
        term in prompt.casefold()
        for term in (
            "кноп", "интерф", "страниц", "окно", "меню", "виджет",
            "button", "widget", "page", "screen",
        )
    )

    class_parents: dict[int, str] = {}
    nested_function_ids: set[int] = set()
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            if isinstance(parent, ast.ClassDef) and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                class_parents[id(child)] = parent.name
            elif isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)) and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nested_function_ids.add(id(child))

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = min([node.lineno] + [
            decorator.lineno for decorator in getattr(node, "decorator_list", [])
        ])
        end = getattr(node, "end_lineno", None) or node.lineno
        header = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else node.name
        owner = class_parents.get(id(node), "")
        symbol_rows.append((
            start,
            f"{owner + '.' if owner else ''}{node.name} — {header[:180]}",
        ))
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if id(node) in nested_function_ids or end - start > 1200:
            continue
        body = "".join(lines[start - 1:end])
        lowered = body.casefold()
        score = sum(1 for term in terms if term in lowered)
        if node.name.casefold() in terms:
            score += 5
        if score:
            candidates.append((score, start, end, node.name, body))

    if not candidates and ui_request:
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            start = min([node.lineno] + [
                decorator.lineno for decorator in getattr(node, "decorator_list", [])
            ])
            end = getattr(node, "end_lineno", None) or node.lineno
            if id(node) in nested_function_ids:
                continue
            if node.name.startswith("_build_") or node.name in {"show_page", "refresh_ui"}:
                body = "".join(lines[start - 1:end])
                if len(body) <= MAX_SINGLE_DEFINITION_CHARS:
                    candidates.append((1, start, end, node.name, body))

    if not candidates and any(
        marker in prompt.casefold()
        for marker in ("анализ", "аудит", "review", "саморазвит", "улучшен")
    ):
        # General reviews have no single search keyword: select bounded functions.
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if id(node) in nested_function_ids:
                continue
            start = min([node.lineno] + [
                decorator.lineno for decorator in getattr(node, "decorator_list", [])
            ])
            end = getattr(node, "end_lineno", None) or node.lineno
            body = "".join(lines[start - 1:end])
            if body and len(body) <= MAX_SINGLE_DEFINITION_CHARS:
                candidates.append((0, start, end, node.name, body))

    if not candidates:
        raise ValueError(
            "Файл большой, и я не нашёл в нём нужный раздел. "
            "Опиши, на какой странице или в какой функции нужно сделать изменение."
        )

    symbol_index = "\n".join(
        f"{line}: {name}" for line, name in sorted(symbol_rows, key=lambda row: row[0])
    )
    symbol_index_limit = min(18_000, max(1_200, max_chars // 5))
    if len(symbol_index) > symbol_index_limit:
        symbol_index = symbol_index[:symbol_index_limit - 300] + "\n… список сокращён"
    parts = [
        "Файл больше лимита полного контекста. Ниже — индекс и точные фрагменты исходника.",
        "Номера строк в заголовках диапазонов — реальные номера строк файла; не добавляй их в сам diff.",
        "Меняй только показанные фрагменты. В заголовках @@ используй исходные номера строк.",
        "\nИндекс функций и методов:\n" + symbol_index,
    ]

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            start = node.lineno
            end = getattr(node, "end_lineno", None) or start
            import_text = "".join(lines[start - 1:end])
            parts.append(
                f"\nИмпорт, исходные строки {start}-{end}:\n@@FENCE@@python\n"
                + import_text
                + ("" if import_text.endswith("\n") else "\n")
                + "@@FENCE@@"
            )

    used = sum(len(part) for part in parts)
    selected = 0
    for _score, start, end, name, body in sorted(
        candidates, key=lambda item: (-item[0], len(item[4]), item[1])
    ):
        block = (
            f"\nФрагмент {name}, исходные строки {start}-{end}:\n"
            f"@@FENCE@@python\n{body}"
            + ("" if body.endswith("\n") else "\n")
            + "@@FENCE@@"
        )
        if used + len(block) > max_chars:
            continue
        parts.append(block)
        used += len(block)
        selected += 1
    if not selected:
        raise ValueError(
            "Нужный раздел файла слишком большой для безопасной отправки. "
            "Выбери более узкий модуль или уточни, какую функцию изменить."
        )
    fence = chr(96) * 3
    return "\n".join(parts).replace("@@FENCE@@", fence)



def project_dir() -> Path:
    configured = os.environ.get("MOYA_PROJECT_DIR", "").strip()
    return Path(configured).resolve() if configured else Path(__file__).resolve().parent


def editable_project_files() -> list[str]:
    root = project_dir()
    ignored_directories = {
        "__pycache__", ".venv", "venv", "tests", ".git", ".github",
        "build", "dist", ".pytest_cache", ".mypy_cache",
    }
    result = []
    for path in root.rglob("*.py"):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part.casefold() in ignored_directories for part in relative.parts):
            continue
        rel = relative.as_posix()
        if rel == "updater_helper.py":
            continue
        result.append(rel)
    return sorted(result)


def _normal(line: str) -> str:
    return line.rstrip("\r\n")


def _normalize_diff_path(header: str) -> str:
    value = header.strip().split("\t", 1)[0].strip().strip('"')
    if value.startswith(("a/", "b/")):
        value = value[2:]
    return value.replace("\\", "/")


def extract_unified_diff(response: str, expected_path: str) -> str:
    """Extract a single-file unified diff from Markdown or explanatory output."""
    if not isinstance(response, str) or not response.strip():
        raise ValueError("Модель не вернула diff. Повтори генерацию.")
    text = response.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines(keepends=True)
    hunk_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
    start = next((i for i, line in enumerate(lines) if hunk_re.match(line)), None)
    if start is None:
        raise ValueError("Не найден блок изменений @@. Повтори генерацию.")
    header = None
    for i in range(start - 1):
        if lines[i].startswith("--- ") and i + 1 < start and lines[i + 1].startswith("+++ "):
            header = i
    expected = expected_path.replace("\\", "/")
    if header is not None:
        old_path = _normalize_diff_path(lines[header][4:])
        new_path = _normalize_diff_path(lines[header + 1][4:])
        if old_path != expected or new_path != expected:
            raise ValueError("Diff пытается изменить другой файл.")
    body = []
    for line in lines[start:]:
        if hunk_re.match(line) or line.startswith("\\ No newline at end of file"):
            body.append(line if line.endswith("\n") else line + "\n")
        elif line and line[0] in " +-":
            body.append(line if line.endswith("\n") else line + "\n")
        else:
            break
    if not any(line.startswith(("+", "-")) for line in body):
        raise ValueError("В diff нет строк добавления или удаления.")
    return "--- a/" + expected + "\n+++ b/" + expected + "\n" + "".join(body)


def _iter_json_objects(text: str):
    """Yield balanced JSON object candidates, ignoring braces inside quoted strings."""
    for start, char in enumerate(text):
        if char != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            current = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    yield text[start:index + 1]
                    break


def _repair_json_controls(candidate: str) -> str:
    """Escape literal control characters inside JSON strings and remove trailing commas."""
    output = []
    in_string = False
    escaped = False
    index = 0
    while index < len(candidate):
        char = candidate[index]
        if in_string:
            if escaped:
                output.append(char)
                escaped = False
            elif char == "\\":
                next_char = candidate[index + 1] if index + 1 < len(candidate) else ""
                valid_escape = next_char in '"\\/bfnrt'
                valid_unicode = (
                    next_char == "u"
                    and index + 5 < len(candidate)
                    and all(item in "0123456789abcdefABCDEF" for item in candidate[index + 2:index + 6])
                )
                if valid_escape or valid_unicode:
                    output.append(char)
                    escaped = True
                else:
                    # Model code examples often contain regex/path escapes such as \d or \w.
                    output.append("\\\\")
            elif char == '"':
                output.append(char)
                in_string = False
            elif char == "\n":
                output.append("\\n")
            elif char == "\r":
                output.append("\\r")
            elif char == "\t":
                output.append("\\t")
            elif ord(char) < 0x20:
                output.append(" ")
            else:
                output.append(char)
        else:
            if char == '"':
                output.append(char)
                in_string = True
            elif char == ",":
                lookahead = index + 1
                while lookahead < len(candidate) and candidate[lookahead].isspace():
                    lookahead += 1
                if not (lookahead < len(candidate) and candidate[lookahead] in "}]"):
                    output.append(char)
            else:
                output.append(char)
        index += 1
    return "".join(output)


def _python_literal_fallback(candidate: str):
    """Safely parse Python-style dict/list literals; never evaluates executable code."""
    normalized = []
    in_string = None
    escaped = False
    index = 0
    while index < len(candidate):
        char = candidate[index]
        if in_string:
            normalized.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == in_string:
                in_string = None
            index += 1
            continue
        if char in ("'", '"'):
            in_string = char
            normalized.append(char)
            index += 1
            continue
        match = re.match(r"(true|false|null)\b", candidate[index:], flags=re.IGNORECASE)
        if match:
            token = match.group(1).lower()
            normalized.append({"true": "True", "false": "False", "null": "None"}[token])
            index += len(match.group(0))
            continue
        normalized.append(char)
        index += 1
    try:
        return ast.literal_eval("".join(normalized))
    except (ValueError, SyntaxError):
        return None


def _decode_suggestion_payload(candidate: str):
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        repaired = _repair_json_controls(candidate)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            return _python_literal_fallback(repaired)


def _clean_suggestion_items(raw: list) -> list[dict[str, str]]:
    result = []
    for item in raw[:5]:
        if not isinstance(item, dict):
            continue
        clean = {
            key: str(item.get(key, "")).strip()[:1800]
            for key in ("title", "problem", "current", "proposed", "benefit")
        }
        if clean["title"] and clean["current"] and clean["proposed"]:
            result.append(clean)
    return result


def parse_improvement_suggestions(response: str) -> list[dict[str, str]]:
    """Parse model suggestions even when wrapped in prose or mildly malformed JSON."""
    if not isinstance(response, str) or not response.strip():
        raise ValueError("Модель не вернула предложения по улучшению.")

    text = response.strip().lstrip("\ufeff")
    for candidate in _iter_json_objects(text):
        payload = _decode_suggestion_payload(candidate)
        if isinstance(payload, dict) and "suggestions" in payload:
            raw = payload.get("suggestions")
            if not isinstance(raw, list):
                raise ValueError("Модель вернула поле suggestions не в виде списка.")
            return _clean_suggestion_items(raw)
        # Recover complete suggestions if the outer response was truncated or malformed.
        if isinstance(payload, dict) and all(
            key in payload for key in ("title", "current", "proposed")
        ):
            recovered = _clean_suggestion_items([payload])
            if recovered:
                return recovered

    if '"suggestions"' in text or "'suggestions'" in text:
        raise ValueError(
            "Ответ модели похож на JSON, но он неполный или повреждён. Повтори анализ."
        )
    raise ValueError(
        "Модель вернула ответ без корректного списка предложений. Повтори анализ."
    )


MAX_MULTI_FILE_PATCHES = 10


def editable_test_files() -> list[str]:
    """Return safe project test modules; tests remain separate from normal source targets."""
    root = project_dir()
    result: list[str] = []
    tests_root = root / "tests"
    if not tests_root.is_dir() or tests_root.is_symlink():
        return result
    for path in tests_root.glob("test_*.py"):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if len(relative) <= 240:
                result.append(relative)
        except (OSError, ValueError):
            continue
    return sorted(result)


def related_test_path(source_relative_path: str) -> str:
    """Choose the conventional test module paired with a source module."""
    source = PurePosixPath(str(source_relative_path).replace("\\", "/"))
    stem = source.stem
    known = {
        "main": "tests/test_core.py",
        "self_improvement": "tests/test_extensions.py",
        "improvement_delivery": "tests/test_improvement_delivery.py",
        "improvement_testing": "tests/test_improvement_testing.py",
        "windows_sandbox": "tests/test_windows_sandbox.py",
        "background_developer": "tests/test_background_developer.py",
    }
    candidate = known.get(stem, f"tests/test_{stem}.py")
    return candidate


def split_multi_file_unified_diff(patch_text: str) -> dict[str, str]:
    """Split a combined unified diff into normalized, independently applicable file patches."""
    if not isinstance(patch_text, str) or not patch_text.strip():
        raise ValueError("Модель не вернула многофайловый diff.")
    normalized = patch_text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines(keepends=True)
    header_indexes = [
        index for index in range(len(lines) - 1)
        if lines[index].startswith("--- ") and lines[index + 1].startswith("+++ ")
    ]
    if not header_indexes:
        raise ValueError("В diff не найдены пары заголовков --- / +++.")
    if len(header_indexes) > MAX_MULTI_FILE_PATCHES:
        raise ValueError(f"В одном пакете разрешено не более {MAX_MULTI_FILE_PATCHES} Python-файлов.")

    result: dict[str, str] = {}
    for position, header_index in enumerate(header_indexes):
        old_header = lines[header_index][4:].strip().split("\t", 1)[0].strip('"')
        new_header = lines[header_index + 1][4:].strip().split("\t", 1)[0].strip('"')
        old_path = _normalize_diff_path(old_header)
        new_path = _normalize_diff_path(new_header)
        if new_path == "/dev/null":
            raise ValueError("Удаление файлов в предложении саморазвития запрещено.")
        relative = PurePosixPath(new_path)
        if (
            not new_path
            or relative.is_absolute()
            or any(part in {"", ".", ".."} or ":" in part for part in relative.parts)
            or any(part.casefold() in {".git", ".github", "__pycache__", ".venv", "venv", "build", "dist"} for part in relative.parts)
            or relative.suffix.casefold() != ".py"
            or len(new_path) > 240
        ):
            raise ValueError(f"Недопустимый путь Python-файла в diff: {new_path!r}.")
        if old_path != "/dev/null" and old_path != new_path:
            raise ValueError("Переименование файлов в пакете пока не поддерживается.")
        if new_path in result:
            raise ValueError(f"Diff содержит повторное изменение файла: {new_path}.")
        body_end = header_indexes[position + 1] if position + 1 < len(header_indexes) else len(lines)
        body = lines[header_index + 2:body_end]
        body = [
            line for line in body
            if not line.startswith(("diff --git ", "index ", "similarity index ", "rename from ", "rename to "))
        ]
        if not any(line.startswith("@@ ") for line in body):
            raise ValueError(f"Для файла {new_path} отсутствует блок изменений @@.")
        result[new_path] = (
            f"--- a/{new_path}\n+++ b/{new_path}\n" + "".join(body)
        )
    return result


def apply_multi_file_unified_diff(
    sources: dict[str, str | None],
    patch_text: str,
) -> dict[str, str]:
    """Apply a multi-file diff to in-memory originals; None denotes a new file."""
    patches = split_multi_file_unified_diff(patch_text)
    if set(patches) != set(sources):
        missing = sorted(set(sources) - set(patches))
        extra = sorted(set(patches) - set(sources))
        raise ValueError(
            "Список изменённых файлов в diff не совпадает с подготовленным пакетом. "
            f"Нет в diff: {missing}; лишние: {extra}."
        )
    updated: dict[str, str] = {}
    for relative, file_patch in patches.items():
        original = sources[relative]
        updated[relative] = apply_unified_diff(original or "", file_patch, relative)
    return updated


def generate_validated_multi_file_unified_diff(
    sources: dict[str, str | None],
    messages: list[dict[str, str]],
    model_call: Callable[[list[dict[str, str]], int], str],
    max_attempts: int = 3,
) -> str:
    """Retry a combined diff until every requested source/test file applies exactly."""
    if not isinstance(sources, dict) or not 1 <= len(sources) <= MAX_MULTI_FILE_PATCHES:
        raise ValueError(f"Пакет должен содержать от 1 до {MAX_MULTI_FILE_PATCHES} файлов.")
    attempts = max(1, min(int(max_attempts), 3))
    conversation = [dict(message) for message in messages]
    last_error: ValueError | None = None
    for attempt in range(attempts):
        response = model_call(conversation, attempt)
        try:
            apply_multi_file_unified_diff(sources, response)
            return response.strip() + "\n"
        except ValueError as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            paths = ", ".join(sources)
            conversation.extend([
                {"role": "assistant", "content": str(response or "")[:16_000]},
                {
                    "role": "user",
                    "content": (
                        f"Предыдущий многофайловый diff не прошёл проверку: {exc}\n"
                        f"Сгенерируй заново один объединённый unified diff только для файлов: {paths}. "
                        "Для каждого существующего файла укажи точные пары --- a/path и +++ b/path. "
                        "Для нового тестового файла используй --- /dev/null и +++ b/path. "
                        "Каждый файл должен иметь свой блок @@; не добавляй переименований или удалений. "
                        "Контекстные и удаляемые строки копируй точно. Выведи только diff."
                    ),
                },
            ])
    detail = str(last_error) if last_error else "Модель не вернула проверяемый diff."
    raise ValueError(
        f"Не удалось получить корректный многофайловый diff после {attempts} попыток. {detail}"
    )


def apply_unified_diff(original: str, patch_text: str, expected_path: str) -> str:
    """Apply a single-file unified diff, relocating unique exact hunks safely."""
    normalized_patch = patch_text.replace("\r\n", "\n").replace("\r", "\n")
    patch_lines = normalized_patch.splitlines(keepends=True)
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
    newline = "\r\n" if "\r\n" in original else "\n"
    hunks: list[tuple[int, int, int, list[tuple[str, str]]]] = []
    i = first_hunk
    while i < len(patch_lines):
        line = patch_lines[i]
        if not line.startswith("@@ "):
            i += 1
            continue
        match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if not match:
            raise ValueError(f"Непонятный заголовок блока изменений: {line.strip()}")
        old_start = int(match.group(1))
        old_count = int(match.group(2) or 1)
        new_count = int(match.group(4) or 1)
        operations: list[tuple[str, str]] = []
        consumed_old = 0
        produced_new = 0
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
            operations.append((marker, content))
            if marker in " -":
                consumed_old += 1
            if marker in " +":
                produced_new += 1
            i += 1
        if consumed_old != old_count or produced_new != new_count:
            raise ValueError(
                "Неполный блок diff: количество строк не совпадает с заголовком @@. "
                "Повтори генерацию изменений."
            )
        hunks.append((old_start, old_count, new_count, operations))

    if not hunks:
        raise ValueError("В diff нет применимых изменений.")

    output: list[str] = []
    source_index = 0

    def patch_line_matches(source_index: int, expected_lines: list[str]) -> bool:
        if source_index < 0 or source_index + len(expected_lines) > len(source_lines):
            return False
        return all(
            _normal(source_lines[source_index + offset]) == _normal(expected)
            for offset, expected in enumerate(expected_lines)
        )

    def format_added_line(content: str) -> str:
        if content.endswith("\n") and newline == "\r\n":
            return content[:-1] + "\r\n"
        return content

    for old_start, old_count, _new_count, operations in hunks:
        old_lines = [content for marker, content in operations if marker in " -"]
        if old_count:
            expected_index = max(0, old_start - 1)
            if expected_index >= source_index and patch_line_matches(expected_index, old_lines):
                actual_index = expected_index
            else:
                matches = [
                    candidate
                    for candidate in range(source_index, len(source_lines) - len(old_lines) + 1)
                    if patch_line_matches(candidate, old_lines)
                ]
                if len(matches) == 1:
                    actual_index = matches[0]
                elif not matches:
                    expected_line = old_lines[0] if old_lines else ""
                    found_line = (
                        _normal(source_lines[expected_index])
                        if expected_index < len(source_lines)
                        else "<конец файла>"
                    )
                    raise ValueError(
                        "Diff не совпадает с текущим содержимым файла: точный блок исходных строк не найден. "
                        "Возможно, модель использовала шаблонный или устаревший текст. "
                        f"Ожидалось: {_normal(expected_line)[:100]!r}; "
                        f"в указанной позиции найдено: {found_line[:100]!r}. "
                        "Изменения не применены."
                    )
                else:
                    raise ValueError(
                        "Diff неоднозначен: одинаковый блок исходных строк встречается в файле несколько раз. "
                        "Уточни фрагмент и повтори генерацию; изменения не применены."
                    )
            output.extend(source_lines[source_index:actual_index])
            cursor = actual_index
            for marker, content in operations:
                if marker == " ":
                    output.append(source_lines[cursor])
                    cursor += 1
                elif marker == "-":
                    cursor += 1
                else:
                    output.append(format_added_line(content))
            source_index = actual_index + old_count
        else:
            # For a pure insertion, the old line number is an anchor (number of
            # source lines before insertion), not a context block to relocate.
            actual_index = old_start
            if actual_index < source_index or actual_index > len(source_lines):
                raise ValueError("Позиция вставки в diff выходит за пределы текущего файла.")
            output.extend(source_lines[source_index:actual_index])
            for marker, content in operations:
                if marker == "+":
                    output.append(format_added_line(content))
            source_index = actual_index

    output.extend(source_lines[source_index:])
    return "".join(output)



def generate_validated_unified_diff(
    original: str,
    expected_path: str,
    messages: list[dict[str, str]],
    model_call: Callable[[list[dict[str, str]], int], str],
    max_attempts: int = 3,
) -> str:
    """Ask the model for a patch, retry malformed output, and validate before returning."""
    attempts = max(1, min(int(max_attempts), 3))
    conversation = [dict(message) for message in messages]
    last_error: ValueError | None = None

    for attempt in range(attempts):
        response = model_call(conversation, attempt)
        try:
            diff = extract_unified_diff(response, expected_path)
            apply_unified_diff(original, diff, expected_path)
            return diff
        except ValueError as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            previous = str(response or "")[:12_000]
            if "блок изменений @@" in str(exc).casefold():
                correction = (
                    f"В прошлом ответе не было корректного заголовка блока изменений @@. "
                    f"Сгенерируй diff заново для файла {expected_path}. "
                    "Начни ответ строго с --- a/путь и +++ b/путь, затем обязательно добавь "
                    "хотя бы один настоящий заголовок вида @@ -12,3 +12,4 @@ и строки изменений. "
                    "Не возвращай описание, план, JSON, обычный код или строку UNABLE_TO_CREATE_PATCH. "
                    "Контекстные и удаляемые строки копируй точно из исходника. "
                    "Сделай минимальное изменение и выведи только unified diff."
                )
            else:
                correction = (
                    f"Предыдущий diff не прошёл проверку: {exc}. "
                    f"Сгенерируй заново минимальный diff только для {expected_path}. "
                    "Используй точные строки из переданного исходника, реальные номера строк и стандартные "
                    "заголовки @@ -N,N +N,N @@. Не добавляй пояснений, Markdown или полный файл."
                )
            conversation.extend([
                {"role": "assistant", "content": previous},
                {"role": "user", "content": correction},
            ])

    detail = str(last_error) if last_error else "Модель не вернула проверяемый diff."
    raise ValueError(
        f"Не удалось получить применимый diff после {attempts} попыток. "
        f"Последняя причина: {detail} Повтори генерацию или уточни нужный участок кода."
    )

def apply_patch_with_backup(relative_path: str, patch_text: str, run_tests: bool = True) -> Path:
    relative = Path(relative_path)
    relative_parts = tuple(part.casefold() for part in relative.parts)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or not relative.parts
        or any(part in {".git", ".github"} for part in relative_parts)
        or relative.suffix.lower() not in EDITABLE_SUFFIXES
    ):
        raise ValueError("Разрешено изменять только Python-файлы проекта вне служебных папок.")
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
