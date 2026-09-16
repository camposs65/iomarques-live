"""No remote service, credentials, Tk window or real user files are used."""

import base64
import copy
import json
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

import cloud_store
from cloud_store import CloudConflictError, CloudDeletedError, CloudLiveStore
from supabase_sync import SyncAuthError, SyncConfig, SyncTemporaryError


def state(key="live_test", client="cliente-teste"):
    return {"updated_at": "2026-09-16T20:00:00", "live": {
        "id": key, "running": True, "started_at": "2026-09-16T19:00:00",
        "first_started_at": "2026-09-16T19:00:00", "finished_at": None,
        "elapsed_seconds": 30}, "rows": [
            {"codigo": "001", "valor": "10,00", "cliente": client,
             "suplente1": "suplente-teste", "suplente2": "", "tempo": "00:00:10"}]}


def history(key="live_test", client="cliente-teste"):
    return {"id": key, "started_at": "2026-09-16T19:00:00",
            "finished_at": "2026-09-16T20:00:00", "duration": "01:00:00",
            "total": "10,00", "pieces_count": 1, "sold_count": 1,
            "rows": copy.deepcopy(state(key, client)["rows"])}


def document(key="live_test", finalized=False):
    data = state(key)
    if finalized:
        data["live"]["running"] = False
        data["live"]["finished_at"] = "2026-09-16T20:00:00"
    return {"estado": data, "historico": history(key) if finalized else None}


def envelope(key="live_test", version=1, doc=None, operation=None):
    return {"id": key, "versao": version, "documento": doc or document(key),
            "ultima_operacao": operation or str(uuid.uuid4()),
            "atualizado_em": "2026-09-16T23:00:00Z"}


class FakeCloud:
    def __init__(self):
        self.rows = {}
        self.tombstones = set()
        self.calls = []
        self.before_save = None
        self.after_save = None

    def rpc(self, name, arguments):
        self.calls.append((name, copy.deepcopy(arguments)))
        if name == "listar_versoes_lives":
            return [{"id": key, "versao": self.rows[key]["versao"]} for key in sorted(self.rows)
                    if key > arguments["p_apos_id"]][:arguments["p_limite"]]
        if name == "obter_dados_lives":
            return [copy.deepcopy(self.rows[key]) for key in sorted(arguments["p_ids"])
                    if key in self.rows]
        if name == "listar_dados_lives":
            return [copy.deepcopy(self.rows[key]) for key in sorted(self.rows)
                    if key > arguments["p_apos_id"]][:arguments["p_limite"]]
        key = arguments["p_live_id"]
        if name == "excluir_live_vendas_importada":
            self.rows.pop(key, None)
            self.tombstones.add(key)
            return key
        if name != "salvar_dados_live":
            raise AssertionError("Unexpected RPC")
        if self.before_save:
            callback, self.before_save = self.before_save, None
            callback(arguments)
        if key in self.tombstones:
            raise CloudDeletedError("Deleted")
        previous = self.rows.get(key)
        if previous and previous["ultima_operacao"] == arguments["p_operacao_id"]:
            if previous["documento"] != arguments["p_documento"]:
                raise CloudConflictError("Mutated operation")
            return copy.deepcopy(previous)
        if arguments["p_versao_esperada"] != (previous["versao"] if previous else 0):
            raise CloudConflictError("Version conflict")
        result = envelope(key, arguments["p_versao_esperada"] + 1,
                          copy.deepcopy(arguments["p_documento"]), arguments["p_operacao_id"])
        self.rows[key] = copy.deepcopy(result)
        if self.after_save:
            callback, self.after_save = self.after_save, None
            callback(result)
        return result


class CloudStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = SyncConfig("https://example.invalid", "public-test", "acesso@example.invalid")
        for name, value in (
            ("load_sync_config", Mock(return_value=self.config)),
            ("_protect_windows", lambda data: b"protected:" + data[::-1]),
            ("_unprotect_windows", lambda data: data[len(b"protected:"):][::-1]),
        ):
            active_patch = patch.object(cloud_store, name, value)
            active_patch.start()
            self.addCleanup(active_patch.stop)
        # Guard against any accidental network call from a test.
        network = patch("urllib.request.urlopen", side_effect=AssertionError("Network forbidden"))
        network.start()
        self.addCleanup(network.stop)
        self.cloud = FakeCloud()
        self.manager = self.make_manager()

    def make_manager(self):
        manager = CloudLiveStore(self.root)
        manager._schedule_flush = Mock()
        manager._rpc = self.cloud.rpc
        manager._load_profile = Mock(return_value=True)
        manager.session = {"username": "teste", "refresh_token": "test",
                           "access_token": "test", "expires_at": time.time() + 3600}
        self.addCleanup(manager.shutdown)
        return manager

    def test_queues_complete_data_encrypted_without_plaintext_files(self):
        data = state()
        self.assertTrue(self.manager.queue_state(data))
        self.assertTrue(self.manager.ready)
        self.assertFalse(self.manager.cloud_loaded)
        self.assertEqual(self.manager.status, "pending")
        raw = self.manager.queue_path.read_bytes()
        self.assertNotIn(b"cliente-teste", raw)
        self.assertNotIn(b"suplente-teste", raw)
        self.assertEqual({p.name for p in self.root.iterdir()}, {"cloud_pendencias.dat"})
        data["rows"].clear()
        self.assertEqual(len(self.manager.current_states()[0]["rows"]), 1)
        recovered = self.make_manager()
        self.assertEqual(recovered.pending_states(), self.manager.pending_states())

    def test_successful_ack_removes_only_temporary_data(self):
        self.manager.queue_state(state())
        self.manager._flush()
        self.assertFalse(self.manager.has_pending)
        self.assertFalse(self.manager.queue_path.exists())
        self.assertEqual(self.manager.status, "synced")
        self.assertEqual(self.manager.current_states(), [state()])
        restarted = self.make_manager()
        self.assertEqual(restarted.current_states(), [])
        self.assertFalse(restarted.ready)
        restarted._flush()
        self.assertEqual(restarted.current_states(), [state()])

    def test_history_keeps_sold_clients_and_substitutes(self):
        self.manager.queue_state(document(finalized=True)["estado"], history())
        self.manager._flush()
        sent = next(args for name, args in self.cloud.calls if name == "salvar_dados_live")
        self.assertEqual(sent["p_documento"]["historico"]["rows"][0]["cliente"], "cliente-teste")
        self.assertEqual(sent["p_documento"]["historico"]["rows"][0]["suplente1"], "suplente-teste")
        self.assertEqual(sent["p_resumo"]["pecas"], [])
        self.assertEqual(sent["p_resumo"]["total_vendido_centavos"], 1000)
        self.assertEqual(self.manager.current_states(), [])
        self.assertEqual(self.manager.history(), [history()])

    def test_history_queue_only_changes_not_every_loaded_record(self):
        self.cloud.rows["live_test"] = envelope(doc=document(finalized=True))
        self.manager._flush()
        self.assertTrue(self.manager.queue_history(self.manager.history()))
        self.assertFalse(self.manager.has_pending)
        self.assertFalse(self.manager.queue_path.exists())

    def test_offline_start_can_only_restore_pending_not_invent_full_history(self):
        self.manager.queue_state(state())
        self.manager._load_profile.side_effect = SyncTemporaryError("Offline")
        self.manager._flush()
        self.assertEqual(self.manager.status, "pending")
        self.assertIn("histórico completo", self.manager.detail)
        self.assertTrue(self.manager.ready)
        self.assertFalse(self.manager.cloud_loaded)
        self.assertEqual(self.manager.history(), [])

    def test_encrypt_failure_does_not_mutate_accepted_state(self):
        with patch.object(cloud_store, "_protect_windows", side_effect=OSError("Cannot protect")):
            self.assertFalse(self.manager.queue_state(state()))
        self.assertEqual(self.manager.status, "error")
        self.assertFalse(self.manager.has_pending)
        self.assertFalse(self.manager.queue_path.exists())

    def test_corrupt_queue_is_preserved_and_blocks_new_saves(self):
        self.manager.queue_path.write_bytes(b"not-a-valid-queue")
        manager = self.make_manager()
        self.assertFalse(manager.ready)
        self.assertFalse(manager.queue_state(state()))
        self.assertEqual(manager.queue_path.read_bytes(), b"not-a-valid-queue")
        self.assertEqual(manager.status, "error")

    def test_queue_from_other_project_is_not_uploaded(self):
        self.manager.queue_state(state())
        with patch.object(cloud_store, "load_sync_config", return_value=SyncConfig(
                "https://other.invalid", "other-public", "acesso@example.invalid")):
            manager = self.make_manager()
        self.assertFalse(manager.ready)
        manager._flush()
        self.assertEqual(self.cloud.calls, [])

    def test_invalid_ack_is_never_marked_saved(self):
        self.manager.queue_state(state())
        original = self.cloud.rpc

        def invalid(name, args):
            result = original(name, args)
            if name == "salvar_dados_live":
                result["versao"] += 1
            return result

        self.manager._rpc = invalid
        self.manager._flush()
        self.assertTrue(self.manager.has_pending)
        self.assertNotEqual(self.manager.status, "synced")
        self.assertTrue(self.manager.queue_path.exists())

    def test_lost_response_reuses_identical_operation_and_payload(self):
        self.manager.queue_state(state())
        self.cloud.after_save = lambda _result: (_ for _ in ()).throw(SyncTemporaryError("Lost response"))
        self.manager._flush()
        self.assertTrue(self.manager.has_pending)
        restarted = self.make_manager()
        restarted._flush()
        calls = [args for name, args in self.cloud.calls if name == "salvar_dados_live"]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(self.cloud.rows["live_test"]["versao"], 1)
        self.assertFalse(restarted.has_pending)

    def test_new_edits_during_upload_wait_for_correct_next_revision(self):
        self.manager.queue_state(state())
        newer = state(client="cliente-nova")
        self.cloud.before_save = lambda _args: self.manager.queue_state(newer)
        self.manager._flush()
        self.assertTrue(self.manager.has_pending)
        self.assertEqual(self.manager.current_states(), [newer])
        self.assertEqual(self.manager._pending["live_test"]["versao_esperada"], 1)
        self.manager._flush()
        self.assertFalse(self.manager.has_pending)
        self.assertEqual(self.cloud.rows["live_test"]["versao"], 2)
        self.assertEqual(self.cloud.rows["live_test"]["documento"]["estado"], newer)

    def test_new_edit_after_uncertain_save_does_not_change_immutable_request(self):
        self.manager.queue_state(state())
        self.cloud.after_save = lambda _result: (_ for _ in ()).throw(SyncTemporaryError("Lost response"))
        self.manager._flush()
        newer = state(client="nova")
        self.manager.queue_state(newer)
        recovered = self.make_manager()
        recovered._flush()
        calls = [args for name, args in self.cloud.calls if name == "salvar_dados_live"]
        self.assertEqual(calls[0], calls[1])
        self.assertTrue(recovered.has_pending)
        recovered._flush()
        self.assertEqual(self.cloud.rows["live_test"]["documento"]["estado"], newer)

    def test_disk_failure_after_ack_retries_without_forgetting_pending(self):
        self.manager.queue_state(state())
        original = self.manager._persist_locked
        failed = {"once": False}

        def failing(pending, legacy_ids=None, legacy_resolved=None):
            if not pending and not failed["once"]:
                failed["once"] = True
                raise cloud_store.CloudStorageError("Disk failure")
            return original(pending, legacy_ids, legacy_resolved)

        self.manager._persist_locked = failing
        self.manager._flush()
        self.assertTrue(self.manager.has_pending)
        self.assertEqual(self.manager.status, "error")
        self.manager._flush()
        self.assertFalse(self.manager.has_pending)
        self.assertEqual(self.cloud.rows["live_test"]["versao"], 1)

    def test_version_conflict_preserves_local_and_remote_without_overwrite(self):
        self.cloud.rows["live_test"] = envelope()
        self.manager._flush()
        self.manager.begin_edit("live_test", state=state())
        self.manager.queue_state(state(client="local"))
        remote = document()
        remote["estado"] = state(client="remote")
        self.cloud.rows["live_test"] = envelope(version=2, doc=remote)
        self.manager._flush()
        self.assertEqual(self.manager.status, "conflict")
        self.assertEqual(self.manager.conflicts()[0]["motivo"], "versao")
        self.assertEqual(self.manager.current_states()[0]["rows"][0]["cliente"], "local")
        self.assertEqual(self.manager.remote_document("live_test"), remote)
        self.assertEqual(self.cloud.rows["live_test"]["versao"], 2)
        self.manager.queue_state(state(client="local-even-newer"))
        self.assertEqual(self.manager.status, "conflict")
        self.assertEqual(self.manager.conflicts()[0]["documento"]["estado"]["rows"][0]["cliente"], "local-even-newer")

    def test_background_pull_cannot_bless_stale_editor_revision(self):
        self.cloud.rows["live_test"] = envelope()
        self.manager._flush()
        self.manager.begin_edit("live_test", state=state())
        newer = document()
        newer["estado"] = state(client="remote")
        self.cloud.rows["live_test"] = envelope(version=2, doc=newer)
        self.manager._flush()
        self.manager.queue_state(state(client="stale-local"))
        self.manager._flush()
        self.assertEqual(self.manager.status, "conflict")
        self.assertEqual(self.cloud.rows["live_test"]["documento"], newer)

    def test_snapshot_already_stale_when_opening_is_not_blessed(self):
        newer = document()
        newer["estado"] = state(client="remote")
        self.cloud.rows["live_test"] = envelope(version=2, doc=newer)
        self.manager._flush()
        self.manager.begin_edit("live_test", state=state())
        self.manager.queue_state(state(client="stale-local"))
        self.manager._flush()
        self.assertEqual(self.manager.status, "conflict")
        self.assertEqual(self.cloud.rows["live_test"]["versao"], 2)

    def test_own_ack_advances_pinned_editor_revision(self):
        self.cloud.rows["live_test"] = envelope()
        self.manager._flush()
        self.manager.begin_edit("live_test", state=state())
        self.manager.queue_state(state(client="edit-one"))
        self.manager._flush()
        self.manager.queue_state(state(client="edit-two"))
        self.manager._flush()
        self.assertEqual(self.manager.status, "synced")
        self.assertEqual(self.cloud.rows["live_test"]["versao"], 3)

    def test_discard_is_explicit_and_only_removes_that_conflict(self):
        self.manager.queue_state(state())
        self.cloud.rows["live_test"] = envelope(version=2)
        self.manager.queue_state(state("another"))
        self.manager._flush()
        self.assertEqual(len(self.manager.conflicts()), 1)
        self.assertFalse(self.manager.discard_conflict("another"))
        self.assertTrue(self.manager.discard_conflict("live_test"))
        self.assertEqual(self.manager.conflicts(), [])
        self.assertEqual(set(self.cloud.rows), {"live_test", "another"})

    def test_staff_cannot_queue_delete_and_admin_sees_data_until_ack(self):
        self.cloud.rows["live_test"] = envelope(doc=document(finalized=True))
        self.manager._load_profile.return_value = False
        self.manager._flush()
        self.assertFalse(self.manager.queue_delete("live_test"))
        self.assertFalse(self.manager.has_pending)
        self.manager._load_profile.return_value = True
        self.manager.sync_now()
        self.manager._flush()
        self.assertTrue(self.manager.queue_delete("live_test"))
        self.assertEqual(len(self.manager.history()), 1)
        self.manager._flush()
        self.assertEqual(self.manager.history(), [])
        self.assertFalse(self.manager.has_pending)
        self.assertIn("live_test", self.cloud.tombstones)

    def test_queued_delete_does_not_bypass_later_revoked_admin(self):
        self.cloud.rows["live_test"] = envelope(doc=document(finalized=True))
        self.manager._flush()
        self.manager.queue_delete("live_test")
        self.manager._load_profile.return_value = False
        self.manager._flush()
        self.assertTrue(self.manager.has_pending)
        self.assertEqual(self.manager.status, "auth_required")
        self.assertEqual(len(self.manager.history()), 1)

    def test_bootstrap_imports_missing_only_and_does_not_replay_on_restart(self):
        self.cloud.rows["existing"] = envelope("existing", doc=document("existing", True))
        old_history = [history("existing"), history("missing")]
        self.manager.bootstrap_legacy(old_history, state("current"))
        self.assertFalse(self.manager.has_pending)
        self.manager._flush()
        self.assertEqual(set(self.cloud.rows), {"existing", "missing", "current"})
        self.assertEqual(self.cloud.rows["existing"]["documento"]["historico"], old_history[0])
        self.assertTrue(self.manager.migration_path.exists())
        self.assertFalse(self.manager.queue_path.exists())
        self.cloud.rows.pop("missing")
        restarted = self.make_manager()
        restarted.bootstrap_legacy(old_history, state("current"))
        restarted._flush()
        self.assertNotIn("missing", self.cloud.rows)

    def test_bootstrap_deleted_legacy_is_skipped_not_resurrected(self):
        self.cloud.tombstones.add("old_deleted")
        self.manager.bootstrap_legacy([history("old_deleted")], None)
        self.manager._flush()
        self.assertNotIn("old_deleted", self.cloud.rows)
        self.assertFalse(self.manager.has_pending)
        self.assertEqual(self.manager.status, "synced")
        self.assertTrue(self.manager.migration_path.exists())

    def test_nonlegacy_deleted_conflict_keeps_pending_work(self):
        self.cloud.tombstones.add("live_test")
        self.manager.queue_state(state())
        self.manager._flush()
        self.assertTrue(self.manager.has_pending)
        self.assertEqual(self.manager.status, "conflict")
        self.assertEqual(self.manager.conflicts()[0]["motivo"], "excluida")

    def test_pull_paginates_and_never_creates_permanent_history_cache(self):
        for index in range(205):
            key = f"live_{index:03d}"
            self.cloud.rows[key] = envelope(key, doc=document(key, True))
        self.manager._flush()
        self.assertEqual(len(self.manager.history()), 205)
        self.assertEqual(len([call for call in self.cloud.calls if call[0] == "listar_versoes_lives"]), 3)
        self.assertEqual(len([call for call in self.cloud.calls if call[0] == "obter_dados_lives"]), 3)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_failed_second_page_preserves_previous_complete_history(self):
        self.cloud.rows["before"] = envelope("before", doc=document("before", True))
        self.manager._flush()
        previous = self.manager.history()
        batch = [{"id": f"new_{index:03d}", "versao": 1} for index in range(100)]
        self.manager._force_pull = True
        self.manager._rpc = Mock(side_effect=[batch, SyncTemporaryError("Offline")])
        self.manager._flush()
        self.assertEqual(self.manager.history(), previous)

    def test_logout_during_network_does_not_restore_remote_data(self):
        self.manager.queue_state(state())
        self.cloud.before_save = lambda _args: self.manager.logout()
        self.manager._flush()
        self.assertEqual(self.manager.status, "auth_required")
        self.assertFalse(self.manager.cloud_loaded)
        self.assertFalse(self.manager.is_admin)
        self.assertIsNone(self.manager.remote_document("live_test"))
        self.assertTrue(self.manager.has_pending)

    def test_shutdown_during_upload_keeps_unacknowledged_outbox(self):
        self.manager.queue_state(state())
        self.cloud.before_save = lambda _args: self.manager.shutdown()
        self.manager._flush()
        self.assertTrue(self.manager.queue_path.exists())
        recovered = self.make_manager()
        recovered._flush()
        self.assertFalse(recovered.has_pending)

    def test_invalid_state_and_history_are_not_silently_partially_saved(self):
        self.assertFalse(self.manager.queue_state({"live": {"id": "bad"}, "rows": None}))
        self.assertFalse(self.manager.queue_history([history("ok"), {"id": "bad"}]))
        self.assertFalse(self.manager.has_pending)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_invalid_profile_blocks_cloud_reads_and_writes(self):
        self.manager.queue_state(state())
        self.manager._load_profile.side_effect = SyncAuthError("Inactive")
        self.manager._flush()
        self.assertFalse(self.manager.is_admin)
        self.assertEqual(self.manager.status, "auth_required")
        self.assertEqual(self.cloud.calls, [])

    def test_history_and_remote_document_return_copies(self):
        self.cloud.rows["live_test"] = envelope(doc=document(finalized=True))
        self.manager._flush()
        self.manager.history()[0]["rows"].clear()
        self.manager.remote_document("live_test")["estado"]["rows"].clear()
        self.assertEqual(len(self.manager.history()[0]["rows"]), 1)
        self.assertEqual(len(self.manager.remote_document("live_test")["estado"]["rows"]), 1)

    def test_legacy_draft_before_timer_receives_durable_id(self):
        draft = state()
        draft["live"]["id"] = None
        draft["live"]["running"] = False
        draft["live"]["started_at"] = None
        draft["live"]["first_started_at"] = None
        self.manager.bootstrap_legacy([], draft)
        self.cloud.after_save = lambda _result: (_ for _ in ()).throw(SyncTemporaryError("Lost response"))
        self.manager._flush()
        pending = self.manager.pending_states()
        self.assertEqual(len(pending), 1)
        generated_id = pending[0]["live"]["id"]
        self.assertTrue(generated_id.startswith("live_"))
        self.assertEqual(pending[0]["rows"], draft["rows"])
        self.assertIsNone(draft["live"]["id"])
        recovered = self.make_manager()
        recovered._flush()
        self.assertEqual(set(self.cloud.rows), {generated_id})
        self.assertEqual(recovered.current_states()[0]["live"]["id"], generated_id)

    def test_empty_unstarted_legacy_sheet_is_not_uploaded(self):
        draft = state()
        draft["live"]["id"] = None
        draft["rows"] = []
        self.manager.bootstrap_legacy([], draft)
        self.manager._flush()
        self.assertEqual(self.cloud.rows, {})
        self.assertTrue(self.manager.migration_path.exists())

    def test_invalid_legacy_id_is_reported_and_does_not_mark_migrated(self):
        record = history()
        record["id"] = None
        self.manager.bootstrap_legacy([record], None)
        self.manager._flush()
        self.assertEqual(self.manager.status, "error")
        self.assertFalse(self.manager.migration_path.exists())

    def test_resumed_finished_live_is_recoverable_after_ack_and_restart(self):
        self.cloud.rows["live_test"] = envelope(doc=document(finalized=True))
        self.manager._flush()
        self.manager.begin_edit("live_test", history=history())
        resumed = state(client="new-client")
        self.manager.queue_state(resumed)
        self.assertEqual(self.manager.pending_states(), [resumed])
        self.manager._flush()
        recovered = self.make_manager()
        recovered._flush()
        self.assertEqual(recovered.current_states(), [resumed])
        self.assertEqual(recovered.history(), [history()])

    def test_confirmed_inactive_account_cannot_edit_cached_or_pending_data(self):
        self.manager.queue_state(state())
        self.manager._flush()
        self.assertTrue(self.manager.ready)
        self.manager._load_profile.side_effect = cloud_store.CloudInactiveError("Inactive")
        self.manager.sync_now()
        self.manager._flush()
        self.assertFalse(self.manager.ready)
        self.assertFalse(self.manager.cloud_loaded)
        self.assertFalse(self.manager.queue_state(state(client="unauthorized")))
        self.assertFalse(self.manager.has_pending)

    @unittest.skipUnless(sys.platform.startswith("win"), "DPAPI is Windows-only")
    def test_real_windows_dpapi_roundtrip_uses_fictional_data_only(self):
        from supabase_sync import _protect_windows, _unprotect_windows
        with patch.object(cloud_store, "_protect_windows", _protect_windows), \
                patch.object(cloud_store, "_unprotect_windows", _unprotect_windows):
            manager = self.make_manager()
            self.assertTrue(manager.queue_state(state(client="ficticio-apenas-teste")))
            ciphertext = base64.b64decode(manager.queue_path.read_bytes())
            self.assertNotIn(b"ficticio-apenas-teste", ciphertext)
            recovered = self.make_manager()
            self.assertEqual(recovered.pending_states(), manager.pending_states())
            recovered._flush()
            self.assertFalse(recovered.queue_path.exists())

    def test_divergent_legacy_history_is_preserved_as_migration_conflict(self):
        self.cloud.rows["live_test"] = envelope(doc=document(finalized=True))
        old = history(client="local-legacy-different")
        self.manager.bootstrap_legacy([old], None)
        self.manager._flush()
        self.assertEqual(self.manager.status, "conflict")
        self.assertEqual(self.manager.conflicts()[0]["motivo"], "migracao")
        self.assertEqual(self.manager.history(), [old])
        self.assertEqual(self.manager.remote_document("live_test")["historico"], history())
        self.assertFalse(self.manager.migration_path.exists())
        self.assertFalse(any(name == "salvar_dados_live" for name, _ in self.cloud.calls))
        self.assertTrue(self.manager.discard_conflict("live_test"))
        self.assertTrue(self.manager.migration_path.exists())
        self.assertEqual(self.manager.history(), [history()])
        restarted = self.make_manager()
        restarted.bootstrap_legacy([old], None)
        restarted._flush()
        self.assertEqual(restarted.conflicts(), [])

    def test_legacy_state_timestamp_only_difference_does_not_conflict(self):
        self.cloud.rows["live_test"] = envelope()
        old = state()
        old["updated_at"] = "2026-09-16T19:59:59"
        self.manager.bootstrap_legacy([], old)
        self.manager._flush()
        self.assertEqual(self.manager.conflicts(), [])
        self.assertFalse(self.manager.has_pending)
        self.assertTrue(self.manager.migration_path.exists())
        self.assertEqual(self.manager.remote_document("live_test")["estado"], state())

    def test_partial_migration_conflict_resolution_survives_restart(self):
        self.cloud.rows["one"] = envelope("one", doc=document("one", True))
        self.cloud.rows["two"] = envelope("two", doc=document("two", True))
        old = [history("one", "different"), history("two", "different")]
        self.manager.bootstrap_legacy(old, None)
        self.manager._flush()
        self.assertEqual(len(self.manager.conflicts()), 2)
        self.manager.discard_conflict("one")
        self.assertFalse(self.manager.migration_path.exists())
        restarted = self.make_manager()
        restarted.bootstrap_legacy(old, None)
        restarted._flush()
        self.assertEqual([item["id"] for item in restarted.conflicts()], ["two"])
        restarted.discard_conflict("two")
        self.assertTrue(restarted.migration_path.exists())
        self.assertFalse(restarted.queue_path.exists())

    def test_idless_legacy_draft_not_duplicated_when_bootstrap_repeated_after_restart(self):
        draft = state()
        draft["live"]["id"] = None
        self.manager.bootstrap_legacy([], draft)
        self.cloud.after_save = lambda _result: (_ for _ in ()).throw(SyncTemporaryError("Lost response"))
        self.manager._flush()
        restarted = self.make_manager()
        restarted.bootstrap_legacy([], draft)
        restarted._flush()
        self.assertEqual(len(self.cloud.rows), 1)
        self.assertEqual(len(restarted.current_states()), 1)
        self.assertFalse(restarted.has_pending)

    def test_corrupt_legacy_error_never_marks_migration_complete(self):
        self.manager.bootstrap_legacy([], {}, error="Unreadable legacy file")
        self.manager._flush()
        self.assertEqual(self.manager.status, "error")
        self.assertFalse(self.manager.migration_path.exists())
        self.assertTrue(self.manager.needs_legacy_bootstrap)

    def test_keystroke_saves_do_not_repeat_history_download_or_profile_lookup(self):
        self.cloud.rows["live_test"] = envelope()
        self.manager._flush()
        self.manager.begin_edit("live_test", state=state())
        initial_reads = len([name for name, _ in self.cloud.calls if name != "salvar_dados_live"])
        for index in range(5):
            self.manager.queue_state(state(client=f"edited-{index}"))
            self.manager._flush()
        self.assertEqual(self.manager._load_profile.call_count, 1)
        self.assertEqual(len([name for name, _ in self.cloud.calls if name != "salvar_dados_live"]), initial_reads)
        self.assertEqual(self.cloud.rows["live_test"]["versao"], 6)
        self.manager._last_pull -= 61
        self.manager._last_profile -= 61
        self.manager._flush()
        self.assertEqual(self.manager._load_profile.call_count, 2)
        self.assertEqual(len([name for name, _ in self.cloud.calls if name == "obter_dados_lives"]), 1)

    def test_incremental_refresh_downloads_only_changed_documents(self):
        for key in ("one", "two", "three"):
            self.cloud.rows[key] = envelope(key, doc=document(key, True))
        self.manager._flush()
        self.cloud.calls.clear()
        self.cloud.rows["two"] = envelope("two", version=2, doc=document("two", True))
        self.cloud.rows.pop("three")
        self.manager.sync_now()
        self.manager._flush()
        fetched = [args["p_ids"] for name, args in self.cloud.calls if name == "obter_dados_lives"]
        self.assertEqual(fetched, [["two"]])
        self.assertEqual(len(self.manager.history()), 2)
        self.assertTrue(self.manager.is_deleted("three"))
        self.assertFalse(self.manager.is_deleted("brand-new-id"))

    def test_partial_incremental_response_does_not_replace_complete_history(self):
        self.cloud.rows["live_test"] = envelope(doc=document(finalized=True))
        self.manager._flush()
        before = self.manager.history()
        self.manager.sync_now()
        self.manager._rpc = Mock(side_effect=[[{"id": "live_test", "versao": 2}], []])
        self.manager._flush()
        self.assertEqual(self.manager.history(), before)
        self.assertEqual(self.manager.remote_document("live_test"), document(finalized=True))
        self.assertNotEqual(self.manager.status, "synced")

    def test_confirmed_deleted_id_cannot_be_silently_requeued(self):
        self.cloud.rows["live_test"] = envelope(doc=document(finalized=True))
        self.manager._flush()
        self.manager.queue_delete("live_test")
        self.manager._flush()
        self.assertTrue(self.manager.is_deleted("live_test"))
        self.assertFalse(self.manager.queue_state(state()))
        self.assertFalse(self.manager.has_pending)

    def test_crash_after_legacy_ack_before_marker_does_not_duplicate_idless_draft(self):
        draft = state()
        draft["live"]["id"] = None
        self.manager.bootstrap_legacy([], draft)
        original = self.manager._finish_migration_if_confirmed

        def failed_marker():
            if not self.manager.has_pending:
                raise cloud_store.CloudStorageError("Could not write migration marker")
            return original()

        self.manager._finish_migration_if_confirmed = failed_marker
        self.manager._flush()
        self.assertEqual(self.manager.status, "error")
        self.assertFalse(self.manager.migration_path.exists())
        self.assertTrue(self.manager.queue_path.exists())
        restarted = self.make_manager()
        restarted.bootstrap_legacy([], draft)
        restarted._flush()
        self.assertEqual(len(self.cloud.rows), 1)
        self.assertEqual(len(restarted.current_states()), 1)
        self.assertTrue(restarted.migration_path.exists())
        self.assertFalse(restarted.queue_path.exists())

    def test_malformed_operation_in_remote_ack_does_not_crash_worker(self):
        self.manager.queue_state(state())
        original = self.cloud.rpc

        def invalid(name, args):
            result = original(name, args)
            if name == "salvar_dados_live":
                result["ultima_operacao"] = 42
            return result

        self.manager._rpc = invalid
        self.manager._flush()
        self.assertTrue(self.manager.has_pending)
        self.assertEqual(self.manager.status, "pending")


if __name__ == "__main__":
    unittest.main()
