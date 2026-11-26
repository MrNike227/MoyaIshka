import json
import tempfile
import unittest
from pathlib import Path
from improvement_delivery import save_change_bundle
from improvement_testing import ProposalTestError, test_change_bundle

class ImprovementTestingTests(unittest.TestCase):
    def make_project(self, root):
        (root / "tests").mkdir(parents=True)
        (root / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "tests" / "test_sample.py").write_text(
            "import unittest\nimport sample\nclass SampleTests(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(sample.VALUE, 2)\n", encoding="utf-8")

    def make_bundle(self, temp, value):
        return save_change_bundle(
            Path(temp) / "Updates", "sample.py", "VALUE = 1\n",
            f"VALUE = {value}\n", "--- a/sample.py\n+++ b/sample.py\n")

    def test_passed_proposal_runs_in_copy_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"; root.mkdir(); self.make_project(root)
            bundle = self.make_bundle(temp, 2)
            result = test_change_bundle(bundle, root)
            self.assertTrue(result["passed"], result)
            self.assertEqual((root / "sample.py").read_text(encoding="utf-8"), "VALUE = 1\n")
            self.assertTrue((bundle / "test_report.json").is_file())
            self.assertEqual(json.loads((bundle / "manifest.json").read_text())["test"]["status"], "passed")

    def test_failed_proposal_is_reported_without_source_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"; root.mkdir(); self.make_project(root)
            bundle = self.make_bundle(temp, 3)
            result = test_change_bundle(bundle, root)
            self.assertFalse(result["passed"])
            self.assertEqual(result["tests_exit_code"], 1)
            self.assertEqual((root / "sample.py").read_text(encoding="utf-8"), "VALUE = 1\n")

    def test_stale_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"; root.mkdir(); self.make_project(root)
            bundle = self.make_bundle(temp, 2)
            (root / "sample.py").write_text("VALUE = 9\n", encoding="utf-8")
            with self.assertRaisesRegex(ProposalTestError, "уже изменился"):
                test_change_bundle(bundle, root)

    def test_tampered_proposal_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"; root.mkdir(); self.make_project(root)
            bundle = self.make_bundle(temp, 2)
            (bundle / "files" / "sample.py").write_text("VALUE = 100\n", encoding="utf-8")
            with self.assertRaisesRegex(ProposalTestError, "SHA-256"):
                test_change_bundle(bundle, root)

if __name__ == "__main__":
    unittest.main()
