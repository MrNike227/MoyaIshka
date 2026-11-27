import json
import unittest

from self_improvement import (
    apply_multi_file_unified_diff,
    build_multi_file_repair_context,
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
            allow_subset=True,
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

    def test_retry_can_omit_unchanged_context_modules(self):
        sources = {
            "feature.py": "VALUE = 1\n",
            "tests/test_feature.py": None,
            "consumer.py": "from feature import VALUE\nRESULT = VALUE\n",
        }
        bad = (
            "--- a/feature.py\n+++ b/feature.py\n@@ -1 +1 @@\n"
            "-NOT_THE_SOURCE\n+VALUE = 2\n"
        )
        good = (
            "--- a/feature.py\n+++ b/feature.py\n@@ -1 +1 @@\n"
            "-VALUE = 1\n+VALUE = 2\n"
            "--- /dev/null\n+++ b/tests/test_feature.py\n@@ -0,0 +1,2 @@\n"
            "+import unittest\n+class FeatureTests(unittest.TestCase): pass\n"
        )
        calls = []

        def model_call(messages, attempt):
            calls.append(messages)
            return [bad, good][attempt]

        result = generate_validated_multi_file_unified_diff(
            sources,
            [{"role": "user", "content": "Измени модуль и добавь тест"}],
            model_call,
            max_attempts=3,
            allow_subset=True,
        )
        self.assertIn("--- a/feature.py", result)
        self.assertIn("--- /dev/null", result)
        self.assertNotIn("consumer.py", result)
        self.assertIn("неизменённые файлы можно не включать", calls[1][-1]["content"])

    def test_context_only_dependency_files_may_be_omitted_from_diff(self):
        sources = {
            "feature.py": "VALUE = 1\n",
            "tests/test_feature.py": "import unittest\nVALUE = 1\n",
            "consumer.py": "from feature import VALUE\nRESULT = VALUE\n",
        }
        patch = (
            "--- a/feature.py\n+++ b/feature.py\n@@ -1 +1 @@\n"
            "-VALUE = 1\n+VALUE = 2\n"
            "--- a/tests/test_feature.py\n+++ b/tests/test_feature.py\n@@ -2 +2 @@\n"
            "-VALUE = 1\n+VALUE = 2\n"
        )
        updated = apply_multi_file_unified_diff(sources, patch, allow_subset=True)
        self.assertEqual(set(updated), {"feature.py", "tests/test_feature.py"})
        self.assertEqual(updated["feature.py"], "VALUE = 2\n")
        self.assertNotIn("consumer.py", updated)

    def test_rejects_noop_file_and_retries_with_authoritative_file_heads(self):
        sources = {
            "main.py": "#!/usr/bin/env python3\nVALUE = 1\n",
            "tests/test_core.py": "import json\nVALUE = 1\n",
        }
        bad = (
            "--- a/main.py\n+++ b/main.py\n@@ -1,1 +1,1 @@\n"
            "-#!/usr/bin/env python3\n+#!/usr/bin/env python3\n"
            "--- a/tests/test_core.py\n+++ b/tests/test_core.py\n@@ -1,1 +1,1 @@\n"
            "-#!/usr/bin/env python3\n+#!/usr/bin/env python3\n"
        )
        good = (
            "--- a/main.py\n+++ b/main.py\n@@ -2,1 +2,1 @@\n-VALUE = 1\n+VALUE = 2\n"
            "--- a/tests/test_core.py\n+++ b/tests/test_core.py\n@@ -2,1 +2,1 @@\n-VALUE = 1\n+VALUE = 2\n"
        )
        responses = [bad, good]
        calls = []

        def model_call(messages, attempt):
            calls.append([dict(message) for message in messages])
            return responses[attempt]

        result = generate_validated_multi_file_unified_diff(
            sources,
            [{"role": "user", "content": "Измени main.py и связанные тесты"}],
            model_call,
            max_attempts=3,
        )

        self.assertIn("--- a/main.py", result)
        self.assertEqual(len(calls), 2)
        retry_prompt = calls[1][-1]["content"]
        self.assertIn("ФАЙЛ: main.py", retry_prompt)
        self.assertIn("1 | #!/usr/bin/env python3", retry_prompt)
        self.assertIn("ФАЙЛ: tests/test_core.py", retry_prompt)
        self.assertIn("1 | import json", retry_prompt)
        updated = apply_multi_file_unified_diff(sources, result)
        self.assertEqual(updated["main.py"], "#!/usr/bin/env python3\nVALUE = 2\n")
        self.assertEqual(updated["tests/test_core.py"], "import json\nVALUE = 2\n")

    def test_unable_response_triggers_scoped_minimal_change_retry(self):
        responses = ["UNABLE_TO_CREATE_PATCH", self.valid_patch]
        calls = []

        def model_call(messages, attempt):
            calls.append([dict(message) for message in messages])
            return responses[attempt]

        result = generate_validated_multi_file_unified_diff(
            self.sources,
            [{"role": "user", "content": "Добавь улучшение и связанные тесты"}],
            model_call,
            max_attempts=3,
        )

        self.assertIn("--- a/feature.py", result)
        self.assertEqual(len(calls), 2)
        retry = calls[1][-1]["content"]
        self.assertIn("уменьши объём задачи", retry)
        self.assertIn("минимальное полезное изменение", retry)
        self.assertIn("ФАЙЛ: feature.py", retry)
        self.assertIn("Файл ещё не существует", retry)

    def test_repeated_unable_response_returns_actionable_message(self):
        calls = []

        def model_call(messages, attempt):
            calls.append(attempt)
            return "UNABLE_TO_CREATE_PATCH: не найден нужный обработчик"

        with self.assertRaisesRegex(ValueError, "Модель не смогла подготовить безопасное изменение"):
            generate_validated_multi_file_unified_diff(
                self.sources,
                [{"role": "user", "content": "Перепиши всю программу"}],
                model_call,
                max_attempts=3,
            )
        self.assertEqual(calls, [0, 1])

    def test_diff_mismatch_diagnostic_includes_the_file_path(self):
        sources = {"tests/test_core.py": "import json\n"}
        patch = (
            "--- a/tests/test_core.py\n+++ b/tests/test_core.py\n"
            "@@ -1,1 +1,1 @@\n-#!/usr/bin/env python3\n+import json\n"
        )
        with self.assertRaisesRegex(ValueError, "tests/test_core.py.*точный блок исходных строк не найден"):
            apply_multi_file_unified_diff(sources, patch)

    def test_repair_context_keeps_file_heads_separate(self):
        context = build_multi_file_repair_context({
            "main.py": "#!/usr/bin/env python3\nimport os\n",
            "tests/test_core.py": "import json\nimport unittest\n",
        })
        self.assertIn("ФАЙЛ: main.py", context)
        self.assertIn("1 | #!/usr/bin/env python3", context)
        self.assertIn("ФАЙЛ: tests/test_core.py", context)
        self.assertIn("1 | import json", context)


    def test_reports_useful_diagnostic_when_response_has_no_diff(self):
        with self.assertRaisesRegex(ValueError, "отсутствуют пары заголовков"):
            split_multi_file_unified_diff("Я не могу подготовить изменения.")


    def test_repeated_file_sections_merge_non_overlapping_hunks(self):
        sources = {"plugin_manager.py": "# -*- coding: utf-8 -*-\nVALUE = 1\nOTHER = 2\n"}
        patch = """--- a/plugin_manager.py
+++ b/plugin_manager.py
@@ -2 +2 @@
-VALUE = 1
+VALUE = 10
--- a/plugin_manager.py
+++ b/plugin_manager.py
@@ -3 +3 @@
-OTHER = 2
+OTHER = 20
"""
        updated = apply_multi_file_unified_diff(sources, patch)
        self.assertEqual(
            updated["plugin_manager.py"],
            "# -*- coding: utf-8 -*-\nVALUE = 10\nOTHER = 20\n",
        )

    def test_repeated_sections_preserve_context_lines_that_look_like_hunk_headers(self):
        sources = {"plugin_manager.py": "@@ literal marker\nVALUE = 1\nOTHER = 2\n"}
        patch = """--- a/plugin_manager.py
+++ b/plugin_manager.py
@@ -1,2 +1,2 @@
 @@ literal marker
-VALUE = 1
+VALUE = 10
--- a/plugin_manager.py
+++ b/plugin_manager.py
@@ -3,1 +3,1 @@
-OTHER = 2
+OTHER = 20
"""
        updated = apply_multi_file_unified_diff(sources, patch)
        self.assertEqual(
            updated["plugin_manager.py"],
            "@@ literal marker\nVALUE = 10\nOTHER = 20\n",
        )

    def test_repeated_sections_recalculate_downstream_new_offsets(self):
        sources = {"plugin_manager.py": "A\nB\nC\nD\nE\n"}
        patch = """--- a/plugin_manager.py
+++ b/plugin_manager.py
@@ -1,1 +1,1 @@
-A
+A
X
--- a/plugin_manager.py
+++ b/plugin_manager.py
@@ -4,1 +4,1 @@
-D
+D2
"""
        sections = split_multi_file_unified_diff(patch)
        self.assertIn("@@ -4,1 +5,1 @@", sections["plugin_manager.py"])
        updated = apply_multi_file_unified_diff(sources, patch)
        self.assertEqual(updated["plugin_manager.py"], "A\nX\nB\nC\nD2\nE\n")

    def test_exact_duplicate_hunk_is_deduplicated(self):
        patch = """--- a/plugin_manager.py
+++ b/plugin_manager.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
--- a/plugin_manager.py
+++ b/plugin_manager.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
"""
        sections = split_multi_file_unified_diff(patch)
        self.assertEqual(sections["plugin_manager.py"].count("@@ "), 1)

    def test_overlapping_duplicate_hunks_are_rejected(self):
        patch = """--- a/plugin_manager.py
+++ b/plugin_manager.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
--- a/plugin_manager.py
+++ b/plugin_manager.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 3
"""
        with self.assertRaisesRegex(ValueError, "повторные или пересекающиеся"):
            split_multi_file_unified_diff(patch)

    def test_distinct_insertions_at_same_source_anchor_are_rejected(self):
        patch = """--- a/plugin_manager.py
+++ b/plugin_manager.py
@@ -1,0 +1 @@
+FIRST = 1
--- a/plugin_manager.py
+++ b/plugin_manager.py
@@ -1,0 +1 @@
+SECOND = 2
"""
        with self.assertRaisesRegex(ValueError, "повторные или пересекающиеся"):
            split_multi_file_unified_diff(patch)

    def test_context_line_starting_with_hunk_marker_is_not_a_header(self):
        sources = {"plugin_manager.py": "@@ source marker\nVALUE = 1\n"}
        patch = """--- a/plugin_manager.py
+++ b/plugin_manager.py
@@ -1,2 +1,2 @@
 @@ source marker
-VALUE = 1
+VALUE = 2
"""
        updated = apply_multi_file_unified_diff(sources, patch)
        self.assertEqual(updated["plugin_manager.py"], "@@ source marker\nVALUE = 2\n")

    def test_context_lines_resembling_file_headers_are_not_new_sections(self):
        source = "--- a/decoy.py\n+++ b/decoy.py\nVALUE = 1\n"
        patch = """--- a/plugin_manager.py
+++ b/plugin_manager.py
@@ -1,3 +1,3 @@
 --- a/decoy.py
 +++ b/decoy.py
-VALUE = 1
+VALUE = 2
"""
        updated = apply_multi_file_unified_diff({"plugin_manager.py": source}, patch)
        self.assertEqual(
            updated["plugin_manager.py"],
            "--- a/decoy.py\n+++ b/decoy.py\nVALUE = 2\n",
        )

    def test_wrapped_added_line_is_recovered(self):
        sources = {"plugin_manager.py": "class PluginManager:\n    pass\n"}
        patch = """--- a/plugin_manager.py
+++ b/plugin_manager.py
@@ -1,2 +1,2 @@
-class PluginManager:
+class
PluginManager:
     pass
"""
        sections = split_multi_file_unified_diff(patch)
        self.assertIn("@@ -1,2 +1,3 @@", sections["plugin_manager.py"])
        updated = apply_multi_file_unified_diff(sources, patch)
        self.assertEqual(updated["plugin_manager.py"], "class\nPluginManager:\n    pass\n")


    def test_wrapped_hunk_updates_following_new_line_offsets(self):
        sources = {"plugin_manager.py": "A\nB\nC\nD\nE\n"}
        patch = """--- a/plugin_manager.py
+++ b/plugin_manager.py
@@ -1,1 +1,1 @@
-A
+A
X
@@ -4,1 +4,1 @@
-D
+D2
"""
        sections = split_multi_file_unified_diff(patch)
        self.assertIn("@@ -4,1 +5,1 @@", sections["plugin_manager.py"])
        updated = apply_multi_file_unified_diff(sources, patch)
        self.assertEqual(updated["plugin_manager.py"], "A\nX\nB\nC\nD2\nE\n")

    def test_rejects_test_framework_import_leaking_into_application_module(self):
        sources = {"plugin_manager.py": "# -*- coding: utf-8 -*-\nclass PluginManager: pass\n"}
        patch = """--- a/plugin_manager.py
+++ b/plugin_manager.py
@@ -1 +1 @@
-# -*- coding: utf-8 -*-
+from unittest.mock import MagicMock
"""
        with self.assertRaisesRegex(ValueError, "в исходный модуль попал импорт тестового фреймворка"):
            apply_multi_file_unified_diff(sources, patch)


if __name__ == "__main__":
    unittest.main()
