import copy
import tkinter as tk
import unittest
from tkinter import ttk
from unittest.mock import Mock, patch

from app import LiveSalesApp


ROW = {"valor": "R$ 20,00", "codigo": "001", "cliente": "", "suplente": "", "tempo": "00:00:03"}


class FakeStore:
    def __init__(self, *_args, **_kwargs):
        self.ready = self.cloud_loaded = self.is_authenticated = self.is_configured = True
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
        for index, column in enumerate(("valor", "codigo", "cliente", "suplente", "tempo")):
            self.app.row_vars[0][index].set(row[column])

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
        self.app.row_vars[0][2].set("cliente de teste")
        self.app._save_rows()
        self.assertEqual(self.app.live_id, first_id)
        self.assertEqual(self.store.saved[-1][0]["rows"][0]["cliente"], "cliente de teste")

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
