import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from package_onedir import build_package
from updater import _extract_package, _safe_member_path, validate_app_folder
from updater_helper import validate_app_directory


def write_fake_app(root: Path, version: str = "4.1.8.4") -> Path:
    app = root / "MoyaIshka"
    internal = app / "_internal"
    internal.mkdir(parents=True)
    content = {
        "MoyaIshka.exe": b"fake exe",
        "_internal/python311.dll": b"python dll",
        "_internal/vcruntime140.dll": b"runtime 1",
        "_internal/vcruntime140_1.dll": b"runtime 2",
        "_internal/msvcp140.dll": b"runtime 3",
    }
    entries = []
    for relative, data in content.items():
        path = app / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        entries.append({
            "path": relative, "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    exe_hash = next(item["sha256"] for item in entries if item["path"] == "MoyaIshka.exe")
    manifest = {
        "schema": 1, "version": version, "entrypoint": "MoyaIshka.exe",
        "exe_sha256": exe_hash, "files": entries,
    }
    (app / "build_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return app


class OnedirUpdateTests(unittest.TestCase):
    def test_large_package_download_streams_to_disk_and_checks_size(self):
        from unittest.mock import patch
        from io import BytesIO
        from updater import _download_to

        class FakeResponse:
            headers = {"Content-Length": "6"}
            def __init__(self):
                self.stream = BytesIO(b"abcdef")
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self, size=-1):
                return self.stream.read(size)

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "download.zip"
            with patch("updater.urllib.request.urlopen", return_value=FakeResponse()):
                digest, size = _download_to("https://example.test/file", path, 10)
            self.assertEqual(size, 6)
            self.assertEqual(path.read_bytes(), b"abcdef")
            self.assertEqual(digest, hashlib.sha256(b"abcdef").hexdigest())

    def test_stream_download_rejects_announced_oversized_content(self):
        from unittest.mock import patch
        from io import BytesIO
        from updater import _download_to

        class FakeResponse:
            headers = {"Content-Length": "100"}
            def __init__(self):
                self.stream = BytesIO(b"small")
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self, size=-1):
                return self.stream.read(size)

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "download.zip"
            with patch("updater.urllib.request.urlopen", return_value=FakeResponse()):
                with self.assertRaisesRegex(RuntimeError, "лимит"):
                    _download_to("https://example.test/file", path, 10)
            self.assertFalse(path.exists())

    def test_archive_path_traversal_is_rejected(self):
        for path in ("../evil.txt", "MoyaIshka/../../evil.txt", "/MoyaIshka/file", "C:/evil.txt"):
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                _safe_member_path(path)

    def test_archive_root_must_be_moyaishka(self):
        with self.assertRaisesRegex(RuntimeError, "корневую папку"):
            _safe_member_path("OtherApp/MoyaIshka.exe")

    def test_folder_manifest_is_checked_file_by_file(self):
        with tempfile.TemporaryDirectory() as temp:
            app = write_fake_app(Path(temp))
            self.assertEqual(validate_app_folder(app, "4.1.8.4")["version"], "4.1.8.4")
            self.assertEqual(validate_app_directory(app, "4.1.8.4")["version"], "4.1.8.4")

    def test_unmanifested_runtime_dll_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            app = write_fake_app(Path(temp))
            (app / "_internal" / "unexpected.dll").write_bytes(b"unexpected")
            with self.assertRaisesRegex(RuntimeError, "лишние"):
                validate_app_folder(app, "4.1.8.4")
            with self.assertRaisesRegex(RuntimeError, "лишние"):
                validate_app_directory(app, "4.1.8.4")

    def test_tampered_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            app = write_fake_app(Path(temp))
            (app / "_internal/python311.dll").write_bytes(b"tampered")
            with self.assertRaisesRegex(RuntimeError, "Размер файла"):
                validate_app_folder(app, "4.1.8.4")

    def test_wrong_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            app = write_fake_app(Path(temp), "4.1.8.3")
            with self.assertRaisesRegex(RuntimeError, "Версия"):
                validate_app_folder(app, "4.1.8.4")

    def test_built_zip_extracts_to_staging_folder_and_validates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = write_fake_app(root)
            output = root / "MoyaIshka.zip"
            build_package(source, output, "4.1.8.4")
            staged = _extract_package(output, root / "update_staging", "4.1.8.4")
            self.assertTrue(staged.is_file())
            self.assertTrue((staged.parent / "_internal/python311.dll").is_file())
            self.assertEqual(validate_app_folder(staged.parent, "4.1.8.4")["version"], "4.1.8.4")

    def test_duplicate_archive_entries_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("MoyaIshka/file.txt", "one")
                bundle.writestr("MoyaIshka/file.txt", "two")
            with self.assertRaisesRegex(RuntimeError, "повторяется путь"):
                _extract_package(archive, root / "stage", "4.1.8.4")


if __name__ == "__main__":
    unittest.main()
