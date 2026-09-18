import copy
import unittest
from decimal import Decimal
from unittest.mock import Mock

from app import COLUMNS, LiveSalesApp


class ClientMessagesTests(unittest.TestCase):
    def setUp(self):
        # Only exercise pure message generation: no Tk window or cloud session.
        self.app = LiveSalesApp.__new__(LiveSalesApp)
        self.app._rows = Mock(return_value=[])

    def set_rows(self, rows):
        self.rows = [{column: row.get(column, "") for column in COLUMNS} for row in rows]
        self.app._rows.return_value = self.rows

    def test_identification_has_one_at_sign_and_no_outer_whitespace(self):
        for cliente in ("ana.silva", "@ana.silva", "@@ana.silva", "  @@ana.silva  "):
            with self.subTest(cliente=cliente):
                self.set_rows([{"cliente": cliente, "codigo": "024", "valor": "R$ 25,00"}])
                messages = self.app._client_messages()
                self.assertEqual(len(messages), 1)
                identification, message = messages[0]
                self.assertEqual(identification, "@ana.silva")
                self.assertTrue(message.startswith("@ana.silva\n\nOi amada!😃\n"))
                self.assertNotIn("@@", message)

    def test_message_preserves_items_total_and_payment_instructions(self):
        items = [
            {"codigo": "024", "valor": Decimal("39.90")},
            {"codigo": "256", "valor": Decimal("20.10")},
            {"codigo": "", "valor": Decimal("5.00")},
        ]
        expected = "\n".join([
            "@ana.silva",
            "",
            "Oi amada!😃",
            "Você arrematou na LIVE ",
            "as seguintes peças:",
            "- Peça 024: R$ 39,90",
            "- Peça 256: R$ 20,10",
            "- Peça sem código: R$ 5,00",
            "e ficou o total de R$ 65,00.",
            "",
            "👉 Você pode optar por fazer Pix",
            "👉 Ou link de pagamento para pagar no cartão de crédito.",
            "",
            "Espero seu retorno com brevidade.",
            "Pois temos muitas vezes fila nas peças.",
            "",
            "Aaaa não esqueça de mandar o comprovante.",
            " Combinado?😉",
        ])
        self.assertEqual(self.app._client_message_text(items, "@ana.silva"), expected)

    def test_multiple_clients_keep_separate_items_and_totals(self):
        self.set_rows([
            {"cliente": "bia", "codigo": "543", "valor": "R$ 100,00"},
            {"cliente": "ana", "codigo": "024", "valor": "R$ 39,90"},
            {"cliente": "ana", "codigo": "256", "valor": "R$ 20,10"},
        ])
        messages = self.app._client_messages()
        self.assertEqual([cliente for cliente, _ in messages], ["@ana", "@bia"])
        ana, bia = [message for _, message in messages]
        self.assertIn("- Peça 024: R$ 39,90", ana)
        self.assertIn("- Peça 256: R$ 20,10", ana)
        self.assertIn("e ficou o total de R$ 60,00.", ana)
        self.assertNotIn("543", ana)
        self.assertTrue(bia.startswith("@bia\n\nOi amada!😃\n"))
        self.assertIn("- Peça 543: R$ 100,00", bia)
        self.assertIn("e ficou o total de R$ 100,00.", bia)
        self.assertNotIn("024", bia)
        self.assertNotIn("256", bia)

    def test_presentation_does_not_change_rows_or_existing_grouping(self):
        self.set_rows([
            {"cliente": "ana", "codigo": "024", "valor": "R$ 10,00"},
            {"cliente": "@ana", "codigo": "256", "valor": "R$ 20,00"},
        ])
        original_rows = copy.deepcopy(self.rows)
        original_summary = self.app._summary()
        messages = self.app._client_messages()
        self.assertEqual(len(messages), 2)
        self.assertEqual([cliente for cliente, _ in messages], ["@ana", "@ana"])
        self.assertEqual(self.app._summary(), original_summary)
        self.assertEqual(self.rows, original_rows)

    def test_all_messages_text_includes_identification_in_heading_and_body(self):
        self.set_rows([
            {"cliente": "bia", "codigo": "543", "valor": "R$ 20,00"},
            {"cliente": "ana", "codigo": "024", "valor": "R$ 10,00"},
        ])
        text = self.app._client_messages_text()
        self.assertTrue(text.startswith("[ ] 1 - Cliente: @ana\n\n@ana\n\nOi amada!😃\n"))
        self.assertIn("\n\n---\n\n[ ] 2 - Cliente: @bia\n\n@bia\n\nOi amada!😃\n", text)
        self.assertTrue(text.endswith("\n"))

    def test_automation_jobs_keep_destination_without_at_sign(self):
        self.set_rows([
            {"cliente": "  @@ana.silva  ", "codigo": "024", "valor": "R$ 10,00"},
            {"cliente": "bia", "codigo": "256", "valor": "R$ 20,00"},
        ])
        jobs = self.app._client_message_jobs()
        self.assertEqual([job["cliente"] for job in jobs], ["ana.silva", "bia"])
        self.assertEqual(set(jobs[0]), {"cliente", "mensagem"})
        self.assertTrue(jobs[0]["mensagem"].startswith("@ana.silva\n\nOi amada!😃\n"))
        self.assertTrue(jobs[1]["mensagem"].startswith("@bia\n\nOi amada!😃\n"))

    def test_empty_rows_have_no_messages_or_automation_jobs(self):
        self.assertEqual(self.app._client_messages(), [])
        self.assertEqual(self.app._client_message_jobs(), [])
        self.assertEqual(self.app._client_messages_text(), "Nenhuma peça com cliente titular ainda.\n")

    def test_unsold_pieces_and_alternates_are_not_message_recipients(self):
        self.set_rows([
            {"codigo": "999", "valor": "R$ 500,00", "suplente": "suplente"},
            {"cliente": "   ", "codigo": "888", "valor": "R$ 100,00"},
            {"cliente": "ana", "codigo": "024", "valor": "R$ 10,00", "suplente": "outra"},
        ])
        messages = self.app._client_messages()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0][0], "@ana")
        self.assertIn("e ficou o total de R$ 10,00.", messages[0][1])
        for excluded in ("999", "888", "suplente", "outra"):
            self.assertNotIn(excluded, messages[0][1])
        self.assertEqual([job["cliente"] for job in self.app._client_message_jobs()], ["ana"])


if __name__ == "__main__":
    unittest.main()
