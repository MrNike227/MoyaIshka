import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseBuildTests(unittest.TestCase):
    def test_pyinstaller_bootloader_version_is_pinned(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("pyinstaller==6.22.3", requirements)

    def test_application_version_is_4183(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('APP_VERSION = "4.1.8.3"', source)

    def test_custom_bootloader_preloads_vc_runtime_before_python_dll(self):
        from build_patched_pyinstaller import (
            PATCH_MARKER, PRELOAD_C_BLOCK, SOURCE_ANCHOR, patch_bootloader_source,
        )
        with tempfile.TemporaryDirectory() as temp:
            source_path = Path(temp) / "pyi_dylib_python.c"
            source_path.write_text("before\n" + SOURCE_ANCHOR + "\n", encoding="utf-8")
            self.assertTrue(patch_bootloader_source(source_path))
            patched = source_path.read_text(encoding="utf-8")
            self.assertLess(patched.index(PATCH_MARKER), patched.index(SOURCE_ANCHOR))
            self.assertIn('L"vcruntime140.dll"', patched)
            self.assertIn('L"vcruntime140_1.dll"', patched)
            self.assertIn('L"msvcp140.dll"', patched)
            self.assertLess(patched.index('L"vcruntime140.dll"'), patched.index('L"msvcp140.dll"'))
            self.assertFalse(patch_bootloader_source(source_path))

    def test_custom_bootloader_builder_uses_utf8_console_and_waf(self):
        source = (ROOT / "build_patched_pyinstaller.py").read_text(encoding="utf-8")
        self.assertIn('stream.reconfigure(encoding="utf-8", errors="backslashreplace")', source)
        self.assertIn('build_env["PYTHONIOENCODING"] = "utf-8:backslashreplace"', source)
        self.assertIn('build_env["PYTHONUTF8"] = "1"', source)
        self.assertIn('os.environ.get("RUNNER_TEMP")', source)
        self.assertIn('tempfile.mkdtemp(prefix="mpyi_", dir=str(temp_parent))', source)

    def test_build_uses_custom_bootloader_builder(self):
        build = (ROOT / "BUILD_EXE.bat").read_text(encoding="utf-8-sig")
        source = (ROOT / "build_patched_pyinstaller.py").read_text(encoding="utf-8")
        self.assertIn("%PY% build_patched_pyinstaller.py", build)
        self.assertIn('--add-data "build_patched_pyinstaller.py;source_project"', build)
        self.assertIn("waf", source)
        self.assertIn("BOOTLOADER_SOURCE_RELATIVE", source)
        self.assertIn("PYINSTALLER_TAG", source)
        self.assertIn('marker.get("patch") == PATCH_MARKER', source)
        main_source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('"build_patched_pyinstaller.py"', main_source)

    def test_python_vc_runtime_is_bundled_for_python_dll_loader(self):
        build = (ROOT / "BUILD_EXE.bat").read_text(encoding="utf-8-sig")
        helper = (ROOT / "prepare_python_runtime.py").read_text(encoding="utf-8")
        self.assertIn("%PY% prepare_python_runtime.py", build)
        self.assertIn('--add-binary "build_runtime\\python_runtime;."', build)
        self.assertIn("vcruntime140.dll", helper)
        self.assertIn("vcruntime140_1.dll", helper)
        self.assertIn("ucrtbase.dll", helper)
        self.assertIn('api-ms-win-crt-*.dll', helper)

    def test_runtime_dll_collector_copies_vc_runtime_and_ucrt(self):
        from prepare_python_runtime import collect_runtime_dlls
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "python"
            destination = root / "bundle"
            source.mkdir()
            def write_pe(path, machine=0x8664):
                data = bytearray(0x86)
                data[:2] = b"MZ"
                data[0x3C:0x40] = (0x80).to_bytes(4, "little")
                data[0x80:0x84] = b"PE\0\0"
                data[0x84:0x86] = machine.to_bytes(2, "little")
                path.write_bytes(data)

            write_pe(source / "vcruntime140.dll")
            write_pe(source / "vcruntime140_1.dll")
            write_pe(source / "msvcp140.dll")
            write_pe(source / "ucrtbase.dll")
            write_pe(source / "api-ms-win-crt-runtime-l1-1-0.dll")
            copied = collect_runtime_dlls([source], destination)
            self.assertEqual(
                {item.name for item in copied},
                {"vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "ucrtbase.dll", "api-ms-win-crt-runtime-l1-1-0.dll"},
            )
            self.assertEqual((destination / "vcruntime140.dll").read_bytes()[:2], b"MZ")

    def test_runtime_dll_collector_rejects_32_bit_runtime_dll(self):
        from prepare_python_runtime import collect_runtime_dlls
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "python"
            source.mkdir()

            def write_pe(path, machine=0x8664):
                data = bytearray(0x86)
                data[:2] = b"MZ"
                data[0x3C:0x40] = (0x80).to_bytes(4, "little")
                data[0x80:0x84] = b"PE\0\0"
                data[0x84:0x86] = machine.to_bytes(2, "little")
                path.write_bytes(data)

            write_pe(source / "vcruntime140.dll", 0x14C)
            write_pe(source / "vcruntime140_1.dll")
            write_pe(source / "msvcp140.dll")
            write_pe(source / "ucrtbase.dll")
            write_pe(source / "api-ms-win-crt-runtime-l1-1-0.dll")
            with self.assertRaisesRegex(RuntimeError, "требуется x64"):
                collect_runtime_dlls([source], root / "bundle")

    def test_runtime_dll_collector_fails_when_core_runtime_is_missing(self):
        from prepare_python_runtime import collect_runtime_dlls
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "python"
            source.mkdir()
            with self.assertRaisesRegex(RuntimeError, "vcruntime140.dll"):
                collect_runtime_dlls([source], root / "bundle")

    def test_cryptography_is_explicitly_collected(self):
        build = (ROOT / "BUILD_EXE.bat").read_text(encoding="utf-8-sig")
        self.assertIn("--additional-hooks-dir hooks", build)
        self.assertIn("--collect-all cryptography", build)
        self.assertIn("--collect-submodules cryptography", build)
        self.assertIn("--collect-binaries cryptography", build)
        self.assertIn("--hidden-import=cryptography", build)
        self.assertIn("--hidden-import=cryptography.hazmat.bindings._rust", build)
        self.assertIn("--copy-metadata cryptography", build)
        self.assertIn("--self-test-imports", build)

    def test_custom_cryptography_hook_collects_package(self):
        hook = ROOT / "hooks" / "hook-cryptography.py"
        self.assertTrue(hook.is_file())
        source = hook.read_text(encoding="utf-8")
        self.assertIn('collect_all("cryptography")', source)
        self.assertIn('collect_all("cffi")', source)
        self.assertIn('"_cffi_backend"', source)
        self.assertIn('cryptography.hazmat.bindings._rust', source)

    def test_cffi_backend_is_declared_and_collected(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        build = (ROOT / "BUILD_EXE.bat").read_text(encoding="utf-8-sig")
        self.assertIn("cffi>=2.0.0", requirements)
        self.assertIn("--collect-all cffi", build)
        self.assertIn("--hidden-import=_cffi_backend", build)

    def test_frozen_smoke_test_requires_app_local_runtime_dlls(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('getattr(sys, "frozen", False)', source)
        self.assertIn('"vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "ucrtbase.dll", "api-ms-win-crt-runtime-l1-1-0.dll"', source)
        self.assertIn('"_MEIPASS"', source)

    def test_runtime_smoke_test_performs_aes_gcm_roundtrip(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('"--self-test-imports"', source)
        self.assertIn("_CryptoAESGCM", source)
        self.assertIn("_CryptoScrypt", source)
        self.assertIn("cipher.decrypt", source)
        self.assertIn("import _cffi_backend", source)

    def test_github_actions_use_node24_compatible_action_majors(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("actions/checkout@v7", workflow)
        self.assertIn("actions/setup-python@v7.1.0", workflow)
        self.assertNotIn("softprops/action-gh-release@v2", workflow)
        self.assertIn("Verify x64 VC runtime and Universal CRT DLLs are embedded", workflow)
        self.assertIn("pyi-archive_viewer -l", workflow)
        self.assertIn("Verify frozen EXE imports and encryption round-trip", workflow)
        self.assertIn("Start-Process -FilePath $exePath -ArgumentList '--self-test-imports' -Wait -PassThru", workflow)
        self.assertIn("$process.ExitCode", workflow)
        self.assertIn("if ($process.ExitCode -ne 0)", workflow)
        self.assertIn("Verify custom PyInstaller bootloader is installed", workflow)

    def test_runtime_crypto_self_test_passes_in_build_environment(self):
        from main import _packaged_import_self_test
        self.assertEqual(_packaged_import_self_test(), 0)

    def test_cryptography_is_declared_as_a_build_dependency(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        build_script = (ROOT / "BUILD_EXE.bat").read_text(encoding="utf-8-sig")
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("cryptography", requirements)
        self.assertIn("cffi>=2.0.0", requirements)
        self.assertIn("cryptography", build_script)
        self.assertIn("Verify active Python and install dependencies", workflow)

    def test_source_launcher_bootstraps_missing_cryptography(self):
        launcher = (ROOT / "START.bat").read_text(encoding="utf-8-sig")
        self.assertIn('import cryptography, cffi, _cffi_backend', launcher)
        self.assertIn('pip install --upgrade "cryptography>=42.0" "cffi>=2.0.0"', launcher)
        self.assertIn(":crypto_install_failed", launcher)

    def test_windows_version_resource_is_418(self):
        source = (ROOT / "version_info.txt").read_text(encoding="utf-8")
        self.assertIn("filevers=(4,1,8,3)", source)
        self.assertIn("prodvers=(4,1,8,3)", source)
        self.assertIn("FileVersion', '4.1.8.3'", source)
        self.assertIn("ProductVersion', '4.1.8.3'", source)



    def test_build_uses_actions_selected_python_interpreter(self):
        build = (ROOT / "BUILD_EXE.bat").read_text(encoding="utf-8-sig")
        self.assertIn("if defined MOYA_BUILD_PYTHON", build)
        self.assertIn("set \"PY=%MOYA_BUILD_PYTHON%\"", build)
        self.assertIn("-m pip install --upgrade -r requirements.txt", build)

    def test_workflow_uses_distinct_verified_name_and_node24_actions(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("Build Windows release — verified 4.1.8.3", workflow)
        self.assertIn("Verify custom PyInstaller bootloader is installed", workflow)
        self.assertIn("actions/checkout@v7", workflow)
        self.assertIn("actions/setup-python@v7.1.0", workflow)
        self.assertNotIn("actions/checkout@v4", workflow)
        self.assertNotIn("actions/setup-python@v5", workflow)
        self.assertNotIn("softprops/action-gh-release@v2", workflow)

    def test_workflow_publishes_only_after_frozen_exe_smoke_test(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertLess(workflow.index("Verify x64 VC runtime and Universal CRT DLLs are embedded"), workflow.index("Verify frozen EXE imports"))
        self.assertLess(workflow.index("Verify frozen EXE imports"), workflow.index("Create release or replace assets"))
        self.assertIn("MoyaIshka.exe.sha256", workflow)

    def test_self_improvement_retries_when_model_does_not_return_diff(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("except ValueError as first_error", source)
        self.assertIn("repaired_response = self._current_model_call(repair_messages)", source)
        self.assertIn("Не добавляй пояснений, Markdown или полный файл", source)

    def test_portable_exe_is_checked_without_python_on_path(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("Verify EXE starts without Python on PATH", workflow)
        self.assertIn("$startInfo.Environment['PATH'] = ", workflow)
        self.assertIn("'python311.dll'", workflow)



    def test_push_to_main_does_not_create_or_replace_a_release(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        release_step = workflow[workflow.index("- name: Create release or replace assets"): ]
        self.assertIn("if: startsWith(github.ref, 'refs/tags/')", release_step)
        self.assertNotIn("github.ref == 'refs/heads/main'", release_step)
        self.assertNotIn("$tag = if ($env:GITHUB_REF", release_step)
if __name__ == "__main__":
    unittest.main()

