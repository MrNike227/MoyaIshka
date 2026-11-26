import base64
import unittest
from unittest.mock import patch

from github_pr import GitHubProposalError, create_draft_pull_request


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


if __name__ == "__main__":
    unittest.main()
