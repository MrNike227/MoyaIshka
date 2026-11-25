import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseBuildTests(unittest.TestCase):
    def test_application_version_is_415(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('APP_VERSION = "4.1.5"', source)

    def test_cryptography_is_explicitly_collected(self):
        build = (ROOT / "BUILD_EXE.bat").read_text(encoding="utf-8-sig")
        self.assertIn("--additional-hooks-dir hooks", build)
        self.assertIn("--collect-all cryptography", build)
        self.assertIn("--hidden-import=cryptography", build)
        self.assertIn("--hidden-import=cryptography.hazmat.bindings._rust", build)
        self.assertIn("--copy-metadata cryptography", build)
        self.assertIn("--self-test-imports", build)

    def test_custom_cryptography_hook_collects_package(self):
        hook = ROOT / "hooks" / "hook-cryptography.py"
        self.assertTrue(hook.is_file())
        source = hook.read_text(encoding="utf-8")
        self.assertIn('collect_all("cryptography")', source)
        self.assertIn('cryptography.hazmat.bindings._rust', source)

    def test_runtime_smoke_test_performs_aes_gcm_roundtrip(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('"--self-test-imports"', source)
        self.assertIn("_CryptoAESGCM", source)
        self.assertIn("_CryptoScrypt", source)
        self.assertIn("cipher.decrypt", source)

    def test_github_actions_use_node24_compatible_action_majors(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("actions/checkout@v7.0.1", workflow)
        self.assertIn("actions/setup-python@v7.1.0", workflow)
        self.assertNotIn("softprops/action-gh-release@v2", workflow)
        self.assertIn("Verify frozen EXE crypto imports", workflow)

    def test_runtime_crypto_self_test_passes_in_build_environment(self):
        from main import _packaged_import_self_test
        self.assertEqual(_packaged_import_self_test(), 0)

    def test_cryptography_is_declared_as_a_build_dependency(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        build_script = (ROOT / "BUILD_EXE.bat").read_text(encoding="utf-8-sig")
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("cryptography", requirements)
        self.assertIn("cryptography", build_script)
        self.assertIn("Verify cryptography build environment", workflow)

    def test_windows_version_resource_is_415(self):
        source = (ROOT / "version_info.txt").read_text(encoding="utf-8")
        self.assertIn("filevers=(4,1,5,0)", source)
        self.assertIn("ProductVersion', '4.1.5'", source)


if __name__ == "__main__":
    unittest.main()
