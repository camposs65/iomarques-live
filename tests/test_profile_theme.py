import copy
import tkinter as tk
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from app import COLORS, COLUMNS, LiveSalesApp
from live_profiles import IO_MARQUES, MARQUES_BRECHO, MARQUES_MESSAGE_TEMPLATE, MONOCHROME_COLORS


class ThemeStore:
    ready = cloud_loaded = is_authenticated = is_configured = perfis_disponiveis = True
    is_admin = needs_legacy_bootstrap = has_pending = False
    username = "teste"
    erro_perfis = ""

    def __init__(self):
        for method in ("start", "shutdown", "sync_now", "begin_edit", "end_edit"):
            setattr(self, method, Mock())
        self.is_deleted = Mock(return_value=False)
        self.queue_state = Mock(return_value=True)
        self.history = Mock(return_value=[])
        self.current_states = Mock(return_value=[])
        self.pending_states = Mock(return_value=[])
        self.perfis_live = Mock(return_value={MARQUES_BRECHO: {"mensagem_cliente": MARQUES_MESSAGE_TEMPLATE}})


class ProfileThemeTests(unittest.TestCase):
    def setUp(self):
        self.store = ThemeStore()
        self.store_patch = patch("app.CloudLiveStore", return_value=self.store)
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)
        self.app = LiveSalesApp()
        self.app.withdraw()
        self.addCleanup(self.cleanup_app)

    def cleanup_app(self):
        for job in (self.app.timer_job, self.app.autosave_job, self.app.cloud_poll_job):
            if job is not None:
                self.app.after_cancel(job)
        self.app.update_idletasks()
        self.app.destroy()

    def test_switch_keeps_table_fields_focus_history_and_timestamps(self):
        self.app.row_vars[0][1].set("024")
        self.app.row_vars[0][0].set("R$ 39,90")
        original_entry = self.app.cell_entries[0][0]
        self.app.undo_stack = [["exemplo"]]
        history = copy.deepcopy(self.app.undo_stack)
        self.assertTrue(self.app._set_live_profile(MARQUES_BRECHO))
        self.assertIs(self.app.cell_entries[0][0], original_entry)
        self.assertEqual(self.app.row_vars[0][1].get(), "024")
        self.assertEqual(self.app.row_vars[0][0].get(), "R$ 39,90")
        self.assertEqual(self.app.undo_stack, history)
        self.assertIsNone(self.app.live_first_started_at)
        self.assertIsNone(self.app.live_started_at)
        self.assertIn("Marques Brechó", self.app.title())
        self.assertEqual(self.app.brand_logo_label.cget("text"), "MB")
        self.assertTrue(self.app.profile_switch_var.get())

    def test_masculine_operational_colors_are_monochrome_and_return_to_io(self):
        self.app._set_live_profile(MARQUES_BRECHO)
        self.app._add_row(["R$ 30,00", "1", "ana", "", "00:00:03"])
        self.app._apply_sync_status("synced", "OK", "teste")

        def walk(widget):
            yield widget
            for child in widget.winfo_children():
                yield from walk(child)

        for widget in walk(self.app):
            for option in ("background", "foreground", "activebackground", "activeforeground", "selectcolor"):
                if option not in widget.keys():
                    continue
                value = str(widget.cget(option))
                if value.startswith("#"):
                    red, green, blue = self.app.winfo_rgb(value)
                    self.assertEqual((red, red, red), (red, green, blue), (widget, option, value))
        self.assertEqual(self.app.colors, MONOCHROME_COLORS)
        self.assertTrue(self.app._set_live_profile(IO_MARQUES))
        self.assertEqual(self.app.brand_header.cget("background"), COLORS["primary"])
        self.assertEqual(self.app.cget("background"), COLORS["app_bg"])
        self.assertEqual(self.app.colors, COLORS)

    def test_keyboard_toggle_is_locked_after_start_and_finish(self):
        self.app._toggle_live_profile()
        self.assertEqual(self.app.live_profile, MARQUES_BRECHO)
        self.app.live_first_started_at = datetime(2026, 10, 22, 18, 30)
        self.app._refresh_profile_controls()
        self.app._toggle_live_profile()
        self.assertEqual(self.app.live_profile, MARQUES_BRECHO)
        self.app.live_first_started_at = None
        self.app.live_finished_at = datetime(2026, 10, 22, 19, 30)
        self.app._refresh_profile_controls()
        self.app._toggle_live_profile()
        self.assertEqual(self.app.live_profile, MARQUES_BRECHO)

    def test_headless_old_instance_retains_original_palette(self):
        old_instance = LiveSalesApp.__new__(LiveSalesApp)
        self.assertIs(old_instance.colors, COLORS)
        self.assertEqual(old_instance._theme_color("#DDF4E5"), "#DDF4E5")

    def test_selector_and_message_button_fit_minimum_window(self):
        self.app.geometry("960x580")
        self.app._set_live_profile(MARQUES_BRECHO)
        self.app.deiconify()
        self.app.update()
        button = self.app.profile_message_button
        status = self.app.profile_status_label
        self.assertGreater(self.app.profile_switch_canvas.winfo_width(), 40)
        self.assertGreater(button.winfo_width(), 100)
        self.assertLessEqual(status.winfo_x() + status.winfo_width(), button.winfo_x())
        self.assertLessEqual(button.winfo_x() + button.winfo_width(), self.app.profile_panel.winfo_width())
        self.app.withdraw()


if __name__ == "__main__":
    unittest.main()
