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
import textwrap
from datetime import datetime
from pathlib import Path, PurePosixPath
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
            for key in ("title", "problem", "location", "current", "proposed", "benefit")
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


def _multi_diff_candidates(response: str) -> list[str]:
    """Extract unified-diff candidates from plain text, fenced blocks, or JSON."""
    if not isinstance(response, str) or not response.strip():
        raise ValueError("Модель не вернула текст с diff.")
    text = response.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    candidates: list[str] = []

    json_snippets = [text]
    try:
        json_snippets.extend(_iter_json_objects(text))
    except Exception:
        pass
    fence = chr(96) * 3
    fence_pattern = re.compile(r"(?s)" + re.escape(fence) + r"[^\n]*\n(.*?)" + re.escape(fence))
    fenced_blocks = [textwrap.dedent(item).strip("\n") for item in fence_pattern.findall(text) if item.strip()]
    tilde_pattern = re.compile(r"(?s)~~~[^\n]*\n(.*?)~~~")
    fenced_blocks.extend(textwrap.dedent(item).strip("\n") for item in tilde_pattern.findall(text) if item.strip())
    if fenced_blocks:
        candidates.append("\n".join(fenced_blocks))
        candidates.extend(fenced_blocks)
        for block in fenced_blocks:
            try:
                json_snippets.extend(_iter_json_objects(block))
            except Exception:
                pass

    for snippet in json_snippets:
        try:
            payload = json.loads(_repair_json_controls(snippet))
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            for key in ("diff", "patch", "unified_diff", "changes", "content", "output"):
                item = payload.get(key)
                if isinstance(item, str) and item.strip():
                    candidates.append(textwrap.dedent(item).strip("\n"))

    candidates.append(textwrap.dedent(text).strip("\n"))
    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate = candidate.strip("\n")
        if candidate and candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def _extract_multi_diff_hunks(lines: list[str]) -> str:
    """Extract hunk lines and discard prose surrounding the unified diff."""
    start = next((i for i, line in enumerate(lines) if line.lstrip().startswith("@@ ")), None)
    if start is None:
        return ""
    body: list[str] = []
    for line in lines[start:]:
        value = line.rstrip("\r\n")
        if value.lstrip().startswith("@@ "):
            body.append(value + "\n")
        elif value.lstrip().startswith("\\ No newline at end of file"):
            body.append("\\ No newline at end of file\n")
        elif value and value[0] in " +-":
            body.append(value + "\n")
        else:
            break
    return "".join(body)


def _validated_multi_diff_path(old_header: str, new_header: str) -> str:
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
    return new_path


def _parse_multi_file_unified_diff(candidate: str) -> dict[str, str]:
    """Parse file sections, tolerating wrappers and common Git metadata."""
    normalized = textwrap.dedent(candidate).replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines()
    pairs: list[tuple[int, int]] = []

    for index, line in enumerate(lines):
        old_line = line.lstrip(" \t")
        if not old_line.startswith("--- "):
            continue
        old_header = old_line[4:].strip().split("\t", 1)[0].strip('"')
        if old_header != "/dev/null" and not old_header.startswith("a/"):
            continue
        for next_index in range(index + 1, min(len(lines), index + 7)):
            next_line = lines[next_index].lstrip(" \t")
            if next_line.startswith("+++ "):
                new_header = next_line[4:].strip().split("\t", 1)[0].strip('"')
                if new_header.startswith("b/"):
                    pairs.append((index, next_index))
                break
            if next_line.strip() and not next_line.strip().startswith(("index ", "Index: ", "diff --git ")):
                break

    result: dict[str, str] = {}

    def add_section(old_header: str, new_header: str, section_lines: list[str]) -> None:
        path = _validated_multi_diff_path(old_header, new_header)
        hunks = _extract_multi_diff_hunks(section_lines)
        if not hunks:
            raise ValueError(f"Для файла {path} не найден корректный блок изменений @@.")
        if path in result:
            raise ValueError(f"Diff содержит повторное изменение файла: {path}.")
        result[path] = f"--- a/{path}\n+++ b/{path}\n" + hunks

    if pairs:
        for position, (old_index, new_index) in enumerate(pairs):
            old_header = lines[old_index].lstrip(" \t")[4:].strip().split("\t", 1)[0].strip('"')
            new_header = lines[new_index].lstrip(" \t")[4:].strip().split("\t", 1)[0].strip('"')
            section_end = pairs[position + 1][0] if position + 1 < len(pairs) else len(lines)
            add_section(old_header, new_header, lines[new_index + 1:section_end])
    else:
        # Some models emit Git sections and valid hunks but omit --- / +++ lines.
        git_indexes = [i for i, line in enumerate(lines) if line.startswith("diff --git ")]
        for position, start in enumerate(git_indexes):
            match = re.fullmatch(r"diff --git a/(.+) b/(.+)", lines[start].strip())
            if not match:
                continue
            old_git_path, new_git_path = match.groups()
            end = git_indexes[position + 1] if position + 1 < len(git_indexes) else len(lines)
            section = lines[start + 1:end]
            if any(line.startswith("deleted file mode ") for line in section):
                raise ValueError("Удаление файлов в предложении саморазвития запрещено.")
            is_new_file = any(line.startswith("new file mode ") for line in section)
            old_header = "/dev/null" if is_new_file else "a/" + old_git_path
            new_header = "b/" + new_git_path
            add_section(old_header, new_header, section)

    if not result:
        if re.search(r"\bUNABLE_TO_CREATE_PATCH\b", normalized, re.IGNORECASE):
            raise ValueError(
                "Модель сообщила, что не смогла подготовить изменения. Уточни задачу или выбери более узкий модуль."
            )
        preview = " ".join(normalized.split())[:220]
        suffix = f" Фрагмент ответа модели: {preview!r}." if preview else ""
        raise ValueError(
            "В ответе модели не найден unified diff: отсутствуют пары заголовков --- / +++. "
            "Ожидаются строки --- a/файл.py и +++ b/файл.py перед блоком @@." + suffix
        )
    return result


def split_multi_file_unified_diff(patch_text: str) -> dict[str, str]:
    """Split a combined diff after extracting common model response wrappers."""
    errors: list[str] = []
    for candidate in _multi_diff_candidates(patch_text):
        try:
            result = _parse_multi_file_unified_diff(candidate)
            if len(result) > MAX_MULTI_FILE_PATCHES:
                raise ValueError(
                    f"В одном пакете разрешено не более {MAX_MULTI_FILE_PATCHES} Python-файлов."
                )
            return result
        except ValueError as exc:
            errors.append(str(exc))
    raise ValueError(errors[-1] if errors else "Не удалось извлечь unified diff из ответа модели.")


def build_multi_file_repair_context(
    sources: dict[str, str | None],
    patch_text: str = "",
    *,
    max_chars: int = 12_000,
    head_lines: int = 20,
    context_radius: int = 8,
) -> str:
    """Build bounded, line-numbered source excerpts for a trustworthy diff retry."""
    if not isinstance(sources, dict) or not sources:
        return "Нет исходников для повторной генерации diff."
    if max_chars < 1_000:
        raise ValueError("Лимит контекста исправления слишком мал.")

    patches: dict[str, str] = {}
    if patch_text:
        try:
            patches = split_multi_file_unified_diff(patch_text)
        except ValueError:
            # Even a malformed patch must not prevent us from showing the real
            # file heads during recovery.
            patches = {}

    sections: list[str] = [
        "НИЖЕ — ТОЧНЫЕ ФРАГМЕНТЫ ТЕКУЩИХ ФАЙЛОВ, ИЗ КОТОРЫХ ВАЛИДАТОР ПРИМЕНЯЕТ DIFF.",
        "Нумерация строк начинается с 1. Не переноси строки из одного файла в другой.",
        "Используй только реально показанные строки; не добавляй шаблонный shebang или импорты, которых нет в конкретном файле.",
    ]
    per_file_limit = max(900, (max_chars - sum(len(part) for part in sections)) // len(sources))

    for relative, original in sources.items():
        label = f"\nФАЙЛ: {relative}\n"
        if original is None:
            block = label + "Файл ещё не существует. Для него можно только добавить новый тестовый код.\n"
            sections.append(block[:per_file_limit])
            continue

        lines = original.splitlines()
        if not lines:
            sections.append(label + "Файл существует, но пуст.\n")
            continue

        windows: list[tuple[int, int]] = [(1, min(len(lines), max(1, head_lines)))]
        file_patch = patches.get(relative, "")
        for match in re.finditer(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@", file_patch, re.MULTILINE):
            anchor = int(match.group(1))
            windows.append((
                max(1, anchor - context_radius),
                min(len(lines), anchor + context_radius),
            ))

        merged: list[list[int]] = []
        for start, end in sorted(windows):
            if merged and start <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])

        body: list[str] = []
        for start, end in merged:
            body.extend(
                f"{number:>5} | {lines[number - 1]}"
                for number in range(start, end + 1)
            )
            if end < len(lines):
                body.append("      | …")
        block = label + "\n".join(body) + "\n"
        if len(block) > per_file_limit:
            block = block[: max(0, per_file_limit - 32)] + "\n… фрагмент сокращён\n"
        sections.append(block)

    result = "\n".join(sections)
    if len(result) > max_chars:
        result = result[: max_chars - 32] + "\n… контекст сокращён"
    return result


def apply_multi_file_unified_diff(
    sources: dict[str, str | None],
    patch_text: str,
) -> dict[str, str]:
    """Apply a multi-file diff to exact in-memory originals; reject no-op file sections."""
    patches = split_multi_file_unified_diff(patch_text)
    extra = sorted(set(patches) - set(sources))
    if extra:
        raise ValueError(
            "Diff содержит файлы, для которых не был подготовлен исходный контекст: "
            f"{extra}."
        )
    if not patches:
        raise ValueError("Diff не содержит ни одного файла для изменения.")
    # Sources may also include read-only context modules. Only paths explicitly
    # present in the diff are returned; callers can require particular files.
    updated: dict[str, str] = {}
    for relative, file_patch in patches.items():
        original = sources[relative]
        try:
            result = apply_unified_diff(original or "", file_patch, relative)
        except ValueError as exc:
            raise ValueError(f"Файл {relative}: {exc}") from exc
        if original is not None and result == original:
            raise ValueError(
                f"Файл {relative}: diff не изменяет содержимое файла. "
                "Вместо фиктивного или шаблонного блока создай реальное изменение, "
                "используя точные строки именно этого файла."
            )
        updated[relative] = result
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
    last_response = ""
    scoped_retry_used = False
    attempts_made = 0
    for attempt in range(attempts):
        attempts_made = attempt + 1
        response = model_call(conversation, attempt)
        last_response = str(response or "")

        # A refusal is not a malformed diff. Give the model one scoped recovery
        # prompt, instead of treating the sentinel as patch syntax and repeating
        # the same broad instruction.
        if re.search(r"\bUNABLE_TO_CREATE_PATCH\b", last_response, re.IGNORECASE):
            note = re.sub(r".*?\bUNABLE_TO_CREATE_PATCH\b", "", last_response, count=1, flags=re.IGNORECASE)
            note = " ".join(note.split())[:400]
            last_error = ValueError(
                "Модель не смогла выбрать безопасное изменение в текущем объёме задачи."
                + (f" Комментарий модели: {note}" if note else "")
            )
            if scoped_retry_used or attempt + 1 >= attempts:
                break
            scoped_retry_used = True
            paths = ", ".join(sources)
            repair_context = build_multi_file_repair_context(
                sources,
                max_chars=16_000,
                head_lines=32,
                context_radius=12,
            )
            scoped_retry = (
                f"Предыдущий ответ был UNABLE_TO_CREATE_PATCH. Это не команда остановиться: "
                "уменьши объём задачи и попробуй выполнить минимальное полезное изменение.\n"
                f"Работай только с этими файлами: {paths}. Измени один узкий участок исходного модуля "
                "и добавь/обнови один соответствующий тест. Не делай общий рефакторинг и не пытайся "
                "реализовать все возможные улучшения сразу.\n"
                "Не выдумывай функции и исходные строки. Используй приведённые ниже реальные фрагменты "
                "и исходный контекст из первого сообщения. Если часть задачи не покрыта показанным кодом, "
                "реализуй только ту безопасную часть, для которой кода достаточно.\n"
                "Верни полноценный unified diff для каждого указанного файла. Не возвращай "
                "UNABLE_TO_CREATE_PATCH только потому, что исходная просьба слишком широкая. "
                "Используй этот маркер повторно лишь тогда, когда из переданного кода нельзя определить "
                "ни одного безопасного и проверяемого изменения; в этом случае после маркера назови "
                "конкретный отсутствующий файл, класс или функцию.\n"
                f"{repair_context}"
            )
            conversation.extend([
                {"role": "assistant", "content": last_response[:16_000]},
                {"role": "user", "content": scoped_retry},
            ])
            continue

        try:
            updated = apply_multi_file_unified_diff(sources, last_response)
            sections = split_multi_file_unified_diff(last_response)
            for path, original in sources.items():
                if original is None:
                    sections[path] = sections[path].replace(
                        f"--- a/{path}\n", "--- /dev/null\n", 1
                    )
            return "\n".join(sections.values()).rstrip() + "\n"
        except ValueError as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            paths = ", ".join(sources)
            new_files = [path for path, original in sources.items() if original is None]
            example_path = next(iter(sources))
            example = (
                f"--- a/{example_path}\n+++ b/{example_path}\n"
                "@@ -1,1 +1,1 @@\n-СТАРАЯ_СТРОКА\n+НОВАЯ_СТРОКА"
            )
            repair_context = build_multi_file_repair_context(
                sources,
                last_response,
                max_chars=12_000,
                head_lines=24,
                context_radius=10,
            )
            retry_instruction = (
                f"Предыдущий ответ не прошёл проверку: {exc}\n"
                f"Создай ОДИН обычный unified diff для всех и только этих файлов: {paths}.\n"
                "Для каждого файла обязательно выведи отдельную пару заголовков --- и +++ непосредственно "
                "перед блоком @@. Не заменяй diff полным текстом файлов.\n"
                "КРИТИЧЕСКИ ВАЖНО: используй нижеприведённые актуальные фрагменты как единственный источник "
                "исходных строк. У каждого файла своё содержимое. Не копируй shebang, импорты или другие строки "
                "из main.py в tests/test_*.py. Не повторяй фиктивные hunks вида -строка/+та же строка: "
                "каждый файл должен действительно измениться.\n"
                "Если предыдущий hunk не совпал, выбери реальный участок из соответствующего файла ниже и "
                "перестрой diff целиком. Не пытайся исправить только номер строки, сохраняя выдуманный текст.\n"
                "Для существующего файла используй --- a/путь.py и +++ b/путь.py. "
                + (
                    "Для нового теста используй --- /dev/null и +++ b/путь_теста.py. "
                    if new_files else ""
                )
                + "Не используй apply_patch, JSON, Markdown-таблицы или обычный текст. "
                "Не добавляй пояснений, только diff. Не переименовывай и не удаляй файлы.\n"
                f"Точный пример пары заголовков и hunk:\n{example}\n\n"
                f"{repair_context}"
            )
            conversation.extend([
                {"role": "assistant", "content": last_response[:16_000]},
                {"role": "user", "content": retry_instruction},
            ])
    detail = str(last_error) if last_error else "Модель не вернула проверяемый diff."
    preview = last_response.strip()[:700]
    suffix = f"\nПоследний ответ модели (фрагмент, переносы строк сохранены): {preview!r}." if preview else ""
    if last_response and re.search(r"\bUNABLE_TO_CREATE_PATCH\b", last_response, re.IGNORECASE):
        raise ValueError(
            f"Модель не смогла подготовить безопасное изменение после {attempts_made} попыток. "
            f"{detail} Уточни задачу до одного небольшого изменения в указанном модуле "
            "или выбери файл, где находится нужная функция." + suffix
        )
    raise ValueError(
        f"Не удалось получить корректный многофайловый diff после {attempts_made} попыток. {detail}{suffix}"
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
