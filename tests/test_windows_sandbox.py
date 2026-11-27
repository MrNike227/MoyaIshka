import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from windows_sandbox import create_sandbox_config, get_windows_sandbox_status, request_windows_sandbox_install


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
            self.assertEqual(values["Networking"], "Disable")
            self.assertEqual(values["VGpu"], "Disable")
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


if __name__ == "__main__":
    unittest.main()
