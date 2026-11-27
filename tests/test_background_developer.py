import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from background_developer import run_background_development, _load_recent_development_lessons, _has_explicit_reviewer_approval


def test_executor(project_dir: Path, result_dir: Path, timeout_seconds: int) -> dict:
    process = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
        cwd=project_dir, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout_seconds,
    )
    output = process.stdout + "\n" + process.stderr
    match = re.search(r"Ran\s+(\d+)\s+tests?", output)
    tests_run = int(match.group(1)) if match else 0
    return {
        "passed": process.returncode == 0 and tests_run > 0,
        "tests_exit_code": process.returncode,
        "tests_run": tests_run,
        "failures": 0 if process.returncode == 0 else 1,
        "errors": 0,
        "stdout": output[-12000:],
        "stderr": "",
        "sandboxed": False,
    }


class BackgroundDeveloperTests(unittest.TestCase):
    def test_reviewer_approval_requires_json_boolean_true(self):
        self.assertTrue(_has_explicit_reviewer_approval({"approved": True, "findings": []}))
        self.assertFalse(_has_explicit_reviewer_approval({"approved": "true", "findings": []}))
        self.assertFalse(_has_explicit_reviewer_approval({"approved": "false", "findings": []}))
        self.assertFalse(_has_explicit_reviewer_approval({"approved": 1, "findings": []}))
        self.assertFalse(_has_explicit_reviewer_approval({"approved": True, "findings": ["Проблема"]}))
        self.assertFalse(_has_explicit_reviewer_approval({"approved": True, "findings": "нет"}))
        self.assertFalse(_has_explicit_reviewer_approval([]))
    def make_project(self, root: Path) -> None:
        (root / "tests").mkdir(parents=True)
        (root / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "consumer.py").write_text("from feature import VALUE\nRESULT = VALUE\n", encoding="utf-8")
        (root / "tests" / "test_feature.py").write_text(
            "import unittest\nimport feature\n\n"
            "class FeatureTests(unittest.TestCase):\n"
            "    def test_old_value(self): self.assertEqual(feature.VALUE, 1)\n",
            encoding="utf-8",
        )

    def test_background_developer_reuses_lessons_from_failed_attempts(self):
        with tempfile.TemporaryDirectory() as temp:
            updates = Path(temp) / "Updates"
            history = updates / "Analysis" / "background_development_history.json"
            history.parent.mkdir(parents=True)
            history.write_text(json.dumps({
                "schema": 1,
                "items": [
                    {"passed": False, "review_approved": False, "title": "Плохой diff",
                     "error": "Тесты не прошли", "review_findings": ["Нарушен контракт API"]},
                    {"passed": True, "review_approved": True, "title": "Успешное изменение"},
                ],
            }, ensure_ascii=False), encoding="utf-8")
            lessons = _load_recent_development_lessons(updates)
            self.assertEqual(len(lessons), 1)
            self.assertIn("Тесты не прошли", lessons[0])
            self.assertIn("Нарушен контракт API", lessons[0])

    def test_background_developer_generates_feature_and_matching_test_together(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            self.make_project(root)
            responses = [
                json.dumps({
                    "suggestions": [{
                        "title": "Обновить значение функции",
                        "problem": "Используется старое значение",
                        "current": "VALUE = 1",
                        "proposed": "VALUE = 2",
                        "benefit": "Проверка нового поведения",
                    }]
                }, ensure_ascii=False),
                (
                    "--- a/feature.py\n+++ b/feature.py\n@@ -1 +1 @@\n"
                    "-VALUE = 1\n+VALUE = 2\n"
                    "--- a/tests/test_feature.py\n+++ b/tests/test_feature.py\n"
                    "@@ -4,2 +4,2 @@\n"
                    " class FeatureTests(unittest.TestCase):\n"
                    "-    def test_old_value(self): self.assertEqual(feature.VALUE, 1)\n"
                    "+    def test_new_value(self): self.assertEqual(feature.VALUE, 2)\n"
                ),
                json.dumps({
                    "approved": True,
                    "summary": "Изменение локальное, API сохранён, тест проверяет новое поведение.",
                    "findings": [],
                }, ensure_ascii=False),
            ]
            calls = []

            def model_call(messages, attempt):
                calls.append(messages)
                return responses[len(calls) - 1]

            result = run_background_development(
                root,
                "feature.py",
                Path(temp) / "Updates",
                model_call,
                version="4.2.6",
                sandbox_executor=test_executor,
            )

            self.assertTrue(result["passed"], result)
            self.assertTrue(result["review_approved"], result)
            self.assertIn("API сохранён", result["review_summary"])
            self.assertEqual(result["files"], ["feature.py", "tests/test_feature.py"])
            self.assertEqual(result["tests_run"], 1)
            self.assertIn("tests/test_feature.py", calls[1][-1]["content"])
            self.assertIn("consumer.py", calls[0][-1]["content"])
            self.assertIn("consumer.py", calls[1][-1]["content"])
            self.assertIn("consumer.py", result["dependency_report"])
            self.assertEqual((root / "feature.py").read_text(encoding="utf-8"), "VALUE = 1\n")
            self.assertIn(
                "test_old_value",
                (root / "tests" / "test_feature.py").read_text(encoding="utf-8"),
            )
            bundle = Path(result["bundle_path"])
            self.assertTrue((bundle / "files" / "tests" / "test_feature.py").is_file())
            self.assertTrue((bundle / "test_report.json").is_file())


if __name__ == "__main__":
    unittest.main()
