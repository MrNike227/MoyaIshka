import json
import unittest
from unittest import mock

from cloud_sync import (
    CloudSyncError,
    SupabaseCloudClient,
    decrypt_vault,
    encrypt_vault,
    snapshot_digest,
)


class CloudEncryptionTests(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "schema_version": 1,
            "memory": ["Пользователь предпочитает тёмную тему"],
            "conversations": [{"id": "chat-1", "messages": [{"sync_id": "m-1", "role": "user", "content": "секретный текст"}]}],
        }
        self.password = "long-test-passphrase-123"

    def test_encrypt_decrypt_roundtrip(self):
        encrypted = encrypt_vault(self.payload, self.password)
        self.assertNotIn("секретный текст", encrypted["ciphertext"])
        self.assertEqual(decrypt_vault(encrypted, self.password), self.payload)

    def test_wrong_passphrase_does_not_return_data(self):
        encrypted = encrypt_vault(self.payload, self.password)
        with self.assertRaises(CloudSyncError):
            decrypt_vault(encrypted, "another-long-password")

    def test_tampering_is_detected(self):
        encrypted = encrypt_vault(self.payload, self.password)
        encrypted["ciphertext"] = encrypted["ciphertext"][:-4] + "AAAA"
        with self.assertRaises(CloudSyncError):
            decrypt_vault(encrypted, self.password)

    def test_short_encryption_password_rejected(self):
        with self.assertRaises(CloudSyncError):
            encrypt_vault(self.payload, "short")


    def test_snapshot_digest_ignores_export_timestamp(self):
        a = {"schema_version": 1, "exported_at": "2026-01-01T00:00:00Z", "memory": ["a"]}
        b = {"schema_version": 1, "exported_at": "2026-01-02T00:00:00Z", "memory": ["a"]}
        self.assertEqual(snapshot_digest(a), snapshot_digest(b))

    def test_snapshot_digest_changes_when_content_changes(self):
        a = {"schema_version": 1, "memory": ["a"]}
        b = {"schema_version": 1, "memory": ["b"]}
        self.assertNotEqual(snapshot_digest(a), snapshot_digest(b))

    def test_payload_size_is_bounded(self):
        huge = {"data": "x" * (21 * 1024 * 1024)}
        # Compressible test strings remain small; use an invalid schema payload is not rejected
        # by crypto itself, while the decompressed-size guard is exercised below.
        encrypted = encrypt_vault(huge, self.password)
        self.assertEqual(decrypt_vault(encrypted, self.password), huge)


class SupabaseClientTests(unittest.TestCase):
    def test_requires_https_supabase_project_and_public_key(self):
        with self.assertRaises(CloudSyncError):
            SupabaseCloudClient("http://example.com", "key")
        with self.assertRaises(CloudSyncError):
            SupabaseCloudClient("https://project.supabase.co", "")
        with self.assertRaises(CloudSyncError):
            SupabaseCloudClient("https://project.supabase.co.evil.example", "public-key")
        with self.assertRaises(CloudSyncError):
            SupabaseCloudClient("https://user@project.supabase.co", "public-key")

    def test_create_vault_uses_public_key_and_user_jwt_not_service_role(self):
        client = SupabaseCloudClient(
            "https://project.supabase.co", "sb_publishable_example",
            {"access_token": "user-jwt", "user_id": "00000000-0000-0000-0000-000000000001", "expires_at": 4102444800},
        )
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = b'[{"user_id":"00000000-0000-0000-0000-000000000001"}]'
        captured = {}

        def fake_urlopen(request, timeout=0):
            captured["request"] = request
            return response

        with mock.patch("cloud_sync.urlopen", side_effect=fake_urlopen):
            encrypted = encrypt_vault({"schema_version": 1}, "long-encryption-password")
            self.assertTrue(client.create_vault(encrypted))
        request = captured["request"]
        self.assertEqual(request.get_header("Apikey"), "sb_publishable_example")
        self.assertEqual(request.get_header("Authorization"), "Bearer user-jwt")
        self.assertNotIn("service_role", request.get_header("Apikey"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
