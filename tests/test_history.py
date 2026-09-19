import copy
import tempfile
import tkinter as tk
import unittest
from decimal import Decimal
from pathlib import Path
from tkinter import ttk
from unittest.mock import Mock, patch

from openpyxl import load_workbook

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
        self.assertEqual(groups[0]["commission"], Decimal("300.00"))
        self.assertEqual(groups[1]["commission"], Decimal("70.00"))
        self.assertEqual(groups[2]["commission"], Decimal("55.00"))
        self.assertEqual(lives, original)

    def test_month_commission_is_sum_of_displayed_cents(self):
        lives = [{"finished_at": "2026-09-07", "total": "R$ 0,06"} for _ in range(2)]
        group = self.app._history_month_groups(lives)[0]
        self.assertEqual(group["total"], Decimal("0.12"))
        self.assertEqual(group["commission"], Decimal("100.02"))
        self.assertEqual(self.app._history_commission_value(lives[0]), "R$ 50,01")

    def test_missing_dates_use_start_date_then_unknown_group(self):
        lives = [
            {"id": "missing", "total": "R$ 10,00"},
            {"id": "invalid", "finished_at": "invalid", "started_at": 123, "total": "R$ 20,00"},
            {"id": "fallback", "finished_at": None, "started_at": "2026-03-01", "total": 50},
        ]
        groups = self.app._history_month_groups(lives)
        self.assertEqual([group["label"] for group in groups], ["Março de 2026", "Sem data"])
        self.assertEqual(groups[1]["total"], Decimal("30.00"))
        self.assertEqual(groups[1]["commission"], Decimal("103.00"))

    def test_empty_history_and_legacy_totals(self):
        self.assertEqual(self.app._history_month_groups([]), [])
        lives = [{"total": ""}, {"total": None}, {}, {"total": "25,50"}]
        group = self.app._history_month_groups(lives)[0]
        self.assertEqual(len(group["lives"]), 4)
        self.assertEqual(group["total"], Decimal("25.50"))
        self.assertEqual(group["commission"], Decimal("202.55"))

    def test_fixed_amount_applies_once_per_live_without_changing_sales(self):
        for total, expected in [("1.000,00", "150.00"), ("0,00", "50.00"),
                                ("0,05", "50.00"), ("0,15", "50.02")]:
            with self.subTest(total=total):
                live = {"total": total}
                original = copy.deepcopy(live)
                for _ in range(2):
                    self.assertEqual(self.app._history_commission_amount(live), Decimal(expected))
                self.assertEqual(live, original)

    def test_excel_history_uses_same_commission_and_preserves_sales_total(self):
        self.app._finish_active_cell = Mock()
        self.app._rows = Mock(return_value=[{
            "valor": "1.000,00", "codigo": "001", "cliente": "Teste",
            "suplente": "", "tempo": "00:01:00",
        }])
        self.app._read_history = Mock(return_value=[{"total": "R$ 1.000,00"}])
        self.app._save_rows = Mock()
        self.app._current_live_status = Mock(return_value="Finalizada")
        self.app._current_elapsed_seconds = Mock(return_value=60)
        self.app.live_id = "teste"
        self.app.live_first_started_at = None
        self.app.live_finished_at = None
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "historico.xlsx"
            with patch("app.filedialog.asksaveasfilename", return_value=str(path)), \
                    patch("app.messagebox.showinfo"):
                self.app.export_excel()
            workbook = load_workbook(path)
            try:
                history = workbook["Histórico de lives"]
                self.assertEqual(history["H1"].value, "10% + R$ 50")
                self.assertEqual(history["H2"].value, "R$ 150,00")
                self.assertEqual(history["G2"].value, "R$ 1.000,00")
            finally:
                workbook.close()


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
        self.assertEqual(self.tree.set("month_2026_9", "commission"), "R$ 130,00")
        self.assertEqual(self.tree.heading("commission", "text"), "10% + R$ 50")
        self.assertEqual(self.tree.set("month_2026_9_live_0", "commission"), "R$ 60,00")
        self.assertEqual(self.tree.set("month_2026_9_live_1", "commission"), "R$ 70,00")
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
        self.assertEqual(self.tree.set("month_2026_9", "commission"), "R$ 70,00")
        self.app._write_history.assert_called_once_with(self.lives, deleted_live_ids=["one"])
        self.assertEqual(self.tree.item("month_2026_9", "text"), "Setembro de 2026 (1 live)")
        self.assertFalse(self.tree.item("month_2026_8", "open"))
        self.select("month_2026_9_live_0")
        self.delete_button.invoke()
        self.app.update()
        self.assertEqual(self.tree.get_children(), ("month_2026_8",))
        self.assertEqual(self.tree.set("month_2026_8", "total"), "R$ 50,00")
        self.assertEqual(self.tree.set("month_2026_8", "commission"), "R$ 55,00")
        self.assertEqual(confirm.call_count, 2)

    def test_empty_history_disables_actions(self):
        self.lives.clear()
        self.open_history()
        self.assertEqual(self.tree.get_children(), ())
        self.assertTrue(self.open_button.instate(["disabled"]))
        self.assertTrue(self.delete_button.instate(["disabled"]))


if __name__ == "__main__":
    unittest.main()
