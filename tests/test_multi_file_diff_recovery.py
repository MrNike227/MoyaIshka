import json
import unittest

from self_improvement import (
    apply_multi_file_unified_diff,
    generate_validated_multi_file_unified_diff,
    split_multi_file_unified_diff,
)


class MultiFileDiffRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.sources = {
            "feature.py": "VALUE = 1\n",
            "tests/test_feature.py": None,
        }
        self.valid_patch = (
            "--- a/feature.py\n+++ b/feature.py\n@@ -1,1 +1,1 @@\n"
            "-VALUE = 1\n+VALUE = 2\n"
            "--- /dev/null\n+++ b/tests/test_feature.py\n@@ -0,0 +1,3 @@\n"
            "+import unittest\n+import feature\n+class FeatureTests(unittest.TestCase): pass\n"
        )

    def test_extracts_diff_from_explanation_and_markdown_fence(self):
        response = "Вот исправления:\n\n" + chr(96) * 3 + "diff\n" + self.valid_patch + chr(96) * 3 + "\nГотово."
        updated = apply_multi_file_unified_diff(self.sources, response)
        self.assertEqual(updated["feature.py"], "VALUE = 2\n")
        self.assertIn("class FeatureTests", updated["tests/test_feature.py"])

    def test_combines_separate_fenced_diff_blocks(self):
        first, second = self.valid_patch.split("--- /dev/null", 1)
        fence = chr(96) * 3
        response = (
            "Файл программы:\n" + fence + "diff\n" + first.rstrip() + "\n" + fence + "\n"
            "Файл теста:\n" + fence + "patch\n--- /dev/null" + second + fence + "\n"
        )
        updated = apply_multi_file_unified_diff(self.sources, response)
        self.assertEqual(set(updated), set(self.sources))

    def test_tolerates_blank_lines_and_git_metadata_between_headers(self):
        patch = (
            "--- a/feature.py\n\nindex abc..def 100644\n+++ b/feature.py\n"
            "@@ -1,1 +1,1 @@\n-VALUE = 1\n+VALUE = 2\n"
        )
        self.assertIn("feature.py", split_multi_file_unified_diff(patch))
        updated = apply_multi_file_unified_diff({"feature.py": "VALUE = 1\n"}, patch)
        self.assertEqual(updated["feature.py"], "VALUE = 2\n")

    def test_recovers_after_first_response_has_no_diff_headers(self):
        responses = ["Готово, я исправил код. Изменения ниже.", self.valid_patch]
        calls = []

        def model_call(messages, attempt):
            calls.append([dict(message) for message in messages])
            return responses[attempt]

        result = generate_validated_multi_file_unified_diff(
            self.sources,
            [{"role": "user", "content": "Измени функцию и тест"}],
            model_call,
            max_attempts=3,
        )
        self.assertIn("--- a/feature.py", result)
        self.assertIn("--- /dev/null", result)
        self.assertEqual(len(calls), 2)
        self.assertIn("обязательно выведи отдельную пару заголовков", calls[1][-1]["content"])

    def test_accepts_git_diff_without_standard_header_pairs(self):
        patch = (
            "diff --git a/feature.py b/feature.py\n"
            "index abc..def 100644\n"
            "@@ -1,1 +1,1 @@\n-VALUE = 1\n+VALUE = 2\n"
        )
        parsed = split_multi_file_unified_diff(patch)
        self.assertIn("--- a/feature.py", parsed["feature.py"])
        updated = apply_multi_file_unified_diff({"feature.py": "VALUE = 1\n"}, patch)
        self.assertEqual(updated["feature.py"], "VALUE = 2\n")

    def test_json_response_with_diff_field_is_supported(self):
        response = json.dumps({"diff": self.valid_patch})
        self.assertEqual(
            set(split_multi_file_unified_diff(response)),
            set(self.sources),
        )

    def test_reports_useful_diagnostic_when_response_has_no_diff(self):
        with self.assertRaisesRegex(ValueError, "отсутствуют пары заголовков"):
            split_multi_file_unified_diff("Я не могу подготовить изменения.")


if __name__ == "__main__":
    unittest.main()
