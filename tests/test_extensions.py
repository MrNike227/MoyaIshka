import json
import tempfile
import unittest
from pathlib import Path

from auto_memory import extract_memory_candidate
from plugin_manager import PluginManager
from self_improvement import apply_unified_diff
from updater import version_tuple
from updater_helper import install_from_source, _powershell_script, write_powershell_updater_script, user_install_dir, resolve_install_dir, ensure_install_dir_writable, scan_application_copies, delete_application_copy
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
    def test_application_release_version_is_4151(self):
        import os
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from main import APP_VERSION
        self.assertEqual(APP_VERSION, "4.1.5.1")

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


class UpdaterTests(unittest.TestCase):
    def test_source_update_installs_to_stable_user_programs_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            staged_dir = Path(temp) / "update_staging"
            staged_dir.mkdir()
            staged = staged_dir / "MoyaIshka_update.exe"
            staged.write_bytes(b"fake exe bytes")
            install_dir = Path(temp) / "Programs" / "MoyaIshka"
            installed = install_from_source(staged, install_dir)
            self.assertEqual(installed, install_dir / "MoyaIshka.exe")
            self.assertEqual(installed.read_bytes(), b"fake exe bytes")
            self.assertFalse((install_dir / "MoyaIshka.new.exe").exists())

    def test_windows_updater_waits_then_replaces_and_restarts(self):
        script = _powershell_script()
        self.assertIn("AddSeconds(120)", script)
        self.assertIn("CloseMainWindow()", script)
        self.assertIn("Get-FileHash", script)
        self.assertIn("MoyaIshka.installing.exe", script)
        self.assertIn("Start-Process -FilePath $TargetPath", script)
        self.assertIn("MoyaIshka.previous.exe", script)
        self.assertIn("Выполняется откат", script)
        self.assertIn("ПОДТВЕРЖДЕНО", script)

    def test_updater_script_is_written_with_windows_crlf_and_bom(self):
        with tempfile.TemporaryDirectory() as temp:
            path = write_powershell_updater_script(Path(temp) / "update.ps1")
            raw = path.read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
            self.assertIn(b"\r\n", raw)
            self.assertEqual(raw.replace(b"\r\n", b"").count(b"\n"), 0)
            self.assertIn("Get-FileHash".encode("utf-8"), raw)

    def test_windows_launcher_builds_detached_command_with_exact_target(self):
        from unittest.mock import patch
        import updater_helper
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            staged = root / "staging" / "MoyaIshka.exe"
            staged.parent.mkdir()
            staged.write_bytes(b"test exe bytes")
            target = root / "ISH" / "MoyaIshka.exe"
            log = root / "AppData" / "MoyaIshka" / "update.log"
            with patch.object(updater_helper.shutil, "which", return_value="powershell.exe"), \
                 patch.object(updater_helper.subprocess, "Popen") as popen:
                helper = updater_helper.launch_windows_replacement(
                    staged, target, 1234, log, expected_version="4.1.5.1",
                    receipt_path=log.with_name("update_confirmation.json"), platform_name="nt",
                )
            self.assertTrue(helper.is_file())
            args = popen.call_args.args[0]
            self.assertEqual(args[0], "powershell.exe")
            self.assertIn("-File", args)
            self.assertIn(str(staged.resolve()), args)
            self.assertIn(str(target.resolve()), args)
            self.assertIn("4.1.5.1", args)

    def test_staged_exe_first_launch_uses_detached_transactional_installer(self):
        import inspect
        import main
        source = inspect.getsource(main.migrate_staged_exe_on_first_launch)
        self.assertIn("launch_windows_replacement(", source)
        self.assertIn("expected_version=APP_VERSION", source)
        self.assertIn('"update.log"', source)
        self.assertNotIn("shutil.copy2(running_exe, temporary)", source)

    def test_missing_windows_version_metadata_does_not_block_app_handshake(self):
        script = _powershell_script()
        self.assertIn("нет FileVersion/ProductVersion", script)
        self.assertIn("версию подтвердит само приложение при запуске", script)
        self.assertIn("if ($embeddedVersion -notmatch", script)
        self.assertIn("Новая версия не подтвердила запуск", script)

    def test_updater_launcher_creates_log_before_starting_powershell(self):
        from unittest.mock import patch
        import updater_helper
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            staged = root / "staging" / "MoyaIshka.exe"
            staged.parent.mkdir()
            staged.write_bytes(b"test exe bytes")
            log = root / "AppData" / "MoyaIshka" / "update.log"
            target = root / "ISH" / "MoyaIshka.exe"
            with patch.object(updater_helper.shutil, "which", return_value="powershell.exe"), \
                 patch.object(updater_helper.subprocess, "Popen"):
                updater_helper.launch_windows_replacement(
                    staged, target, 1234, log, expected_version="4.1.5.1", platform_name="nt",
                )
            self.assertTrue(log.is_file())
            content = log.read_text(encoding="utf-8")
            self.assertIn("Запуск установщика обновления 4.1.5.1", content)
            self.assertIn(str(target.resolve()), content)

    def test_updater_confirmation_requires_version_hash_and_exact_target(self):
        script = _powershell_script()
        self.assertIn("ExpectedVersion", script)
        self.assertIn("ExpectedSha256", script)
        self.assertIn("ReceiptPath", script)
        self.assertIn("Новая версия не подтвердила запуск", script)

    def test_user_install_path_is_named_for_app(self):
        self.assertEqual(user_install_dir().name, "MoyaIshka")

    def test_explicit_install_folder_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            selected = Path(temp) / "MyApps" / "Ishka"
            self.assertEqual(resolve_install_dir(configured=selected), selected.resolve())
            self.assertEqual(ensure_install_dir_writable(selected), selected.resolve())
            self.assertTrue((selected / ".").exists())

    def test_old_version_scanner_finds_copies_but_excludes_running_exe(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current_dir = root / "Installed"
            old_dir = root / "Downloads" / "MoyaIshka_v4.1.1"
            current_dir.mkdir(parents=True)
            old_dir.mkdir(parents=True)
            current_exe = current_dir / "MoyaIshka.exe"
            old_exe = old_dir / "MoyaIshka.exe"
            current_exe.write_bytes(b"current")
            old_exe.write_bytes(b"old version")
            found = scan_application_copies(
                configured_install_dir=old_dir,
                current_exe=current_exe,
                local_app_data=root / "LocalAppData",
                roaming_app_data=root / "RoamingAppData",
                user_home=root / "User",
            )
            paths = {item["path"] for item in found}
            self.assertIn(str(old_exe.resolve()), paths)
            self.assertNotIn(str(current_exe.resolve()), paths)
            old_record = next(item for item in found if item["path"] == str(old_exe.resolve()))
            self.assertEqual(old_record["version"], "4.1.1")

    def test_old_version_delete_removes_only_selected_app_exe(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current_exe = root / "Current" / "MoyaIshka.exe"
            old_exe = root / "Old" / "MoyaIshka.exe"
            current_exe.parent.mkdir()
            old_exe.parent.mkdir()
            current_exe.write_bytes(b"current")
            old_exe.write_bytes(b"old")
            with self.assertRaises(ValueError):
                delete_application_copy(current_exe, current_exe=current_exe)
            delete_application_copy(old_exe, current_exe=current_exe)
            self.assertTrue(current_exe.exists())
            self.assertFalse(old_exe.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
