import json
import os
import tempfile
import unittest
import inspect
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path
from xml.etree import ElementTree

from windows_sandbox import (
    _is_reparse_point, create_sandbox_config, get_windows_sandbox_status,
    record_windows_sandbox_install, request_windows_sandbox_install,
    wait_for_install_result,
)


class WindowsSandboxTests(unittest.TestCase):
    def test_config_disables_network_clipboard_and_host_devices(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            readonly = root / "readonly"
            results = root / "results"
            readonly.mkdir()
            results.mkdir()
            config = create_sandbox_config(readonly, results, root / "test.wsb")
            tree = ElementTree.parse(config)
            values = {child.tag: (child.text or "").strip() for child in tree.getroot()}
            self.assertEqual(values["MemoryInMB"], "4096")
            self.assertEqual(values["Networking"], "Disable")
            self.assertEqual(values["VGpu"], "Disable")
            self.assertEqual(values["ProtectedClient"], "Enable")
            self.assertEqual(values["ClipboardRedirection"], "Disable")
            self.assertEqual(values["AudioInput"], "Disable")
            self.assertEqual(values["VideoInput"], "Disable")
            self.assertEqual(values["PrinterRedirection"], "Disable")
            mappings = tree.find("MappedFolders")
            self.assertIsNotNone(mappings)
            mapped = mappings.findall("MappedFolder")
            self.assertEqual(len(mapped), 2)
            self.assertEqual(mapped[0].findtext("ReadOnly"), "true")
            self.assertEqual(mapped[1].findtext("ReadOnly"), "false")
            self.assertEqual(mapped[0].findtext("SandboxFolder"), r"C:\MoyaTest\ReadOnly")
            self.assertEqual(mapped[1].findtext("SandboxFolder"), r"C:\MoyaTest\Result")

    def test_config_rejects_nested_host_mappings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            readonly = root / "readonly"
            results = readonly / "results"
            results.mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "отдельной"):
                create_sandbox_config(readonly, results, root / "test.wsb")

    def test_sandbox_install_is_not_started_outside_windows(self):
        if __import__("os").name == "nt":
            self.skipTest("Only verifies the non-Windows guard")
        status = get_windows_sandbox_status()
        self.assertFalse(status["supported"])
        with self.assertRaisesRegex(RuntimeError, "только в Windows"):
            request_windows_sandbox_install()


    def test_status_check_does_not_call_admin_only_optional_feature_cmdlets(self):
        source = inspect.getsource(get_windows_sandbox_status)
        self.assertNotIn("Get-WindowsOptionalFeature", source)
        self.assertIn("Get-CimInstance Win32_OperatingSystem", source)
        self.assertIn("HypervisorPresent", source)

    def test_install_result_is_persisted_for_reboot_detection(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, {"APPDATA": temp}):
                path = record_windows_sandbox_install({
                    "success": True,
                    "state": "EnablePending",
                    "restart_required": True,
                    "boot_time": "2026-11-27T06:00:00.0000000Z",
                    "error": "",
                    "unexpected": "must not be persisted",
                })
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data["schema"], 1)
                self.assertTrue(data["success"])
                self.assertEqual(data["state"], "EnablePending")
                self.assertTrue(data["restart_required"])
                self.assertIn("2026-11-27", data["boot_time"])
                self.assertNotIn("unexpected", data)



    def test_missing_install_result_is_not_a_reparse_point(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "install_result.json"
            self.assertFalse(missing.exists())
            self.assertFalse(_is_reparse_point(missing))

    def test_wait_for_install_result_waits_until_powershell_creates_file(self):
        with tempfile.TemporaryDirectory() as temp:
            result_path = Path(temp) / "install_result.json"
            expected = {
                "success": True,
                "state": "Enabled",
                "restart_required": False,
                "boot_time": "2026-11-27T06:00:00.0000000Z",
                "error": "",
            }

            def create_result(_delay):
                result_path.write_text(json.dumps(expected), encoding="utf-8")

            with patch("windows_sandbox.time.sleep", side_effect=create_result):
                result = wait_for_install_result(result_path, timeout_seconds=30)

            self.assertEqual(result, expected)

    def test_windows_reparse_points_are_rejected(self):
        with patch("windows_sandbox.Path.lstat", return_value=SimpleNamespace(st_file_attributes=0x400)):
            self.assertTrue(_is_reparse_point(Path("not-followed")))
        with tempfile.TemporaryDirectory() as temp:
            normal_file = Path(temp) / "report.json"
            normal_file.write_text("{}", encoding="utf-8")
            self.assertFalse(_is_reparse_point(normal_file))



if __name__ == "__main__":
    unittest.main()
