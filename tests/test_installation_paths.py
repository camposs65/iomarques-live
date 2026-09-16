import json
import tempfile
import unittest
from pathlib import Path

from app import APP_VERSION, find_message_automation_app


class InstallationPathsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app_dir = self.root / "programs" / "IoMarquesLive"
        self.app_dir.mkdir(parents=True)
        self.user_home = self.root / "user"
        self.user_home.mkdir()

    def make_script(self, parent):
        parent.mkdir(parents=True, exist_ok=True)
        script = parent / "app.py"
        script.write_text("# Test fixture; never executed.\n", encoding="utf-8")
        return script

    def configure(self, value):
        (self.app_dir / "instalacao.json").write_text(
            json.dumps(value), encoding="utf-8"
        )

    def find(self):
        return find_message_automation_app(self.app_dir, self.user_home)

    def test_version_updated(self):
        self.assertEqual(APP_VERSION, "1.7")

    def test_no_companion_returns_none(self):
        self.assertIsNone(self.find())

    def test_original_user_home_discovery(self):
        script = self.make_script(self.user_home / "iomarques-instagram-direct")
        self.assertEqual(self.find(), script)

    def test_original_sibling_discovery(self):
        script = self.make_script(self.app_dir.parent / "iomarques-instagram-direct")
        self.assertEqual(self.find(), script)

    def test_original_checkout_dist_discovery(self):
        script = self.make_script(self.app_dir.parent.parent / "iomarques-instagram-direct")
        self.assertEqual(self.find(), script)

    def test_configured_absolute_path_survives_new_installation_location(self):
        script = self.make_script(self.root / "projects" / "iomarques-instagram-direct")
        self.configure({"automation_app": str(script)})
        self.assertEqual(self.find(), script)

    def test_explicit_installation_metadata_takes_precedence(self):
        self.make_script(self.user_home / "iomarques-instagram-direct")
        configured = self.make_script(self.root / "projects" / "iomarques-instagram-direct")
        self.configure({"automation_app": str(configured)})
        self.assertEqual(self.find(), configured)

    def test_missing_configured_script_falls_back(self):
        script = self.make_script(self.user_home / "iomarques-instagram-direct")
        self.configure({"automation_app": str(self.root / "missing" / "app.py")})
        self.assertEqual(self.find(), script)

    def test_relative_path_not_accepted(self):
        self.configure({"automation_app": "../iomarques-instagram-direct/app.py"})
        self.assertIsNone(self.find())

    def test_configured_directory_is_not_a_script(self):
        folder = self.root / "app.py"
        folder.mkdir()
        self.configure({"automation_app": str(folder)})
        self.assertIsNone(self.find())

    def test_other_file_name_is_not_a_script(self):
        file = self.root / "different.py"
        file.touch()
        self.configure({"automation_app": str(file)})
        self.assertIsNone(self.find())

    def test_invalid_metadata_falls_back(self):
        script = self.make_script(self.user_home / "iomarques-instagram-direct")
        path = self.app_dir / "instalacao.json"
        for content in ("not-json", "[]", "null", '{"automation_app": 12}',
                        '{"automation_app": null}', '{"automation_app": ""}'):
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                self.assertEqual(self.find(), script)

    def test_invalid_encoding_falls_back(self):
        script = self.make_script(self.user_home / "iomarques-instagram-direct")
        (self.app_dir / "instalacao.json").write_bytes(b"\xff")
        self.assertEqual(self.find(), script)


if __name__ == "__main__":
    unittest.main()
