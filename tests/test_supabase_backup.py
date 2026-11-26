import hashlib, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from supabase_backup import BUCKET_NAME, MAX_SUPABASE_FILE_BYTES, SupabaseBackupClient, SupabaseBackupError, sha256_file

USER_ID = "11111111-2222-4333-8444-555555555555"
SESSION = {"access_token": "access-token", "user_id": USER_ID}

class FakeResponse:
    def __init__(self, body=b"", headers=None, status=200):
        self.body, self.headers, self.status = body, headers or {}, status
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self, size=-1):
        if size < 0: result, self.body = self.body, b""
        else: result, self.body = self.body[:size], self.body[size:]
        return result

class SupabaseBackupTests(unittest.TestCase):
    def setUp(self):
        self.client = SupabaseBackupClient("https://exampleproject.supabase.co", "publishable-key", SESSION)

    def test_client_uses_direct_storage_host_and_user_folder(self):
        self.assertEqual(self.client.storage_host, "exampleproject.storage.supabase.co")
        name = "MoyaIshka_backup_manual_" + "a" * 64 + ".mibak"
        self.assertEqual(self.client._object_path(name), USER_ID + "/" + name)

    def test_rejects_invalid_project_url_and_user_id(self):
        with self.assertRaises(SupabaseBackupError):
            SupabaseBackupClient("http://exampleproject.supabase.co", "key", SESSION)
        with self.assertRaises(SupabaseBackupError):
            SupabaseBackupClient("https://exampleproject.supabase.co", "key", {"access_token":"t","user_id":"../../other"})

    def test_listing_is_scoped_to_user_and_filters_invalid_files(self):
        seen = []
        def fake_request(method, path, payload=None):
            seen.append((method, path, payload))
            return [
                {"name":"MoyaIshka_backup_manual_" + "a"*64 + ".mibak", "metadata":{"size":1234}, "created_at":"2026-11-26T10:00:00Z"},
                {"name":"unrelated.txt","metadata":{"size":9}},
            ]
        with patch.object(self.client, "_api_request", side_effect=fake_request):
            rows = self.client.list_backup_files()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["path"].startswith(USER_ID + "/"))
        self.assertEqual(rows[0]["size"], 1234)
        self.assertEqual(seen[0][2]["prefix"], USER_ID + "/")
        self.assertEqual(BUCKET_NAME, "moyaishka-backups")

    def test_upload_uses_tus_endpoint_and_chunk_headers(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "backup.mibak"
            path.write_bytes(b"encrypted-test-data" * 100)
            digest = sha256_file(path)
            name = "MoyaIshka_backup_manual_" + digest + ".mibak"
            calls = []
            def fake_urlopen(request, timeout=0):
                calls.append(request)
                if request.get_method() == "POST":
                    return FakeResponse(headers={"Location":"https://exampleproject.storage.supabase.co/storage/v1/upload/resumable/test-id"}, status=201)
                if request.get_method() == "PATCH":
                    return FakeResponse(headers={"Upload-Offset":str(int(request.headers["Content-length"]))}, status=204)
                raise AssertionError(request.get_method())
            with patch("supabase_backup.urllib.request.urlopen", side_effect=fake_urlopen), patch.object(self.client, "get_storage_usage", return_value=0):
                result = self.client.upload_backup_file(path, remote_name=name, sha256=digest, app_version="4.1.9")
            self.assertEqual(result["sha256"], digest)
            self.assertEqual(result["path"], USER_ID + "/" + name)
            self.assertEqual(calls[0].full_url, self.client.storage_endpoint)
            self.assertEqual(calls[0].get_method(), "POST")
            self.assertEqual(calls[0].headers["Tus-resumable"], "1.0.0")
            self.assertEqual(calls[1].get_method(), "PATCH")
            self.assertEqual(calls[1].headers["Content-type"], "application/offset+octet-stream")

    def test_upload_rejects_files_over_free_plan_limit_before_network(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "large.mibak"
            with path.open("wb") as stream: stream.truncate(MAX_SUPABASE_FILE_BYTES + 1)
            with patch("supabase_backup.urllib.request.urlopen") as opener:
                with self.assertRaisesRegex(SupabaseBackupError, "50 МБ"):
                    self.client.upload_backup_file(path, remote_name="large_" + "a"*64 + ".mibak", sha256="a"*64)
                opener.assert_not_called()

    def test_download_verifies_sha256_in_object_name(self):
        with tempfile.TemporaryDirectory() as temp:
            body = b"encrypted-backup"
            digest = hashlib.sha256(body).hexdigest()
            name = "MoyaIshka_backup_manual_" + digest + ".mibak"
            destination = Path(temp) / name
            with patch("supabase_backup.urllib.request.urlopen", return_value=FakeResponse(body, {"Content-Length":str(len(body))})):
                info = self.client.download_backup_file(USER_ID + "/" + name, destination)
            self.assertEqual(destination.read_bytes(), body)
            self.assertEqual(info["sha256"], digest)

    def test_download_rejects_other_user_path(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch("supabase_backup.urllib.request.urlopen") as opener:
                with self.assertRaises(SupabaseBackupError):
                    self.client.download_backup_file("99999999-2222-4333-8444-555555555555/x_"+"a"*64+".mibak", Path(temp)/"bad.mibak")
                opener.assert_not_called()

if __name__ == "__main__":
    unittest.main(verbosity=2)
