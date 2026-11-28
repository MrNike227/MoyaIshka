# -*- coding: utf-8 -*-
"""Regression checks for readable, grouped module-level imports."""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT_MODULES = {path.stem for path in ROOT.glob("*.py")}


def _module_name(node: ast.Import | ast.ImportFrom) -> str:
    if isinstance(node, ast.Import):
        return node.names[0].name
    return str(node.module or "")


def _import_group(module: str) -> int:
    root = module.split(".", 1)[0]
    if root == "__future__":
        return 0
    if root in sys.stdlib_module_names:
        return 1
    if root in PROJECT_MODULES:
        return 3
    return 2


def _top_level_imports(path: str) -> tuple[str, list[ast.Import | ast.ImportFrom]]:
    source = (ROOT / path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=path)
    nodes = [
        node for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    return source, nodes


class ImportOrganizationTests(unittest.TestCase):
    ORDERED_MODULES = (
        "main.py",
        "cloud_sync.py",
        "github_pr.py",
        "google_drive_sync.py",
        "supabase_backup.py",
        "updater_helper.py",
        "windows_control.py",
        "windows_sandbox.py",
        "voice_manager.py",
    )

    def test_module_imports_are_grouped_by_dependency_type_and_sorted(self):
        for path in self.ORDERED_MODULES:
            with self.subTest(path=path):
                _source, nodes = _top_level_imports(path)
                keys = []
                for node in nodes:
                    module = _module_name(node)
                    group = _import_group(module)
                    if path == "main.py" and group == 3:
                        continue  # main.py deliberately groups local imports by feature area
                    kind = 0 if isinstance(node, ast.Import) else 1
                    keys.append((group, kind, module.casefold()))
                    if isinstance(node, ast.Import):
                        self.assertEqual(
                            len(node.names), 1,
                            f"{path}:{node.lineno}: split multiple import names onto separate lines",
                        )
                self.assertEqual(
                    keys,
                    sorted(keys),
                    f"{path}: imports must be grouped as standard library, third-party, then application modules",
                )

    def test_main_application_imports_are_grouped_by_feature_area(self):
        source, nodes = _top_level_imports("main.py")
        headings = (
            "# Core desktop interface, memory and plugin services.",
            "# Backup and cloud storage integrations.",
            "# Self-development, dependency analysis and safe proposal delivery.",
            "# Updates, voice input/output, desktop control and online research.",
        )
        offsets = [source.index(item) for item in headings]
        heading_lines = [source[:offset].count(chr(10)) + 1 for offset in offsets]
        self.assertEqual(heading_lines, sorted(heading_lines))

        expected = (
            [
                "avatar_widget", "auto_memory", "context_engine",
                "intelligent_memory", "plugin_manager", "resource_governor",
            ],
            [
                "backup_cloud", "backup_manager", "cloud_sync",
                "google_drive_sync", "supabase_backup",
            ],
            [
                "background_developer", "github_pr", "improvement_delivery",
                "improvement_testing", "module_dependency_analyzer", "self_improvement",
            ],
            [
                "updater", "updater_helper", "voice_assistant", "voice_manager",
                "web_research", "windows_control", "windows_sandbox",
            ],
        )
        local_nodes = [
            node for node in nodes
            if _import_group(_module_name(node)) == 3
        ]
        for index, heading_line in enumerate(heading_lines):
            next_heading_line = (
                heading_lines[index + 1] if index + 1 < len(heading_lines) else len(source.splitlines()) + 1
            )
            actual = [
                _module_name(node).split(".", 1)[0]
                for node in local_nodes
                if heading_line < node.lineno < next_heading_line
            ]
            self.assertEqual(actual, expected[index], headings[index])

    def test_imports_inside_functions_are_reserved_for_lazy_or_platform_specific_dependencies(self):
        expected_local = {
            "main.py": {
                "improvement_testing", "pypdf", "docx", "tkinter.font",
                "webbrowser", "cffi", "_cffi_backend", "updater_helper",
            },
            "improvement_testing.py": {
                "windows_sandbox", "ctypes", "ctypes.wintypes",
                "importlib.machinery", "unittest",
            },
            "plugin_manager.py": {"windows_control"},
            "resource_governor.py": {"psutil", "ctypes"},
            "voice_assistant.py": {"speech_recognition", "piper", "winsound", "pyttsx3"},
            "windows_control.py": {"ctypes", "ctypes.wintypes"},
            "windows_sandbox.py": {"winreg"},
            "updater_helper.py": set(),
        }
        for path, allowed in expected_local.items():
            with self.subTest(path=path):
                tree = ast.parse((ROOT / path).read_text(encoding="utf-8"), filename=path)
                nested = []
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Import, ast.ImportFrom)) and node not in tree.body:
                        names = (
                            [item.name for item in node.names]
                            if isinstance(node, ast.Import)
                            else [node.module or ""]
                        )
                        nested.extend(name for name in names if name)
                self.assertLessEqual(
                    set(nested), allowed,
                    f"{path}: unexpected nested import; document its lazy-loading reason or move it to the module block",
                )

    def test_main_has_no_duplicate_top_level_module_imports(self):
        _source, nodes = _top_level_imports("main.py")
        names = []
        for node in nodes:
            kind = "import" if isinstance(node, ast.Import) else "from"
            imported = tuple(item.name for item in node.names)
            names.append((kind, _module_name(node).casefold(), imported))
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
