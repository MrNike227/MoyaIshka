# -*- coding: utf-8 -*-
"""Static Python import graph for estimating the impact of self-improvement proposals."""
from __future__ import annotations

import ast
import os
from collections import defaultdict, deque
from pathlib import Path, PurePosixPath
from typing import Any

MAX_ANALYZED_FILES = 1200
MAX_SOURCE_BYTES = 1_000_000
SKIP_DIRECTORIES = {
    ".git", ".github", ".venv", "venv", "__pycache__", "build", "dist",
    ".pytest_cache", ".mypy_cache", "node_modules", "site-packages",
}


def _module_name(relative_path: str) -> str:
    parts = list(PurePosixPath(relative_path).parts)
    if not parts:
        return ""
    if parts[-1] == "__init__.py":
        parts.pop()
    else:
        parts[-1] = PurePosixPath(parts[-1]).stem
    return ".".join(parts)


def _safe_python_files(root: Path, warnings: list[str]) -> list[tuple[str, Path]]:
    result: list[tuple[str, Path]] = []
    root = root.resolve()
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        directories[:] = sorted(
            name for name in directories
            if name.casefold() not in SKIP_DIRECTORIES
            and not (current_path / name).is_symlink()
        )
        for filename in sorted(filenames):
            if not filename.casefold().endswith(".py"):
                continue
            candidate = current_path / filename
            try:
                if candidate.is_symlink():
                    continue
                resolved = candidate.resolve()
                if root not in resolved.parents or not resolved.is_file():
                    continue
                if resolved.stat().st_size > MAX_SOURCE_BYTES:
                    warnings.append(
                        f"{resolved.relative_to(root).as_posix()}: пропущен файл больше 1 МБ."
                    )
                    continue
                relative = resolved.relative_to(root).as_posix()
                result.append((relative, resolved))
                if len(result) >= MAX_ANALYZED_FILES:
                    warnings.append(
                        f"Достигнут предел анализа: {MAX_ANALYZED_FILES} Python-файлов."
                    )
                    return result
            except OSError:
                continue
    return result


def _resolve_import_candidates(
    current_module: str,
    relative_path: str,
    module: str | None,
    level: int,
    names: list[str],
    module_to_path: dict[str, str],
) -> set[str]:
    if level:
        is_package_init = PurePosixPath(relative_path).name == "__init__.py"
        package_parts = current_module.split(".") if is_package_init else current_module.split(".")[:-1]
        trim = max(0, int(level) - 1)
        if trim > len(package_parts):
            return set()
        base_parts = package_parts[:len(package_parts) - trim]
        if module:
            base_parts.extend(part for part in module.split(".") if part)
        base_module = ".".join(base_parts)
    else:
        base_module = str(module or "").strip(".")

    candidates: set[str] = set()
    if base_module in module_to_path:
        candidates.add(base_module)
    for name in names:
        if name == "*":
            continue
        full_name = ".".join(part for part in (base_module, name) if part)
        if full_name in module_to_path:
            candidates.add(full_name)
    return candidates


def _static_dynamic_import(
    node: ast.Call,
    current_module: str,
    relative_path: str,
    module_to_path: dict[str, str],
) -> tuple[set[str], bool]:
    function = node.func
    is_dynamic_import = (
        isinstance(function, ast.Name) and function.id == "__import__"
    ) or (
        isinstance(function, ast.Attribute) and function.attr == "import_module"
    )
    if not is_dynamic_import:
        return set(), False
    if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
        return set(), True
    raw_name = node.args[0].value
    if raw_name.startswith("."):
        package_value = None
        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
            package_value = node.args[1].value if isinstance(node.args[1].value, str) else None
        package = str(package_value or (current_module.rpartition(".")[0]))
        parts = package.split(".") if package else []
        level = len(raw_name) - len(raw_name.lstrip("."))
        if level > len(parts) + 1:
            return set(), False
        base = parts[:len(parts) - max(0, level - 1)]
        raw_name = ".".join(base + [part for part in raw_name.lstrip(".").split(".") if part])
    return ({raw_name} if raw_name in module_to_path else set()), False


def analyze_module_dependencies(
    project_root: str | Path,
    target_relative_path: str,
) -> dict[str, Any]:
    """Build a bounded AST import graph and list modules/tests that may be affected."""
    root = Path(project_root).expanduser().resolve()
    relative = str(target_relative_path).replace("\\", "/").strip()
    pure = PurePosixPath(relative)
    if (
        not relative
        or pure.is_absolute()
        or any(part in {"", ".", ".."} or ":" in part for part in pure.parts)
        or pure.suffix.casefold() != ".py"
    ):
        raise ValueError("Для анализа связей укажи относительный путь к Python-модулю.")
    target = root.joinpath(*pure.parts)
    if target.is_symlink() or not target.is_file() or root not in target.resolve().parents:
        raise ValueError("Выбранный модуль отсутствует или находится вне проекта.")
    target_resolved = target.resolve()
    relative = target_resolved.relative_to(root).as_posix()
    warnings: list[str] = []
    files = _safe_python_files(root, warnings)

    path_to_module: dict[str, str] = {}
    module_to_path: dict[str, str] = {}
    source_by_path: dict[str, str] = {}
    tree_by_path: dict[str, ast.Module] = {}
    for path, full_path in files:
        module_name = _module_name(path)
        if not module_name:
            continue
        path_to_module[path] = module_name
        # The first stable match wins if a project has both a module and a
        # package initializer with the same import name.
        module_to_path.setdefault(module_name, path)
        try:
            source = full_path.read_text(encoding="utf-8")
            tree_by_path[path] = ast.parse(source, filename=path)
            source_by_path[path] = source
        except (OSError, UnicodeError, SyntaxError) as exc:
            warnings.append(f"{path}: не удалось разобрать статические импорты ({type(exc).__name__}).")

    if relative not in path_to_module:
        raise ValueError("Выбранный модуль не удалось включить в карту Python-импортов.")
    target_module = path_to_module[relative]

    edges: dict[str, set[str]] = {path: set() for path in tree_by_path}
    imports_by_path: dict[str, set[str]] = {path: set() for path in tree_by_path}
    imported_names_by_path: dict[str, set[str]] = defaultdict(set)
    for path, tree in tree_by_path.items():
        current_module = path_to_module[path]
        for node in ast.walk(tree):
            candidates: set[str] = set()
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in module_to_path:
                        candidates.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                names = [alias.name for alias in node.names]
                candidates.update(_resolve_import_candidates(
                    current_module, path, node.module, node.level, names, module_to_path,
                ))
                if node.module:
                    imported_names_by_path[path].update(
                        alias.name for alias in node.names if alias.name != "*"
                    )
            elif isinstance(node, ast.Call):
                dynamic_candidates, unresolved = _static_dynamic_import(
                    node, current_module, path, module_to_path,
                )
                candidates.update(dynamic_candidates)
                function = node.func
                is_dynamic_import = (
                    isinstance(function, ast.Name) and function.id == "__import__"
                ) or (
                    isinstance(function, ast.Attribute) and function.attr == "import_module"
                )
                if unresolved:
                    warnings.append(
                        f"{path}:{getattr(node, 'lineno', 1)}: динамический импорт нельзя определить статически."
                    )
                elif is_dynamic_import and not dynamic_candidates:
                    warnings.append(
                        f"{path}:{getattr(node, 'lineno', 1)}: импорт может выходить за пределы карты проекта."
                    )
            for imported_module in candidates:
                imported_path = module_to_path.get(imported_module)
                if imported_path and imported_path != path:
                    edges[path].add(imported_path)
                    imports_by_path[path].add(imported_module)

    reverse: dict[str, set[str]] = defaultdict(set)
    for source_path, destinations in edges.items():
        for destination in destinations:
            reverse[destination].add(source_path)

    def walk(start: str, graph: dict[str, set[str]]) -> list[str]:
        found: set[str] = set()
        pending = deque(sorted(graph.get(start, set())))
        while pending:
            item = pending.popleft()
            if item == start or item in found:
                continue
            found.add(item)
            pending.extend(sorted(graph.get(item, set()) - found))
        return sorted(found)

    direct_dependencies = sorted(edges.get(relative, set()))
    transitive_dependencies = walk(relative, edges)
    direct_dependents = sorted(reverse.get(relative, set()))
    transitive_dependents = walk(relative, reverse)
    affected_modules = sorted({relative, *direct_dependents, *transitive_dependents})
    tests = []
    for path in sorted(tree_by_path):
        parts = PurePosixPath(path).parts
        if not parts or parts[0].casefold() != "tests" or not PurePosixPath(path).name.startswith("test_"):
            continue
        if edges.get(path, set()).intersection(affected_modules):
            tests.append(path)

    def module_rows(paths: list[str]) -> list[dict[str, str]]:
        return [
            {"path": path, "module": path_to_module.get(path, "")}
            for path in paths
        ]

    imported_names = {
        path: sorted(names) for path, names in imported_names_by_path.items() if names
    }
    return {
        "target": relative,
        "target_module": target_module,
        "scanned_files": len(tree_by_path),
        "direct_dependencies": module_rows(direct_dependencies),
        "transitive_dependencies": module_rows(transitive_dependencies),
        "direct_dependents": module_rows(direct_dependents),
        "transitive_dependents": module_rows(transitive_dependents),
        "affected_modules": module_rows(affected_modules),
        "affected_tests": tests,
        "imported_names_by_path": imported_names,
        "warnings": sorted(set(warnings))[:30],
        "analysis_kind": "static_ast_import_graph",
    }


def format_dependency_report(analysis: dict[str, Any]) -> str:
    """Format the dependency graph as a compact Russian report for users and models."""
    def paths(key: str, limit: int = 12) -> str:
        items = [str(item.get("path", "")) for item in analysis.get(key, []) if isinstance(item, dict)]
        if len(items) > limit:
            return ", ".join(items[:limit]) + f" и ещё {len(items) - limit}"
        return ", ".join(items) if items else "не обнаружены"

    tests = [str(item) for item in analysis.get("affected_tests", [])]
    tests_text = ", ".join(tests[:12]) if tests else "не обнаружены"
    warnings = [str(item) for item in analysis.get("warnings", []) if item]
    report = [
        f"Целевой модуль: {analysis.get('target', '')}",
        f"Непосредственные импорты: {paths('direct_dependencies')}",
        f"Модули, которые напрямую используют его: {paths('direct_dependents')}",
        f"Косвенно затрагиваемые модули: {paths('transitive_dependents')}",
        f"Вероятно затронутые тесты: {tests_text}",
        f"Проанализировано Python-модулей: {int(analysis.get('scanned_files', 0) or 0)}",
        "Метод: статический разбор AST импортов; динамические связи могут быть не видны.",
    ]
    if warnings:
        report.append("Ограничения/предупреждения: " + " | ".join(warnings[:6]))
    return "\n".join(report)


def build_dependency_context(
    project_root: str | Path,
    analysis: dict[str, Any],
    *,
    prompt: str = "",
    max_chars: int = 6000,
) -> str:
    """Provide bounded import/API excerpts from modules around the selected target."""
    if max_chars < 500:
        return ""
    root = Path(project_root).expanduser().resolve()
    target = str(analysis.get("target", ""))
    rows: list[str] = []
    candidates: list[str] = []
    for key in ("direct_dependents", "direct_dependencies", "transitive_dependents", "affected_tests"):
        values = analysis.get(key, [])
        if key == "affected_tests":
            candidates.extend(str(value) for value in values if isinstance(value, str))
        else:
            candidates.extend(
                str(value.get("path", "")) for value in values if isinstance(value, dict)
            )
    unique: list[str] = []
    for path in candidates:
        if path and path != target and path not in unique:
            unique.append(path)
    imported_names = analysis.get("imported_names_by_path", {})
    used_chars = 0
    for relative in unique[:12]:
        pure = PurePosixPath(relative)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            continue
        path = root.joinpath(*pure.parts)
        try:
            if path.is_symlink() or root not in path.resolve().parents or path.stat().st_size > MAX_SOURCE_BYTES:
                continue
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=relative)
        except (OSError, UnicodeError, SyntaxError):
            continue
        lines = source.splitlines()
        excerpts = [f"\n### {relative}"]
        names = imported_names.get(relative, []) if isinstance(imported_names, dict) else []
        if names:
            excerpts.append("Имена, импортируемые этим модулем: " + ", ".join(names[:20]))
        import_lines = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)) and 1 <= node.lineno <= len(lines):
                import_lines.append((node.lineno, lines[node.lineno - 1].strip()))
        if import_lines:
            excerpts.append("Импорты: " + " | ".join(text for _, text in sorted(set(import_lines))[:10]))
        signatures = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                end = min(len(lines), max(node.lineno, min(node.body[0].lineno if node.body else node.lineno, node.lineno + 3)))
                signature = " ".join(line.strip() for line in lines[node.lineno - 1:end])
                signatures.append(signature[:350])
        if signatures:
            excerpts.append("Публичные точки взаимодействия: " + " | ".join(signatures[:12]))
        entry = "\n".join(excerpts)
        if used_chars + len(entry) > max_chars:
            available = max_chars - used_chars
            if available > 160:
                rows.append(entry[:available])
            break
        rows.append(entry)
        used_chars += len(entry)
    if not rows:
        return "Связанные исходники для дополнительного контекста не найдены."
    return "\n".join(rows)
