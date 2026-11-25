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

    def test_python_vc_runtime_is_bundled_without_redundant_windows_ucrt(self):
        build = (ROOT / "BUILD_EXE.bat").read_text(encoding="utf-8-sig")
        helper = (ROOT / "prepare_python_runtime.py").read_text(encoding="utf-8")
        self.assertIn("%PY% prepare_python_runtime.py", build)
        self.assertIn('--add-binary "build_runtime\\python_runtime;."', build)
        self.assertIn("vcruntime140.dll", helper)
        self.assertIn("vcruntime140_1.dll", helper)
        self.assertIn('"vcruntime140.dll"', helper)
        self.assertIn('"vcruntime140_1.dll"', helper)
        self.assertIn('"msvcp140.dll"', helper)
        self.assertNotIn('"ucrtbase.dll"', helper)
        self.assertNotIn('"api-ms-win-crt-*.dll"', helper)
        audit = (ROOT / "verify_pe_dependencies.py").read_text(encoding="utf-8")
        self.assertIn("CArchiveReader", audit)
        self.assertIn("DIRECTORY_ENTRY_DELAY_IMPORT", audit)
        self.assertIn("LoadLibraryExW", audit)
        self.assertIn("LOAD_LIBRARY_SEARCH_SYSTEM32", audit)

    def test_runtime_dll_collector_copies_only_vc_runtime(self):
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
            copied = collect_runtime_dlls([source], destination)
            self.assertEqual(
                {item.name for item in copied},
                {"vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"},
            )
            self.assertFalse((destination / "ucrtbase.dll").exists())
            self.assertFalse((destination / "api-ms-win-crt-runtime-l1-1-0.dll").exists())
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
        self.assertIn('"vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"', source)
        self.assertNotIn("ucrtbase.dll", source)
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
        self.assertIn("Verify app-local VC runtime DLLs are embedded", workflow)
        self.assertIn("pyi-archive_viewer -l", workflow)
        self.assertIn("Verify frozen EXE imports and encryption round-trip", workflow)
        self.assertIn("Start-Process -FilePath $exePath -ArgumentList '--self-test-imports' -Wait -PassThru", workflow)
        self.assertIn("$process.ExitCode", workflow)
        self.assertIn("if ($process.ExitCode -ne 0)", workflow)
        self.assertIn("Audit python311.dll PE dependency tree on Windows", workflow)
        self.assertIn("runs-on: windows-2022", workflow)
        self.assertNotIn("build_patched_pyinstaller.py", workflow)

    def test_windows_and_voice_page_has_mousewheel_scroll_container(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        page_start = source.index("    def _build_windows_page(self) -> None:")
        page_end = source.index("    def _build_workshop_page(self) -> None:", page_start)
        windows_page = source[page_start:page_end]
        self.assertIn("scroll_canvas = tk.Canvas(page", windows_page)
        self.assertIn('ttk.Scrollbar(page, orient="vertical", command=scroll_canvas.yview)', windows_page)
        self.assertIn("body = ttk.Frame(scroll_canvas)", windows_page)
        self.assertIn("scroll_canvas.create_window", windows_page)
        self.assertIn('"<Configure>"', windows_page)
        self.assertIn("self.windows_page_canvas = scroll_canvas", windows_page)
        self.assertIn("if isinstance(ancestor, tk.Canvas):", source)
        self.assertIn("ancestor.yview_scroll(steps, \"units\")", source)

    def test_native_dependency_audit_is_required_before_release(self):
        audit = (ROOT / "verify_pe_dependencies.py").read_text(encoding="utf-8")
        build = (ROOT / "BUILD_EXE.bat").read_text(encoding="utf-8-sig")
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("CArchiveReader", audit)
        self.assertIn("DIRECTORY_ENTRY_DELAY_IMPORT", audit)
        self.assertIn("LoadLibraryExW", audit)
        self.assertIn("LOAD_WITH_ALTERED_SEARCH_PATH", audit)
        self.assertIn("%PY% verify_pe_dependencies.py", build)
        self.assertIn("Audit python311.dll PE dependency tree on Windows", workflow)
        self.assertIn("python verify_pe_dependencies.py dist/MoyaIshka.exe", workflow)

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
        self.assertIn("Audit python311.dll PE dependency tree on Windows", workflow)
        self.assertIn("runs-on: windows-2022", workflow)
        self.assertNotIn("build_patched_pyinstaller.py", workflow)
        self.assertIn("actions/checkout@v7", workflow)
        self.assertIn("actions/setup-python@v7.1.0", workflow)
        self.assertNotIn("actions/checkout@v4", workflow)
        self.assertNotIn("actions/setup-python@v5", workflow)
        self.assertNotIn("softprops/action-gh-release@v2", workflow)

    def test_workflow_publishes_only_after_frozen_exe_smoke_test(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertLess(workflow.index("Verify app-local VC runtime DLLs are embedded"), workflow.index("Verify frozen EXE imports"))
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



    def test_push_to_main_does_not_create_or_replace_a_release_without_hotfix_marker(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        release_step = workflow[workflow.index("- name: Create release or replace assets"): ]
        self.assertIn("startsWith(github.ref, 'refs/tags/')", release_step)
        self.assertIn("contains(github.event.head_commit.message, '[refresh-v4.1.8.3]')", release_step)
        self.assertIn("elseif ($env:GITHUB_REF -eq 'refs/heads/main')", release_step)
        self.assertIn("Refusing to create a new release from main", release_step)

    def test_hotfix_marker_refreshes_existing_v4183_release_and_tag_only(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("RELEASE_TAG: ${{ startsWith(github.ref, 'refs/tags/') && github.ref_name || 'v4.1.8.3' }}", workflow)
        self.assertIn("- name: Update v4.1.8.3 tag to validated root source", workflow)
        self.assertIn("git push --force origin refs/tags/v4.1.8.3", workflow)
        self.assertIn("git tag --force --annotate --message", workflow)
        self.assertIn('git config user.name "github-actions[bot]"', workflow)
        self.assertIn('git config user.email "41898282+github-actions[bot]@users.noreply.github.com"', workflow)
        self.assertLess(workflow.index("Create release or replace assets"), workflow.index("Update v4.1.8.3 tag to validated root source"))
if __name__ == "__main__":
    unittest.main()

