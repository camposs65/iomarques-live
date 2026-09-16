import copy
import tkinter as tk
import unittest
from decimal import Decimal
from tkinter import ttk
from unittest.mock import Mock, patch

from app import LiveSalesApp


class HistoryMonthTests(unittest.TestCase):
    def setUp(self):
        self.app = LiveSalesApp.__new__(LiveSalesApp)

    def test_groups_by_finish_month_and_year_newest_first_without_changing_records(self):
        lives = [
            {"id": "old", "finished_at": "2025-09-10T12:00:00", "total": "R$ 50,00"},
            {"id": "august", "finished_at": "2026-08-31T23:00:00", "total": "R$ 200,00"},
            {"id": "first", "started_at": "2026-08-31T23:00:00",
             "finished_at": "2026-09-01T01:00:00", "total": "R$ 1.234,56"},
            {"id": "second", "finished_at": "2026-09-07T15:00:00", "total": "R$ 765,44"},
        ]
        original = copy.deepcopy(lives)
        groups = self.app._history_month_groups(lives)
        self.assertEqual([group["key"] for group in groups], [(2026, 9), (2026, 8), (2025, 9)])
        self.assertEqual(groups[0]["label"], "Setembro de 2026")
        self.assertEqual([live["id"] for live in groups[0]["lives"]], ["second", "first"])
        self.assertEqual(groups[0]["total"], Decimal("2000.00"))
        self.assertEqual(groups[0]["commission"], Decimal("200.00"))
        self.assertEqual(lives, original)

    def test_month_commission_is_sum_of_displayed_cents(self):
        lives = [{"finished_at": "2026-09-07", "total": "R$ 0,06"} for _ in range(2)]
        group = self.app._history_month_groups(lives)[0]
        self.assertEqual(group["total"], Decimal("0.12"))
        self.assertEqual(group["commission"], Decimal("0.02"))
        self.assertEqual(self.app._history_commission_value(lives[0]), "R$ 0,01")

    def test_missing_dates_use_start_date_then_unknown_group(self):
        lives = [
            {"id": "missing", "total": "R$ 10,00"},
            {"id": "invalid", "finished_at": "invalid", "started_at": 123, "total": "R$ 20,00"},
            {"id": "fallback", "finished_at": None, "started_at": "2026-03-01", "total": 50},
        ]
        groups = self.app._history_month_groups(lives)
        self.assertEqual([group["label"] for group in groups], ["Março de 2026", "Sem data"])
        self.assertEqual(groups[1]["total"], Decimal("30.00"))
        self.assertEqual(groups[1]["commission"], Decimal("3.00"))

    def test_empty_history_and_legacy_totals(self):
        self.assertEqual(self.app._history_month_groups([]), [])
        lives = [{"total": ""}, {"total": None}, {}, {"total": "25,50"}]
        group = self.app._history_month_groups(lives)[0]
        self.assertEqual(len(group["lives"]), 4)
        self.assertEqual(group["total"], Decimal("25.50"))
        self.assertEqual(group["commission"], Decimal("2.55"))


class HistoryWindowTests(unittest.TestCase):
    def setUp(self):
        self.lives = [
            {"id": "one", "finished_at": "2026-09-07T15:00:00", "total": "R$ 100,00"},
            {"id": "two", "finished_at": "2026-09-01T15:00:00", "total": "R$ 200,00"},
            {"id": "three", "finished_at": "2026-08-01T15:00:00", "total": "R$ 50,00"},
        ]
        self.app = LiveSalesApp.__new__(LiveSalesApp)
        tk.Tk.__init__(self.app)
        self.addCleanup(self.app.destroy)
        self.app.withdraw()
        self.app._setup_style()
        self.app.sync_manager = Mock(is_admin=True, cloud_loaded=True)
        self.app.history_refreshers = {}
        self.app.live_id = None
        self.app._read_history = Mock(side_effect=lambda: list(self.lives))
        def confirm_write(lives, deleted_live_ids=None):
            # Este teste trata do agrupamento depois do ACK, sem banco real.
            self.lives[:] = lives
            return True
        self.app._write_history = Mock(side_effect=confirm_write)
        self.app._load_history_live_into_main_sheet = Mock(return_value=False)

    def open_history(self):
        self.app.show_history()
        window = next(child for child in self.app.winfo_children() if isinstance(child, tk.Toplevel))
        window.withdraw()
        widgets = []

        def collect(parent):
            for child in parent.winfo_children():
                widgets.append(child)
                collect(child)

        collect(window)
        self.tree = next(widget for widget in widgets if isinstance(widget, ttk.Treeview))
        buttons = {widget.cget("text"): widget for widget in widgets if isinstance(widget, ttk.Button)}
        self.open_button = buttons["Abrir na planilha principal"]
        self.delete_button = buttons["Excluir live selecionada"]
        self.app.update()

    def select(self, item):
        self.tree.selection_set(item)
        self.app.update()

    def test_months_show_subtotals_and_cannot_be_opened_or_deleted_as_lives(self):
        self.open_history()
        self.assertEqual(self.tree.get_children(), ("month_2026_9", "month_2026_8"))
        self.assertEqual(self.tree.set("month_2026_9", "total"), "R$ 300,00")
        self.assertEqual(self.tree.set("month_2026_9", "commission"), "R$ 30,00")
        self.assertTrue(self.tree.item("month_2026_9", "open"))
        self.select("month_2026_9")
        self.assertTrue(self.open_button.instate(["disabled"]))
        self.assertTrue(self.delete_button.instate(["disabled"]))
        self.open_button.invoke()
        self.delete_button.invoke()
        self.app._load_history_live_into_main_sheet.assert_not_called()
        self.app._write_history.assert_not_called()
        self.select("month_2026_9_live_0")
        self.assertTrue(self.open_button.instate(["!disabled"]))
        self.open_button.invoke()
        self.app._load_history_live_into_main_sheet.assert_called_once_with(self.lives[0])

    @patch("app.messagebox.showinfo")
    @patch("app.messagebox.askyesno", return_value=True)
    def test_deletion_recalculates_month_and_removes_empty_month(self, confirm, _info):
        self.open_history()
        self.tree.item("month_2026_8", open=False)
        self.select("month_2026_9_live_0")
        self.delete_button.invoke()
        self.app.update()
        self.assertEqual(self.tree.set("month_2026_9", "total"), "R$ 200,00")
        self.assertEqual(self.tree.set("month_2026_9", "commission"), "R$ 20,00")
        self.app._write_history.assert_called_once_with(self.lives, deleted_live_ids=["one"])
        self.assertEqual(self.tree.item("month_2026_9", "text"), "Setembro de 2026 (1 live)")
        self.assertFalse(self.tree.item("month_2026_8", "open"))
        self.select("month_2026_9_live_0")
        self.delete_button.invoke()
        self.app.update()
        self.assertEqual(self.tree.get_children(), ("month_2026_8",))
        self.assertEqual(self.tree.set("month_2026_8", "total"), "R$ 50,00")
        self.assertEqual(confirm.call_count, 2)

    def test_empty_history_disables_actions(self):
        self.lives.clear()
        self.open_history()
        self.assertEqual(self.tree.get_children(), ())
        self.assertTrue(self.open_button.instate(["disabled"]))
        self.assertTrue(self.delete_button.instate(["disabled"]))


if __name__ == "__main__":
    unittest.main()
