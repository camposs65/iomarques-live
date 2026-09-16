import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from supabase_sync import SupabaseHistorySync, SyncConfig, prepare_history_payload


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
        elif self.path.endswith("/importar_lives_nao_vendidas"):
            response = len(body["p_lives"])
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
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeSupabaseHandler)
        self.server_thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.server_thread.start()
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def test_payload_contains_only_unsold_piece(self):
        payload = prepare_history_payload(SAMPLE_HISTORY)

        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["duracao_segundos"], 3600)
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

    def test_login_protected_session_and_automatic_flush(self):
        statuses = []
        synced = threading.Event()

        def status_callback(status, detail, username):
            statuses.append((status, detail, username))
            if status == "synced":
                synced.set()

        manager = SupabaseHistorySync(
            Path(self.temp.name), status_callback=status_callback
        )
        manager.config = SyncConfig(
            url=f"http://127.0.0.1:{self.server.server_port}",
            publishable_key="publishable-test",
            auth_email_base="acesso@example.com",
        )
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
            if call[0].endswith("/importar_lives_nao_vendidas")
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
