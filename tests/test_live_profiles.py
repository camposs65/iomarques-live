"""Profile selection and historical snapshots, using only fictional cloud data."""

import copy
import tkinter as tk
import unittest
from datetime import datetime
from tkinter import ttk
from unittest.mock import Mock, patch

from app import LiveSalesApp
from live_profiles import (DEFAULT_MESSAGES, IO_MARQUES, IO_MESSAGE_TEMPLATE,
                           MARQUES_BRECHO, normalize_profile,
                           render_client_message, validate_message_template)
from test_profile_theme import ThemeStore


def saved_state(perfil=None, message=None, finished=False):
    live = {
        "id": "live_fictional_snapshot", "running": False,
        "first_started_at": "2026-10-22T18:00:00", "started_at": None,
        "finished_at": "2026-10-22T19:00:00" if finished else None,
        "elapsed_seconds": 3600 if finished else 10,
    }
    if perfil is not None:
        live.update(perfil=perfil, mensagem_cliente=message or DEFAULT_MESSAGES[perfil])
    return {"live": live, "rows": [{"codigo": "024", "valor": "R$ 25,00", "cliente": "ana",
                                     "suplente": "", "tempo": "00:00:03"}]}


class LiveProfilePureTests(unittest.TestCase):
    def test_legacy_default_and_both_known_identifiers(self):
        self.assertEqual(normalize_profile(None), IO_MARQUES)
        self.assertEqual(normalize_profile(IO_MARQUES), IO_MARQUES)
        self.assertEqual(normalize_profile(MARQUES_BRECHO), MARQUES_BRECHO)
        for invalid in ("", "masculino", "IO_MARQUES", 1, True, [], {}, set()):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                normalize_profile(invalid)

    def test_template_validation_preserves_exact_text_and_requires_purchase_values(self):
        original = "  Olá!\n{pecas}\nTotal {total}\n"
        self.assertEqual(validate_message_template(original), original)
        for invalid in (None, "", "   ", "{cliente} {pecas}", "{cliente} {total}",
                        "{pecas} {total} {senha}", "{pecas} {total}\0", "x" * 10001):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_message_template(invalid)

    def test_render_is_one_pass_and_treats_braces_in_values_as_data(self):
        result = render_client_message("{cliente}\n{pecas}\n{total}",
                                       "@@ana{total}", "- Peça {cliente}: R$ 5,00", "R$ {pecas}")
        self.assertEqual(result, "@ana{total}\n- Peça {cliente}: R$ 5,00\nR$ {pecas}")

    def test_missing_client_token_still_includes_one_handle(self):
        result = render_client_message("Compras\n{pecas}\n{total}", "  @@ana  ", "Peça 024", "R$ 25,00")
        self.assertEqual(result, "@ana\n\nCompras\nPeça 024\nR$ 25,00")

    def test_legacy_state_gets_original_io_message_without_mutating_saved_data(self):
        instance = LiveSalesApp.__new__(LiveSalesApp)
        state = saved_state()
        original = copy.deepcopy(state)
        self.assertEqual(instance._saved_profile_metadata(state), (IO_MARQUES, IO_MESSAGE_TEMPLATE))
        self.assertEqual(state, original)


class LiveProfileIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.store = ThemeStore()
        self.records = {perfil: {"perfil": perfil, "mensagem_cliente": message, "versao": 1}
                        for perfil, message in DEFAULT_MESSAGES.items()}
        self.store.perfis_live = Mock(side_effect=lambda: copy.deepcopy(self.records))
        active = patch("app.CloudLiveStore", return_value=self.store)
        active.start()
        self.addCleanup(active.stop)
        self.app = LiveSalesApp()
        self.app.withdraw()
        self.addCleanup(self.cleanup_app)

    def cleanup_app(self):
        for job in (self.app.timer_job, self.app.autosave_job, self.app.cloud_poll_job):
            if job is not None:
                self.app.after_cancel(job)
        self.app.update_idletasks()
        self.app.destroy()

    def fill_row(self):
        for index, value in enumerate(("R$ 25,00", "024", "ana", "", "00:00:03")):
            self.app.row_vars[0][index].set(value)

    def test_start_captures_latest_shared_model_and_keeps_it_for_state_and_history(self):
        self.assertTrue(self.app._set_live_profile(MARQUES_BRECHO))
        self.fill_row()
        newest = "Mensagem mais recente\n{pecas}\nTotal {total}"
        self.records[MARQUES_BRECHO].update(mensagem_cliente=newest, versao=2)
        self.app.start_live()
        self.assertTrue(self.app.live_running)
        state = self.store.queue_state.call_args.args[0]
        self.assertEqual(state["live"]["perfil"], MARQUES_BRECHO)
        self.assertEqual(state["live"]["mensagem_cliente"], newest)
        self.records[MARQUES_BRECHO].update(mensagem_cliente="Próxima live {pecas} {total}", versao=3)
        self.app._apply_cloud_data()
        self.assertEqual(self.app.live_message_template, newest)
        record = self.app._current_live_history_record(datetime(2026, 10, 22, 19))
        self.assertEqual(record["perfil"], MARQUES_BRECHO)
        self.assertEqual(record["mensagem_cliente"], newest)
        self.assertEqual(state["live"]["mensagem_cliente"], newest)

    def test_reopening_finished_masculine_live_uses_snapshot_instead_of_current_shared_model(self):
        old = "Mensagem usada naquela live\n{pecas}\nTotal {total}"
        state = saved_state(MARQUES_BRECHO, old, finished=True)
        self.records[MARQUES_BRECHO].update(mensagem_cliente="Atual {pecas} {total}", versao=99)
        self.assertTrue(self.app._apply_saved_state(state))
        self.assertEqual(self.app.live_profile, MARQUES_BRECHO)
        self.assertEqual(self.app.live_message_template, old)
        self.assertIn("Mensagem usada naquela live", self.app._client_messages()[0][1])
        self.assertNotIn("Atual", self.app._client_messages()[0][1])
        self.app.start_live()
        self.assertTrue(self.app.live_running)
        self.assertEqual(self.app.live_message_template, old)
        self.assertEqual(self.app._sheet_data()["live"]["mensagem_cliente"], old)

    def test_opening_history_transfers_profile_and_snapshot_to_main_sheet(self):
        old = "Mensagem histórica\n{pecas}\n{total}"
        state = saved_state(MARQUES_BRECHO, old, finished=True)
        history = {"id": state["live"]["id"], "started_at": state["live"]["first_started_at"],
                   "finished_at": state["live"]["finished_at"], "duration": "01:00:00",
                   "rows": state["rows"], "perfil": MARQUES_BRECHO, "mensagem_cliente": old}
        self.assertTrue(self.app._load_history_live_into_main_sheet(history))
        self.assertEqual(self.app.live_profile, MARQUES_BRECHO)
        self.assertEqual(self.app.live_message_template, old)
        self.store.begin_edit.assert_called_with(history["id"], history=history)
        self.assertEqual(self.app.row_vars[0][1].get(), "024")

    def test_legacy_io_reopening_after_masculine_choice_restores_original_message(self):
        self.assertTrue(self.app._set_live_profile(MARQUES_BRECHO))
        self.assertTrue(self.app._apply_saved_state(saved_state(finished=True)))
        self.assertEqual(self.app.live_profile, IO_MARQUES)
        self.assertEqual(self.app.live_message_template, IO_MESSAGE_TEMPLATE)
        self.assertIn("Oi amada!", self.app._client_messages()[0][1])

    def test_invalid_profile_snapshot_is_rejected_before_resetting_current_sheet(self):
        self.fill_row()
        before = self.app._sheet_data()
        for invalid in (
            {"perfil": "unknown", "mensagem_cliente": DEFAULT_MESSAGES[MARQUES_BRECHO]},
            {"perfil": [], "mensagem_cliente": DEFAULT_MESSAGES[MARQUES_BRECHO]},
            {"perfil": None, "mensagem_cliente": DEFAULT_MESSAGES[MARQUES_BRECHO]},
            {"perfil": MARQUES_BRECHO},
            {"mensagem_cliente": DEFAULT_MESSAGES[MARQUES_BRECHO]},
            {"perfil": MARQUES_BRECHO, "mensagem_cliente": "Sem os valores"},
        ):
            with self.subTest(invalid=invalid):
                state = saved_state()
                state["live"].update(invalid)
                with patch("app.messagebox.showerror") as error, \
                        patch.object(self.app, "_reset_sheet_widgets", wraps=self.app._reset_sheet_widgets) as reset:
                    self.assertFalse(self.app._apply_saved_state(state))
                    error.assert_called_once()
                    reset.assert_not_called()
                self.assertEqual(self.app._sheet_data(), before)

    def test_masculine_choice_requires_a_confirmed_shared_model(self):
        self.store.perfis_disponiveis = False
        self.records.clear()
        self.store.queue_state.reset_mock()
        with patch("app.messagebox.showinfo"):
            self.assertFalse(self.app._set_live_profile(MARQUES_BRECHO))
        self.assertEqual(self.app.live_profile, IO_MARQUES)
        self.assertIsNone(self.app.live_first_started_at)
        self.store.queue_state.assert_not_called()

    def test_profile_choice_rolls_back_if_draft_cannot_be_protected(self):
        self.fill_row()
        original = (self.app.live_profile, self.app.live_message_template)
        self.store.queue_state.return_value = False
        with patch("app.messagebox.showerror"):
            self.assertFalse(self.app._set_live_profile(MARQUES_BRECHO))
        self.assertEqual((self.app.live_profile, self.app.live_message_template), original)
        self.assertIsNone(self.app.live_first_started_at)

    def test_history_filters_legacy_io_and_masculine_and_recalculates_totals(self):
        lives = [
            {"id": "legacy", "finished_at": "2026-10-21T19:00:00", "total": "R$ 10,00"},
            {"id": "male", "finished_at": "2026-10-22T19:00:00", "total": "R$ 20,00",
             "perfil": MARQUES_BRECHO, "mensagem_cliente": DEFAULT_MESSAGES[MARQUES_BRECHO]},
            {"id": "io", "finished_at": "2026-10-20T19:00:00", "total": "R$ 30,00",
             "perfil": IO_MARQUES, "mensagem_cliente": IO_MESSAGE_TEMPLATE},
        ]
        original = copy.deepcopy(lives)
        self.store.history.return_value = lives
        self.app.show_history()
        window = next(child for child in self.app.winfo_children() if isinstance(child, tk.Toplevel))
        window.withdraw()

        def walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from walk(child)

        widgets = list(walk(window))
        tree = next(widget for widget in widgets if isinstance(widget, ttk.Treeview))
        selector = next(widget for widget in widgets if isinstance(widget, ttk.Combobox))

        def values():
            return [tree.item(item, "values") for month in tree.get_children()
                    for item in tree.get_children(month)]

        self.assertEqual(len(values()), 3)
        selector.set("Marques Brechó")
        self.app.history_refreshers[window]()
        self.assertEqual([row[-1] for row in values()], ["Marques Brechó"])
        month = tree.get_children()[0]
        self.assertEqual(tree.item(month, "values")[4], "R$ 20,00")
        selector.set("Io Marques")
        self.app.history_refreshers[window]()
        self.assertEqual([row[-1] for row in values()], ["Io Marques", "Io Marques"])
        month = tree.get_children()[0]
        self.assertEqual(tree.item(month, "values")[4], "R$ 40,00")
        selector.set("Todos os perfis")
        self.app.history_refreshers[window]()
        self.assertEqual(len(values()), 3)
        self.assertEqual(lives, original)


if __name__ == "__main__":
    unittest.main()
