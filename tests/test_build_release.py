import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET_VERSION = "4.1.5.2"


class ReleaseBuildTests(unittest.TestCase):
    def test_application_version_is_4152(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn(f'APP_VERSION = "{TARGET_VERSION}"', source)

    def test_cryptography_is_a_required_application_dependency(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertRegex(requirements, r"(?m)^cryptography\s*>?=")

    def test_local_build_collects_crypto_package_and_rust_binding(self):
        build = (ROOT / "BUILD_EXE.bat").read_text(encoding="utf-8-sig")
        self.assertIn("--additional-hooks-dir hooks", build)
        self.assertIn("--collect-all cryptography", build)
        self.assertIn("--hidden-import=cryptography.hazmat.bindings._rust", build)
        self.assertIn("--copy-metadata cryptography", build)
        self.assertIn("--self-test-imports", build)
        self.assertIn("-m pip install --upgrade -r requirements.txt", build)

    def test_custom_cryptography_hook_collects_native_extension(self):
        hook = ROOT / "hooks" / "hook-cryptography.py"
        self.assertTrue(hook.is_file())
        source = hook.read_text(encoding="utf-8")
        self.assertIn('collect_all("cryptography")', source)
        self.assertIn("cryptography.hazmat.bindings._rust", source)

    def test_crypto_runtime_self_test_performs_aes_gcm_and_scrypt(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('"--self-test-imports"', source)
        self.assertIn("_CryptoAESGCM", source)
        self.assertIn("_CryptoScrypt", source)
        self.assertIn("cipher.decrypt", source)

    def test_release_and_verify_workflows_use_node24_actions(self):
        workflow_paths = [
            ROOT / ".github" / "workflows" / "release.yml",
            ROOT / ".github" / "workflows" / "verify.yml",
        ]
        for path in workflow_paths:
            source = path.read_text(encoding="utf-8")
            self.assertIn("actions/checkout@v7", source, path.name)
            self.assertIn("actions/setup-python@v7.1.0", source, path.name)
            self.assertNotIn("actions/checkout@v4", source, path.name)
            self.assertNotIn("actions/setup-python@v5", source, path.name)
            self.assertNotIn("softprops/action-gh-release@v2", source, path.name)
            self.assertIn("BUILD_EXE.bat", source, path.name)
            self.assertIn("Verify frozen EXE imports and encryption round-trip", source, path.name)

    def test_release_is_published_only_after_smoke_test(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertLess(
            workflow.index("Verify frozen EXE imports and encryption round-trip"),
            workflow.index("Publish GitHub Release"),
        )
        self.assertIn("'v4.1.5.2'", workflow)
        self.assertIn("gh release create", workflow)

    def test_windows_version_resource_and_installer_match(self):
        version = (ROOT / "version_info.txt").read_text(encoding="utf-8")
        installer = (ROOT / "INSTALLER.iss").read_text(encoding="utf-8")
        self.assertIn("filevers=(4,1,5,2)", version)
        self.assertIn("prodvers=(4,1,5,2)", version)
        self.assertIn("ProductVersion', '4.1.5.2'", version)
        self.assertIn("AppVersion=4.1.5.2", installer)

    def test_updater_targets_this_repository(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('"update_repo": "MrNike227/MoyaIshka"', source)
        self.assertNotIn("MrNike227/MoyaIshka_v4.1.1", source)

    def test_version_references_are_current_in_runtime_modules(self):
        paths = [
            "main.py",
            "cloud_sync.py",
            "google_drive_sync.py",
            "updater_helper.py",
            "voice_manager.py",
            "web_research.py",
        ]
        for relative in paths:
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("4.1.5.1", source, relative)
            self.assertIn("4.1.5.2", source, relative)

    def test_cffi_native_backend_is_declared_and_collected(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        build = (ROOT / "BUILD_EXE.bat").read_text(encoding="utf-8-sig")
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        hook = (ROOT / "hooks" / "hook-cryptography.py").read_text(encoding="utf-8")
        self.assertRegex(requirements, r"(?m)^cffi\s*>=")
        self.assertRegex(requirements, r"(?m)^pycparser\s*>=")
        self.assertIn("--collect-all cffi", build)
        self.assertIn("--hidden-import=_cffi_backend", build)
        self.assertIn("_cffi_backend", hook)
        self.assertIn("from cffi import FFI as _CffiFFI", source)
        self.assertIn("backend_probe", source)

    def test_packaged_import_self_test_passes_in_build_environment(self):
        from main import _packaged_import_self_test
        self.assertEqual(_packaged_import_self_test(), 0)

    def test_legacy_updater_repository_is_migrated(self):
        from main import DEFAULT_CONFIG, migrate_legacy_update_repository

        config = {"update_repo": "MrNike227/MoyaIshka_v4.1.1"}
        self.assertTrue(migrate_legacy_update_repository(config))
        self.assertEqual(config["update_repo"], DEFAULT_CONFIG["update_repo"])

    def test_custom_updater_repository_is_preserved(self):
        from main import migrate_legacy_update_repository

        custom_repo = "ExampleOwner/CustomAssistant"
        config = {"update_repo": custom_repo}
        self.assertFalse(migrate_legacy_update_repository(config))
        self.assertEqual(config["update_repo"], custom_repo)


if __name__ == "__main__":
    unittest.main(verbosity=2)
