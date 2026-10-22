import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import installation
import update_helper


PACKAGE_NAMES = (installation.EXE_NAME, update_helper.UPDATER_NAME,
                 "integracao_supabase.json", "release.json")


class UpdateHelperTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.packages = patch.object(installation, "PACKAGE_FILES", PACKAGE_NAMES)
        self.packages.start()
        self.addCleanup(self.packages.stop)
        self.destination = installation.install_directory()
        self.destination.mkdir(parents=True)
        self.cache = update_helper.cache_root()
        self.cache.mkdir(parents=True)
        for name in PACKAGE_NAMES:
            (self.destination / name).write_bytes(("old:" + name).encode("utf-8"))
        self.current_config = b'{"public_config":"existing-store"}\n'
        (self.destination / "integracao_supabase.json").write_bytes(self.current_config)
        self.metadata = {"installer_version": 1, "automation_app": str(self.root / "direct" / "app.py"),
                         "custom_option": "preserve-me"}
        (self.destination / "instalacao.json").write_text(json.dumps(self.metadata), encoding="utf-8")
        for name in installation.RUNTIME_FILES:
            (self.destination / name).write_bytes(("runtime:" + name).encode("utf-8"))
        backups = self.destination / "backups"
        backups.mkdir()
        (backups / "historico_lives.bak.json").write_text("preserved backup", encoding="utf-8")
        helper = self.destination / update_helper.UPDATER_NAME
        self.manifest = {"schema": 1, "version": "1.9.0", "files": {
            update_helper.UPDATER_NAME: {"sha256": update_helper._hash_file(helper),
                                         "size": helper.stat().st_size}}}
        (self.destination / "release.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        self.archive = self.cache / "update.zip"
        self.archive.write_bytes(b"a verified archive supplied by the package-validation fixture")
        self.ready = {"path": str(self.archive), "version": "1.9.1", "size": self.archive.stat().st_size,
                      "sha256": update_helper._hash_file(self.archive)}
        self.validate = Mock(return_value={"schema": 1})
        self.extract = Mock(side_effect=self._extract)
        engine = SimpleNamespace(validate_package=self.validate, extract_verified_package=self.extract)
        self.engine = patch.dict(sys.modules, {"update_client": engine})
        self.engine.start()
        self.addCleanup(self.engine.stop)

    def _extract(self, archive, package, version, digest, size):
        for name in PACKAGE_NAMES:
            (Path(package) / name).write_bytes(("new:" + name).encode("utf-8"))
        return {"schema": 1, "version": version}

    def prepare(self, **overrides):
        arguments = {"destination": self.destination, "parent_pid": 123,
                     "parent_executable": self.destination / installation.EXE_NAME}
        arguments.update(overrides)
        with patch.object(update_helper, "_parent_started", return_value=456):
            return update_helper.prepare_update_job(self.ready, **arguments)

    def assert_runtime_preserved(self):
        for name in installation.RUNTIME_FILES:
            self.assertEqual((self.destination / name).read_bytes(), ("runtime:" + name).encode("utf-8"))
        self.assertEqual((self.destination / "backups" / "historico_lives.bak.json").read_text(),
                         "preserved backup")

    def snapshot(self):
        return {path.name: path.read_bytes() for path in self.destination.iterdir() if path.is_file()}

    def test_prepare_copies_verified_installed_helper_to_unique_cache_directory(self):
        prepared = self.prepare()
        self.assertNotEqual(prepared.helper, self.destination / update_helper.UPDATER_NAME)
        self.assertTrue(prepared.helper.parent.name.startswith("aplicar-"))
        self.assertEqual(prepared.helper.read_bytes(), (self.destination / update_helper.UPDATER_NAME).read_bytes())
        self.assertEqual(prepared.command, [str(prepared.helper), "--job", str(prepared.job)])
        job = json.loads(prepared.job.read_text(encoding="utf-8"))
        self.assertEqual(job["parent_started"], 456)
        self.assertEqual(job["parent_pid"], 123)
        self.validate.assert_called_once_with(self.archive, "1.9.1", self.ready["sha256"], self.ready["size"])

    def test_prepare_rejects_other_installation_destination(self):
        with self.assertRaises(RuntimeError):
            self.prepare(destination=self.root / "other-program")
        self.validate.assert_not_called()

    def test_prepare_rejects_archive_outside_cache(self):
        external = self.root / "outside.zip"
        external.write_bytes(b"data")
        self.ready["path"] = str(external)
        with self.assertRaises(RuntimeError):
            self.prepare()
        self.validate.assert_not_called()

    def test_prepare_rejects_other_parent_executable(self):
        with self.assertRaises(RuntimeError):
            self.prepare(parent_executable=self.destination / "Other.exe")
        self.validate.assert_not_called()

    def test_prepare_rejects_bad_download_before_writing_job(self):
        self.validate.side_effect = RuntimeError("bad archive")
        with self.assertRaises(RuntimeError):
            self.prepare()
        self.assertFalse(list(self.cache.glob("aplicar-*")))

    def test_prepare_rejects_installed_helper_without_matching_release_hash(self):
        (self.destination / update_helper.UPDATER_NAME).write_bytes(b"modified executable")
        with self.assertRaises(RuntimeError):
            self.prepare()
        self.assertFalse(list(self.cache.glob("aplicar-*")))

    def test_prepare_rejects_cache_folder_outside_app_cache(self):
        with self.assertRaises(RuntimeError):
            self.prepare(cache_dir=self.root / "downloads")

    def test_prepare_rejects_same_or_older_version_before_parent_shutdown(self):
        for version in ("1.9.0", "1.8.9", "0.99.99"):
            with self.subTest(version=version):
                self.ready["version"] = version
                with patch.object(update_helper, "_parent_started") as identify:
                    with self.assertRaisesRegex(RuntimeError, "versão mais nova"):
                        update_helper.prepare_update_job(self.ready, parent_pid=123,
                                                         parent_executable=self.destination / installation.EXE_NAME)
                identify.assert_not_called()
        self.assertFalse(list(self.cache.glob("aplicar-*")))

    def test_version_comparison_is_numeric_not_lexical(self):
        self.manifest["version"] = "1.9.9"
        (self.destination / "release.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        self.ready["version"] = "1.10.0"
        self.assertIsInstance(self.prepare(), update_helper.PreparedUpdate)

    def test_load_rejects_unknown_fields_boolean_pid_and_invalid_checksums(self):
        prepared = self.prepare()
        original = json.loads(prepared.job.read_text(encoding="utf-8"))
        for change in ({"unknown": 123}, {"parent_pid": True}, {"parent_pid": 0},
                       {"size": True}, {"size": 0}, {"sha256": "no"},
                       {"version": "01.9.1"}, {"version": "١.٩.١"}, {"parent_started": -1},
                       {"destination": None}, {"destination": "relative"}):
            with self.subTest(change=change):
                prepared.job.write_text(json.dumps({**original, **change}), encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    update_helper.load_update_job(prepared.job)

    def test_load_rejects_large_or_invalid_job(self):
        prepared = self.prepare()
        for content in (b"[1,2]", b"not json", b" " * (update_helper.MAX_JOB_SIZE + 1)):
            with self.subTest(size=len(content)):
                prepared.job.write_bytes(content)
                with self.assertRaises(RuntimeError):
                    update_helper.load_update_job(prepared.job)

    def test_load_rejects_ambiguous_duplicate_fields(self):
        prepared = self.prepare()
        raw = prepared.job.read_text(encoding="utf-8")
        prepared.job.write_text(raw.replace('"schema": 1', '"schema": 1, "schema": 1'), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "dados da atualização"):
            update_helper.load_update_job(prepared.job)

    def test_launch_rechecks_copied_helper_and_uses_no_shell(self):
        prepared = self.prepare()
        with patch.object(update_helper.subprocess, "Popen") as popen:
            process = update_helper.launch_update(prepared)
        self.assertIs(process, popen.return_value)
        self.assertEqual(popen.call_args.args[0], prepared.command)
        self.assertNotIn("shell", popen.call_args.kwargs)
        self.assertEqual(popen.call_args.kwargs["cwd"], prepared.job.parent)

    def test_launch_rejects_tampered_helper(self):
        prepared = self.prepare()
        prepared.helper.write_bytes(b"changed")
        with patch.object(update_helper.subprocess, "Popen") as popen:
            with self.assertRaises(RuntimeError):
                update_helper.launch_update(prepared)
        popen.assert_not_called()

    def test_apply_checks_parent_before_package_and_installs_only_program_files(self):
        prepared = self.prepare()
        events = []
        wait = Mock(side_effect=lambda job: events.append("parent"))
        closed = Mock(side_effect=lambda **kwargs: events.append("closed"))
        runner = Mock(return_value=SimpleNamespace(returncode=0))
        launcher = Mock(side_effect=lambda path: events.append("launch"))

        def deploy(package, target):
            events.append("deploy")
            self.assertEqual((package / "integracao_supabase.json").read_bytes(), self.current_config)
            for name in PACKAGE_NAMES:
                (target / name).write_bytes((package / name).read_bytes())

        executable = update_helper.apply_update(prepared.job, wait_parent=wait, ensure_closed=closed,
                                                deploy=deploy, runner=runner, launcher=launcher)
        self.assertEqual(executable, self.destination / installation.EXE_NAME)
        self.assertEqual(events, ["parent", "closed", "closed", "deploy", "launch"])
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(runner.call_args_list[0].args[0][1], "--verificar-instalacao")
        self.assertEqual(runner.call_args_list[1].args[0][1], "--verificar-atualizador")
        self.assertEqual((self.destination / "integracao_supabase.json").read_bytes(), self.current_config)
        self.assertEqual(json.loads((self.destination / "instalacao.json").read_text()), self.metadata)
        self.assertEqual(list(prepared.job.parent.glob("pacote-*")), [])
        self.assert_runtime_preserved()

    def test_wait_failure_never_extracts_deploys_or_reopens_existing_app(self):
        prepared = self.prepare()
        old = self.snapshot()
        deploy, launcher = Mock(), Mock()
        with self.assertRaises(RuntimeError):
            update_helper.apply_update(prepared.job, wait_parent=Mock(side_effect=update_helper.UpdateError("still open")),
                                        ensure_closed=Mock(), deploy=deploy, launcher=launcher)
        self.extract.assert_not_called()
        deploy.assert_not_called()
        launcher.assert_not_called()
        self.assertEqual(self.snapshot(), old)

    def test_other_open_app_blocks_update_without_launching_another_instance(self):
        prepared = self.prepare()
        deploy, launcher = Mock(), Mock()
        with self.assertRaises(RuntimeError):
            update_helper.apply_update(prepared.job, wait_parent=Mock(),
                                        ensure_closed=Mock(side_effect=RuntimeError("app open")),
                                        deploy=deploy, launcher=launcher,
                                        wait_closed=lambda target, check: update_helper._wait_until_app_closed(
                                            target, check, timeout=0))
        self.extract.assert_not_called()
        deploy.assert_not_called()
        launcher.assert_not_called()

    def test_bootloader_cleanup_waits_briefly_and_then_allows_update(self):
        prepared = self.prepare()
        elapsed = [0.0]
        slept = []

        def sleep(seconds):
            slept.append(seconds)
            elapsed[0] += seconds

        def wait_closed(target, check):
            update_helper._wait_until_app_closed(target, check, clock=lambda: elapsed[0], sleeper=sleep)

        closed = Mock(side_effect=[RuntimeError("bootloader cleaning up"), None, None])
        launcher, deploy = Mock(), Mock()
        update_helper.apply_update(prepared.job, wait_parent=Mock(), ensure_closed=closed,
                                    deploy=deploy, runner=Mock(return_value=SimpleNamespace(returncode=0)),
                                    launcher=launcher, wait_closed=wait_closed)
        self.assertEqual(slept, [0.25])
        self.assertEqual(closed.call_count, 3)
        deploy.assert_called_once()
        launcher.assert_called_once_with(self.destination / installation.EXE_NAME)

    def test_persistent_second_instance_is_blocked_after_ten_seconds_without_actual_sleep(self):
        prepared = self.prepare()
        original = self.snapshot()
        elapsed = [0.0]
        slept = []

        def sleep(seconds):
            slept.append(seconds)
            elapsed[0] += seconds

        def wait_closed(target, check):
            update_helper._wait_until_app_closed(target, check, clock=lambda: elapsed[0], sleeper=sleep)

        closed = Mock(side_effect=RuntimeError("a second app instance remains open"))
        deploy, launcher = Mock(), Mock()
        with self.assertRaisesRegex(RuntimeError, "Feche todas as janelas"):
            update_helper.apply_update(prepared.job, wait_parent=Mock(), ensure_closed=closed,
                                        deploy=deploy, launcher=launcher, wait_closed=wait_closed)
        self.assertEqual(elapsed[0], 10.0)
        self.assertEqual(slept, [0.25] * 40)
        self.assertEqual(closed.call_count, 41)
        self.extract.assert_not_called()
        deploy.assert_not_called()
        launcher.assert_not_called()
        self.assertEqual(self.snapshot(), original)

    def test_app_updated_by_another_job_cannot_be_downgraded_by_queued_job(self):
        for version in ("1.9.1", "1.10.0"):
            with self.subTest(installed=version):
                self.manifest["version"] = "1.9.0"
                (self.destination / "release.json").write_text(json.dumps(self.manifest), encoding="utf-8")
                prepared = self.prepare()
                self.manifest["version"] = version
                (self.destination / "release.json").write_text(json.dumps(self.manifest), encoding="utf-8")
                old = self.snapshot()
                deploy, launcher = Mock(), Mock()
                with self.assertRaisesRegex(RuntimeError, "versão mais nova"):
                    update_helper.apply_update(prepared.job, wait_parent=Mock(), ensure_closed=Mock(),
                                                deploy=deploy, launcher=launcher)
                deploy.assert_not_called()
                launcher.assert_not_called()
                self.assertEqual(self.snapshot(), old)
        self.extract.assert_not_called()

    def test_failed_preflight_preserves_original_program_and_reopens_it(self):
        prepared = self.prepare()
        old = self.snapshot()
        deploy, launcher = Mock(), Mock()
        with self.assertRaisesRegex(RuntimeError, "verificação"):
            update_helper.apply_update(prepared.job, wait_parent=Mock(), ensure_closed=Mock(),
                                        deploy=deploy, runner=Mock(return_value=SimpleNamespace(returncode=1)),
                                        launcher=launcher)
        deploy.assert_not_called()
        launcher.assert_called_once_with(self.destination / installation.EXE_NAME)
        self.assertEqual(self.snapshot(), old)
        self.assert_runtime_preserved()

    def test_failed_deploy_reopens_only_if_rollback_preserved_program_files(self):
        prepared = self.prepare()
        launcher = Mock()
        with self.assertRaisesRegex(RuntimeError, "preservados"):
            update_helper.apply_update(prepared.job, wait_parent=Mock(), ensure_closed=Mock(),
                                        deploy=Mock(side_effect=OSError("SECRET-do-not-log")),
                                        runner=Mock(return_value=SimpleNamespace(returncode=0)), launcher=launcher)
        launcher.assert_called_once_with(self.destination / installation.EXE_NAME)
        self.assertNotIn("SECRET", (prepared.job.parent / "atualizacao.log").read_text())
        self.assert_runtime_preserved()

    def test_real_deployment_preserves_metadata_config_sessions_and_backups(self):
        prepared = self.prepare()
        update_helper.apply_update(prepared.job, wait_parent=Mock(), ensure_closed=Mock(),
                                    runner=Mock(return_value=SimpleNamespace(returncode=0)), launcher=Mock())
        self.assertEqual((self.destination / installation.EXE_NAME).read_bytes(),
                         ("new:" + installation.EXE_NAME).encode("utf-8"))
        self.assertEqual((self.destination / "integracao_supabase.json").read_bytes(), self.current_config)
        self.assertEqual(json.loads((self.destination / "instalacao.json").read_text()), self.metadata)
        self.assertEqual(list(self.destination.glob(".instalar-*")), [])
        self.assert_runtime_preserved()

    def test_real_deployment_rolls_back_program_files_after_replace_failure(self):
        prepared = self.prepare()
        original = self.snapshot()
        replace = os.replace
        failed = False

        def fail_once(source, target):
            nonlocal failed
            if Path(target) == self.destination / "integracao_supabase.json" and not failed:
                failed = True
                raise OSError("fixture: failed replacing config")
            return replace(source, target)

        launcher = Mock()
        with patch.object(installation.os, "replace", side_effect=fail_once):
            with self.assertRaisesRegex(RuntimeError, "preservados"):
                update_helper.apply_update(prepared.job, wait_parent=Mock(), ensure_closed=Mock(),
                                            runner=Mock(return_value=SimpleNamespace(returncode=0)), launcher=launcher)
        self.assertTrue(failed)
        launcher.assert_called_once_with(self.destination / installation.EXE_NAME)
        self.assertEqual(self.snapshot(), original)
        self.assertEqual(list(self.destination.glob(".instalar-*")), [])
        self.assert_runtime_preserved()

    def test_incomplete_rollback_does_not_launch_partially_replaced_app(self):
        prepared = self.prepare()
        launcher = Mock()

        def broken_deploy(package, target):
            (target / installation.EXE_NAME).write_bytes(b"partial")
            raise OSError("failure with incomplete rollback")

        with self.assertRaisesRegex(RuntimeError, "instalador novamente"):
            update_helper.apply_update(prepared.job, wait_parent=Mock(), ensure_closed=Mock(),
                                        deploy=broken_deploy, runner=Mock(return_value=SimpleNamespace(returncode=0)),
                                        launcher=launcher)
        launcher.assert_not_called()
        self.assert_runtime_preserved()

    def test_missing_parent_process_is_not_killed_or_waited_again(self):
        with patch.object(update_helper, "_process_handle", return_value=(Mock(), None)):
            update_helper.wait_for_parent({"parent_pid": 123})

    def test_parent_identity_must_match_executable_and_creation_time(self):
        prepared = self.prepare()
        job = update_helper.load_update_job(prepared.job)
        for identity in ((self.destination / "Other.exe", 456),
                         (self.destination / installation.EXE_NAME, 999)):
            with self.subTest(identity=identity):
                kernel = Mock()
                with patch.object(update_helper, "_process_handle", return_value=(kernel, 99)), \
                        patch.object(update_helper, "_process_identity", return_value=identity):
                    with self.assertRaisesRegex(RuntimeError, "processo aberto mudou"):
                        update_helper.wait_for_parent(job)
                kernel.WaitForSingleObject.assert_not_called()
                kernel.CloseHandle.assert_called_once_with(99)

    def test_parent_wait_timeout_never_terminates_process(self):
        prepared = self.prepare()
        job = update_helper.load_update_job(prepared.job)
        kernel = Mock()
        kernel.WaitForSingleObject.return_value = 0x102
        with patch.object(update_helper, "_process_handle", return_value=(kernel, 99)), \
                patch.object(update_helper, "_process_identity",
                             return_value=(self.destination / installation.EXE_NAME, 456)):
            with self.assertRaisesRegex(RuntimeError, "ainda está aberto"):
                update_helper.wait_for_parent(job, timeout=90)
        kernel.WaitForSingleObject.assert_called_once_with(99, 90000)
        kernel.TerminateProcess.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(99)

    def test_self_check_mode_never_reads_jobs_or_starts_app(self):
        with patch.object(update_helper, "apply_update") as apply:
            self.assertEqual(update_helper.main(["--verificar-atualizador"]), 0)
        apply.assert_not_called()

    def test_another_running_update_blocks_all_installation_actions(self):
        prepared = self.prepare()
        wait, deploy = Mock(), Mock()
        with patch.object(update_helper, "_acquire_installation_lock",
                          side_effect=update_helper.UpdateError("Outra atualização")):
            with self.assertRaisesRegex(RuntimeError, "Outra atualização"):
                update_helper.apply_update(prepared.job, wait_parent=wait, deploy=deploy)
        wait.assert_not_called()
        deploy.assert_not_called()
        self.extract.assert_not_called()

    def test_installation_lock_is_released_after_failure(self):
        prepared = self.prepare()
        release = Mock()
        with patch.object(update_helper, "_acquire_installation_lock", return_value=release):
            with self.assertRaises(RuntimeError):
                update_helper.apply_update(prepared.job,
                                            wait_parent=Mock(side_effect=RuntimeError("still open")))
        release.assert_called_once_with()

    def test_reparse_point_cache_path_is_rejected(self):
        external = self.root / "external"
        external.mkdir()
        link = self.cache / "link"
        try:
            link.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Windows did not allow a fixture symlink")
        (external / "update.zip").write_bytes(b"data")
        with self.assertRaises(RuntimeError):
            update_helper._cached_path(link / "update.zip")

    def test_windows_reparse_attribute_is_rejected_without_requiring_link_privilege(self):
        folder = self.cache / "junction-fixture"
        folder.mkdir()
        original = Path.lstat

        def attributes(path, *args, **kwargs):
            if path == folder:
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_file_attributes=0x400)
            return original(path, *args, **kwargs)

        with patch.object(Path, "lstat", attributes):
            with self.assertRaisesRegex(RuntimeError, "links ou junções"):
                update_helper._cached_path(folder / "update.zip")


if __name__ == "__main__":
    unittest.main()
