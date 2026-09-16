import unittest
from unittest.mock import Mock, patch

from app import CHECKBOX_TEXT, COLUMNS, LiveSalesApp


class UnsoldPrintingTests(unittest.TestCase):
    def setUp(self):
        self.app = LiveSalesApp.__new__(LiveSalesApp)
        self.app._finish_active_cell = Mock()
        self.app._current_elapsed_seconds = Mock(return_value=0)
        self.app._send_text_to_printer = Mock()

    def set_rows(self, rows):
        self.app.row_vars = [
            [Mock(get=Mock(return_value=row.get(column, ""))) for column in COLUMNS]
            for row in rows
        ]

    def example_rows(self):
        rows = [
            {"valor": "R$ 39,90", "codigo": str(100 + index), "cliente": "Cliente teste"}
            for index in range(1, 11)
        ]
        for index in (3, 5, 10):
            rows[index - 1]["cliente"] = ""
        rows[1] = {}
        rows[6] = {"tempo": "00:01:00", "suplente": "Outra cliente"}
        return rows

    def printed_indices(self, call_index=-1):
        text = self.app._send_text_to_printer.call_args_list[call_index].args[0]
        return [int(line.split()[2]) for line in text.splitlines() if line.startswith(CHECKBOX_TEXT)]

    def test_unsold_print_keeps_main_sheet_indices_with_empty_rows(self):
        self.set_rows(self.example_rows())
        self.assertTrue(self.app.print_unsold_pieces(show_success=False))
        self.assertEqual(self.printed_indices(), [3, 5, 10])

    def test_identical_pieces_keep_their_own_row_numbers(self):
        piece = {"valor": "R$ 25,00", "codigo": "222"}
        self.set_rows([{"cliente": "Vendida"}, piece, {}, piece.copy()])
        self.assertTrue(self.app.print_unsold_pieces(show_success=False))
        self.assertEqual(self.printed_indices(), [2, 4])

    def test_no_unsold_pieces_sends_nothing_to_printer(self):
        self.set_rows([{"cliente": "Vendida", "codigo": "111"}, {}, {"suplente": "Cliente"}])
        self.assertFalse(self.app.print_unsold_pieces(show_success=False, show_empty=False))
        self.app._send_text_to_printer.assert_not_called()

    def test_full_sheet_still_prints_sequential_numbers(self):
        self.set_rows([{"codigo": "111", "cliente": "Vendida"}, {"codigo": "222"}])
        self.assertTrue(self.app.print_sheet(show_success=False))
        self.assertEqual(self.printed_indices(), [1, 2])

    @patch("app.messagebox.showinfo")
    def test_print_all_keeps_original_indices_in_unsold_report(self, _showinfo):
        self.set_rows(self.example_rows())
        self.app.print_report = Mock(return_value=True)
        self.app.print_all_reports()
        self.assertEqual(self.app._send_text_to_printer.call_count, 2)
        self.assertEqual(self.printed_indices(), [3, 5, 10])


if __name__ == "__main__":
    unittest.main()
