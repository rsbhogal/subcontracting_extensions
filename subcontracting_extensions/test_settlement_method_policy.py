"""J19B1 pure settlement-method catalogue and settings contract tests."""

from copy import deepcopy
from types import SimpleNamespace
import unittest

from subcontracting_extensions.settlement_method_policy import (
    METHODS,
    SettlementMethodPolicyError,
    enabled_methods,
    initial_method_rows,
    normalize_method_rows,
)


class TestSettlementMethodPolicy(unittest.TestCase):
    def test_initial_catalogue_contains_every_method_once(self):
        rows = initial_method_rows()
        self.assertEqual(len(rows), len(METHODS))
        self.assertEqual(len({row["method_code"] for row in rows}), len(rows))

    def test_safe_pending_defaults_are_separate_by_direction(self):
        rows = initial_method_rows()
        defaults = {row["variance_direction"]: row["method_code"]
                    for row in rows if row["is_default"]}
        self.assertEqual(defaults, {
            "Shortage": "PENDING_INVESTIGATION",
            "Excess": "PENDING_OWNERSHIP_INVESTIGATION",
        })

    def test_sales_invoice_always_requires_customer(self):
        row = next(row for row in initial_method_rows()
                   if row["method_code"] == "SALES_INVOICE")
        self.assertTrue(row["requires_customer"])
        self.assertEqual(row["accounting_document_type"], "Sales Invoice")
        self.assertEqual(row["stock_treatment"], "No direct stock update")

    def test_supplier_owned_excess_is_a_future_normal_purchase(self):
        row = next(row for row in initial_method_rows()
                   if row["method_code"] == "PURCHASE_SUPPLIER_OWNED_EXCESS")
        self.assertEqual(row["variance_direction"], "Excess")
        self.assertEqual(row["accounting_document_type"], "Purchase Order")
        self.assertIn("normal Purchase Receipt", row["stock_treatment"])

    def test_disabled_high_judgement_methods_are_not_initially_offered(self):
        codes = {row["method_code"] for row in enabled_methods(initial_method_rows())}
        self.assertNotIn("COMMERCIAL_WAIVER", codes)
        self.assertNotIn("ACCEPT_WITHOUT_ADDITIONAL_CHARGE", codes)
        self.assertNotIn("REALLOCATE_COMPANY_MATERIAL", codes)

    def test_fixed_metadata_cannot_be_overridden_by_settings_row(self):
        rows = initial_method_rows()
        sales = next(row for row in rows if row["method_code"] == "SALES_INVOICE")
        sales.update(requires_customer=False, variance_direction="Excess",
                     accounting_document_type="Journal Entry")
        normalized = normalize_method_rows(rows)
        sales = next(row for row in normalized if row["method_code"] == "SALES_INVOICE")
        self.assertTrue(sales["requires_customer"])
        self.assertEqual(sales["variance_direction"], "Shortage")
        self.assertEqual(sales["accounting_document_type"], "Sales Invoice")

    def test_duplicate_method_is_rejected(self):
        rows = initial_method_rows()
        with self.assertRaisesRegex(SettlementMethodPolicyError, "Duplicate"):
            normalize_method_rows(rows + [deepcopy(rows[0])])

    def test_unknown_method_is_rejected(self):
        with self.assertRaisesRegex(SettlementMethodPolicyError, "Unknown"):
            normalize_method_rows([{"method_code": "UNSAFE"}])

    def test_disabled_default_is_rejected(self):
        rows = initial_method_rows()
        default = next(row for row in rows if row["method_code"] == "PENDING_INVESTIGATION")
        default["enabled"] = False
        with self.assertRaisesRegex(SettlementMethodPolicyError, "must be enabled"):
            normalize_method_rows(rows)

    def test_two_defaults_for_one_direction_are_rejected(self):
        rows = initial_method_rows()
        debit_note = next(
            row for row in rows if row["method_code"] == "PURCHASE_DEBIT_NOTE"
        )
        debit_note["is_default"] = True
        with self.assertRaisesRegex(SettlementMethodPolicyError, "Exactly one"):
            normalize_method_rows(rows)

    def test_missing_catalogue_rows_are_restored_disabled(self):
        rows = initial_method_rows()
        rows = [row for row in rows if row["method_code"] != "COMMERCIAL_WAIVER"]
        normalized = normalize_method_rows(rows)
        restored = next(row for row in normalized if row["method_code"] == "COMMERCIAL_WAIVER")
        self.assertFalse(restored["enabled"])
        self.assertFalse(restored["is_default"])

    def test_document_like_rows_and_approval_role_are_supported(self):
        rows = [SimpleNamespace(**row) for row in initial_method_rows()]
        rows[0].approval_role = "Accounts Manager"
        normalized = normalize_method_rows(rows)
        self.assertEqual(normalized[0]["approval_role"], "Accounts Manager")

    def test_enabled_method_results_are_detached_and_direction_scoped(self):
        rows = initial_method_rows()
        shortage = enabled_methods(rows, "Shortage")
        self.assertTrue(shortage)
        self.assertTrue(all(row["variance_direction"] == "Shortage" for row in shortage))
        shortage[0]["enabled"] = False
        self.assertTrue(rows[0]["enabled"])


if __name__ == "__main__":
    unittest.main()
