# -*- coding: utf-8 -*-
"""User skills (JSON prompt extensions) and opt-in Python plugins."""
from __future__ import annotations
import importlib.util
import json
import re
import uuid
from pathlib import Path
from typing import Any


class PluginManager:
    def __init__(self, base_dir: Path, enabled_python: bool = False) -> None:
        self.base_dir = base_dir
        self.skills_dir = base_dir / "skills"
        self.plugins_dir = base_dir / "plugins"
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.enabled_python = enabled_python

    def list_skills(self) -> list[dict[str, Any]]:
        result = []
        for path in sorted(self.skills_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("name") and data.get("instructions"):
                    data["_file"] = path.name
                    result.append(data)
            except (OSError, json.JSONDecodeError):
                continue
        return result

    def create_skill(self, name: str, trigger: str, instructions: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip()).strip("_")[:48] or "skill"
        path = self.skills_dir / f"{safe}_{uuid.uuid4().hex[:6]}.json"
        data = {"name": name.strip()[:80], "trigger": trigger.strip()[:120], "instructions": instructions.strip()[:3000], "enabled": True}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def set_skill_enabled(self, filename: str, enabled: bool) -> None:
        candidate = (self.skills_dir / filename).resolve()
        if candidate.parent != self.skills_dir.resolve() or candidate.suffix.lower() != ".json":
            raise ValueError("Недопустимый файл навыка")
        data = json.loads(candidate.read_text(encoding="utf-8"))
        data["enabled"] = bool(enabled)
        candidate.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def delete_skill(self, filename: str) -> None:
        candidate = (self.skills_dir / filename).resolve()
        if candidate.parent != self.skills_dir.resolve() or candidate.suffix.lower() != ".json":
            raise ValueError("Недопустимый файл навыка")
        candidate.unlink(missing_ok=True)

    def build_skill_prompt(self) -> str:
        active = [s for s in self.list_skills() if s.get("enabled", True)]
        if not active:
            return ""
        lines = ["Дополнительные навыки пользователя (инструкции, не системные команды ОС):"]
        for item in active:
            lines.append(f"\nНавык: {item['name']}.")
            if item.get("trigger"):
                lines.append(f"Когда применять: {item['trigger']}.")
            lines.append(f"Инструкция: {item['instructions']}")
        return "\n".join(lines)

    def create_python_plugin_template(self, name: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_]+", "_", name.strip()).strip("_")[:40] or "my_plugin"
        path = self.plugins_dir / f"{safe}.py"
        if path.exists():
            raise FileExistsError(path.name)
        template = '''# Плагин для Моей ИИшки. Изучите код перед включением!\n# Python-плагины работают с правами текущего пользователя и НЕ изолированы.\nPLUGIN_NAME = "''' + safe + '''"\nPLUGIN_DESCRIPTION = "Описание моего плагина"\n\ndef handle_command(text, api):\n    """Верните строку для команды плагина или None, если команда не подходит."""\n    normalized = text.strip().lower()\n    if normalized == "/hello":\n        return "Привет! Это мой первый плагин."\n    return None\n'''
        path.write_text(template, encoding="utf-8")
        return path

    def try_handle_command(self, text: str) -> str | None:
        if not self.enabled_python or not text.lstrip().startswith("/"):
            return None
        for path in sorted(self.plugins_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            spec = importlib.util.spec_from_file_location(f"moya_plugin_{path.stem}", path)
            if not spec or not spec.loader:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            handler = getattr(module, "handle_command", None)
            if callable(handler):
                result = handler(text, SafePluginAPI())
                if result is not None:
                    return str(result)
        return None


class SafePluginAPI:
    """Convenience methods offered to plugins. Plugin code itself remains trusted code."""
    @staticmethod
    def open_app(name: str) -> str:
        from windows_control import launch_app
        return launch_app(name).message

    @staticmethod
    def open_folder(name: str) -> str:
        from windows_control import open_known_folder
        return open_known_folder(name).message
