import tempfile
import unittest
from pathlib import Path

from backup_cloud import (
    BackupEncryptionError,
    decrypt_backup_file,
    encrypt_backup_file,
    sha256_file,
)


class BackupEncryptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.plain = self.root / "backup.zip"
        self.plain.write_bytes(b"ZIP-MOCK-DATA-" * 10000)
        self.encrypted = self.root / "backup.mibak"
        self.restored = self.root / "restored.zip"
        self.password = "backup-test-password-2026"

    def tearDown(self):
        self.temp.cleanup()

    def test_streaming_encryption_roundtrip(self):
        info = encrypt_backup_file(self.plain, self.encrypted, self.password)
        self.assertTrue(self.encrypted.is_file())
        self.assertNotEqual(self.encrypted.read_bytes(), self.plain.read_bytes())
        self.assertEqual(info["sha256"], sha256_file(self.encrypted))
        restored = decrypt_backup_file(self.encrypted, self.restored, self.password)
        self.assertEqual(self.restored.read_bytes(), self.plain.read_bytes())
        self.assertEqual(restored["sha256"], sha256_file(self.restored))

    def test_wrong_password_is_rejected_without_output(self):
        encrypt_backup_file(self.plain, self.encrypted, self.password)
        with self.assertRaisesRegex(BackupEncryptionError, "неверный пароль"):
            decrypt_backup_file(self.encrypted, self.restored, "wrong-password-2026")
        self.assertFalse(self.restored.exists())

    def test_tampering_is_rejected(self):
        encrypt_backup_file(self.plain, self.encrypted, self.password)
        raw = bytearray(self.encrypted.read_bytes())
        raw[len(raw) // 2] ^= 0x01
        self.encrypted.write_bytes(raw)
        with self.assertRaises(BackupEncryptionError):
            decrypt_backup_file(self.encrypted, self.restored, self.password)

    def test_short_password_is_rejected(self):
        with self.assertRaises(BackupEncryptionError):
            encrypt_backup_file(self.plain, self.encrypted, "short")

    def test_empty_and_oversized_sources_are_rejected(self):
        empty = self.root / "empty.zip"
        empty.touch()
        with self.assertRaises(BackupEncryptionError):
            encrypt_backup_file(empty, self.encrypted, self.password)


if __name__ == "__main__":
    unittest.main(verbosity=2)
