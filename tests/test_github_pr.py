import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from github_pr import (GitHubProposalError, create_draft_pull_request, create_draft_pull_request_from_bundle, load_tested_proposal_bundle)
from improvement_delivery import save_multi_change_bundle


BASE_SOURCE = "VALUE = 1\n"
PATCH = "--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n"


class GitHubPullRequestTests(unittest.TestCase):
    def _fake_request(self, calls):
        def fake(token, method, endpoint, payload=None, **_kwargs):
            calls.append((method, endpoint, payload))
            if endpoint == "/repos/example/MoyaIshka":
                return {"default_branch": "main"}
            if endpoint.endswith("/git/ref/heads/main"):
                return {"object": {"sha": "base-commit"}}
            if endpoint.endswith("/git/commits/base-commit"):
                return {"tree": {"sha": "base-tree"}}
            if endpoint.startswith("/repos/example/MoyaIshka/contents/"):
                return {
                    "encoding": "base64",
                    "content": base64.b64encode(BASE_SOURCE.encode()).decode(),
                }
            if endpoint.endswith("/git/blobs"):
                return {"sha": "blob-sha"}
            if endpoint.endswith("/git/trees"):
                return {"sha": "tree-sha"}
            if endpoint.endswith("/git/commits"):
                return {"sha": "new-commit"}
            if endpoint.endswith("/git/refs"):
                return {"ref": "refs/heads/moya-improvement/test"}
            if endpoint.endswith("/pulls"):
                self.assertEqual(payload["draft"], True)
                return {"number": 123, "html_url": "https://github.com/example/MoyaIshka/pull/123", "draft": True}
            return {}
        return fake

    def test_creates_draft_pr_and_never_calls_release_or_merge_endpoints(self):
        calls = []
        result = create_draft_pull_request(
            "example/MoyaIshka", "test-token", "main", "main.py", PATCH,
            title="Fix test", request=self._fake_request(calls),
        )
        self.assertEqual(result["number"], 123)
        self.assertTrue(result["draft"])
        self.assertEqual(result["base"], "main")
        endpoints = [entry[1] for entry in calls]
        self.assertFalse(any("releases" in path or "merge" in path for path in endpoints))
        pull_payload = next(payload for method, endpoint, payload in calls if endpoint.endswith("/pulls"))
        self.assertIs(pull_payload["draft"], True)

    def test_rejects_path_traversal_before_api_call(self):
        calls = []
        with self.assertRaises(GitHubProposalError):
            create_draft_pull_request(
                "example/MoyaIshka", "test-token", "main", "../main.py", PATCH,
                request=self._fake_request(calls),
            )
        self.assertEqual(calls, [])

    def test_rejects_invalid_repo(self):
        with self.assertRaises(GitHubProposalError):
            create_draft_pull_request("https://github.com/example/MoyaIshka", "token", "main", "main.py", PATCH)

    def test_does_not_commit_when_patch_no_longer_matches_base(self):
        calls = []

        def fake(token, method, endpoint, payload=None, **kwargs):
            if endpoint == "/repos/example/MoyaIshka":
                return {"default_branch": "main"}
            if endpoint.endswith("/git/ref/heads/main"):
                return {"object": {"sha": "base-commit"}}
            if endpoint.endswith("/git/commits/base-commit"):
                return {"tree": {"sha": "base-tree"}}
            if endpoint.startswith("/repos/example/MoyaIshka/contents/"):
                return {"encoding": "base64", "content": base64.b64encode(b"VALUE = 99\n").decode()}
            calls.append((method, endpoint, payload))
            return {}

        with self.assertRaisesRegex(GitHubProposalError, "Diff не удалось"):
            create_draft_pull_request(
                "example/MoyaIshka", "token", "main", "main.py", PATCH, request=fake,
            )
        self.assertEqual(calls, [])

    def test_requires_token(self):
        with self.assertRaises(GitHubProposalError):
            create_draft_pull_request("example/MoyaIshka", "", "main", "main.py", PATCH)



class GitHubMultiFileProposalTests(unittest.TestCase):
    def make_tested_bundle(self, updates_dir):
        original = "VALUE = 1\n"
        updated = "VALUE = 2\n"
        new_test = (
            "import unittest\nimport feature\n\n"
            "class FeatureTests(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(feature.VALUE, 2)\n"
        )
        patch = (
            "--- a/feature.py\n+++ b/feature.py\n@@ -1 +1 @@\n"
            "-VALUE = 1\n+VALUE = 2\n"
            "--- /dev/null\n+++ b/tests/test_feature.py\n@@ -0,0 +1,5 @@\n"
            "+import unittest\n+import feature\n+\n"
            "+class FeatureTests(unittest.TestCase):\n"
            "+    def test_value(self): self.assertEqual(feature.VALUE, 2)\n"
        )
        bundle = save_multi_change_bundle(
            updates_dir,
            [
                {"path": "feature.py", "original_text": original, "updated_text": updated},
                {"path": "tests/test_feature.py", "original_text": None, "updated_text": new_test},
            ],
            patch,
            title="Проверенное изменение",
            version="4.2.8",
        )
        report = {
            "schema": 1,
            "passed": True,
            "checked_files": 2,
            "tests_exit_code": 0,
            "tests_run": 3,
            "failures": 0,
            "errors": 0,
            "error": "",
            "sandboxed": True,
        }
        (bundle / "test_report.json").write_text(json.dumps(report), encoding="utf-8")
        manifest_path = bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["test"] = {
            "status": "passed",
            "report": "test_report.json",
            "sandboxed": True,
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return bundle

    def test_bundle_requires_a_successful_sandbox_report(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = self.make_tested_bundle(Path(temp) / "Updates")
            loaded = load_tested_proposal_bundle(bundle)
            self.assertEqual(set(loaded["files"]), {"feature.py", "tests/test_feature.py"})
            report = json.loads((bundle / "test_report.json").read_text(encoding="utf-8"))
            report["sandboxed"] = False
            (bundle / "test_report.json").write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(GitHubProposalError, "Windows Sandbox"):
                load_tested_proposal_bundle(bundle)

    def test_creates_one_draft_pr_for_all_files_in_tested_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = self.make_tested_bundle(Path(temp) / "Updates")
            calls = []
            def fake(token, method, endpoint, payload=None, **_kwargs):
                calls.append((method, endpoint, payload))
                if endpoint == "/repos/example/MoyaIshka":
                    return {"default_branch": "main"}
                if endpoint.endswith("/git/ref/heads/main"):
                    return {"object": {"sha": "base-commit"}}
                if endpoint.endswith("/git/commits/base-commit"):
                    return {"tree": {"sha": "base-tree"}}
                if endpoint.startswith("/repos/example/MoyaIshka/contents/feature.py"):
                    return {
                        "encoding": "base64",
                        "content": base64.b64encode(b"VALUE = 1\n").decode(),
                    }
                if endpoint.startswith("/repos/example/MoyaIshka/contents/tests/test_feature.py"):
                    raise GitHubProposalError("GitHub не нашёл репозиторий, базовую ветку или файл.")
                if endpoint.endswith("/git/blobs"):
                    return {"sha": "blob-" + str(len([x for x in calls if x[1].endswith("/git/blobs")]))}
                if endpoint.endswith("/git/trees"):
                    self.assertEqual(len(payload["tree"]), 2)
                    return {"sha": "tree-sha"}
                if endpoint.endswith("/git/commits"):
                    return {"sha": "new-commit"}
                if endpoint.endswith("/git/refs"):
                    return {"ref": payload["ref"]}
                if endpoint.endswith("/pulls"):
                    self.assertTrue(payload["draft"])
                    self.assertIn("tests/test_feature.py", payload["body"])
                    self.assertIn("Проверено тестов: 3", payload["body"])
                    return {
                        "number": 456,
                        "html_url": "https://github.com/example/MoyaIshka/pull/456",
                        "draft": True,
                    }
                return {}

            result = create_draft_pull_request_from_bundle(
                "example/MoyaIshka",
                "test-token",
                "main",
                bundle,
                title="Исправить функцию и тест",
                request=fake,
            )
            self.assertEqual(result["number"], 456)
            self.assertTrue(result["draft"])
            self.assertTrue(result["sandboxed"])
            self.assertEqual(len(result["files"]), 2)
            pull_payload = next(payload for method, endpoint, payload in calls if endpoint.endswith("/pulls"))
            self.assertIn("Windows Sandbox", pull_payload["body"])
            self.assertFalse(any("releases" in endpoint or "merge" in endpoint for _, endpoint, _ in calls))

    def test_bundle_delivery_rejects_stale_base_source_before_creating_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = self.make_tested_bundle(Path(temp) / "Updates")
            calls = []
            def fake(token, method, endpoint, payload=None, **_kwargs):
                if endpoint == "/repos/example/MoyaIshka":
                    return {"default_branch": "main"}
                if endpoint.endswith("/git/ref/heads/main"):
                    return {"object": {"sha": "base-commit"}}
                if endpoint.endswith("/git/commits/base-commit"):
                    return {"tree": {"sha": "base-tree"}}
                if endpoint.startswith("/repos/example/MoyaIshka/contents/feature.py"):
                    return {
                        "encoding": "base64",
                        "content": base64.b64encode(b"VALUE = 99\n").decode(),
                    }
                calls.append((method, endpoint, payload))
                return {}
            with self.assertRaisesRegex(GitHubProposalError, "изменился после тестирования"):
                create_draft_pull_request_from_bundle(
                    "example/MoyaIshka", "test-token", "main", bundle, request=fake,
                )
            self.assertEqual(calls, [])



if __name__ == "__main__":
    unittest.main()
