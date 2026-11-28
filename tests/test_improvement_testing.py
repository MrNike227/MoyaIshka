import hashlib
import json
import os
import tempfile
import unittest
import subprocess
import sys
import re
from unittest.mock import patch
from pathlib import Path
from improvement_delivery import save_change_bundle, save_multi_change_bundle
from improvement_testing import ProposalTestError, _is_windows_sandbox_guest, run_proposal_tests_cli, test_change_bundle

def run_local_test_executor(project_dir: Path, result_dir: Path, timeout_seconds: int) -> dict:
    """Test-only stand-in; production never uses a host-side test runner."""
    process = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
        cwd=project_dir, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout_seconds,
    )
    combined = process.stdout + "\n" + process.stderr
    match = re.search(r"Ran\s+(\d+)\s+tests?", combined)
    tests_run = int(match.group(1)) if match else 0
    return {
        "passed": process.returncode == 0 and tests_run > 0,
        "tests_exit_code": process.returncode,
        "tests_run": tests_run,
        "failures": 0 if process.returncode == 0 else 1,
        "errors": 0,
        "stdout": process.stdout,
        "stderr": process.stderr,
        "error": "В копии проекта не найдено ни одного unit-теста." if tests_run == 0 else "",
        "sandboxed": False,
    }


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
            f"VALUE = {value}\n",
            f"--- a/sample.py\n+++ b/sample.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = {value}",
        )

    def test_passed_proposal_runs_in_copy_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"; root.mkdir(); self.make_project(root)
            bundle = self.make_bundle(temp, 2)
            result = test_change_bundle(bundle, root, sandbox_executor=run_local_test_executor)
            self.assertTrue(result["passed"], result)
            self.assertEqual((root / "sample.py").read_text(encoding="utf-8"), "VALUE = 1\n")
            self.assertTrue((bundle / "test_report.json").is_file())
            self.assertEqual(json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))["test"]["status"], "passed")

    def test_failed_proposal_is_reported_without_source_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"; root.mkdir(); self.make_project(root)
            bundle = self.make_bundle(temp, 3)
            result = test_change_bundle(bundle, root, sandbox_executor=run_local_test_executor)
            self.assertFalse(result["passed"])
            self.assertEqual(result["tests_exit_code"], 1)
            self.assertEqual((root / "sample.py").read_text(encoding="utf-8"), "VALUE = 1\n")

    def test_pass_requires_zero_failures_and_errors_even_if_executor_claims_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            self.make_project(root)
            bundle = self.make_bundle(temp, 2)

            def inconsistent_executor(_project, _results, _timeout):
                return {
                    "passed": True, "tests_exit_code": 0, "tests_run": 1,
                    "failures": 1, "errors": 0, "sandboxed": False,
                }

            result = test_change_bundle(bundle, root, sandbox_executor=inconsistent_executor)
            self.assertFalse(result["passed"])

    def test_production_pass_requires_sandbox_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            self.make_project(root)
            bundle = self.make_bundle(temp, 2)
            with patch("improvement_testing._run_windows_sandbox", return_value={
                "passed": True, "tests_exit_code": 0, "tests_run": 1,
                "failures": 0, "errors": 0, "sandboxed": False,
            }):
                result = test_change_bundle(bundle, root)
            self.assertFalse(result["passed"])
            self.assertFalse(result["sandboxed"])

    def test_source_hash_accepts_crlf_line_endings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"; root.mkdir(); self.make_project(root)
            bundle = self.make_bundle(temp, 2)
            (root / "sample.py").write_bytes(b"VALUE = 1\r\n")
            result = test_change_bundle(bundle, root, sandbox_executor=run_local_test_executor)
            self.assertTrue(result["passed"], result)
            self.assertEqual((root / "sample.py").read_bytes(), b"VALUE = 1\r\n")

    def test_stale_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"; root.mkdir(); self.make_project(root)
            bundle = self.make_bundle(temp, 2)
            (root / "sample.py").write_text("VALUE = 9\n", encoding="utf-8")
            with self.assertRaisesRegex(ProposalTestError, "уже изменился"):
                test_change_bundle(bundle, root)

    def test_mismatched_diff_is_rejected_even_when_its_hash_is_updated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"; root.mkdir(); self.make_project(root)
            bundle = self.make_bundle(temp, 2)
            patch = (
                "--- a/sample.py\n+++ b/sample.py\n@@ -1 +1 @@\n"
                "-VALUE = 1\n+VALUE = 99\n"
            ).encode("utf-8")
            (bundle / "changes.patch").write_bytes(patch)
            manifest_path = bundle / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["patch"]["size"] = len(patch)
            manifest["patch"]["sha256"] = hashlib.sha256(patch).hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ProposalTestError, "diff не соответствует"):
                test_change_bundle(bundle, root)

    def test_empty_test_suite_is_not_reported_as_passed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"; root.mkdir(); self.make_project(root)
            (root / "tests" / "test_sample.py").unlink()
            bundle = self.make_bundle(temp, 2)
            result = test_change_bundle(bundle, root, sandbox_executor=run_local_test_executor)
            self.assertFalse(result["passed"])
            self.assertEqual(result["tests_run"], 0)
            self.assertIn("не найдено", result["error"])

    def test_non_object_manifest_is_rejected_cleanly(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"; root.mkdir(); self.make_project(root)
            bundle = self.make_bundle(temp, 2)
            (bundle / "manifest.json").write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ProposalTestError, "неверный формат"):
                test_change_bundle(bundle, root)

    def test_tampered_proposal_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"; root.mkdir(); self.make_project(root)
            bundle = self.make_bundle(temp, 2)
            (bundle / "files" / "sample.py").write_text("VALUE = 100\n", encoding="utf-8")
            with self.assertRaisesRegex(ProposalTestError, "SHA-256"):
                test_change_bundle(bundle, root)

    def test_production_path_fails_closed_when_sandbox_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"; root.mkdir(); self.make_project(root)
            bundle = self.make_bundle(temp, 2)
            with patch("improvement_testing._run_windows_sandbox", side_effect=ProposalTestError("Windows Sandbox недоступна")):
                result = test_change_bundle(bundle, root)
            self.assertFalse(result["passed"])
            self.assertFalse(result["sandboxed"])
            self.assertIn("Windows Sandbox недоступна", result["error"])

    def test_sandbox_payload_omits_local_secrets_and_databases(self):
        from improvement_testing import _ignore_sandbox_copy
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "main.py").write_text("value = 1", encoding="utf-8")
            (root / ".env").write_text("TOKEN=secret", encoding="utf-8")
            (root / "config.json").write_text("{}", encoding="utf-8")
            (root / "private.db").write_bytes(b"database")
            ignored = _ignore_sandbox_copy(str(root), [p.name for p in root.iterdir()])
            self.assertEqual(ignored, {".env", "config.json", "private.db"})



    def make_multi_bundle(self, temp, value=2):
        test_text = (
            "import unittest\nimport sample\n\n"
            "class SampleFeatureTests(unittest.TestCase):\n"
            "    def test_feature_value(self):\n"
            f"        self.assertEqual(sample.VALUE, {value})\n"
        )
        patch = (
            "--- a/sample.py\n+++ b/sample.py\n@@ -1 +1 @@\n"
            "-VALUE = 1\n+VALUE = " + str(value) + "\n"
            "--- /dev/null\n+++ b/tests/test_sample_feature.py\n@@ -0,0 +1,6 @@\n"
            "+import unittest\n+import sample\n+\n"
            "+class SampleFeatureTests(unittest.TestCase):\n"
            "+    def test_feature_value(self):\n"
            f"+        self.assertEqual(sample.VALUE, {value})\n"
        )
        return save_multi_change_bundle(
            Path(temp) / "Updates",
            [
                {"path": "sample.py", "original_text": "VALUE = 1\n", "updated_text": f"VALUE = {value}\n"},
                {"path": "tests/test_sample_feature.py", "original_text": None, "updated_text": test_text},
            ],
            patch,
        )

    def test_multi_file_bundle_runs_new_test_with_feature_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            self.make_project(root)
            bundle = self.make_multi_bundle(temp, 2)
            observed = {}

            def executor(project_dir, result_dir, timeout_seconds):
                observed["test_exists"] = (project_dir / "tests" / "test_sample_feature.py").is_file()
                return run_local_test_executor(project_dir, result_dir, timeout_seconds)

            result = test_change_bundle(bundle, root, sandbox_executor=executor)
            self.assertTrue(result["passed"], result)
            self.assertEqual(result["checked_files"], 2)
            self.assertTrue(observed["test_exists"])
            self.assertEqual(result["tests_run"], 2)
            self.assertFalse((root / "tests" / "test_sample_feature.py").exists())
            self.assertEqual((root / "sample.py").read_text(encoding="utf-8"), "VALUE = 1\n")

    def test_multi_file_bundle_rejects_diff_missing_test_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            self.make_project(root)
            bundle = self.make_multi_bundle(temp, 2)
            patch = (
                "--- a/sample.py\n+++ b/sample.py\n@@ -1 +1 @@\n"
                "-VALUE = 1\n+VALUE = 2\n"
            ).encode("utf-8")
            (bundle / "changes.patch").write_bytes(patch)
            manifest_path = bundle / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["patch"]["size"] = len(patch)
            manifest["patch"]["sha256"] = hashlib.sha256(patch).hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ProposalTestError, "Состав diff"):
                test_change_bundle(bundle, root)

    def test_multi_file_bundle_rejects_new_test_that_already_exists(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            self.make_project(root)
            bundle = self.make_multi_bundle(temp, 2)
            (root / "tests" / "test_sample_feature.py").write_text("VALUE = 99\n", encoding="utf-8")
            with self.assertRaisesRegex(ProposalTestError, "уже существует"):
                test_change_bundle(bundle, root)


    def test_guest_runner_rejects_non_sandbox_account_even_with_marker(self):
        with patch("improvement_testing.os.name", "nt"), patch.dict(
            os.environ, {"MOYA_WINDOWS_SANDBOX": "1"}, clear=False,
        ), patch("improvement_testing._current_windows_user_sid", return_value=b"host-user"), patch(
            "improvement_testing._lookup_local_windows_account_sid", return_value=b"wdag-user",
        ):
            self.assertFalse(_is_windows_sandbox_guest())

    def test_guest_runner_requires_matching_guest_sid_and_marker(self):
        with patch("improvement_testing.os.name", "nt"), patch.dict(
            os.environ, {"MOYA_WINDOWS_SANDBOX": "1"}, clear=False,
        ), patch("improvement_testing._current_windows_user_sid", return_value=b"wdag-user"), patch(
            "improvement_testing._lookup_local_windows_account_sid", return_value=b"wdag-user",
        ):
            self.assertTrue(_is_windows_sandbox_guest())
        with patch("improvement_testing.os.name", "nt"), patch.dict(
            os.environ, {}, clear=False,
        ), patch("improvement_testing._current_windows_user_sid", return_value=b"wdag-user"), patch(
            "improvement_testing._lookup_local_windows_account_sid", return_value=b"wdag-user",
        ):
            os.environ.pop("MOYA_WINDOWS_SANDBOX", None)
            self.assertFalse(_is_windows_sandbox_guest())

    def test_cli_refuses_host_side_execution_without_sandbox(self):
        with patch("improvement_testing._is_windows_sandbox_guest", return_value=False):
            self.assertEqual(run_proposal_tests_cli("unused-project", "unused-result.json"), 3)


if __name__ == "__main__":
    unittest.main()
