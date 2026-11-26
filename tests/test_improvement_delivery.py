import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from improvement_delivery import (
    default_install_root,
    resolve_install_layout,
    resolve_updates_directory,
    save_change_bundle,
)


class ImprovementDeliveryTests(unittest.TestCase):
    def test_container_layout_keeps_app_and_updates_as_siblings(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = resolve_install_layout(Path(temp) / "MoyaIshka")
            self.assertEqual(layout["app"], layout["root"] / "App")
            self.assertEqual(layout["updates"], layout["root"] / "Updates")
            self.assertEqual(layout["app"].parent, layout["updates"].parent)
            self.assertNotEqual(layout["app"], layout["updates"])

    def test_default_root_does_not_reuse_legacy_install_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            legacy = Path(temp) / "OldInstall"
            root = default_install_root(legacy_install_dir=legacy)
            self.assertNotEqual(root, legacy.resolve())
            self.assertEqual(root.name, "MoyaIshka_Container")

    def test_configured_root_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "SelectedContainer"
            self.assertEqual(default_install_root(configured_root=root), root.resolve())

    def test_updates_directory_is_never_inside_app(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Container"
            layout = resolve_install_layout(root)
            self.assertEqual(resolve_updates_directory(install_root=root), layout["updates"])
            self.assertEqual(
                resolve_updates_directory(app_dir=layout["app"]),
                layout["updates"],
            )
            with self.assertRaisesRegex(ValueError, "внутри"):
                resolve_updates_directory(
                    configured_updates_dir=layout["app"] / "Updates",
                    app_dir=layout["app"],
                )

    def test_change_bundle_contains_full_updated_file_patch_and_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            original = "VALUE = 1\n"
            updated = "VALUE = 2\n"
            patch = "--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n"
            bundle = save_change_bundle(
                Path(temp) / "Updates", "main.py", original, updated, patch,
                title="Исправить значение", version="4.2.1",
            )
            self.assertEqual((bundle / "files" / "main.py").read_text(encoding="utf-8"), updated)
            self.assertEqual((bundle / "changes.patch").read_text(encoding="utf-8"), patch)
            manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "review_only_not_applied")
            self.assertEqual(manifest["version"], "4.2.1")
            self.assertEqual(manifest["files"][0]["sha256"], hashlib.sha256(updated.encode()).hexdigest())
            self.assertEqual(manifest["files"][0]["base_sha256"], hashlib.sha256(original.encode()).hexdigest())

    def test_change_bundle_rejects_traversal_and_non_python_file(self):
        with tempfile.TemporaryDirectory() as temp:
            args = (Path(temp) / "Updates",)
            with self.assertRaises(ValueError):
                save_change_bundle(*args, "../outside.py", "x=1\n", "x=2\n", "patch")
            with self.assertRaises(ValueError):
                save_change_bundle(*args, "config.json", "{}", "{}", "patch")

    def test_change_bundle_rejects_invalid_python(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(SyntaxError):
                save_change_bundle(Path(temp) / "Updates", "main.py", "x=1\n", "def (", "patch")


if __name__ == "__main__":
    unittest.main()
