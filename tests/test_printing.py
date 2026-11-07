import unittest
from unittest.mock import Mock, patch

from app import CHECKBOX_TEXT, ROW_COLUMNS, LiveSalesApp


class UnsoldPrintingTests(unittest.TestCase):
    def setUp(self):
        self.app = LiveSalesApp.__new__(LiveSalesApp)
        self.app._finish_active_cell = Mock()
        self.app._current_elapsed_seconds = Mock(return_value=0)
        self.app._send_text_to_printer = Mock()

    def set_rows(self, rows):
        self.app.row_vars = [
            [Mock(get=Mock(return_value=row.get(column, ""))) for column in ROW_COLUMNS]
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

    def test_dot_customer_is_unsold_but_its_value_appears_in_every_summary(self):
        self.set_rows([{}, {"codigo": "014", "valor": "R$ 59,90", "cliente": " . "}])

        self.assertTrue(self.app.print_unsold_pieces(show_success=False))
        self.assertEqual(self.printed_indices(), [2])
        self.assertEqual(self.app._client_messages(), [])
        widget = Mock()
        self.app._insert_summary_text(widget)
        screen_summary = "".join(call.args[1] for call in widget.insert.call_args_list)
        for summary in (self.app._summary_text(), self.app._printable_report(), screen_summary):
            with self.subTest(summary=summary):
                self.assertIn("Total vendido: R$ 59,90", summary)
                self.assertIn("Peças vendidas: 0", summary)
                self.assertIn("Clientes: 0", summary)
                self.assertNotIn("014", summary)

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

    def test_size_is_printed_and_time_stays_available_for_unsold_pieces(self):
        self.set_rows([{"codigo": "111", "tamanho": "P/M", "tempo": "00:01:23"}])
        self.assertTrue(self.app.print_sheet(show_success=False))
        sales = self.app._send_text_to_printer.call_args.args[0]
        self.assertIn("Tamanho", sales)
        self.assertIn("P/M", sales)
        self.assertNotIn("00:01:23", sales)
        self.assertTrue(self.app.print_unsold_pieces(show_success=False))
        unsold = self.app._send_text_to_printer.call_args.args[0]
        self.assertIn("P/M", unsold)
        self.assertIn("00:01:23", unsold)

    def test_full_size_description_is_printed_in_both_tables(self):
        size = "P 90 COS ESTICADO"
        self.set_rows([{"codigo": "024", "tamanho": size, "tempo": "00:01:23"}])
        for print_table in (self.app.print_sheet, self.app.print_unsold_pieces):
            with self.subTest(print_table=print_table.__name__):
                self.assertTrue(print_table(show_success=False))
                text = self.app._send_text_to_printer.call_args.args[0]
                self.assertIn(size, text)
                self.assertNotIn("P 90 ...", text)

    def test_maximum_size_wraps_under_its_column_with_original_index_and_time(self):
        size = "ABCDEFGHIJKLMNOPQRST" * 5
        self.set_rows([{}, {"codigo": "024", "tamanho": size, "tempo": "00:01:23"},
                       {"codigo": "543"}])

        self.assertTrue(self.app.print_unsold_pieces(show_success=False))
        self.assertEqual(self.printed_indices(), [2, 3])
        lines = self.app._send_text_to_printer.call_args.args[0].splitlines()
        start = lines[3].index("Tamanho")
        end = lines[3].index("Cliente") - 1
        piece_lines = lines[5:-1]
        self.assertEqual("".join(line[start:end].strip() for line in piece_lines), size)
        self.assertIn("00:01:23", piece_lines[0])
        for continuation in piece_lines[1:]:
            self.assertEqual(continuation[:start], " " * start)
            self.assertLessEqual(len(continuation), end)

    def test_narrow_windows_print_wraps_without_losing_text(self):
        gdi = Mock()

        def measure(_device, line, length, extent):
            self.assertEqual(length, len(line.encode("utf-16-le")) // 2)
            extent._obj.cx = length
            return True

        gdi.GetTextExtentPoint32W.side_effect = measure
        line = "   P 90 COS ESTICADO 📏 012345678901"
        wrapped = list(self.app._wrap_line_for_print_width(gdi, None, line, 10))
        self.assertGreater(len(wrapped), 1)
        self.assertEqual("".join(part.replace(" ", "") for part in wrapped), line.replace(" ", ""))
        self.assertTrue(all(len(part.encode("utf-16-le")) // 2 <= 10 for part in wrapped))

    def test_summary_shows_size_per_piece_without_changing_messages_or_totals(self):
        rows = [
            {"codigo": "024", "tamanho": "P/M", "cliente": "Teste", "valor": "20,00", "tempo": "00:01:23"},
            {"codigo": "543", "cliente": "Teste", "valor": "25,90", "tempo": "00:02:00"},
        ]
        self.set_rows(rows)
        widget = Mock()
        self.app._insert_summary_text(widget)
        screen = "".join(call.args[1] for call in widget.insert.call_args_list)
        for summary in (screen, self.app._summary_text(), self.app._printable_report()):
            self.assertIn("024 - Tam. P/M - 00:01:23 - R$ 20,00", summary)
            self.assertIn("543 - 00:02:00 - R$ 25,90", summary)
            self.assertIn("Peças vendidas: 2", summary)
            self.assertIn("Total vendido: R$ 45,90", summary)
        with_size = self.app._client_messages()
        self.set_rows([{key: value for key, value in row.items() if key != "tamanho"} for row in rows])
        self.assertEqual(self.app._client_messages(), with_size)

    @patch("app.messagebox.showinfo")
    def test_print_all_keeps_original_indices_in_unsold_report(self, _showinfo):
        self.set_rows(self.example_rows())
        self.app.print_report = Mock(return_value=True)
        self.app.print_all_reports()
        self.assertEqual(self.app._send_text_to_printer.call_count, 2)
        self.assertEqual(self.printed_indices(), [3, 5, 10])


if __name__ == "__main__":
    unittest.main()
