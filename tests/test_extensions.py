import json
import tempfile
import unittest
from pathlib import Path

from auto_memory import extract_memory_candidate
from plugin_manager import PluginManager
from self_improvement import apply_unified_diff
from updater import version_tuple
from windows_control import handle_voice_command


class AutoMemoryTests(unittest.TestCase):
    def test_explicit_memory_phrase(self):
        self.assertEqual(extract_memory_candidate("Запомни: я предпочитаю тёмную тему"), "я предпочитаю тёмную тему")

    def test_explicit_preference(self):
        value = extract_memory_candidate("Я предпочитаю короткие ответы")
        self.assertIn("короткие ответы", value or "")

    def test_secret_is_not_saved(self):
        self.assertIsNone(extract_memory_candidate("Запомни: мой пароль 123456"))
        self.assertIsNone(extract_memory_candidate("Запомни: мой API key abc123"))

    def test_arbitrary_chat_is_not_saved(self):
        self.assertIsNone(extract_memory_candidate("Привет, как дела?"))


class PatchTests(unittest.TestCase):
    def test_apply_single_file_diff(self):
        original = "first\nsecond\nthird\n"
        patch = """--- a/main.py
+++ b/main.py
@@ -1,3 +1,3 @@
 first
-second
+SECOND
 third
"""
        self.assertEqual(apply_unified_diff(original, patch, "main.py"), "first\nSECOND\nthird\n")

    def test_reject_other_file(self):
        with self.assertRaises(ValueError):
            apply_unified_diff("a\n", "--- a/other.py\n+++ b/other.py\n@@ -1 +1 @@\n-a\n+b\n", "main.py")

    def test_reject_mismatched_context(self):
        patch = "--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-old\n+new\n"
        with self.assertRaises(ValueError):
            apply_unified_diff("different\n", patch, "main.py")


class PluginTests(unittest.TestCase):
    def test_skill_create_and_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = PluginManager(Path(temp))
            manager.create_skill("Краткие ответы", "Когда прошу кратко", "Отвечай не более чем в пяти предложениях.")
            prompt = manager.build_skill_prompt()
            self.assertIn("Краткие ответы", prompt)
            self.assertIn("пяти предложениях", prompt)

    def test_disabled_python_plugins_do_not_execute(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = PluginManager(Path(temp), enabled_python=False)
            plugin = manager.plugins_dir / "test_plugin.py"
            plugin.write_text('raise RuntimeError("should not run")\n', encoding="utf-8")
            self.assertIsNone(manager.try_handle_command("/hello"))


class UtilityTests(unittest.TestCase):
    def test_version_tuple(self):
        self.assertGreater(version_tuple("v3.1.0"), version_tuple("3.0.9"))

    def test_unsupported_command_is_handled_safely(self):
        result = handle_voice_command("открой powershell и выполни whoami")
        self.assertTrue(result.handled)
        self.assertFalse(result.success)
        self.assertIn("безопасный список", result.message)

    def test_non_command_is_not_handled(self):
        result = handle_voice_command("Расскажи интересный факт")
        self.assertFalse(result.handled)


if __name__ == "__main__":
    unittest.main(verbosity=2)
