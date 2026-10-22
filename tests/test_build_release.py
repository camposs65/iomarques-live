import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import build_release
import installation


class BuildReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="iomarques-release-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.package = self.root / "package"
        self.package.mkdir()
        for name in installation.PACKAGE_FILES:
            if name != "release.json":
                (self.package / name).write_bytes(name.encode())
        installation.release_manifest(self.package, "1.9.0")
        self.source = self.root / "source"
        self.source.mkdir()
        for name in build_release.INSTALLER_FILES:
            (self.source / name).write_text("fixture", encoding="utf-8")
        (self.source / "assets").mkdir()
        for name in installation.ASSET_FILES:
            (self.source / "assets" / name).write_bytes(b"asset")

    def test_release_zip_contains_only_four_program_files(self):
        (self.package / "integracao_sessao.dat").write_bytes(b"private")
        assets = build_release.package_release(self.package, self.root / "out", "1.9.0")
        with zipfile.ZipFile(assets[0]) as archive:
            self.assertEqual(set(archive.namelist()), set(installation.PACKAGE_FILES))
            for name in installation.PACKAGE_FILES:
                self.assertEqual(archive.read(name), (self.package / name).read_bytes())
        checksum = hashlib.sha256(assets[0].read_bytes()).hexdigest()
        self.assertEqual(assets[-1].read_text(encoding="ascii"),
                         f"{checksum}  {build_release.RELEASE_ASSET}\n")

    def test_installer_zip_uses_allowlist_not_source_directory_scan(self):
        for name in ("integracao_sessao.dat", "cloud_pendencias.dat", "historico_lives.json"):
            (self.source / name).write_bytes(b"private")
        (self.source / "backups-publicados").mkdir()
        (self.source / "backups-publicados" / "backup.json").write_bytes(b"private")
        assets = build_release.package_release(self.package, self.root / "out", "1.9.0", self.source)
        with zipfile.ZipFile(assets[1]) as archive:
            names = {name.split("/", 1)[1] for name in archive.namelist()}
        self.assertEqual(names, set(build_release.INSTALLER_FILES)
                         | {"assets/" + name for name in installation.ASSET_FILES})

    def test_modified_program_or_wrong_version_is_not_published(self):
        for name in (installation.EXE_NAME, installation.UPDATER_EXE, "integracao_supabase.json"):
            with self.subTest(name=name):
                path = self.package / name
                original = path.read_bytes()
                path.write_bytes(b"tampered")
                with self.assertRaisesRegex(RuntimeError, "integridade"):
                    build_release.package_release(self.package, self.root / "out", "1.9.0")
                path.write_bytes(original)
        with self.assertRaisesRegex(RuntimeError, "versão"):
            build_release.package_release(self.package, self.root / "out", "1.8.0")

    def test_manifest_contains_sha256_and_size_for_only_program_files(self):
        manifest = json.loads((self.package / "release.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], 1)
        for name, record in manifest["files"].items():
            data = (self.package / name).read_bytes()
            self.assertEqual(record, {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})

    def test_version_read_does_not_import_or_execute_app(self):
        (self.source / "app.py").write_text(
            'APP_VERSION = "1.9.0"\nraise RuntimeError("must not execute")\n', encoding="utf-8")
        self.assertEqual(installation.source_version(self.source), "1.9.0")
        for value in ('"1.9"', '"1.9.0;command"', '"01.9.0"', 'read_version()', 'None'):
            (self.source / "app.py").write_text(f"APP_VERSION = {value}\n", encoding="utf-8")
            with self.assertRaises((RuntimeError, ValueError)):
                installation.source_version(self.source)


if __name__ == "__main__":
    unittest.main()
