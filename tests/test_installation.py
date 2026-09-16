import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import installation as installer


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="iomarques-install-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / "package"
        self.target = self.root / "installed"
        self.source = self.root / "download"
        self.old = self.root / "old"
        for path in (self.package, self.source, self.old):
            path.mkdir()
        self.write(self.package / installer.EXE_NAME, b"new executable")
        self.write(self.package / "integracao_supabase.json", b'{"public": true}')

    @staticmethod
    def write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def test_install_does_not_depend_on_download_folder(self):
        exe = installer.deploy_package(self.package, self.target)
        self.assertEqual(exe.parent, self.target)
        self.assertEqual(exe.read_bytes(), b"new executable")
        self.assertEqual(set(p.name for p in self.target.iterdir()),
                         {installer.EXE_NAME, "integracao_supabase.json", "instalacao.json"})

    def test_legacy_copy_preserves_originals_and_ignores_unknown_files(self):
        self.write(self.old / "cloud_pendencias.dat", b"encrypted changes")
        self.write(self.old / "integracao_sessao.dat", b"encrypted session")
        self.write(self.old / "backups" / "historico_lives.bak.json", b"[]")
        self.write(self.old / "not-part-of-app.txt", b"private")
        installer.deploy_package(self.package, self.target, self.old)
        self.assertEqual((self.target / "cloud_pendencias.dat").read_bytes(), b"encrypted changes")
        self.assertEqual((self.target / "integracao_sessao.dat").read_bytes(), b"encrypted session")
        self.assertEqual((self.old / "cloud_pendencias.dat").read_bytes(), b"encrypted changes")
        self.assertTrue((self.target / "backups" / "historico_lives.bak.json").exists())
        self.assertFalse((self.target / "not-part-of-app.txt").exists())

    def test_update_does_not_overwrite_session_pending_or_history(self):
        originals = {}
        for name in installer.RUNTIME_FILES:
            originals[name] = ("current " + name).encode()
            self.write(self.target / name, originals[name])
            self.write(self.old / name, b"stale")
        installer.deploy_package(self.package, self.target, self.old)
        for name, expected in originals.items():
            self.assertEqual((self.target / name).read_bytes(), expected)

    def test_existing_runtime_does_not_mix_in_missing_legacy_files(self):
        self.write(self.target / "cloud_migracao.json", b"{}")
        self.write(self.old / "integracao_sessao.dat", b"another account")
        installer.deploy_package(self.package, self.target, self.old)
        self.assertFalse((self.target / "integracao_sessao.dat").exists())

    def test_existing_backup_alone_blocks_automatic_migration(self):
        self.write(self.target / "backups" / "live_atual.bak.json", b"{}")
        self.assertTrue(installer.has_runtime_data(self.target))

    def test_incomplete_package_does_not_touch_existing_install(self):
        self.write(self.target / installer.EXE_NAME, b"old executable")
        (self.package / "integracao_supabase.json").unlink()
        with self.assertRaises(RuntimeError):
            installer.deploy_package(self.package, self.target)
        self.assertEqual((self.target / installer.EXE_NAME).read_bytes(), b"old executable")

    def test_failure_rolls_back_program_and_keeps_runtime(self):
        self.write(self.target / installer.EXE_NAME, b"old executable")
        self.write(self.target / "cloud_pendencias.dat", b"pending")
        self.write(self.target / "integracao_supabase.json", b"old config")
        replace = os.replace

        def fail_config(source, target):
            if Path(source).parent.name == "new" and Path(target).name == "integracao_supabase.json":
                raise PermissionError("locked")
            return replace(source, target)

        with patch.object(installer.os, "replace", side_effect=fail_config):
            with self.assertRaises(PermissionError):
                installer.deploy_package(self.package, self.target)
        self.assertEqual((self.target / installer.EXE_NAME).read_bytes(), b"old executable")
        self.assertEqual((self.target / "cloud_pendencias.dat").read_bytes(), b"pending")
        self.assertEqual((self.target / "integracao_supabase.json").read_bytes(), b"old config")

    def test_failure_removes_only_newly_copied_legacy_data(self):
        self.write(self.old / "historico_lives.json", b"[]")
        replace = os.replace

        def fail_program(source, target):
            if Path(target).name == installer.EXE_NAME:
                raise PermissionError("locked")
            return replace(source, target)

        with patch.object(installer.os, "replace", side_effect=fail_program):
            with self.assertRaises(PermissionError):
                installer.deploy_package(self.package, self.target, self.old)
        self.assertFalse((self.target / "historico_lives.json").exists())
        self.assertEqual((self.old / "historico_lives.json").read_bytes(), b"[]")

    def test_previous_shortcut_takes_priority_over_source_files(self):
        for directory in (self.old, self.source, self.source / "dist"):
            self.write(directory / "live_atual.json", b"{}")
        self.assertEqual(installer.legacy_candidates(self.source, self.target, self.old), [self.old])

    def test_ambiguous_folders_are_not_silently_merged(self):
        self.write(self.source / "live_atual.json", b"{}")
        self.write(self.source / "dist" / "live_atual.json", b"{}")
        self.assertEqual(len(installer.legacy_candidates(self.source, self.target)), 2)

    def test_existing_installation_does_not_offer_legacy_migration(self):
        self.write(self.target / "integracao_sessao.dat", b"session")
        self.write(self.old / "live_atual.json", b"{}")
        self.assertEqual(installer.legacy_candidates(self.source, self.target, self.old), [])

    def test_fresh_zip_has_no_legacy_candidates(self):
        self.assertEqual(installer.legacy_candidates(self.source, self.target), [])

    def test_optional_direct_path_preserved_without_extra_metadata(self):
        automation = self.root / "iomarques-instagram-direct" / "app.py"
        self.write(automation, b"pass")
        self.assertEqual(installer.find_automation(self.source), str(automation))
        installer.deploy_package(self.package, self.target, automation_app=automation)
        saved = json.loads((self.target / "instalacao.json").read_text(encoding="utf-8"))
        self.assertEqual(set(saved), {"installer_version", "automation_app"})
        self.assertEqual(installer.find_automation(self.source, destination=self.target), str(automation))

    def test_local_install_directory_is_not_download_or_desktop(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}):
            self.assertEqual(installer.install_directory(), self.root / "Programs" / "IoMarquesLive")

    def test_command_failure_is_not_accepted_as_success(self):
        with patch.object(installer.subprocess, "run", return_value=subprocess.CompletedProcess([], 23)):
            with self.assertRaisesRegex(RuntimeError, "23"):
                installer.run_command(["fake"], self.source, self.root / "log")

    def test_running_app_blocks_install_before_build(self):
        processes = json.dumps([{"name": installer.EXE_NAME}])
        with patch.object(installer, "_powershell", return_value=processes):
            with self.assertRaisesRegex(RuntimeError, "Feche"):
                installer.ensure_app_closed()

    def test_python_source_app_blocks_install(self):
        processes = json.dumps([{
            "name": "python.exe",
            "command": subprocess.list2cmdline(["python.exe", "-X", "utf8", str(self.source / "app.py")]),
            "title": "",
        }])
        with patch.object(installer, "_powershell", return_value=processes):
            with self.assertRaisesRegex(RuntimeError, "Feche"):
                installer.ensure_app_closed(self.source, self.old, self.target)

    def test_python_legacy_app_blocks_install(self):
        processes = json.dumps([{
            "name": "pythonw.exe",
            "command": subprocess.list2cmdline(["pythonw.exe", str(self.old / "app.py")]),
        }])
        with patch.object(installer, "_powershell", return_value=processes):
            with self.assertRaisesRegex(RuntimeError, "Feche"):
                installer.ensure_app_closed(self.source, self.old, self.target)

    def test_relative_python_source_is_identified_by_sales_window(self):
        processes = json.dumps([{
            "name": "python.exe", "command": '"C:\\Python\\python.exe" .\\app.py',
            "title": "IoMarques Brechó - Controle de Vendas da Live · v1.7",
        }])
        with patch.object(installer, "_powershell", return_value=processes):
            with self.assertRaisesRegex(RuntimeError, "Feche"):
                installer.ensure_app_closed(self.source)

    def test_other_python_projects_do_not_block_install(self):
        commands = [
            ["python.exe", str(self.root / "other-project" / "app.py")],
            ["python.exe", "app.py"],
            ["python.exe", "-c", "print('test')", str(self.source / "app.py")],
            ["python.exe", "-m", "unittest", str(self.source / "app.py")],
            ["python.exe", "-cprint('test')", str(self.source / "app.py")],
        ]
        processes = json.dumps([{"name": "python.exe", "command": subprocess.list2cmdline(command),
                                 "title": "Another project"} for command in commands])
        with patch.object(installer, "_powershell", return_value=processes):
            installer.ensure_app_closed(self.source, self.old, self.target)

    def test_empty_process_list_allows_install(self):
        with patch.object(installer, "_powershell", return_value="[]"):
            installer.ensure_app_closed(self.source)

    def test_invalid_process_response_blocks_install_safely(self):
        for response in ("", "not json", "null", "{}", '["unexpected"]'):
            with self.subTest(response=response), patch.object(installer, "_powershell", return_value=response):
                with self.assertRaisesRegex(RuntimeError, "verificar"):
                    installer.ensure_app_closed(self.source)

    def create_directory_link(self, link, target):
        target.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            # A junction needs no administrator rights. Both paths are inside
            # this test's unique temporary directory, never real user folders.
            self.assertTrue(link.is_relative_to(self.root))
            self.assertTrue(target.is_relative_to(self.root))
            script = ("$ErrorActionPreference = 'Stop'; New-Item -ItemType Junction -Path '"
                      + str(link).replace("'", "''") + "' -Target '"
                      + str(target).replace("'", "''") + "' | Out-Null")
            subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                           check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            link.symlink_to(target, target_is_directory=True)

    def test_destination_junction_is_rejected_before_writing(self):
        outside = self.root / "outside"
        self.create_directory_link(self.target, outside)
        self.write(outside / installer.EXE_NAME, b"do not overwrite")
        with self.assertRaisesRegex(RuntimeError, "junções"):
            installer.deploy_package(self.package, self.target)
        self.assertEqual((outside / installer.EXE_NAME).read_bytes(), b"do not overwrite")
        self.assertEqual([path.name for path in outside.iterdir()], [installer.EXE_NAME])

    def test_destination_ancestor_junction_is_rejected(self):
        outside = self.root / "outside"
        self.create_directory_link(self.target, outside)
        with self.assertRaisesRegex(RuntimeError, "junções"):
            installer.deploy_package(self.package, self.target / "nested")
        self.assertFalse((outside / "nested").exists())

    def test_legacy_backup_junction_is_rejected_without_copy(self):
        outside = self.root / "outside"
        self.create_directory_link(self.old / "backups", outside)
        self.write(outside / "historico_lives.bak.json", b"preserved")
        with self.assertRaisesRegex(RuntimeError, "junções"):
            installer.deploy_package(self.package, self.target, self.old)
        self.assertEqual((outside / "historico_lives.bak.json").read_bytes(), b"preserved")
        self.assertFalse((self.target / installer.EXE_NAME).exists())

    def test_target_backup_junction_is_rejected_and_external_files_kept(self):
        self.target.mkdir()
        outside = self.root / "outside"
        self.create_directory_link(self.target / "backups", outside)
        self.write(outside / "other-file.json", b"unrelated")
        self.write(self.old / "backups" / "historico_lives.bak.json", b"history")
        with self.assertRaisesRegex(RuntimeError, "junções"):
            installer.deploy_package(self.package, self.target, self.old)
        self.assertEqual((outside / "other-file.json").read_bytes(), b"unrelated")
        self.assertFalse((outside / "historico_lives.bak.json").exists())

    def test_failed_rollback_preserves_recovery_and_restores_other_files(self):
        self.write(self.target / installer.EXE_NAME, b"old executable")
        self.write(self.target / "integracao_supabase.json", b"old config")
        self.write(self.old / "historico_lives.json", b"legacy history")
        replace = os.replace

        def fail_publication_and_recovery(source, target):
            source, target = Path(source), Path(target)
            if source.parent.name == "new" and target.name == "integracao_supabase.json":
                raise PermissionError("publication blocked")
            if source.parent.name == "old" and target.name == installer.EXE_NAME:
                raise PermissionError("recovery blocked")
            return replace(source, target)

        with patch.object(installer.os, "replace", side_effect=fail_publication_and_recovery):
            with self.assertRaisesRegex(RuntimeError, "recuperação automática") as failure:
                installer.deploy_package(self.package, self.target, self.old)
        recovery = list(self.target.glob(".instalar-*"))
        self.assertEqual(len(recovery), 1)
        self.assertIn(str(recovery[0]), str(failure.exception))
        self.assertEqual((recovery[0] / "old" / installer.EXE_NAME).read_bytes(), b"old executable")
        self.assertEqual((self.target / "integracao_supabase.json").read_bytes(), b"old config")
        self.assertFalse((self.target / "historico_lives.json").exists())
        self.assertEqual((self.old / "historico_lives.json").read_bytes(), b"legacy history")

    def test_completed_rollback_removes_only_owned_staging_directory(self):
        self.write(self.target / "unrelated-folder" / "keep.txt", b"keep")
        replace = os.replace

        def fail_config(source, target):
            if Path(source).parent.name == "new" and Path(target).name == "integracao_supabase.json":
                raise PermissionError("locked")
            return replace(source, target)

        with patch.object(installer.os, "replace", side_effect=fail_config):
            with self.assertRaises(PermissionError):
                installer.deploy_package(self.package, self.target)
        self.assertEqual(list(self.target.glob(".instalar-*")), [])
        self.assertEqual((self.target / "unrelated-folder" / "keep.txt").read_bytes(), b"keep")

    def test_shortcut_command_escapes_apostrophes_and_uses_windows_desktop(self):
        folder = self.root / "Dudu's folder"
        exe = folder / installer.EXE_NAME
        self.write(exe, b"exe")
        with patch.object(installer, "_powershell") as shell:
            installer.create_shortcut(exe)
        command = shell.call_args[0][0]
        self.assertIn("Dudu''s folder", command)
        self.assertIn("GetFolderPath('Desktop')", command)

    def test_build_allowlist_never_includes_runtime_or_published_backups(self):
        for name in installer.SOURCE_FILES:
            self.write(self.source / name, b"pass")
        for name in installer.ASSET_FILES:
            self.write(self.source / "assets" / name, b"asset")
        self.write(self.source / "integracao_sessao.dat", b"private session")
        self.write(self.source / "backups-publicados" / "backup.json", b"private history")
        work = self.root / "work"
        output = work / "package"

        def fake_config(path, root):
            self.write(path, b'{"public": true}')

        def fake_command(command, cwd, log):
            if "PyInstaller" in command:
                self.write(output / installer.EXE_NAME, b"compiled executable")

        with patch.object(installer, "write_config", side_effect=fake_config), \
                patch.object(installer, "run_command", side_effect=fake_command) as commands:
            installer.build_package(self.source, output, work, "python", self.root / "log")
        self.assertEqual(commands.call_args.args[0], [str(output / installer.EXE_NAME), "--verificar-instalacao"])
        staged = {p.relative_to(work / "source").as_posix()
                  for p in (work / "source").rglob("*") if p.is_file()}
        self.assertEqual(staged, set(installer.SOURCE_FILES) | {"config_publica.json"}
                         | {"assets/" + name for name in installer.ASSET_FILES})


if __name__ == "__main__":
    unittest.main()
