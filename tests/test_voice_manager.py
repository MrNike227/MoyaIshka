import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voice_manager import (
    VOICE_CATALOG,
    _download_to_file,
    delete_voice,
    installed_voice_ids,
    is_voice_installed,
    voice_label,
    voice_paths,
)


class _FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}


class VoiceCatalogTests(unittest.TestCase):
    def test_catalog_contains_four_russian_voices_with_pinned_hashes(self):
        self.assertEqual(len(VOICE_CATALOG), 4)
        for voice_id, item in VOICE_CATALOG.items():
            self.assertTrue(voice_id.startswith("ru_RU-"))
            self.assertEqual(item["language"], "ru_RU")
            self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
            self.assertGreater(item["size_bytes"], 50_000_000)

    def test_voice_paths_are_selected_from_catalog(self):
        with tempfile.TemporaryDirectory() as temp:
            model_path, config_path = voice_paths(temp, "ru_RU-irina-medium")
            self.assertEqual(model_path.name, "ru_RU-irina-medium.onnx")
            self.assertEqual(config_path.name, "ru_RU-irina-medium.onnx.json")
            self.assertTrue(model_path.parent.is_dir())
            with self.assertRaises(ValueError):
                voice_paths(temp, "../../some-file")

    def test_voice_is_installed_only_when_model_and_matching_config_exist(self):
        with tempfile.TemporaryDirectory() as temp:
            model_path, config_path = voice_paths(temp, "ru_RU-irina-medium")
            model_path.write_bytes(b"x" * 1_100_000)
            config_path.write_text(json.dumps({"language": {"code": "ru_RU"}}), encoding="utf-8")
            self.assertTrue(is_voice_installed(temp, "ru_RU-irina-medium"))
            self.assertEqual(installed_voice_ids(temp), ["ru_RU-irina-medium"])
            config_path.write_text(json.dumps({"language": {"code": "en_US"}}), encoding="utf-8")
            self.assertFalse(is_voice_installed(temp, "ru_RU-irina-medium"))

    def test_delete_voice_removes_model_and_config_only(self):
        with tempfile.TemporaryDirectory() as temp:
            model_path, config_path = voice_paths(temp, "ru_RU-denis-medium")
            model_path.write_bytes(b"model")
            config_path.write_text("{}", encoding="utf-8")
            keep = model_path.parent / "other-file.txt"
            keep.write_text("keep", encoding="utf-8")
            delete_voice(temp, "ru_RU-denis-medium")
            self.assertFalse(model_path.exists())
            self.assertFalse(config_path.exists())
            self.assertTrue(keep.exists())

    def test_voice_label_is_localized(self):
        self.assertIn("Ирина", voice_label("ru_RU-irina-medium"))
        self.assertIn("Русский", voice_label("ru_RU-irina-medium"))

    def test_unload_piper_voice_releases_cached_model(self):
        import voice_assistant
        with tempfile.TemporaryDirectory() as temp:
            model_path = Path(temp) / "voice.onnx"
            key = str(model_path.resolve())
            voice_assistant._PIPER_CACHE[key] = object()
            voice_assistant.unload_piper_voice(model_path)
            self.assertNotIn(key, voice_assistant._PIPER_CACHE)


class VoiceDownloadTests(unittest.TestCase):
    def test_download_verifies_sha256_before_replacing_destination(self):
        payload = b"test voice payload"
        expected_hash = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "voice.bin"
            with patch("voice_manager.urllib.request.urlopen", return_value=_FakeResponse(payload)):
                downloaded = _download_to_file(
                    "https://example.invalid/voice.bin",
                    destination,
                    expected_sha256=expected_hash,
                    max_bytes=1024,
                )
            self.assertEqual(downloaded, len(payload))
            self.assertEqual(destination.read_bytes(), payload)
            self.assertFalse(destination.with_name("voice.bin.part").exists())

    def test_bad_sha256_discards_download_and_partial_file(self):
        payload = b"bad voice payload"
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "voice.bin"
            with patch("voice_manager.urllib.request.urlopen", return_value=_FakeResponse(payload)):
                with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                    _download_to_file(
                        "https://example.invalid/voice.bin",
                        destination,
                        expected_sha256="0" * 64,
                        max_bytes=1024,
                    )
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_name("voice.bin.part").exists())


if __name__ == "__main__":
    unittest.main()
