"""Purchase badges with a fake store; no database, credentials or real updater."""

import copy
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import CLIENT_COL, COLUMNS, SUPLENTE_COL, LiveSalesApp, Workbook
from live_profiles import IO_MARQUES, MARQUES_BRECHO
from test_profile_theme import ThemeStore


def row(customer="ana", code="024", alternate=""):
    return {"valor": "R$ 20,00", "codigo": code, "cliente": customer,
            "suplente": alternate, "tempo": "00:00:03"}


def live(live_id, rows, profile=None):
    value = {"id": live_id, "started_at": "2026-10-21T18:00:00",
             "finished_at": "2026-10-21T19:00:00", "duration": "01:00:00",
             "total": "R$ 20,00", "rows": rows, "clients": []}
    if profile is not None:
        value["perfil"] = profile
    return value


class CustomerPurchaseUiTests(unittest.TestCase):
    def setUp(self):
        self.lives = [live("previous", [row("ana"), row("@ANA", "256"), row("bia", "543")])]
        self.store = ThemeStore()
        self.store.history.side_effect = lambda: copy.deepcopy(self.lives)
        for active in (patch("app.CloudLiveStore", return_value=self.store),
                       patch("app.UpdateClient"),
                       patch.object(LiveSalesApp, "_read_legacy_data", return_value=([], {}))):
            active.start()
            self.addCleanup(active.stop)
        self.app = LiveSalesApp()
        self.app.withdraw()
        self.app.update_idletasks()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.app._hide_customer_count_hint()
        for job in self.app.tk.call("after", "info"):
            self.app.after_cancel(job)
        self.app.update_idletasks()
        self.app.destroy()

    def apply_rows(self, rows, live_id="current"):
        self.assertTrue(self.app._apply_saved_state({
            "live": {"id": live_id, "running": False, "elapsed_seconds": 0},
            "rows": copy.deepcopy(rows),
        }))

    def badge(self, index=0):
        return self.app.customer_count_labels[index]

    def type_customer(self, customer, index=0):
        self.app.row_vars[index][CLIENT_COL].set(customer)
        self.app._on_key_release(SimpleNamespace(keysym="a"), index, CLIENT_COL)

    def test_small_badge_is_separate_from_name_and_hidden_on_blank_rows(self):
        self.apply_rows([row("@ANA")])
        badge = self.badge()
        self.assertIsInstance(badge, tk.Label)
        self.assertEqual(badge.cget("text"), "3")
        self.assertEqual(self.app.row_vars[0][CLIENT_COL].get(), "@ANA")
        self.assertEqual(self.app.cell_entries[0][CLIENT_COL].get(), "@ANA")
        self.assertIn("8", badge.cget("font"))
        self.assertEqual(self.badge(1).cget("text"), "")
        self.assertEqual(self.badge(1).winfo_manager(), "")

    def test_cloud_ack_restores_counter_if_data_event_arrived_before_auth_status(self):
        self.apply_rows([row("ana")])
        self.store.is_authenticated = False
        self.store.cloud_loaded = False
        self.app._apply_sync_status("auth_required", "Entre novamente", "")
        self.store.is_authenticated = True
        self.store.cloud_loaded = True
        self.app._apply_cloud_data()
        self.assertEqual(self.badge().cget("text"), "…")
        self.app._apply_sync_status("synced", "Salvo no Supabase", "teste")
        self.assertTrue(self.app.purchase_history_loaded)
        self.assertEqual(self.badge().cget("text"), "3")

    def test_opening_historical_live_does_not_double_its_rows(self):
        self.assertTrue(self.app._load_history_live_into_main_sheet(self.lives[0]))
        self.assertEqual([self.badge(i).cget("text") for i in range(3)], ["2", "2", "1"])
        self.assertEqual(self.app.live_id, "previous")

    def test_handle_variants_and_both_profiles_share_one_total(self):
        self.lives = [
            live("io", [row("@ANA")], IO_MARQUES),
            live("masculine", [row("  ana  ")], MARQUES_BRECHO),
        ]
        self.app._apply_cloud_data()
        self.apply_rows([row("@@AnA"), row("@ana", "256")])
        self.assertEqual(self.badge().cget("text"), "4")
        self.assertEqual(self.badge(1).cget("text"), "4")

    def test_typing_updates_all_rows_for_the_same_customer(self):
        self.apply_rows([row("ana"), row("ana", "256")])
        self.assertEqual(self.badge().cget("text"), "4")
        self.type_customer("bia", 1)
        self.assertEqual(self.badge().cget("text"), "3")
        self.assertEqual(self.badge(1).cget("text"), "2")
        self.type_customer("", 1)
        self.assertEqual(self.badge(1).cget("text"), "")
        self.assertEqual(self.badge(1).winfo_manager(), "")
        self.assertEqual(self.badge().cget("text"), "3")

    def test_clear_promotes_alternate_without_counting_it_twice(self):
        self.apply_rows([row("ana", alternate="bia")])
        self.assertEqual(self.badge().cget("text"), "3")
        self.app._clear_client_cell(0, CLIENT_COL)
        self.assertEqual(self.app.row_vars[0][CLIENT_COL].get(), "bia")
        self.assertEqual(self.app.row_vars[0][SUPLENTE_COL].get(), "")
        self.assertEqual(self.badge().cget("text"), "2")
        self.app._clear_client_cell(0, CLIENT_COL)
        self.assertEqual(self.badge().cget("text"), "")

    def test_undo_and_redo_recompute_badges(self):
        self.apply_rows([row("ana")])
        self.app._record_undo_state()
        self.type_customer("bia")
        self.assertEqual(self.badge().cget("text"), "2")
        self.app.undo()
        self.assertEqual(self.app.row_vars[0][CLIENT_COL].get(), "ana")
        self.assertEqual(self.badge().cget("text"), "3")
        self.app.redo()
        self.assertEqual(self.app.row_vars[0][CLIENT_COL].get(), "bia")
        self.assertEqual(self.badge().cget("text"), "2")

    def test_last_row_removal_and_state_restore_keep_label_lists_aligned(self):
        self.apply_rows([row("ana"), row("bia", "256")])
        removed = self.badge(2)
        self.app._remove_last_row()
        self.assertEqual(removed.winfo_exists(), 0)
        self.assertEqual(len(self.app.customer_count_labels), len(self.app.row_vars))
        self.assertEqual(len(self.app.row_vars), 2)
        old = self.badge()
        self.apply_rows([row("carla")], "other")
        self.assertEqual(old.winfo_exists(), 0)
        self.assertEqual(self.badge().cget("text"), "1")
        self.assertEqual(len(self.app.customer_count_labels), len(self.app.row_vars))

    def test_badges_follow_both_themes_and_sold_background(self):
        self.apply_rows([row("ana")])
        badge = self.badge()
        for profile in (MARQUES_BRECHO, IO_MARQUES):
            self.assertTrue(self.app._set_live_profile(profile))
            self.assertEqual(badge.cget("background"), self.app._row_bg(0))
            self.assertEqual(badge.cget("foreground"), self.app.colors["button_text"])
            self.assertEqual(badge.cget("text"), "3")

    def test_loading_history_never_presents_pending_total_as_zero(self):
        self.store.cloud_loaded = False
        self.app._apply_cloud_data()
        self.apply_rows([row("unknown")])
        self.assertFalse(self.app.purchase_history_loaded)
        self.assertEqual(self.badge().cget("text"), "…")
        self.assertIn("Aguardando", self.badge().purchase_hint)
        self.store.cloud_loaded = True
        self.app._apply_cloud_data()
        self.assertEqual(self.badge().cget("text"), "1")

    def test_auth_required_clears_cached_counts_until_next_authenticated_load(self):
        self.apply_rows([row("ana")])
        self.assertEqual(self.badge().cget("text"), "3")
        self.store.is_authenticated = False
        self.store.ready = False
        self.app._apply_sync_status("auth_required", "Entre novamente", "")
        self.assertFalse(self.app.purchase_history_loaded)
        self.assertEqual(self.app.purchase_history.totals, {})
        self.assertEqual(self.badge().cget("text"), "…")
        self.app._apply_cloud_data()
        self.assertEqual(self.badge().cget("text"), "…")
        self.lives = []
        self.store.is_authenticated = self.store.ready = True
        self.app._apply_sync_status("synced", "Confirmado", "teste")
        self.app._apply_cloud_data()
        self.assertEqual(self.badge().cget("text"), "1")

    def test_account_context_change_during_loading_cannot_show_previous_counts(self):
        self.store.contexto_autenticacao = 1
        self.app._apply_cloud_data()
        self.apply_rows([row("ana")])
        self.assertEqual(self.badge().cget("text"), "3")
        self.assertEqual(self.app.purchase_auth_context, 1)
        self.store.contexto_autenticacao = 2
        self.store.cloud_loaded = False
        self.app._apply_sync_status("loading", "Carregando outra conta", "outra_conta")
        self.assertFalse(self.app.purchase_history_loaded)
        self.assertEqual(self.app.purchase_history.totals, {})
        self.assertEqual(self.badge().cget("text"), "…")
        self.app._apply_cloud_data()
        self.assertEqual(self.badge().cget("text"), "…")
        self.lives = [live("new_account", [row("bia")])]
        self.store.cloud_loaded = True
        self.app._apply_cloud_data()
        self.assertEqual(self.badge().cget("text"), "1")
        self.assertEqual(self.app.purchase_auth_context, 2)

    def test_changed_account_invalidates_counter_even_before_loading_flag_arrives(self):
        self.store.contexto_autenticacao = 10
        self.app._apply_cloud_data()
        self.apply_rows([row("ana")])
        self.assertEqual(self.badge().cget("text"), "3")
        self.store.contexto_autenticacao = 11
        self.app._apply_sync_status("loading", "Outra conta", "outra_conta")
        self.assertEqual(self.badge().cget("text"), "…")
        self.assertEqual(self.app.purchase_history.totals, {})

    def test_cloud_refresh_rebuilds_cache_including_removed_lives(self):
        self.apply_rows([row("ana")])
        self.lives.append(live("new", [row("ana")]))
        self.app._apply_cloud_data()
        self.assertEqual(self.badge().cget("text"), "4")
        self.lives = []
        self.app._apply_cloud_data()
        self.assertEqual(self.badge().cget("text"), "1")

    def test_typing_reuses_cache_instead_of_reloading_history(self):
        self.apply_rows([row("ana")])
        self.store.history.reset_mock()
        self.type_customer("@ANA")
        self.type_customer("bia")
        self.store.history.assert_not_called()

    def test_partial_history_is_explicit_and_tooltip_does_not_change_rows(self):
        self.lives.append({"id": "summary", "sold_count": 20, "clients": ["ana"]})
        self.app._apply_cloud_data()
        self.apply_rows([row("ana")])
        self.assertEqual(self.badge().cget("text"), "3+")
        self.assertIn("Pelo menos 3", self.badge().purchase_hint)
        before = self.app._rows()
        self.app._show_customer_count_hint(SimpleNamespace(x_root=10, y_root=10), 0)
        self.assertIsNotNone(self.app.customer_count_tooltip)
        self.app._hide_customer_count_hint()
        self.assertIsNone(self.app.customer_count_tooltip)
        self.assertEqual(self.app._rows(), before)

    def test_counts_do_not_enter_rows_client_messages_or_excel(self):
        self.lives[0]["rows"] = [row("ana", str(index)) for index in range(74)]
        self.app._apply_cloud_data()
        self.apply_rows([row("ana")])
        self.assertEqual(self.badge().cget("text"), "75")
        rows = self.app._rows()
        self.assertEqual(rows, [row("ana")])
        self.assertEqual(set(rows[0]), set(COLUMNS))
        self.assertNotIn("75", self.app._client_messages_text())
        if Workbook is None:
            self.skipTest("openpyxl não está instalado")
        workbook = Workbook()
        with patch("app.Workbook", return_value=workbook), \
                patch.object(workbook, "save") as save, \
                patch("app.filedialog.asksaveasfilename", return_value="ficticio.xlsx"), \
                patch("app.messagebox.showinfo"):
            self.app.export_excel()
        save.assert_called_once_with("ficticio.xlsx")
        self.assertEqual(workbook["Vendas"].max_column, len(COLUMNS) + 1)
        self.assertEqual(workbook["Vendas"].cell(2, CLIENT_COL + 2).value, "ana")
        self.assertEqual(workbook["Resumo por cliente"].cell(2, 2).value, "ana")

    def test_minimum_window_keeps_counter_entry_and_clear_button_separate(self):
        self.lives[0]["rows"] = [row("nome_muito_longo_da_cliente", str(index)) for index in range(150)]
        self.app._apply_cloud_data()
        self.apply_rows([row("nome_muito_longo_da_cliente")])
        self.app.geometry("960x580")
        self.app.deiconify()
        self.app._show_clear_button(0, CLIENT_COL)
        self.app.update_idletasks()
        cell = self.app.cell_frames[0][CLIENT_COL]
        entry = self.app.cell_entries[0][CLIENT_COL]
        badge = self.badge()
        clear = self.app.clear_buttons[0][CLIENT_COL]
        self.assertEqual(badge.cget("text"), "151")
        for widget in (entry, badge, clear):
            self.assertTrue(widget.winfo_ismapped())
            self.assertGreater(widget.winfo_width(), 1)
            self.assertGreaterEqual(widget.winfo_x(), 0)
            self.assertLessEqual(widget.winfo_x() + widget.winfo_width(), cell.winfo_width())
        intervals = sorted((widget.winfo_x(), widget.winfo_x() + widget.winfo_width())
                           for widget in (entry, badge, clear))
        self.assertLessEqual(intervals[0][1], intervals[1][0])
        self.assertLessEqual(intervals[1][1], intervals[2][0])
        self.app.withdraw()


if __name__ == "__main__":
    unittest.main()
