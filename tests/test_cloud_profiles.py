"""Profile templates use fictional credentials and an isolated in-memory RPC."""

import copy
import io
import json
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

import cloud_store
from cloud_store import (CloudLiveStore, CloudProfileConflictError,
                         CloudProfilesUnavailableError)
from live_profiles import DEFAULT_MESSAGES, IO_MARQUES, MARQUES_BRECHO
from supabase_sync import SyncAuthError, SyncConfig, SyncTemporaryError


class CloudProfilesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        config = SyncConfig("https://example.invalid", "public-fictional", "test@example.invalid")
        for name, value in (
            ("load_sync_config", Mock(return_value=config)),
            ("_protect_windows", lambda data: b"fictional:" + data[::-1]),
            ("_unprotect_windows", lambda data: data[len(b"fictional:"):][::-1]),
        ):
            active = patch.object(cloud_store, name, value)
            active.start()
            self.addCleanup(active.stop)
        network = patch("urllib.request.urlopen", side_effect=AssertionError("Network forbidden"))
        network.start()
        self.addCleanup(network.stop)
        self.server = {perfil: {"perfil": perfil, "mensagem_cliente": message, "versao": 1}
                       for perfil, message in DEFAULT_MESSAGES.items()}
        self.calls = []
        self.manager = CloudLiveStore(self.root)
        self.addCleanup(self.manager.shutdown)
        self.manager._schedule_flush = Mock()
        self.manager._load_profile = Mock(return_value=True)
        self.manager.session = {"username": "fictional", "refresh_token": "fake-refresh",
                                "access_token": "fake-access", "expires_at": time.time() + 3600}
        self.manager._rpc = self.rpc

    def rpc(self, name, arguments):
        self.calls.append((name, copy.deepcopy(arguments)))
        if name == "listar_versoes_lives":
            return []
        if name == "listar_perfis_live_app":
            return copy.deepcopy(list(self.server.values()))
        if name == "salvar_mensagem_perfil_live":
            previous = self.server[arguments["p_perfil"]]
            expected = arguments["p_versao_esperada"]
            if expected != previous["versao"]:
                if (previous["versao"] != expected + 1
                        or previous["mensagem_cliente"] != arguments["p_mensagem_cliente"]):
                    raise CloudProfileConflictError("Modelo alterado em outro computador.")
            elif previous["mensagem_cliente"] != arguments["p_mensagem_cliente"]:
                previous["versao"] += 1
                previous["mensagem_cliente"] = arguments["p_mensagem_cliente"]
            return copy.deepcopy(previous)
        raise AssertionError(f"Unexpected RPC {name}")

    def load_profiles(self):
        self.manager._flush()
        self.assertTrue(self.manager.perfis_disponiveis)
        self.assertEqual(self.manager.erro_perfis, "")

    def save(self, message="Obrigado! {pecas}\nTotal {total}", expected=1, perfil=MARQUES_BRECHO):
        called = threading.Event()
        results = []

        def complete(*values):
            results.append(values)
            called.set()

        self.manager.salvar_mensagem_perfil_live(perfil, message, expected, complete)
        self.assertTrue(called.wait(3), "Profile worker did not complete")
        self.assertEqual(len(results), 1)
        return results[0]

    def test_profiles_require_confirmation_and_are_only_cached_in_ram(self):
        self.assertFalse(self.manager.perfis_disponiveis)
        self.assertEqual(self.manager.perfis_live(), {})
        self.load_profiles()
        self.assertEqual(self.manager.perfis_live(), self.server)
        self.manager.perfis_live()[MARQUES_BRECHO]["mensagem_cliente"] = "Changed copy"
        self.assertEqual(self.manager.perfis_live(), self.server)
        self.assertEqual(list(self.root.iterdir()), [])
        restarted = CloudLiveStore(self.root)
        self.addCleanup(restarted.shutdown)
        self.assertFalse(restarted.perfis_disponiveis)
        self.assertEqual(restarted.perfis_live(), {})

    def test_auth_context_marker_changes_on_logout_without_exposing_session_values(self):
        initial = self.manager.contexto_autenticacao
        self.assertIs(type(initial), int)
        self.manager.logout()
        self.assertEqual(self.manager.contexto_autenticacao, initial + 1)
        self.manager.logout()
        self.assertEqual(self.manager.contexto_autenticacao, initial + 2)

    def test_missing_migration_preserves_existing_history_sync(self):
        original = self.manager._rpc

        def missing(name, arguments):
            if name == "listar_perfis_live_app":
                raise CloudProfilesUnavailableError("Aplique a atualização dos perfis.")
            return original(name, arguments)

        self.manager._rpc = missing
        self.manager._flush()
        self.assertTrue(self.manager.cloud_loaded)
        self.assertTrue(self.manager.ready)
        self.assertEqual(self.manager.status, "synced")
        self.assertFalse(self.manager.perfis_disponiveis)
        self.assertIn("atualização", self.manager.erro_perfis)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_refresh_runs_at_most_every_minute_or_on_manual_sync(self):
        self.load_profiles()
        self.manager._flush()
        self.assertEqual(sum(name == "listar_perfis_live_app" for name, _ in self.calls), 1)
        self.manager._last_perfis -= 61
        self.manager._flush()
        self.assertEqual(sum(name == "listar_perfis_live_app" for name, _ in self.calls), 2)
        self.manager.sync_now()
        self.manager._flush()
        self.assertEqual(sum(name == "listar_perfis_live_app" for name, _ in self.calls), 3)

    def test_temporary_refresh_failure_keeps_confirmed_ram_template_and_error(self):
        self.load_profiles()
        before = self.manager.perfis_live()
        original = self.manager._rpc

        def offline(name, arguments):
            if name == "listar_perfis_live_app":
                raise SyncTemporaryError("Sem conexão para atualizar os modelos.")
            return original(name, arguments)

        self.manager._rpc = offline
        self.manager._last_perfis -= 61
        self.manager._flush()
        self.assertTrue(self.manager.perfis_disponiveis)
        self.assertEqual(self.manager.perfis_live(), before)
        self.assertIn("Sem conexão", self.manager.erro_perfis)
        self.assertEqual(self.manager.status, "synced")

    def test_partial_duplicate_invalid_and_rolled_back_lists_are_not_installed(self):
        self.load_profiles()
        before = self.manager.perfis_live()
        invalid_lists = [
            [self.server[IO_MARQUES]],
            [self.server[IO_MARQUES], self.server[IO_MARQUES]],
            [{**self.server[IO_MARQUES], "versao": True}, self.server[MARQUES_BRECHO]],
            [{**self.server[IO_MARQUES], "perfil": []}, self.server[MARQUES_BRECHO]],
            [self.server[IO_MARQUES], {**self.server[MARQUES_BRECHO], "mensagem_cliente": "Sem valores"}],
            [self.server[IO_MARQUES], {**self.server[MARQUES_BRECHO], "mensagem_cliente": "Mudou {pecas} {total}"}],
        ]
        for invalid in invalid_lists:
            with self.subTest(invalid=invalid):
                self.manager._rpc = Mock(return_value=copy.deepcopy(invalid))
                self.assertFalse(self.manager._refresh_perfis_live(self.manager._generation))
                self.assertEqual(self.manager.perfis_live(), before)
                self.assertTrue(self.manager.erro_perfis)

    def test_save_confirms_exact_message_without_queue_or_local_files(self):
        self.load_profiles()
        result = self.save()
        self.assertTrue(result[0])
        self.assertEqual(result[2]["versao"], 2)
        self.assertEqual(self.manager.perfis_live()[MARQUES_BRECHO], result[2])
        self.assertFalse(self.manager.has_pending)
        self.assertEqual(list(self.root.iterdir()), [])
        result[2]["mensagem_cliente"] = "Mutable callback value"
        self.assertNotEqual(self.manager.perfis_live()[MARQUES_BRECHO]["mensagem_cliente"], result[2]["mensagem_cliente"])

    def test_io_invalid_message_and_invalid_version_do_not_make_save_requests(self):
        self.load_profiles()
        self.calls.clear()
        self.assertFalse(self.save(perfil=IO_MARQUES)[0])
        self.assertFalse(self.save(message="Sem valores")[0])
        for expected in (True, 0, -1, 1.0, 9007199254740992):
            self.assertFalse(self.save(expected=expected)[0])
        self.assertEqual(self.calls, [])

    def test_no_save_before_profiles_are_confirmed(self):
        self.assertFalse(self.save()[0])
        self.assertEqual(self.calls, [])
        self.assertFalse(self.manager.has_pending)

    def test_background_refresh_does_not_bless_an_open_editors_revision(self):
        self.load_profiles()
        self.server[MARQUES_BRECHO].update(versao=2, mensagem_cliente="Outro {pecas} {total}")
        self.manager._refresh_perfis_live(self.manager._generation)
        result = self.save(expected=1)
        self.assertFalse(result[0])
        self.assertEqual(self.server[MARQUES_BRECHO]["mensagem_cliente"], "Outro {pecas} {total}")
        sent = [arguments for name, arguments in self.calls if name == "salvar_mensagem_perfil_live"][-1]
        self.assertEqual(sent["p_versao_esperada"], 1)
        self.assertFalse(self.manager.has_pending)

    def test_lost_save_response_can_retry_same_message_and_pinned_revision(self):
        self.load_profiles()
        original = self.manager._rpc
        lost = [True]

        def uncertain(name, arguments):
            result = original(name, arguments)
            if name == "salvar_mensagem_perfil_live" and lost[0]:
                lost[0] = False
                raise SyncTemporaryError("Resposta perdida")
            return result

        self.manager._rpc = uncertain
        self.assertFalse(self.save()[0])
        self.assertEqual(self.manager.perfis_live()[MARQUES_BRECHO]["versao"], 1)
        self.assertTrue(self.save()[0])
        self.assertEqual(self.server[MARQUES_BRECHO]["versao"], 2)
        self.assertEqual(self.manager.perfis_live()[MARQUES_BRECHO]["versao"], 2)

    def test_unchanged_message_can_be_confirmed_at_same_version(self):
        self.load_profiles()
        result = self.save(message=DEFAULT_MESSAGES[MARQUES_BRECHO])
        self.assertTrue(result[0])
        self.assertEqual(result[2]["versao"], 1)

    def test_mismatched_ack_does_not_change_template_or_invent_success(self):
        self.load_profiles()
        before = self.manager.perfis_live()
        for changes in ({"perfil": IO_MARQUES}, {"versao": 4}, {"versao": True},
                        {"mensagem_cliente": "Outro texto {pecas} {total}"}):
            with self.subTest(changes=changes):
                invalid = {"perfil": MARQUES_BRECHO, "versao": 2,
                           "mensagem_cliente": "Obrigado! {pecas}\nTotal {total}", **changes}
                self.manager._rpc = Mock(return_value=invalid)
                self.assertFalse(self.save()[0])
                self.assertEqual(self.manager.perfis_live(), before)

    def test_logout_or_shutdown_during_save_drops_ack_and_callback(self):
        for action in ("logout", "shutdown"):
            with self.subTest(action=action):
                self.manager.closed = False
                self.manager.session = {"refresh_token": "fake"}
                self.manager._perfis_live = copy.deepcopy(self.server)
                self.manager._rpc = Mock(side_effect=lambda *_args: (
                    getattr(self.manager, action)(),
                    {"perfil": MARQUES_BRECHO, "versao": 2,
                     "mensagem_cliente": "Obrigado! {pecas}\nTotal {total}"})[1])
                callback = Mock()
                # Capture a real thread to wait without relying on a sleep.
                real_thread = threading.Thread
                threads = []

                def make_thread(*args, **kwargs):
                    thread = real_thread(*args, **kwargs)
                    threads.append(thread)
                    return thread

                with patch.object(cloud_store.threading, "Thread", side_effect=make_thread):
                    self.manager.salvar_mensagem_perfil_live(
                        MARQUES_BRECHO, "Obrigado! {pecas}\nTotal {total}", 1, callback)
                    threads[0].join(3)
                callback.assert_not_called()
                self.assertFalse(self.manager.perfis_disponiveis)
                if action == "logout":
                    self.assertEqual(self.manager.perfis_live(), {})

    def test_logout_during_profile_pull_drops_response(self):
        self.load_profiles()
        original = self.manager._rpc

        def disconnect(name, arguments):
            result = original(name, arguments)
            self.manager.logout()
            return result

        self.manager._rpc = disconnect
        generation = self.manager._generation
        self.assertFalse(self.manager._refresh_perfis_live(generation))
        self.assertEqual(self.manager.perfis_live(), {})
        self.assertFalse(self.manager.perfis_disponiveis)

    def test_older_background_pull_cannot_overwrite_a_confirmed_save(self):
        self.load_profiles()
        original = self.manager._rpc
        before = copy.deepcopy(list(self.server.values()))

        def delayed(name, arguments):
            if name == "listar_perfis_live_app":
                self.manager._rpc = original
                self.assertTrue(self.save()[0])
                return before
            return original(name, arguments)

        self.manager._rpc = delayed
        self.assertFalse(self.manager._refresh_perfis_live(self.manager._generation))
        self.assertEqual(self.manager.perfis_live()[MARQUES_BRECHO]["versao"], 2)

    def test_reload_has_explicit_confirmation_and_returns_a_copy(self):
        complete = Mock()
        finished = threading.Event()

        def capture(*args):
            complete(*args)
            finished.set()

        self.manager.recarregar_perfis_live(capture)
        self.assertTrue(finished.wait(3))
        success, _detail, records = complete.call_args.args
        self.assertTrue(success)
        records[MARQUES_BRECHO]["mensagem_cliente"] = "Changed callback copy"
        self.assertEqual(self.manager.perfis_live(), self.server)
        finished.clear()
        self.manager._rpc = Mock(side_effect=SyncTemporaryError("Offline"))
        self.manager.recarregar_perfis_live(capture)
        self.assertTrue(finished.wait(3))
        self.assertEqual(complete.call_args.args, (False, "Offline", None))
        self.assertEqual(self.manager.perfis_live(), self.server)

    def test_refresh_after_shutdown_or_account_change_does_not_start_a_request(self):
        generation = self.manager._generation
        self.manager.logout()
        self.manager._rpc = Mock()
        self.assertFalse(self.manager._refresh_perfis_live(generation))
        self.manager._rpc.assert_not_called()
        self.manager.shutdown()
        self.assertFalse(self.manager._refresh_perfis_live(self.manager._generation))
        self.manager._rpc.assert_not_called()

    def test_explicit_reload_failure_after_logout_is_not_delivered_to_old_editor(self):
        self.load_profiles()
        callback = Mock()
        requested = threading.Event()
        released = threading.Event()
        self.addCleanup(released.set)

        def delayed(_name, _arguments):
            requested.set()
            released.wait(3)
            return copy.deepcopy(list(self.server.values()))

        self.manager._rpc = delayed
        real_thread = threading.Thread
        threads = []

        def make_thread(*args, **kwargs):
            thread = real_thread(*args, **kwargs)
            threads.append(thread)
            return thread

        with patch.object(cloud_store.threading, "Thread", side_effect=make_thread):
            self.manager.recarregar_perfis_live(callback)
            self.assertTrue(requested.wait(3))
            self.manager.logout()
            released.set()
            threads[0].join(3)
        callback.assert_not_called()
        self.assertEqual(self.manager.perfis_live(), {})

    def test_failed_save_keeps_model_out_of_durable_live_queue(self):
        self.load_profiles()
        previous = self.manager.perfis_live()
        self.manager._rpc = Mock(side_effect=SyncTemporaryError("Offline"))
        self.assertFalse(self.save()[0])
        self.assertEqual(self.manager.perfis_live(), previous)
        self.assertFalse(self.manager.has_pending)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_profile_auth_retry_cannot_switch_to_a_different_logged_in_account(self):
        self.manager._rpc = CloudLiveStore._rpc.__get__(self.manager)
        generation = self.manager._generation

        def account_changed(_path, _payload, access_token=None):
            self.assertEqual(access_token, "fake-access")
            self.manager._generation += 1
            self.manager.session = {"refresh_token": "new-user-refresh", "access_token": "new-user-access",
                                    "expires_at": time.time() + 3600}
            raise SyncAuthError("Old account token rejected")

        self.manager._request_json = Mock(side_effect=account_changed)
        with self.assertRaises(SyncAuthError):
            self.manager._rpc_perfil_live("listar_perfis_live_app", {}, generation)
        self.assertEqual(self.manager._request_json.call_count, 1)
        self.assertFalse(hasattr(self.manager._perfil_rpc_context, "generation"))

    def test_changing_login_clears_previous_account_before_network_response(self):
        self.load_profiles()
        requested = threading.Event()
        release = threading.Event()
        complete = threading.Event()
        self.addCleanup(release.set)

        def waiting(*_args, **_kwargs):
            requested.set()
            release.wait(3)
            raise SyncAuthError("Fictional login rejected")

        self.manager._request_json = Mock(side_effect=waiting)
        self.manager.login_async("fictional-other", "fictional-password", lambda *_args: complete.set())
        self.assertTrue(requested.wait(3))
        self.assertFalse(self.manager.is_authenticated)
        self.assertFalse(self.manager.perfis_disponiveis)
        self.assertEqual(self.manager.perfis_live(), {})
        release.set()
        self.assertTrue(complete.wait(3))

    def test_profile_snapshot_is_validated_without_retagging_legacy_documents(self):
        state = {"live": {"id": "fictional-live"}, "rows": []}
        history = {"id": "fictional-live", "started_at": "2026-10-22T19:00:00",
                   "finished_at": "2026-10-22T20:00:00", "duration": "01:00:00", "rows": [],
                   "total": "0,00", "pieces_count": 0, "sold_count": 0}
        legacy = {"estado": state, "historico": history}
        self.manager._validate_document("fictional-live", legacy)
        self.assertNotIn("perfil", state["live"])
        invalid_metadata = [
            {"perfil": MARQUES_BRECHO},
            {"mensagem_cliente": DEFAULT_MESSAGES[MARQUES_BRECHO]},
            {"perfil": "unknown", "mensagem_cliente": DEFAULT_MESSAGES[MARQUES_BRECHO]},
            {"perfil": MARQUES_BRECHO, "mensagem_cliente": "No purchase values"},
        ]
        for invalid in invalid_metadata:
            with self.subTest(invalid=invalid):
                document = copy.deepcopy(legacy)
                document["estado"]["live"].update(invalid)
                with self.assertRaises(ValueError):
                    self.manager._validate_document("fictional-live", document)
        document = copy.deepcopy(legacy)
        document["estado"]["live"].update(perfil=MARQUES_BRECHO,
                                          mensagem_cliente=DEFAULT_MESSAGES[MARQUES_BRECHO])
        # A masculine state cannot be paired with a historical legacy Io record.
        with self.assertRaises(ValueError):
            self.manager._validate_document("fictional-live", document)
        document["historico"].update(perfil=MARQUES_BRECHO,
                                     mensagem_cliente=DEFAULT_MESSAGES[MARQUES_BRECHO])
        self.manager._validate_document("fictional-live", document)
        document["historico"]["mensagem_cliente"] = "Another {pecas} {total}"
        with self.assertRaises(ValueError):
            self.manager._validate_document("fictional-live", document)

    def test_legacy_io_message_fallback_must_match_snapshot_before_queueing(self):
        state = {"live": {"id": "fictional-live"}, "rows": []}
        history = {"id": "fictional-live", "started_at": "2026-10-22T19:00:00",
                   "finished_at": "2026-10-22T20:00:00", "duration": "01:00:00", "rows": [],
                   "total": "0,00", "pieces_count": 0, "sold_count": 0}
        # Explicit canonical Io metadata is compatible with a legacy partner.
        for snapshot_in_state in (True, False):
            with self.subTest(snapshot_in_state=snapshot_in_state):
                document = {"estado": copy.deepcopy(state), "historico": copy.deepcopy(history)}
                target = document["estado"]["live"] if snapshot_in_state else document["historico"]
                target.update(perfil=IO_MARQUES, mensagem_cliente=DEFAULT_MESSAGES[IO_MARQUES])
                self.manager._validate_document("fictional-live", document)
                target["mensagem_cliente"] = "Custom Io {pecas} {total}"
                with self.assertRaises(ValueError):
                    self.manager._validate_document("fictional-live", document)
                self.assertFalse(self.manager.queue_state(document["estado"], document["historico"]))
                self.assertFalse(self.manager.has_pending)
                self.assertEqual(list(self.root.iterdir()), [])
    def test_rpc_errors_are_classified_without_exposing_arbitrary_server_text(self):
        examples = [
            (404, "schema cache unavailable", "listar_perfis_live_app", CloudProfilesUnavailableError, "perfis"),
            (400, "PERFIL_LIVE_CONFLITO secret", "salvar_mensagem_perfil_live", CloudProfileConflictError, "Outra pessoa"),
            (400, "PERFIL_LIVE_ATUALIZE secret", "salvar_dados_live", SyncTemporaryError, "Atualize"),
            (400, "Arbitrary secret", "salvar_mensagem_perfil_live", SyncTemporaryError, "não confirmou"),
        ]
        for code, message, rpc_name, error_type, expected in examples:
            with self.subTest(message=message):
                error = urllib.error.HTTPError("https://example.invalid", code, "Failure", {},
                                               io.BytesIO(json.dumps({"message": message}).encode()))
                with patch("urllib.request.urlopen", side_effect=error):
                    with self.assertRaises(error_type) as captured:
                        self.manager._request_json(f"/rest/v1/rpc/{rpc_name}", {}, "fake-token")
                self.assertIn(expected, str(captured.exception))
                self.assertNotIn("secret", str(captured.exception))


if __name__ == "__main__":
    unittest.main()
