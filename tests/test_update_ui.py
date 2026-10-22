import copy
import queue
import threading
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app import APP_VERSION, LiveSalesApp
from test_profile_theme import ThemeStore


READY = {"version": "9.0.0", "path": str(Path("ficcao.zip").absolute()),
         "sha256": "a" * 64, "size": 100}


class UpdateUiTests(unittest.TestCase):
    def setUp(self):
        self.store = ThemeStore()
        active = patch("app.CloudLiveStore", return_value=self.store)
        active.start()
        self.addCleanup(active.stop)
        self.client_patch = patch("app.UpdateClient")
        self.client = self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.app = LiveSalesApp()
        self.app.withdraw()
        self.app.update_idletasks()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        if not self.app.closing:
            for job in (self.app.timer_job, self.app.autosave_job, self.app.cloud_poll_job):
                if job is not None:
                    self.app.after_cancel(job)
            self.app.update_idletasks()
            self.app.destroy()

    def make_ready(self):
        self.app._apply_update_event("ready", copy.deepcopy(READY))

    def test_source_checkout_never_starts_network_updater(self):
        self.client.assert_not_called()
        self.assertIsNone(self.app.update_client)
        self.assertEqual(self.app.update_bar.winfo_manager(), "")

    def test_check_in_development_explains_installation_without_network(self):
        with patch("app.messagebox.showinfo") as info:
            self.app._check_updates()
        self.client.assert_not_called()
        self.assertIn("Instalar.bat", info.call_args.args[1])

    def test_callback_only_enqueues_and_tk_applies_later(self):
        self.app.cloud_events = queue.Queue()
        self.app._refresh_update_controls = Mock()
        worker = threading.Thread(target=self.app._handle_update_event, args=("ready", READY))
        worker.start()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertIsNone(self.app.update_ready)
        self.app._refresh_update_controls.assert_not_called()
        kind, value = self.app.cloud_events.get_nowait()
        self.assertEqual(kind, "update")
        self.app._apply_update_event(*value)
        self.assertEqual(self.app.update_ready, READY)
        self.app._refresh_update_controls.assert_called_once()

    def test_ready_button_is_blocked_during_live_then_enabled(self):
        self.app.live_running = True
        self.make_ready()
        self.assertTrue(self.app.update_button.instate(["disabled"]))
        self.assertIn("Finalize a live", self.app.update_label.cget("text"))
        self.app.live_running = False
        self.app._refresh_live_controls()
        self.assertTrue(self.app.update_button.instate(["!disabled"]))

    def test_ready_button_waits_for_actual_cloud_ack(self):
        self.store.has_pending = True
        self.make_ready()
        self.assertTrue(self.app.update_button.instate(["disabled"]))
        self.assertIn("envios pendentes", self.app.update_label.cget("text"))
        self.store.has_pending = False
        self.app._apply_cloud_data()
        self.assertTrue(self.app.update_button.instate(["!disabled"]))

    def test_failed_save_blocks_update(self):
        self.app.last_save_failed = True
        self.make_ready()
        with patch("app.messagebox.showinfo"), patch("update_helper.prepare_update_job") as prepare:
            self.app._update_action()
        prepare.assert_not_called()
        self.assertTrue(self.app.winfo_exists())

    def test_current_cell_changes_must_be_acknowledged_before_helper(self):
        self.make_ready()
        self.app._updates_supported = Mock(return_value=True)
        self.app._finish_active_cell = Mock()

        def save(**kwargs):
            self.store.has_pending = True
            return True

        self.app._save_rows = Mock(side_effect=save)
        with patch("app.messagebox.showinfo"), patch("update_helper.prepare_update_job") as prepare:
            self.app._update_action()
        self.app._finish_active_cell.assert_called_once()
        self.store.sync_now.assert_called_once()
        prepare.assert_not_called()
        self.assertFalse(self.app.closing)

    def prepare_apply(self):
        self.make_ready()
        self.app._updates_supported = Mock(return_value=True)
        self.app._finish_active_cell = Mock()
        self.app._save_rows = Mock(return_value=True)
        self.app._close_application = Mock()

    def finish_preparation(self):
        self.app.update_prepare_thread.join(3)
        self.assertFalse(self.app.update_prepare_thread.is_alive())
        while not self.app.cloud_events.empty():
            kind, value = self.app.cloud_events.get_nowait()
            if kind == "call":
                value()

    def test_cancel_keeps_app_open_and_never_launches_helper(self):
        self.prepare_apply()
        with patch("app.messagebox.askyesno", return_value=False), \
                patch("update_helper.prepare_update_job") as prepare:
            self.app._update_action()
        prepare.assert_not_called()
        self.app._close_application.assert_not_called()

    def test_launch_failure_keeps_app_open_with_retry(self):
        self.prepare_apply()
        with patch("app.messagebox.askyesno", return_value=True), \
                patch("app.messagebox.showerror"), \
                patch("update_helper.prepare_update_job", return_value=Mock()), \
                patch("update_helper.launch_update", side_effect=OSError("fixture")):
            self.app._update_action()
            self.finish_preparation()
        self.app._close_application.assert_not_called()
        self.assertFalse(self.app.update_applying)
        self.assertEqual(self.app.update_state, "error")

    def test_helper_is_launched_before_shutdown_and_double_click_ignored(self):
        self.prepare_apply()
        order = []
        self.app._close_application.side_effect = lambda: order.append("close")
        with patch("app.messagebox.askyesno", return_value=True), \
                patch("update_helper.prepare_update_job", return_value=Mock()) as prepare, \
                patch("update_helper.launch_update", side_effect=lambda job: order.append("launch")) as launch:
            self.app._update_action()
            self.app._update_action()
            self.finish_preparation()
        self.assertEqual(order, ["launch", "close"])
        prepare.assert_called_once()
        launch.assert_called_once()

    def test_new_pending_change_during_confirmation_prevents_install(self):
        self.prepare_apply()

        def confirm(*args, **kwargs):
            self.store.has_pending = True
            return True

        with patch("app.messagebox.askyesno", side_effect=confirm), \
                patch("update_helper.prepare_update_job") as prepare:
            self.app._update_action()
        prepare.assert_not_called()
        self.app._close_application.assert_not_called()

    def test_preparation_runs_off_tk_thread_and_new_live_prevents_launch(self):
        self.prepare_apply()
        thread_ids = []

        def prepare(*args, **kwargs):
            thread_ids.append(threading.get_ident())
            return Mock()

        with patch("app.messagebox.askyesno", return_value=True), \
                patch("app.messagebox.showinfo"), \
                patch("update_helper.prepare_update_job", side_effect=prepare), \
                patch("update_helper.launch_update") as launch:
            self.app._update_action()
            self.app.live_running = True
            self.finish_preparation()
        self.assertNotEqual(thread_ids, [threading.get_ident()])
        launch.assert_not_called()
        self.app._close_application.assert_not_called()
        self.assertFalse(self.app.update_applying)

    def test_closing_during_preparation_never_launches_helper(self):
        self.prepare_apply()
        with patch("app.messagebox.askyesno", return_value=True), \
                patch("update_helper.prepare_update_job", return_value=Mock()), \
                patch("update_helper.launch_update") as launch:
            self.app._update_action()
            self.app.closing = True
            self.finish_preparation()
            self.app.closing = False
        launch.assert_not_called()

    def test_changed_cell_during_preparation_waits_for_cloud_ack(self):
        self.prepare_apply()
        with patch("app.messagebox.askyesno", return_value=True), \
                patch("app.messagebox.showinfo"), \
                patch("update_helper.prepare_update_job", return_value=Mock()), \
                patch("update_helper.launch_update") as launch:
            self.app._update_action()
            self.store.has_pending = True
            self.finish_preparation()
        launch.assert_not_called()
        self.app._close_application.assert_not_called()
        self.store.sync_now.assert_called_once()

    def test_network_failure_does_not_change_cloud_state_or_live_fields(self):
        self.app.row_vars[0][1].set("024")
        snapshot = self.app._sheet_data()
        self.app._apply_update_event("error", {"message": "Sem conexão com o GitHub."})
        self.assertEqual(self.app._sheet_data(), snapshot)
        self.assertIs(self.app.sync_manager, self.store)
        self.assertEqual(self.app.update_button.cget("text"), "Tentar novamente")

    def test_shutdown_stops_background_update_checks(self):
        client = self.app.update_client = Mock()
        self.app._close_application()
        client.shutdown.assert_called_once()
        self.store.shutdown.assert_called_once()

    def test_manual_current_version_is_clear_and_hides_banner(self):
        self.app.manual_update_check = True
        with patch("app.messagebox.showinfo") as info:
            self.app._apply_update_event("current", {"version": APP_VERSION})
        self.assertIn(APP_VERSION, info.call_args.args[1])
        self.assertEqual(self.app.update_bar.winfo_manager(), "")


if __name__ == "__main__":
    unittest.main()
