import copy
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from supabase_sync import (
    MAX_TOTAL_CENTS,
    SupabaseHistorySync,
    SyncConfig,
    _parse_money_cents,
    prepare_history_payload,
)


SAMPLE_HISTORY = [
    {
        "id": "live_teste",
        "started_at": "2026-09-16T19:00:00",
        "finished_at": "2026-09-16T20:00:00",
        "duration": "01:00:00",
        "rows": [
            {
                "valor": "R$ 39,90",
                "codigo": "024",
                "cliente": "",
                "suplente": "nao-deve-sair-do-computador",
                "tempo": "00:05:10",
            },
            {
                "valor": "R$ 20,00",
                "codigo": "025",
                "cliente": "cliente-privada",
                "suplente": "",
                "tempo": "00:06:00",
            },
        ],
    }
]


class FakeSupabaseHandler(BaseHTTPRequestHandler):
    calls = []
    missing_migration = False
    invalid_confirmation = False

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(size).decode("utf-8"))
        self.__class__.calls.append((self.path, body, dict(self.headers)))

        if self.path.startswith("/auth/v1/token"):
            response = {
                "access_token": "access-token-test",
                "refresh_token": "refresh-token-test",
                "expires_in": 3600,
            }
        elif self.path.endswith("/importar_historico_lives"):
            if self.__class__.missing_migration:
                encoded = json.dumps({"message": "Could not find function in schema cache"}).encode("utf-8")
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return
            response = True if self.__class__.invalid_confirmation else len(body["p_lives"])
        elif self.path.endswith("/excluir_live_vendas_importada"):
            response = body["p_live_id"]
        else:
            self.send_error(404)
            return

        encoded = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format, *_args):
        return


class SupabaseSyncTests(unittest.TestCase):
    def setUp(self):
        FakeSupabaseHandler.calls = []
        FakeSupabaseHandler.missing_migration = False
        FakeSupabaseHandler.invalid_confirmation = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeSupabaseHandler)
        self.server_thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.server_thread.start()
        self.temp = tempfile.TemporaryDirectory()

    def make_manager(self, status_callback=None):
        manager = SupabaseHistorySync(Path(self.temp.name), status_callback=status_callback)
        self.addCleanup(manager.shutdown)
        manager.config = SyncConfig(
            url=f"http://127.0.0.1:{self.server.server_port}",
            publishable_key="publishable-test",
            auth_email_base="acesso@example.com",
        )
        return manager

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def test_payload_contains_only_unsold_piece(self):
        payload = prepare_history_payload(SAMPLE_HISTORY)

        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["duracao_segundos"], 3600)
        self.assertEqual(payload[0]["total_vendido_centavos"], 2000)
        self.assertEqual(payload[0]["total_pecas"], 2)
        self.assertEqual(payload[0]["pecas_vendidas"], 1)
        self.assertTrue(payload[0]["possui_detalhamento"])
        self.assertEqual(payload[0]["iniciada_em"], "2026-09-16T22:00:00+00:00")
        self.assertEqual(
            payload[0]["pecas"],
            [
                {
                    "numero": 1,
                    "codigo": "024",
                    "valor_texto": "R$ 39,90",
                    "valor_centavos": 3990,
                    "tempo_segundos": 310,
                }
            ],
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("cliente-privada", serialized)
        self.assertNotIn("nao-deve-sair-do-computador", serialized)
        self.assertNotIn("025", serialized)
        self.assertNotIn("comissao", serialized)

    def test_saved_summary_takes_priority_and_sold_out_live_is_sent(self):
        live = copy.deepcopy(SAMPLE_HISTORY[0])
        live.update(total="R$ 1.234,50", pieces_count=8, sold_count=8)
        live["rows"] = [live["rows"][1]]
        live["jogos"] = {"cliente": "nome-do-jogo-privado"}
        payload = prepare_history_payload([live])[0]
        self.assertEqual(payload["total_vendido_centavos"], 123450)
        self.assertEqual(payload["total_pecas"], 8)
        self.assertEqual(payload["pecas_vendidas"], 8)
        self.assertEqual(payload["pecas"], [])
        self.assertTrue(payload["possui_detalhamento"])
        self.assertNotIn("nome-do-jogo-privado", json.dumps(payload))

    def test_legacy_summary_without_rows_and_missing_values(self):
        live = {key: value for key, value in SAMPLE_HISTORY[0].items() if key != "rows"}
        live.update(total="R$ 500,00", pieces_count=20, sold_count=10)
        payload = prepare_history_payload([live])[0]
        self.assertEqual(payload["total_vendido_centavos"], 50000)
        self.assertEqual(payload["total_pecas"], 20)
        self.assertEqual(payload["pecas_vendidas"], 10)
        self.assertEqual(payload["pecas"], [])
        self.assertFalse(payload["possui_detalhamento"])
        for key in ("total", "pieces_count", "sold_count"):
            live.pop(key)
        payload = prepare_history_payload([live])[0]
        self.assertIsNone(payload["total_vendido_centavos"])
        self.assertIsNone(payload["total_pecas"])
        self.assertIsNone(payload["pecas_vendidas"])

    def test_invalid_summary_is_not_silently_replaced_by_row_fallback(self):
        live = copy.deepcopy(SAMPLE_HISTORY[0])
        live.update(total="NaN", pieces_count=-1, sold_count=True)
        payload = prepare_history_payload([live])[0]
        self.assertIsNone(payload["total_vendido_centavos"])
        self.assertIsNone(payload["total_pecas"])
        self.assertIsNone(payload["pecas_vendidas"])

    def test_counts_accept_json_integer_numbers_and_reject_invalid_values(self):
        for value, expected in ((2.0, 2), ("2", 2), (" 2 ", 2), (2, 2), (0, 0), (True, None),
                                (2.5, None), (float("inf"), None), (float("nan"), None),
                                ("2.0", None), ("2e0", None), ("0x2", None),
                                (1_000_001, None), ({"value": 2}, None)):
            with self.subTest(value=value):
                live = copy.deepcopy(SAMPLE_HISTORY[0])
                live.update(pieces_count=value, sold_count=value)
                payload = prepare_history_payload([live])[0]
                self.assertEqual(payload["total_pecas"], expected)
                self.assertEqual(payload["pecas_vendidas"], expected)

    def test_zero_piece_value_is_preserved_and_malformed_clients_are_not_sold(self):
        live = copy.deepcopy(SAMPLE_HISTORY[0])
        live["rows"] = [
            None,
            {"valor": 0},
            {"valor": "10,00", "cliente": True},
            {"valor": "10,00", "cliente": {"nome": "privado"}},
            {"valor": "20,00", "cliente": 0},
        ]
        payload = prepare_history_payload([live])[0]
        self.assertEqual(payload["total_pecas"], 5)
        self.assertEqual(payload["pecas_vendidas"], 1)
        self.assertEqual(payload["total_vendido_centavos"], 2000)
        self.assertEqual([piece["numero"] for piece in payload["pecas"]], [2, 3, 4])
        self.assertEqual(payload["pecas"][0]["valor_texto"], "0")
        self.assertEqual(payload["pecas"][0]["valor_centavos"], 0)

    def test_duration_format_and_half_second_fallback_match_pwa(self):
        live = copy.deepcopy(SAMPLE_HISTORY[0])
        live["duration"] = "0:1:2"
        self.assertEqual(prepare_history_payload([live])[0]["duracao_segundos"], 3600)
        live["duration"] = None
        live["finished_at"] = "2026-09-16T19:00:00.500"
        self.assertEqual(prepare_history_payload([live])[0]["duracao_segundos"], 1)
        for invalid in ("20260916T190000", "2026-W38-3T19:00:00"):
            with self.subTest(invalid=invalid):
                live["started_at"] = invalid
                self.assertEqual(prepare_history_payload([live]), [])

    def test_invalid_sold_price_makes_fallback_total_unknown(self):
        for invalid in ("NaN", "Infinity", "inválido", "", "-50,00"):
            with self.subTest(invalid=invalid):
                live = copy.deepcopy(SAMPLE_HISTORY[0])
                live["rows"][1]["valor"] = invalid
                payload = prepare_history_payload([live])[0]
                self.assertIsNone(payload["total_vendido_centavos"])
                self.assertEqual(payload["pecas_vendidas"], 1)

    def test_money_formats_limits_and_invalid_values(self):
        samples = [
            (0, 0), ("0", 0), ("R$ 1.234,50", 123450),
            ("1.234", 123400), ("1234,50", 123450), ("1234.50", 123450),
            ("R$\u00a039,90", 3990), ("9.999.999.999,99", MAX_TOTAL_CENTS),
            ("10.000.000.000,00", None), ("-1", None), (True, None),
            ("NaN", None), ("Infinity", None), (float("nan"), None),
            (float("inf"), None), ("1e10000", None), ("1.23.45", None),
            ("1,2,3", None), (None, None), ("", None),
        ]
        for value, expected in samples:
            with self.subTest(value=value):
                self.assertEqual(_parse_money_cents(value, MAX_TOTAL_CENTS), expected)

    def test_naive_store_time_explicit_offsets_and_invalid_dates(self):
        live = copy.deepcopy(SAMPLE_HISTORY[0])
        live["started_at"] = "2026-09-16T19:00:00-02:00"
        live["finished_at"] = "2026-09-16T22:00:00Z"
        payload = prepare_history_payload([live])[0]
        self.assertEqual(payload["iniciada_em"], "2026-09-16T21:00:00+00:00")
        self.assertEqual(payload["finalizada_em"], "2026-09-16T22:00:00+00:00")
        for invalid in ("2026-02-30T19:00:00", "", None, "0001-01-01T00:00:00+23:00"):
            with self.subTest(invalid=invalid):
                live["started_at"] = invalid
                self.assertEqual(prepare_history_payload([live]), [])

    def test_empty_detail_has_zero_total_but_missing_detail_is_unknown(self):
        live = copy.deepcopy(SAMPLE_HISTORY[0])
        live["rows"] = []
        payload = prepare_history_payload([live])[0]
        self.assertEqual(payload["total_vendido_centavos"], 0)
        self.assertEqual(payload["total_pecas"], 0)
        self.assertTrue(payload["possui_detalhamento"])
        live["rows"] = None
        payload = prepare_history_payload([live])[0]
        self.assertIsNone(payload["total_vendido_centavos"])
        self.assertFalse(payload["possui_detalhamento"])

    def test_requeue_history_upgrades_old_payload_without_losing_deletions(self):
        manager = self.make_manager()
        old = prepare_history_payload(SAMPLE_HISTORY)[0]
        for key in ("total_vendido_centavos", "total_pecas", "pecas_vendidas", "possui_detalhamento"):
            old.pop(key)
        manager.pending_lives[old["id"]] = old
        manager.pending_deletions.add("outra_live_excluida")
        with manager.lock:
            manager._save_queue_locked()
        reopened = self.make_manager()
        self.assertNotIn("total_vendido_centavos", reopened.pending_lives[old["id"]])
        reopened.queue_history(SAMPLE_HISTORY)
        upgraded = reopened.pending_lives[old["id"]]
        self.assertEqual(upgraded["total_vendido_centavos"], 2000)
        self.assertIn("outra_live_excluida", reopened.pending_deletions)

    def test_missing_migration_keeps_queue_pending_and_uses_new_rpc_only(self):
        FakeSupabaseHandler.missing_migration = True
        manager = self.make_manager()
        manager.queue_history(SAMPLE_HISTORY)
        manager.session = {"access_token": "test", "refresh_token": "test", "expires_at": time.time() + 3600}
        with patch.object(manager, "_schedule_flush") as retry:
            manager._flush()
        self.assertEqual(manager.status, "pending")
        self.assertIn("ainda não foi aplicada", manager.detail)
        self.assertEqual(len(manager.pending_lives), 1)
        retry.assert_called_once()
        self.assertEqual(FakeSupabaseHandler.calls[0][0], "/rest/v1/rpc/importar_historico_lives")

    def test_boolean_is_not_an_import_confirmation(self):
        FakeSupabaseHandler.invalid_confirmation = True
        manager = self.make_manager()
        manager.queue_history(SAMPLE_HISTORY)
        manager.session = {"access_token": "test", "refresh_token": "test", "expires_at": time.time() + 3600}
        with patch.object(manager, "_schedule_flush"):
            manager._flush()
        self.assertEqual(manager.status, "pending")
        self.assertEqual(len(manager.pending_lives), 1)

    def test_login_protected_session_and_automatic_flush(self):
        statuses = []
        synced = threading.Event()

        def status_callback(status, detail, username):
            statuses.append((status, detail, username))
            if status == "synced":
                synced.set()

        manager = self.make_manager(status_callback=status_callback)
        manager.queue_history(SAMPLE_HISTORY)

        login_done = threading.Event()
        login_result = []
        manager.login_async(
            "admin",
            "senha-teste",
            lambda success, message: (
                login_result.append((success, message)),
                login_done.set(),
            ),
        )

        self.assertTrue(login_done.wait(5), "login did not finish")
        self.assertTrue(login_result[0][0])
        self.assertTrue(synced.wait(5), "automatic sync did not finish")
        self.assertTrue(manager.session_path.exists())
        self.assertNotIn(b"refresh-token-test", manager.session_path.read_bytes())

        auth_call = next(call for call in FakeSupabaseHandler.calls if "/auth/" in call[0])
        self.assertEqual(auth_call[1]["email"], "acesso+admin@example.com")
        import_call = next(
            call
            for call in FakeSupabaseHandler.calls
            if call[0].endswith("/importar_historico_lives")
        )
        sent = json.dumps(import_call[1], ensure_ascii=False)
        self.assertIn("024", sent)
        self.assertNotIn("025", sent)
        self.assertNotIn("cliente-privada", sent)
        self.assertTrue(any(status == "synced" for status, _, _ in statuses))

        deadline = time.time() + 2
        while manager.pending_lives and time.time() < deadline:
            time.sleep(0.01)
        self.assertFalse(manager.pending_lives)
        manager.shutdown()


if __name__ == "__main__":
    unittest.main()
