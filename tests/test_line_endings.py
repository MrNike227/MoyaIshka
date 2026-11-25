import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LF_FILES = [
    ".gitattributes",
    ".github/workflows/release.yml",
    ".github/workflows/verify.yml",
    "CHANGELOG_RU.md",
    "GOOGLE_DRIVE_SETUP_RU.md",
    "HOTFIX_4.1.4_GITHUB_RU.md",
    "INSTALLER.iss",
    "LICENSE.txt",
    "PATCH_INSTALL_RU.txt",
    "PATCH_INSTRUCTIONS_GITHUB_RU.md",
    "README_RU.md",
    "SUPABASE_SETUP_RU.md",
    "THIRD_PARTY_NOTICES.md",
    "version_info.txt",
    "ОБНОВЛЕНИЕ_НА_GITHUB_RU.txt",
    "auto_memory.py",
    "avatar_widget.py",
    "cloud_sync.py",
    "google_drive_sync.py",
    "main.py",
    "plugin_manager.py",
    "resource_governor.py",
    "self_improvement.py",
    "updater.py",
    "updater_helper.py",
    "voice_assistant.py",
    "voice_manager.py",
    "web_research.py",
    "windows_control.py",
    "hooks/hook-cryptography.py",
    "plugins/hello_plugin.py",
    "requirements.txt",
    "requirements_voice.txt",
    "skills/example_skill.json",
    "sql/supabase_setup.sql",
    "tests/test_build_release.py",
    "tests/test_cloud_sync.py",
    "tests/test_core.py",
    "tests/test_extensions.py",
    "tests/test_google_drive_sync.py",
    "tests/test_line_endings.py",
    "tests/test_resource_governor.py",
    "tests/test_voice_manager.py",
    "tests/test_web_research.py",
]
CRLF_FILES = [
    "BUILD_EXE.bat",
    "START.bat",
    "INSTALL_OPTIONAL_DOCS.bat",
    "INSTALL_VOICE.bat",
]


class LineEndingTests(unittest.TestCase):
    def test_lf_files_have_no_crlf_or_missing_final_newline(self):
        for relative in LF_FILES:
            with self.subTest(path=relative):
                data = (ROOT / relative).read_bytes()
                self.assertTrue(data.endswith(b"\n"), "missing final newline")
                self.assertNotIn(b"\r", data, "expected LF-only line endings")

    def test_windows_scripts_use_crlf_without_bare_lf(self):
        for relative in CRLF_FILES:
            with self.subTest(path=relative):
                data = (ROOT / relative).read_bytes()
                self.assertTrue(data.endswith(b"\r\n"), "missing final CRLF")
                normalized = data.replace(b"\r\n", b"")
                self.assertNotIn(b"\n", normalized, "bare LF found in Windows script")
                self.assertNotIn(b"\r", normalized, "bare CR found in Windows script")


if __name__ == "__main__":
    unittest.main(verbosity=2)
