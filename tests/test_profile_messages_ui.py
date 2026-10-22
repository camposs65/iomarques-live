import copy
import re
import threading
import tkinter as tk
import unittest
from datetime import datetime
from tkinter import ttk
from unittest.mock import Mock, patch

from app import LiveSalesApp
from live_profiles import MARQUES_BRECHO, MARQUES_MESSAGE_TEMPLATE
from test_profile_theme import ThemeStore


class MessageStore(ThemeStore):
    def __init__(self):
        super().__init__()
        self.contexto_autenticacao = 1
        self.record = {"perfil": MARQUES_BRECHO, "mensagem_cliente": MARQUES_MESSAGE_TEMPLATE, "versao": 3}
        self.perfis_live = Mock(side_effect=lambda: {MARQUES_BRECHO: copy.deepcopy(self.record)})
        self.salvar_mensagem_perfil_live = Mock()
        self.recarregar_perfis_live = Mock()


class ProfileMessageUiTests(unittest.TestCase):
    def setUp(self):
        self.store = MessageStore()
        self.store_patch = patch("app.CloudLiveStore", return_value=self.store)
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)
        self.app = LiveSalesApp()
        self.app.withdraw()
        self.addCleanup(self.cleanup_app)
        self.assertTrue(self.app._set_live_profile(MARQUES_BRECHO))
        self.app.show_profile_message_editor()
        self.window = self.app.profile_message_window
        self.window.withdraw()
        widgets = list(self.walk(self.window))
        self.text, self.preview = [widget for widget in widgets if isinstance(widget, tk.Text)]
        self.buttons = {widget.cget("text"): widget for widget in widgets if isinstance(widget, ttk.Button)}
        self.status_label = next(widget for widget in widgets if isinstance(widget, tk.Label)
                                 and str(widget.cget("textvariable")))

    def cleanup_app(self):
        if not self.app.winfo_exists():
            return
        for job in (self.app.timer_job, self.app.autosave_job, self.app.cloud_poll_job):
            if job is not None:
                self.app.after_cancel(job)
        self.app.update_idletasks()
        self.app.destroy()

    def walk(self, widget):
        for child in widget.winfo_children():
            yield child
            yield from self.walk(child)

    def status(self):
        return self.app.getvar(self.status_label.cget("textvariable"))

    def set_text(self, value):
        self.text.delete("1.0", "end")
        self.text.insert("1.0", value)
        # Invoke the registered KeyRelease callback without depending on window focus.
        callback = re.search(r"\[([^\s]+)", self.text.bind("<KeyRelease>")).group(1)
        self.app.tk.call(callback)

    def drain_callbacks(self):
        while not self.app.cloud_events.empty():
            kind, callback = self.app.cloud_events.get_nowait()
            self.assertEqual(kind, "call")
            callback()

    def save(self):
        self.buttons["Salvar no Supabase"].invoke()

    def complete_save(self, ok=True, detail="Resposta do servidor", version=4):
        record = {"perfil": MARQUES_BRECHO, "mensagem_cliente": self.store.salvar_mensagem_perfil_live.call_args.args[1],
                  "versao": version}
        callback = self.store.salvar_mensagem_perfil_live.call_args.args[3]
        worker = threading.Thread(target=lambda: callback(ok, detail, record if ok else None))
        worker.start()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())

    def test_preview_adds_single_handle_and_fills_items_total(self):
        self.set_text("Olá!\n{pecas}\nTotal: {total}")
        preview = self.preview.get("1.0", "end-1c")
        self.assertTrue(preview.startswith("@cliente_exemplo\n\nOlá!"))
        self.assertIn("Peça 024: R$ 39,90", preview)
        self.assertIn("Total: R$ 60,00", preview)
        self.assertNotIn("@@", preview)
        self.assertEqual(self.preview.cget("state"), "disabled")

    def test_required_and_unknown_tokens_prevent_saving(self):
        for invalid in ("Olá {pecas}", "Olá {total}", "{pecas} {total} {senha}"):
            self.set_text(invalid)
            self.save()
            self.store.salvar_mensagem_perfil_live.assert_not_called()
            self.assertNotEqual(self.status(), "Salvando no Supabase…")
            self.assertFalse(self.buttons["Salvar no Supabase"].instate(["disabled"]))

    def test_async_success_uses_pinned_editor_version_and_updates_draft_snapshot(self):
        self.store.record["versao"] = 99  # A background refresh cannot change the editor revision.
        message = "Novo modelo\n{pecas}\nTotal {total}"
        self.set_text(message)
        self.save()
        self.save()
        self.store.salvar_mensagem_perfil_live.assert_called_once()
        self.assertEqual(self.store.salvar_mensagem_perfil_live.call_args.args[:3], (MARQUES_BRECHO, message, 3))
        self.assertTrue(self.app.profile_message_saving)
        self.complete_save()
        self.assertTrue(self.app.profile_message_saving)
        self.assertEqual(self.status(), "Salvando no Supabase…")
        self.drain_callbacks()
        self.assertFalse(self.app.profile_message_saving)
        self.assertIn("Mensagem salva no Supabase", self.status())
        self.assertEqual(self.app.live_message_template, message)

    def test_already_queued_save_response_is_ignored_after_auth_generation_changes(self):
        snapshot = self.app.live_message_template
        message = "Texto preservado da conta anterior\n{pecas}\n{total}"
        self.set_text(message)
        self.save()
        self.complete_save()
        self.assertEqual(self.app.cloud_events.qsize(), 1)
        self.assertTrue(self.app.profile_message_saving)

        # Logout/account change happens after the server callback was enqueued,
        # but before Tk applies it. Models are already ready after reconnecting;
        # their availability alone cannot authorize the old response.
        self.store.contexto_autenticacao = 2
        with patch.object(self.app, "_save_rows") as protect:
            self.drain_callbacks()
            protect.assert_not_called()
        self.assertEqual(self.app.live_message_template, snapshot)
        self.assertEqual(self.text.get("1.0", "end-1c"), message)
        self.assertIn("A conta mudou ou foi desconectada", self.status())
        self.assertNotIn("Mensagem salva no Supabase", self.status())
        self.assertFalse(self.app.profile_message_saving)
        self.assertEqual(self.text.cget("state"), "normal")
        self.assertTrue(self.buttons["Salvar no Supabase"].instate(["disabled"]))
        self.assertTrue(self.buttons["Recarregar modelo"].instate(["disabled"]))
        self.assertFalse(self.buttons["Fechar"].instate(["disabled"]))
        # Logging back in must require a new editor, even if settings are ready again.
        self.assertFalse(self.app.profile_message_context_check())
        self.save()
        self.store.salvar_mensagem_perfil_live.assert_called_once()

    def test_failure_preserves_text_snapshot_and_editor_version(self):
        snapshot = self.app.live_message_template
        message = "Tentar novamente\n{pecas}\nTotal {total}"
        self.set_text(message)
        self.save()
        self.complete_save(False, "Conflito de versão. Recarregue o modelo.")
        self.drain_callbacks()
        self.assertEqual(self.status(), "Conflito de versão. Recarregue o modelo.")
        self.assertEqual(self.app.live_message_template, snapshot)
        self.assertEqual(self.text.get("1.0", "end-1c"), message)
        self.assertFalse(self.app.profile_message_saving)
        self.save()
        self.assertEqual(self.store.salvar_mensagem_perfil_live.call_args.args[2], 3)

    def test_failure_to_start_saving_keeps_text_and_allows_retry(self):
        message = "Mensagem intacta\n{pecas}\nTotal {total}"
        snapshot = self.app.live_message_template
        self.set_text(message)
        self.store.salvar_mensagem_perfil_live.side_effect = RuntimeError("Falha controlada")
        self.save()
        self.assertIn("Não foi possível iniciar o salvamento", self.status())
        self.assertEqual(self.text.get("1.0", "end-1c"), message)
        self.assertEqual(self.app.live_message_template, snapshot)
        self.assertFalse(self.app.profile_message_saving)
        self.assertFalse(self.buttons["Salvar no Supabase"].instate(["disabled"]))
        self.store.salvar_mensagem_perfil_live.side_effect = None
        self.save()
        self.assertTrue(self.app.profile_message_saving)

    def test_acknowledged_model_reports_unprotected_draft_without_false_local_success(self):
        self.set_text("Modelo confirmado\n{pecas}\n{total}")
        self.save()
        self.complete_save()
        with patch.object(self.app, "_save_rows", return_value=False) as protect:
            self.drain_callbacks()
            protect.assert_called_once_with(show_error=False)
        self.assertIn("Modelo salvo no Supabase", self.status())
        self.assertIn("rascunho atual ainda não foi protegida", self.status())
        self.assertFalse(self.app.profile_message_saving)

    def test_saving_shared_model_does_not_change_started_live_snapshot(self):
        snapshot = self.app.live_message_template
        self.app.live_first_started_at = datetime(2026, 10, 22, 18)
        self.app.live_running = True
        self.store.queue_state.reset_mock()
        self.set_text("Mensagem futura\n{pecas}\nTotal {total}")
        self.save()
        self.complete_save()
        self.drain_callbacks()
        self.assertIn("Mensagem salva no Supabase", self.status())
        self.assertEqual(self.app.live_message_template, snapshot)
        self.store.queue_state.assert_not_called()

    def test_reload_requires_confirmation_for_unsaved_text_and_updates_revision(self):
        self.set_text("Minha alteração\n{pecas}\n{total}")
        with patch("app.messagebox.askyesno", return_value=False) as confirm:
            self.buttons["Recarregar modelo"].invoke()
            confirm.assert_called_once()
        self.store.recarregar_perfis_live.assert_not_called()
        self.assertIn("Minha alteração", self.text.get("1.0", "end-1c"))
        with patch("app.messagebox.askyesno", return_value=True):
            self.buttons["Recarregar modelo"].invoke()
        refreshed = {"perfil": MARQUES_BRECHO, "mensagem_cliente": "Servidor\n{pecas}\n{total}", "versao": 8}
        self.store.recarregar_perfis_live.call_args.args[0](True, "OK", {MARQUES_BRECHO: refreshed})
        self.assertNotIn("Servidor", self.text.get("1.0", "end-1c"))
        self.drain_callbacks()
        self.assertIn("Servidor", self.text.get("1.0", "end-1c"))
        self.save()
        self.assertEqual(self.store.salvar_mensagem_perfil_live.call_args.args[2], 8)

    def test_editor_close_is_blocked_while_saving(self):
        self.save()
        self.assertTrue(self.buttons["Fechar"].instate(["disabled"]))
        self.app.tk.call(self.window.protocol("WM_DELETE_WINDOW"))
        self.assertTrue(self.window.winfo_exists())
        self.assertTrue(self.app.profile_message_saving)

    def test_app_close_is_blocked_while_saving(self):
        self.save()
        with patch.object(self.app, "destroy") as destroy, patch("app.messagebox.showinfo"):
            self.app._on_close()
            destroy.assert_not_called()
        self.assertFalse(self.app.closing)
        self.assertTrue(self.app.profile_message_saving)


if __name__ == "__main__":
    unittest.main()
