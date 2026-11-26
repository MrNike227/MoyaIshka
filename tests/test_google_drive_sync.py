import json
import unittest
from unittest.mock import patch

from google_drive_sync import (
    DRIVE_SCOPE,
    GoogleDriveError,
    GoogleDriveVaultClient,
    _b64url,
    _json_bytes,
)


class GoogleDriveSyncTests(unittest.TestCase):
    def test_drive_scope_is_narrow_file_scope(self):
        self.assertEqual(DRIVE_SCOPE, "https://www.googleapis.com/auth/drive.file")
        self.assertNotEqual(DRIVE_SCOPE, "https://www.googleapis.com/auth/drive")

    def test_b64url_is_url_safe_and_unpadded(self):
        value = _b64url(b"hello world")
        self.assertNotIn("=", value)
        self.assertNotIn("+", value)
        self.assertNotIn("/", value)

    def test_json_bytes_is_utf8_and_compact(self):
        raw = _json_bytes({"папка": "Моя ИИшка", "n": 1})
        self.assertIn("Моя ИИшка".encode("utf-8"), raw)
        self.assertNotIn(b" : ", raw)
        self.assertEqual(json.loads(raw.decode("utf-8"))["папка"], "Моя ИИшка")

    def test_multipart_payload_contains_metadata_and_encrypted_content(self):
        metadata = {"name": "vault.enc", "parents": ["folder-id"]}
        content = b'{"ciphertext":"opaque"}'
        body, content_type = GoogleDriveVaultClient._multipart(metadata, content)
        self.assertIn("multipart/related; boundary=", content_type)
        self.assertIn(b"vault.enc", body)
        self.assertIn(b"folder-id", body)
        self.assertIn(content, body)
        self.assertTrue(body.endswith(b"--\r\n"))

    def test_client_rejects_missing_client_credentials(self):
        client = GoogleDriveVaultClient("", "", {})
        with self.assertRaises(GoogleDriveError):
            client.ensure_fresh_session()

    def test_client_rejects_expired_session_without_refresh_token(self):
        client = GoogleDriveVaultClient("x.apps.googleusercontent.com", "secret", {"expires_at": 0})
        with self.assertRaisesRegex(GoogleDriveError, "истекла"):
            client.ensure_fresh_session()

    def test_valid_access_token_does_not_refresh(self):
        client = GoogleDriveVaultClient("x.apps.googleusercontent.com", "secret", {
            "access_token": "access", "expires_at": 4_000_000_000,
        })
        client.ensure_fresh_session()
        self.assertEqual(client.session["access_token"], "access")

    def test_backup_folder_is_created_under_app_folder(self):
        client = GoogleDriveVaultClient("id", "secret", {"access_token": "x", "expires_at": 4_000_000_000})
        client._folder = lambda: "app-folder"
        queries = []
        def fake_list(query, **_kwargs):
            queries.append(query)
            return []
        client._list = fake_list
        client._api = lambda *_args, **_kwargs: (b'{"id":"backup-folder"}', {})
        self.assertEqual(client._backup_folder(), "backup-folder")
        self.assertIn("'app-folder' in parents", queries[0])

    def test_backup_listing_filters_to_encrypted_app_backups(self):
        client = GoogleDriveVaultClient("id", "secret", {"access_token": "x", "expires_at": 4_000_000_000})
        client._backup_folder = lambda: "backup-folder"
        client._list = lambda *_args, **_kwargs: [
            {"id": "ok", "name": "a.mibak", "size": "123", "appProperties": {
                "moya_backup_format": "1", "moya_backup_sha256": "a" * 64}},
            {"id": "unrelated", "name": "other.zip", "size": "30", "appProperties": {}},
        ]
        rows = client.list_backup_files()
        self.assertEqual([row["id"] for row in rows], ["ok"])
        self.assertEqual(rows[0]["sha256"], "a" * 64)

    def test_backup_upload_rejects_bad_hash_without_network(self):
        client = GoogleDriveVaultClient("id", "secret", {"access_token": "x", "expires_at": 4_000_000_000})
        with patch("google_drive_sync.urllib.request.urlopen") as opener:
            with self.assertRaisesRegex(GoogleDriveError, "SHA-256"):
                client.upload_backup_file(__file__, remote_name="bad.mibak", sha256="wrong")
            opener.assert_not_called()

    def test_quota_usage_sums_only_app_folder_files(self):
        client = GoogleDriveVaultClient("id", "secret", {"access_token": "x", "expires_at": 4_000_000_000})
        client._folder = lambda: "folder-id"
        client._list = lambda *_args, **_kwargs: [
            {"id": "a", "size": "100"}, {"id": "b", "size": "250"}, {"id": "c"},
        ]
        self.assertEqual(client.get_app_folder_usage(), 350)

    def test_find_vault_file_query_is_limited_to_own_folder(self):
        client = GoogleDriveVaultClient("id", "secret", {"access_token": "x", "expires_at": 4_000_000_000})
        client._folder = lambda: "folder-id"
        queries = []
        client._list = lambda q, **_kwargs: (queries.append(q) or [{"id": "f1", "name": "moyaishka-vault.enc"}])
        self.assertEqual(client._find_vault_file()["id"], "f1")
        self.assertIn("'folder-id' in parents", queries[0])
        self.assertIn("moyaishka-vault.enc", queries[0])

    def test_get_vault_returns_encrypted_payload_and_remote_revision(self):
        client = GoogleDriveVaultClient("id", "secret", {"access_token": "x", "expires_at": 4_000_000_000})
        client._find_vault_file = lambda: {
            "id": "file-id", "modifiedTime": "2026-11-25T08:00:00Z", "version": "7", "size": "123",
        }
        encrypted = {"ciphertext": "opaque", "salt": "s", "nonce": "n", "schema_version": 1}
        client._api = lambda *_args, **_kwargs: (json.dumps(encrypted).encode(), {})
        row = client.get_vault()
        self.assertEqual(row["ciphertext"], "opaque")
        self.assertEqual(row["updated_at"], "2026-11-25T08:00:00Z")
        self.assertEqual(row["file_id"], "file-id")

    def test_create_vault_does_not_overwrite_existing_entry(self):
        client = GoogleDriveVaultClient("id", "secret", {"access_token": "x", "expires_at": 4_000_000_000})
        client._find_vault_file = lambda: {"id": "existing"}
        with patch.object(client, "_api") as api:
            self.assertFalse(client.create_vault({"ciphertext": "x"}))
            api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
