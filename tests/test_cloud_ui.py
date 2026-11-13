import copy
import tkinter as tk
import unittest
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import CLIENT_COL, CODE_COL, COLUMNS, ROW_COLUMNS, SIZE_COL, SUPLENTE_COL, TIME_COL, VALUE_COL, LiveSalesApp
from supabase_sync import prepare_live_payload


ROW = {"valor": "R$ 20,00", "codigo": "001", "cliente": "", "suplente": "", "tempo": "00:00:03"}


class FakeStore:
    def __init__(self, *_args, **_kwargs):
        self.ready = self.cloud_loaded = self.is_authenticated = self.is_configured = True
        self.tamanho_pecas_disponivel = False
        self.is_admin = False
        self.needs_legacy_bootstrap = True
        self.has_pending = False
        self.username = "teste"
        self.saved = []
        self.lives = []
        self.states = []
        self.pending = []
        self.start = Mock()
        self.shutdown = Mock()
        self.sync_now = Mock()
        self.bootstrap_legacy = Mock()
        self.begin_edit = Mock()
        self.end_edit = Mock()
        self.queue_history = Mock(return_value=True)
        self.queue_delete = Mock(return_value=True)
        self.conflicts = Mock(return_value=[])
        self.is_deleted = Mock(return_value=False)

    def history(self):
        return copy.deepcopy(self.lives)

    def current_states(self):
        return copy.deepcopy(self.states)

    def pending_states(self):
        return copy.deepcopy(self.pending)

    def queue_state(self, data, history=None):
        self.saved.append((copy.deepcopy(data), copy.deepcopy(history)))
        self.has_pending = True
        return True


class CloudUiTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.store_patch = patch("app.CloudLiveStore", return_value=self.store)
        self.legacy_patch = patch.object(LiveSalesApp, "_read_legacy_data", return_value=([], {}))
        self.store_patch.start()
        self.legacy_patch.start()
        self.addCleanup(self.store_patch.stop)
        self.addCleanup(self.legacy_patch.stop)
        self.app = LiveSalesApp()
        self.app.withdraw()
        self.addCleanup(self.cleanup_app)

    def cleanup_app(self):
        for job in (self.app.timer_job, self.app.autosave_job, self.app.cloud_poll_job):
            if job is not None:
                self.app.after_cancel(job)
        self.app.update_idletasks()
        self.app.destroy()

    def fill_row(self, row=None):
        row = row or ROW
        for index, column in enumerate(ROW_COLUMNS):
            self.app.row_vars[0][index].set(row.get(column, ""))

    def history_widgets(self):
        self.app.show_history()
        window = next(child for child in self.app.winfo_children() if isinstance(child, tk.Toplevel))
        window.withdraw()
        widgets = []

        def walk(parent):
            for child in parent.winfo_children():
                widgets.append(child)
                walk(child)

        walk(window)
        tree = next(widget for widget in widgets if isinstance(widget, ttk.Treeview))
        buttons = {widget.cget("text"): widget for widget in widgets if isinstance(widget, ttk.Button)}
        return window, tree, buttons

    def test_empty_start_does_not_create_draft_or_write_queue(self):
        self.assertEqual(self.store.saved, [])
        self.assertIsNone(self.app.live_id)
        self.app._save_rows()
        self.assertEqual(self.store.saved, [])

    def test_draft_id_is_stable_and_unchanged_autosave_is_deduplicated(self):
        self.fill_row()
        self.assertTrue(self.app._save_rows())
        first_id = self.app.live_id
        self.store.begin_edit.assert_called_once_with(first_id)
        self.assertTrue(first_id.startswith("live_"))
        self.assertEqual(len(first_id), 37)
        self.assertIn("updated_at", self.store.saved[0][0])
        self.assertTrue(self.app._save_rows())
        self.assertEqual(len(self.store.saved), 1)
        self.app.row_vars[0][CLIENT_COL].set("cliente de teste")
        self.app._save_rows()
        self.assertEqual(self.app.live_id, first_id)
        self.assertEqual(self.store.saved[-1][0]["rows"][0]["cliente"], "cliente de teste")

    def test_size_column_is_visible_but_does_not_change_legacy_sync(self):
        self.assertEqual(COLUMNS, ("valor", "codigo", "tamanho", "cliente", "suplente"))
        self.assertEqual(len(self.app.cell_entries[0]), len(COLUMNS))
        self.assertFalse(self.app._cell_exists(0, TIME_COL))
        self.assertEqual(self.app.cell_entries[0][SIZE_COL].cget("state"), "disabled")
        self.assertEqual(self.app.size_notice.winfo_manager(), "pack")
        self.assertEqual(self.app.cell_entries[0][CODE_COL].cget("state"), "normal")
        event = SimpleNamespace(widget=self.app.cell_entries[0][SIZE_COL])
        with patch.object(self.app, "open_sync_dialog") as dialog:
            self.assertEqual(self.app._open_size_sync_if_blocked(event), "break")
            dialog.assert_called_once()
        self.fill_row()
        self.assertTrue(self.app._save_rows())
        self.assertEqual(self.store.saved[-1][0]["rows"], [ROW])
        self.assertNotIn("formato_linhas", self.store.saved[-1][0])
        with patch.object(self.app, "_focus_cell") as focus:
            self.app._move_focus_right(0, CODE_COL)
            focus.assert_called_once_with(0, CLIENT_COL)
        self.store.tamanho_pecas_disponivel = True
        self.app._apply_cloud_data()
        size_entry = self.app.cell_entries[0][SIZE_COL]
        self.assertEqual(size_entry.cget("state"), "normal")
        self.assertEqual(self.app.size_notice.winfo_manager(), "")
        with patch.object(self.app, "open_sync_dialog") as dialog:
            self.assertIsNone(self.app._open_size_sync_if_blocked(event))
            dialog.assert_not_called()
        size_entry.insert(0, "X" * 101)
        self.assertEqual(size_entry.get(), "")
        with patch.object(self.app, "_focus_cell") as focus:
            self.app._move_focus_right(0, CODE_COL)
            self.app._move_focus_right(0, SIZE_COL)
            self.app._move_focus_right(0, SUPLENTE_COL)
            self.assertEqual([call.args for call in focus.call_args_list], [(0, SIZE_COL), (0, CLIENT_COL), (1, 0)])
        self.assertEqual(len(self.store.saved), 1)

    def test_header_and_sheet_keep_identical_column_edges_when_resized_and_filtered(self):
        self.fill_row()
        self.app._ensure_blank_row()
        self.app.deiconify()
        self.app.filter_buttons[SIZE_COL].configure(font=("Segoe UI", 18))
        self.app.filter_vars[CODE_COL].set(ROW["codigo"])
        self.app._apply_filters()
        for width in (960, 1434, 1160):
            with self.subTest(width=width):
                self.app.geometry(f"{width}x580")
                self.app.update()
                self.assertTrue(self.app.size_notice.winfo_ismapped())
                cells = (self.app.index_frames[0], *self.app.cell_frames[0])
                for column, body in enumerate(cells):
                    header = self.app.header_frame.grid_slaves(row=0, column=column)[0]
                    self.assertEqual(header.winfo_rootx(), body.winfo_rootx())
                    self.assertEqual(header.winfo_width(), body.winfo_width())
                self.assertEqual(self.app.header_frame.winfo_width(), self.app.canvas.winfo_width())
                self.assertEqual(sum(cell.winfo_width() for cell in cells), self.app.canvas.winfo_width())
        self.app.withdraw()

    @patch("app.messagebox.askyesno", return_value=True)
    def test_live_column_order_preserves_rows_focus_filters_and_saves(self, _confirm):
        self.store.tamanho_pecas_disponivel = True
        rows = [{**ROW, "tamanho": "38"}, {**ROW, "codigo": "999", "tamanho": "M", "cliente": "Teste"}]
        self.assertTrue(self.app._apply_saved_state({"live": {"id": "current", "running": False}, "rows": rows}))
        entries = tuple(self.app.cell_entries[1])
        self.app._set_active_cell(1, SIZE_COL)
        self.app._record_undo_state()
        undo = copy.deepcopy(self.app.undo_stack)
        self.app.filter_vars[CODE_COL].set("999")
        self.app._apply_filters()
        self.assertEqual(self.app._visual_column_order(), tuple(range(len(COLUMNS))))
        self.app.start_live()
        order = (SIZE_COL, VALUE_COL, CODE_COL, CLIENT_COL, SUPLENTE_COL)
        self.assertEqual(self.app._visual_column_order(), order)
        self.assertEqual(self.app._rows(), rows)
        self.assertEqual(tuple(self.app.cell_entries[1]), entries)
        self.assertEqual(self.app.active_cell, (1, SIZE_COL))
        self.assertEqual(self.app.undo_stack, undo)
        self.assertEqual(self.app.cell_frames[0][CODE_COL].winfo_manager(), "")
        for position, column in enumerate(order, start=1):
            self.assertEqual(int(self.app.header_cells[column].grid_info()["column"]), position)
            self.assertEqual(int(self.app.cell_frames[1][column].grid_info()["column"]), position)
        saves = copy.deepcopy(self.store.saved)
        with patch.object(self.app, "_apply_filters", wraps=self.app._apply_filters) as filters:
            self.app._refresh_live_controls()
            self.app._refresh_live_controls()
            filters.assert_not_called()
        self.app.filter_vars[CODE_COL].set("")
        self.app._apply_filters()
        self.app._add_row(["R$ 12,00", "999", "G", "", "", "00:00:12"])
        for position, column in enumerate(order, start=1):
            self.assertEqual(int(self.app.cell_frames[-1][column].grid_info()["column"]), position)
        self.assertEqual(self.store.saved, saves)
        finished_rows = self.app._rows()
        self.app.finish_live()
        self.assertEqual(self.app._visual_column_order(), tuple(range(len(COLUMNS))))
        self.assertEqual(self.app._rows(), finished_rows)
        for column in range(len(COLUMNS)):
            self.assertEqual(int(self.app.header_cells[column].grid_info()["column"]), column + 1)
            self.assertEqual(int(self.app.cell_frames[1][column].grid_info()["column"]), column + 1)

    def test_live_keyboard_navigation_follows_visual_order_and_skips_locked_size(self):
        self.app.live_running = True
        self.app._refresh_live_controls()
        self.store.tamanho_pecas_disponivel = True
        with patch.object(self.app, "_focus_cell") as focus:
            self.app._move_focus_right(0, SIZE_COL)
            self.app._move_focus_right(0, VALUE_COL)
            self.app._move_focus_right(0, CODE_COL)
            self.app._move_focus_right(0, SUPLENTE_COL)
            self.app._move_focus_left(1, SIZE_COL)
            self.assertEqual([call.args for call in focus.call_args_list],
                             [(0, VALUE_COL), (0, CODE_COL), (0, CLIENT_COL), (1, SIZE_COL), (0, SUPLENTE_COL)])
        self.store.tamanho_pecas_disponivel = False
        with patch.object(self.app, "_focus_cell") as focus:
            self.app._move_focus_right(0, SUPLENTE_COL)
            self.app._move_focus_left(1, VALUE_COL)
            self.assertEqual([call.args for call in focus.call_args_list], [(1, VALUE_COL), (0, SUPLENTE_COL)])
        self.app.live_running = False
        self.app._refresh_live_controls()

    @patch("app.messagebox.askyesno", return_value=True)
    def test_live_headers_stay_aligned_after_start_resizes_and_finish(self, _confirm):
        self.fill_row()
        self.app._ensure_blank_row()
        self.app.deiconify()
        for start_live in (True, False):
            if start_live:
                self.app.start_live()
            else:
                self.app.finish_live()
            for width in (960, 1434, 1160):
                with self.subTest(running=start_live, width=width):
                    self.app.geometry(f"{width}x580")
                    self.app.update()
                    order = self.app._visual_column_order()
                    cells = (self.app.index_frames[0], *(self.app.cell_frames[0][col] for col in order))
                    for column, body in enumerate(cells):
                        header = self.app.header_frame.grid_slaves(row=0, column=column)[0]
                        self.assertEqual(header.winfo_rootx(), body.winfo_rootx())
                        self.assertEqual(header.winfo_width(), body.winfo_width())
                    self.assertEqual(sum(cell.winfo_width() for cell in cells), self.app.canvas.winfo_width())
        self.app.withdraw()

    def test_sold_color_updates_on_customer_change_without_keyboard_or_new_save(self):
        self.fill_row()
        self.store.tamanho_pecas_disponivel = True
        self.app._apply_cloud_data()
        customer = self.app.row_vars[0][CLIENT_COL]
        sold = self.app.colors["sold_row"]

        customer.set("cliente_teste")
        for frame, entry in zip(self.app.cell_frames[0], self.app.cell_entries[0]):
            self.assertEqual(frame.cget("background"), sold)
            self.assertEqual(entry.cget("background"), sold)
        self.app._apply_sync_status("synced", "Salvo", "teste")
        self.assertEqual(self.app.cell_entries[0][CLIENT_COL].cget("background"), sold)

        self.store.ready = False
        self.app._refresh_cloud_editability()
        for entry in self.app.cell_entries[0]:
            self.assertEqual(entry.cget("state"), "disabled")
            self.assertEqual(entry.cget("disabledbackground"), sold)

        for name in (".", ""):
            customer.set(name)
            self.assertEqual(self.app.cell_frames[0][CLIENT_COL].cget("background"), self.app.colors["table_bg"])
            self.assertEqual(self.app.cell_entries[0][CLIENT_COL].cget("disabledbackground"),
                             self.app.colors["table_header"])
        self.assertEqual(self.store.saved, [])

    @patch("app.messagebox.askyesno", return_value=True)
    def test_size_and_hidden_time_reach_history_and_recorder_projection(self, _confirm):
        self.store.tamanho_pecas_disponivel = True
        self.app._apply_cloud_data()
        self.app.start_live()
        edit = SimpleNamespace(keysym="a")
        with patch.object(self.app, "_current_elapsed_seconds", return_value=23):
            self.app.cell_entries[0][SIZE_COL].insert(0, "P/M")
            self.app._on_key_release(edit, 0, SIZE_COL)
            self.assertEqual(self.app.row_vars[0][TIME_COL].get(), "")
            self.app.row_vars[0][CODE_COL].set("014")
            self.app._on_key_release(edit, 0, CODE_COL)
        self.assertEqual(self.app.row_vars[0][TIME_COL].get(), "00:00:23")
        self.assertTrue(self.app.time_check_vars[0].get())
        self.app.row_vars[0][CLIENT_COL].set(".")
        with patch.object(self.app, "_current_elapsed_seconds", return_value=42):
            self.app._on_key_release(edit, 0, CODE_COL)
            self.app.finish_live()
        state, history = self.store.saved[-1]
        self.assertEqual(state["formato_linhas"], 2)
        self.assertEqual(history["formato_linhas"], 2)
        self.assertEqual(state["rows"], history["rows"])
        self.assertEqual(history["rows"][0]["tamanho"], "P/M")
        self.assertEqual(history["rows"][0]["tempo"], "00:00:23")
        self.assertEqual(history["rows"][0]["cliente"], ".")
        self.assertEqual(history["sold_count"], 0)
        projection = prepare_live_payload(history)
        self.assertEqual(projection["pecas"][0]["tempo_segundos"], 23)
        self.assertNotIn("tamanho", projection["pecas"][0])

    def test_reopen_and_undo_size_edits_keep_manual_mark_and_legacy_guard(self):
        self.store.tamanho_pecas_disponivel = True
        live = {"id": "old", "started_at": "2026-09-01T12:00:00", "finished_at": "2026-09-01T13:00:00",
                "duration": "01:00:00", "total": "R$ 0,00", "formato_linhas": 2,
                "rows": [{**ROW, "tamanho": "38"}]}
        self.assertTrue(self.app._load_history_live_into_main_sheet(live))
        self.assertEqual(self.app._rows(), live["rows"])
        self.assertTrue(self.app._save_rows())
        self.assertEqual(self.store.saved, [])
        self.app.time_check_buttons[0].invoke()
        self.assertEqual(self.app.row_vars[0][TIME_COL].get(), "")
        with patch.object(self.app, "_current_elapsed_seconds", return_value=31):
            self.app.time_check_buttons[0].invoke()
        self.app._record_undo_state()
        self.app.row_vars[0][SIZE_COL].set("")
        self.assertTrue(self.app._save_rows())
        state, history = self.store.saved[-1]
        self.assertEqual((state["formato_linhas"], history["formato_linhas"]), (2, 2))
        self.assertNotIn("tamanho", history["rows"][0])
        self.app.undo()
        self.assertEqual(self.app._rows()[0]["tamanho"], "38")
        self.assertEqual(self.app._rows()[0]["tempo"], "00:00:31")
        self.app.redo()
        self.assertNotIn("tamanho", self.app._rows()[0])
        self.assertEqual(self.app._rows()[0]["tempo"], "00:00:31")

    @patch("app.messagebox.askyesno", return_value=True)
    def test_finish_saves_state_and_full_history_in_one_operation(self, _confirm):
        self.fill_row()
        self.app.start_live()
        self.store.begin_edit.assert_called_once_with(self.app.live_id)
        self.app.finish_live()
        state, history = self.store.saved[-1]
        self.assertFalse(state["live"]["running"])
        self.assertIsNotNone(state["live"]["finished_at"])
        self.assertEqual(history["id"], state["live"]["id"])
        self.assertEqual(history["rows"], state["rows"])
        self.store.queue_history.assert_not_called()

    def test_status_callback_only_enqueues_and_never_calls_tk_from_worker(self):
        with patch.object(self.app, "after") as after:
            self.app._handle_sync_status("pending", "Aguardando conexão", "teste")
            self.app._handle_cloud_data()
        after.assert_not_called()
        self.assertEqual(self.app.cloud_events.qsize(), 2)

    def test_failed_local_durability_cannot_show_saved_in_cloud(self):
        self.fill_row()
        self.store.queue_state = Mock(return_value=False)
        self.assertFalse(self.app._save_rows(show_error=False))
        self.app._apply_sync_status("synced", "Tudo salvo", "teste")
        self.assertEqual(self.app.sync_status, "error")
        self.assertIn("protegidas", self.app.sync_detail)

    def test_remote_pull_never_overwrites_dirty_sheet(self):
        self.fill_row()
        self.app._save_rows()
        original = self.app._sheet_data()
        self.store.states = [{"live": {"id": "remote", "running": False}, "rows": [{**ROW, "codigo": "999"}]}]
        self.app._apply_cloud_data()
        self.assertEqual(self.app._sheet_data(), original)

    def test_remote_draft_is_not_automatically_resumed(self):
        self.store.states = [{"live": {"id": "remote", "running": True}, "rows": [ROW]}]
        self.app._apply_cloud_data()
        self.assertFalse(self.app.live_running)
        self.assertIsNone(self.app.live_id)

    def test_pending_recovery_runs_only_once_and_does_not_reopen_cleared_sheet(self):
        self.store.pending = [{"live": {"id": "pending", "running": False}, "rows": [ROW]}]
        self.app.initial_pending_recovery = True
        self.app._apply_cloud_data()
        self.assertEqual(self.app.live_id, "pending")
        self.app._apply_saved_state({})
        self.app._apply_cloud_data()
        self.assertIsNone(self.app.live_id)

    def test_loading_history_is_read_only_and_pins_shown_revision(self):
        live = {"id": "old", "started_at": "2026-09-01T12:00:00", "finished_at": "2026-09-01T13:00:00",
                "duration": "01:00:00", "total": "R$ 0,00", "rows": [ROW]}
        self.assertTrue(self.app._load_history_live_into_main_sheet(live))
        self.store.begin_edit.assert_called_once_with("old", history=live)
        self.assertEqual(self.store.saved, [])
        self.app._save_rows()
        self.assertEqual(self.store.saved, [])

    def test_failed_history_load_blocks_all_saves_until_complete_reload(self):
        live = {"id": "old", "started_at": "2026-09-01T12:00:00", "finished_at": "2026-09-01T13:00:00",
                "duration": "01:00:00", "total": "R$ 20,00", "rows": [ROW]}
        self.store.lives = [copy.deepcopy(live)]
        with patch.object(self.app, "_load_saved_rows", side_effect=RuntimeError("Falha simulada")), \
                patch("app.messagebox.showerror") as error:
            self.assertFalse(self.app._load_history_live_into_main_sheet(live))
            error.assert_called_once()
        self.assertTrue(self.app.sheet_load_error)
        self.assertFalse(self.app._save_rows(show_error=False))
        self.app._apply_sync_status("synced", "Confirmado", "teste")
        self.assertIn("carregamento", self.app.sync_detail)
        self.assertNotIn("Não feche", self.app.sync_detail)
        self.assertFalse(self.app._save_rows(show_error=False))
        self.assertEqual(self.store.saved, [])
        self.assertEqual(self.store.lives, [live])
        self.assertFalse(self.app._sheet_is_pristine())
        self.assertTrue(self.app.start_button.instate(["disabled"]))
        self.assertTrue(self.app._load_history_live_into_main_sheet(live))
        self.assertFalse(self.app.sheet_load_error)
        self.assertEqual(self.app._rows(), [ROW])
        self.assertTrue(self.app._save_rows(show_error=False))
        self.assertEqual(self.store.saved, [])

    def test_invalid_saved_rows_do_not_clear_existing_sheet(self):
        self.fill_row()
        before = self.app._sheet_data()
        with patch("app.messagebox.showerror"):
            self.assertFalse(self.app._apply_saved_state({"live": {"id": "invalid"}, "rows": [None]}))
        self.assertEqual(self.app._sheet_data(), before)
        self.assertFalse(self.app.sheet_load_error)
        self.assertEqual(self.store.saved, [])

    def test_close_after_failed_load_never_saves_the_incomplete_sheet(self):
        self.app.sheet_load_error = True
        with patch("app.messagebox.askyesno", return_value=True) as confirm, \
                patch.object(self.app, "_close_application") as close, \
                patch.object(self.app, "_save_rows") as save:
            self.app._on_close()
        confirm.assert_called_once()
        self.assertIn("não será salva", confirm.call_args.args[1])
        save.assert_not_called()
        close.assert_called_once()
        self.assertEqual(self.store.saved, [])

    def test_opening_history_from_treeview_preserves_all_rows_without_saving(self):
        rows = [
            {**ROW, "cliente": "cliente um"},
            {**ROW, "valor": "R$ 35,00", "codigo": "002", "cliente": "cliente dois"},
            {**ROW, "valor": "R$ 50,00", "codigo": "003", "suplente": "suplente"},
        ]
        live = {"id": "old", "started_at": "2026-09-01T12:00:00",
                "finished_at": "2026-09-01T13:00:00", "duration": "01:00:00",
                "total": "R$ 55,00", "pieces_count": 3, "sold_count": 2,
                "clients_count": 2, "rows": rows}
        self.store.lives = [copy.deepcopy(live)]
        window, tree, buttons = self.history_widgets()
        tree.selection_set("month_2026_9_live_0")
        self.app.update()

        buttons["Abrir na planilha principal"].invoke()

        self.assertFalse(window.winfo_exists())
        self.assertEqual(self.app.live_id, "old")
        self.assertEqual(self.app._rows(), rows)
        self.store.begin_edit.assert_called_once_with("old", history=live)
        self.assertEqual(self.store.saved, [])
        self.assertTrue(self.app._save_rows())
        self.assertEqual(self.store.saved, [])
        self.assertEqual(self.store.lives, [live])

    def test_history_uses_store_and_non_admin_has_no_delete_action(self):
        self.store.lives = [{"id": "a", "finished_at": "2026-09-01", "total": "R$ 20,00"}]
        _window, tree, buttons = self.history_widgets()
        self.store.sync_now.assert_called_once()
        tree.selection_set("month_2026_9_live_0")
        self.app.update()
        delete = buttons["Excluir live selecionada"]
        self.assertTrue(delete.instate(["disabled"]))
        self.assertEqual(delete.winfo_manager(), "")
        delete.invoke()
        self.store.queue_delete.assert_not_called()

    @patch("app.messagebox.showinfo")
    @patch("app.messagebox.askyesno", return_value=True)
    def test_admin_delete_keeps_history_until_server_confirms(self, _confirm, notice):
        self.store.is_admin = True
        self.store.lives = [{"id": "a", "finished_at": "2026-09-01", "total": "R$ 20,00"}]
        window, tree, buttons = self.history_widgets()
        tree.selection_set("month_2026_9_live_0")
        self.app.update()
        buttons["Excluir live selecionada"].invoke()
        self.store.queue_delete.assert_called_once_with("a", immediate=True)
        self.assertEqual(len(tree.get_children("month_2026_9")), 1)
        notice.assert_called_once()
        self.store.lives.clear()
        self.app.history_refreshers[window]()
        self.assertEqual(tree.get_children(), ())

    def test_history_refresh_preserves_selection_and_collapsed_month(self):
        self.store.lives = [
            {"id": "a", "finished_at": "2026-09-01", "total": "R$ 20,00"},
            {"id": "b", "finished_at": "2026-08-01", "total": "R$ 30,00"},
        ]
        window, tree, _buttons = self.history_widgets()
        tree.item("month_2026_8", open=False)
        tree.selection_set("month_2026_9_live_0")
        self.store.lives.insert(0, {"id": "new", "finished_at": "2026-09-09", "total": "R$ 1,00"})
        self.app.history_refreshers[window]()
        self.assertFalse(tree.item("month_2026_8", "open"))
        self.assertEqual(tree.selection(), ("month_2026_9_live_1",))

    @patch("app.messagebox.askyesno", return_value=False)
    def test_close_with_undurable_changes_requires_explicit_discard(self, confirm):
        self.app._finish_active_cell = Mock()
        self.app._save_rows = Mock(return_value=False)
        with patch.object(self.app, "destroy") as destroy:
            self.app._on_close()
        destroy.assert_not_called()
        self.store.shutdown.assert_not_called()
        self.assertIn("perdê-las", confirm.call_args.args[1])

    @patch("app.messagebox.askyesno", return_value=False)
    def test_close_with_pending_data_explains_local_recovery(self, confirm):
        self.app._finish_active_cell = Mock()
        self.store.has_pending = True
        with patch.object(self.app, "destroy") as destroy:
            self.app._on_close()
        destroy.assert_not_called()
        self.assertIn("fila protegida", confirm.call_args.args[1])

    def test_offline_without_recovered_data_disables_editing(self):
        self.store.ready = False
        self.app._refresh_cloud_editability()
        self.assertEqual(self.app.cell_entries[0][0].cget("state"), "disabled")
        self.app.start_live()
        self.assertFalse(self.app.live_running)

    def test_expired_session_with_pending_data_offers_login_without_logout(self):
        self.store.has_pending = True
        self.app.sync_status = "auth_required"
        self.app.open_sync_dialog()
        self.app.sync_window.withdraw()
        buttons = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, ttk.Button):
                    buttons.append(child.cget("text"))
                walk(child)

        walk(self.app.sync_window)
        self.assertIn("Conectar", buttons)
        self.assertNotIn("Desconectar", buttons)

    def test_deleted_remote_live_is_read_only_but_sheet_is_preserved(self):
        self.fill_row()
        self.app._save_rows()
        original = self.app._rows()
        self.store.is_deleted.return_value = True
        self.app._apply_cloud_data()
        self.assertEqual(self.app._rows(), original)
        self.assertEqual(self.app.cell_entries[0][0].cget("state"), "disabled")
        self.app._apply_sync_status("synced", "Tudo salvo", "teste")
        self.assertEqual(self.app.sync_status, "deleted")
        self.assertIn("exportação", self.app.sync_detail)


class LegacyImportReadTests(unittest.TestCase):
    def setUp(self):
        self.app = LiveSalesApp.__new__(LiveSalesApp)

    def test_invalid_primary_and_backup_raise_instead_of_importing_empty_history(self):
        self.app._read_json_file = Mock(return_value=None)
        history = Mock()
        backup = Mock()
        history.exists.return_value = True
        backup.exists.return_value = False
        with patch("app.HISTORY_PATH", history), patch("app.HISTORY_BACKUP_PATH", backup):
            with self.assertRaises(ValueError):
                self.app._read_legacy_data()

    def test_valid_backup_is_read_without_rewriting_any_file(self):
        history, backup, state, state_backup = [Mock() for _index in range(4)]
        source = {history: {"lives": "invalid"}, backup: {"lives": [{"id": "old"}]},
                  state: {"rows": [], "live": {}}, state_backup: None}
        self.app._read_json_file = Mock(side_effect=lambda path: source[path])
        with patch.multiple("app", HISTORY_PATH=history, HISTORY_BACKUP_PATH=backup,
                            AUTOSAVE_PATH=state, AUTOSAVE_BACKUP_PATH=state_backup):
            lives, current = self.app._read_legacy_data()
        self.assertEqual(lives, [{"id": "old"}])
        self.assertEqual(current, {"rows": [], "live": {}})
        for path in (history, backup, state, state_backup):
            path.write_text.assert_not_called()
            path.unlink.assert_not_called()

    def test_missing_legacy_files_are_a_valid_empty_bootstrap(self):
        paths = [Mock() for _index in range(4)]
        for path in paths:
            path.exists.return_value = False
        self.app._read_json_file = Mock(return_value=None)
        with patch.multiple("app", HISTORY_PATH=paths[0], HISTORY_BACKUP_PATH=paths[1],
                            AUTOSAVE_PATH=paths[2], AUTOSAVE_BACKUP_PATH=paths[3]):
            self.assertEqual(self.app._read_legacy_data(), ([], {}))


if __name__ == "__main__":
    unittest.main()
