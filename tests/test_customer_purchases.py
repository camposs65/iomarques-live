"""Lifetime purchase totals using fictional live data only."""

import copy
import unittest
from collections import Counter

from customer_purchases import (PurchaseHistory, build_purchase_history,
                                count_customer_rows, effective_purchase_counts,
                                normalize_customer)


class CustomerPurchaseTests(unittest.TestCase):
    def test_normalization_matches_handles_without_changing_display_names(self):
        for value in ("Ana.Silva", "@ana.silva", "  @@ANA.SILVA  ", "@ ana.silva "):
            with self.subTest(value=value):
                self.assertEqual(normalize_customer(value), "ana.silva")
        self.assertEqual(normalize_customer(" Cliente Teste "), "cliente teste")
        self.assertEqual(normalize_customer("ÁlVArO"), "álvaro")
        for value in (None, "", "   ", "@@", 3, False, [], {}):
            with self.subTest(value=value):
                self.assertEqual(normalize_customer(value), "")

    def test_counts_only_titular_rows_including_name_only_rows(self):
        rows = [
            {"cliente": "ana", "codigo": "024", "suplente": "bia"},
            {"cliente": " @ANA ", "valor": "R$ 0,00"},
            {"cliente": "bia"},
            {"codigo": "543", "suplente": "ana", "suplente1": "ana"},
            {"cliente": "   ", "suplente": "bia"},
            {"cliente": None}, {"cliente": 123}, {}, None, "ana",
        ]
        self.assertEqual(count_customer_rows(rows), Counter(ana=2, bia=1))
        for rows in (None, {}, "ana", 8):
            with self.subTest(rows=rows):
                self.assertEqual(count_customer_rows(rows), Counter())

    def test_history_sums_both_profiles_and_normalized_names(self):
        history = build_purchase_history([
            {"id": "live_io", "perfil": "io_marques", "rows": [
                {"cliente": "ana"}, {"cliente": "bia"}]},
            {"id": "live_masculino", "perfil": "marques_brecho", "rows": [
                {"cliente": " @ANA "}, {"cliente": "ana"}]},
        ])
        self.assertIsInstance(history, PurchaseHistory)
        self.assertEqual(history.totals, Counter(ana=3, bia=1))
        self.assertEqual(history.by_live["live_io"], Counter(ana=1, bia=1))
        self.assertFalse(history.incomplete)

    def test_opened_history_is_replaced_not_counted_twice(self):
        old_rows = [{"cliente": "ana"}, {"cliente": "ana"}, {"cliente": "bia"}]
        history = build_purchase_history([
            {"id": "old", "rows": [{"cliente": "ana"}]},
            {"id": "opened", "rows": old_rows},
        ])
        self.assertEqual(effective_purchase_counts(history, "opened", old_rows),
                         Counter(ana=3, bia=1))
        self.assertEqual(effective_purchase_counts(history, "new", [{"cliente": "ana"}]),
                         Counter(ana=4, bia=1))

    def test_edit_remove_and_promote_recompute_the_opened_live(self):
        history = build_purchase_history([
            {"id": "old", "rows": [{"cliente": "ana"}, {"cliente": "bia"}]},
            {"id": "opened", "rows": [
                {"cliente": "ana", "suplente": "bia"},
                {"cliente": "ana"}, {"cliente": "deleted"}]},
        ])
        current_rows = [{"cliente": "bia"}, {"cliente": "carla"}, {"cliente": ""}]
        self.assertEqual(effective_purchase_counts(history, "opened", current_rows),
                         Counter(ana=1, bia=2, carla=1))
        self.assertNotIn("deleted", effective_purchase_counts(history, "opened", []))

    def test_duplicate_ids_keep_the_first_snapshot_only(self):
        lives = [
            {"id": "same", "rows": [{"cliente": "newest"}]},
            {"id": "same", "rows": [{"cliente": "older"}, {"cliente": "older"}]},
        ]
        history = build_purchase_history(lives)
        self.assertEqual(history.totals, Counter(newest=1))
        self.assertEqual(set(history.by_live), {"same"})
        self.assertFalse(history.incomplete)
        self.assertEqual(build_purchase_history(lives).totals, history.totals)

    def test_first_duplicate_still_wins_when_its_detail_is_missing(self):
        history = build_purchase_history([
            {"id": "same", "sold_count": 20},
            {"id": "same", "rows": [{"cliente": "outdated"}]},
        ])
        self.assertEqual(history.totals, Counter())
        self.assertTrue(history.incomplete)

    def test_summary_cannot_be_divided_among_client_names(self):
        history = build_purchase_history([
            {"id": "summary", "sold_count": 20, "clients": ["ana", "bia"]},
            {"id": "detailed", "rows": [{"cliente": "ana"}]},
        ])
        self.assertEqual(history.totals, Counter(ana=1))
        self.assertTrue(history.incomplete)

    def test_missing_ids_count_each_record_without_colliding_with_real_ids(self):
        history = build_purchase_history([
            {"rows": [{"cliente": "ana"}]},
            {"id": None, "rows": [{"cliente": "ana"}]},
            {"id": "anonymous_0", "rows": [{"cliente": "bia"}]},
        ])
        self.assertEqual(history.totals, Counter(ana=2, bia=1))
        self.assertEqual(set(history.by_live), {"anonymous_0"})
        self.assertTrue(history.incomplete)

    def test_malformed_records_are_flagged_without_inventing_purchases(self):
        history = build_purchase_history([
            None, "invalid", {"id": "bad_rows", "rows": {}},
            {"id": "partial", "rows": [None, {"cliente": 5}, {"cliente": "ana"}]},
        ])
        self.assertTrue(history.incomplete)
        self.assertEqual(history.totals, Counter(ana=1))
        self.assertEqual(history.by_live["partial"], Counter(ana=1))
        self.assertTrue(build_purchase_history(None).incomplete)
        self.assertFalse(build_purchase_history([]).incomplete)
        self.assertFalse(build_purchase_history([{"id": "empty", "rows": []}]).incomplete)

    def test_current_unsaved_rows_are_counted_without_an_id(self):
        history = build_purchase_history([{"id": "old", "rows": [{"cliente": "ana"}]}])
        self.assertEqual(effective_purchase_counts(history, None, [{"cliente": "@ANA"}]),
                         Counter(ana=2))

    def test_inputs_and_cached_history_are_not_mutated_or_aliased(self):
        lives = [{"id": "old", "rows": [{"cliente": "ana", "suplente": "bia"}]}]
        current_rows = [{"cliente": "carla"}]
        original_lives = copy.deepcopy(lives)
        original_rows = copy.deepcopy(current_rows)
        history = build_purchase_history(lives)
        original_history = copy.deepcopy(history)
        result = effective_purchase_counts(history, "old", current_rows)
        result["ana"] = 99
        self.assertEqual(lives, original_lives)
        self.assertEqual(current_rows, original_rows)
        self.assertEqual(history, original_history)
        history.by_live["old"]["ana"] = 100
        self.assertEqual(history.totals, Counter(ana=1))


if __name__ == "__main__":
    unittest.main()
