"""J19B2J static safety-contract tests."""

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class TestRetainedMaterialSalesInvoiceDraftCreation(unittest.TestCase):
    def test_service_creates_one_invoice_and_one_event_without_submit(self):
        source = (ROOT / "retained_material_sales_invoice_draft_creation.py").read_text()
        tree = ast.parse(source)
        self.assertEqual(source.count('new_doc("Sales Invoice")'), 1)
        self.assertEqual(source.count(
            'new_doc("Processor Lot Sales Invoice Draft Creation Event")'), 1)
        self.assertNotIn(".submit()", source)
        self.assertNotIn("make_gl_entries", source)
        inserts = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Attribute) and node.func.attr == "insert"]
        self.assertEqual(len(inserts), 2)

    def test_standard_tax_controller_is_used_and_expected_values_checked(self):
        source = (ROOT / "retained_material_sales_invoice_draft_creation.py").read_text()
        self.assertIn("invoice.set_missing_values()", source)
        self.assertIn("invoice.calculate_taxes_and_totals()", source)
        self.assertIn("expected_taxes_and_charges", source)
        self.assertIn("expected_tax_rows", source)
        self.assertIn('invoice.meta.has_field("cost_center")', source)
        self.assertIn('invoice.cost_center = draft.get("cost_center")', source)

    def test_submission_is_always_blocked_in_this_checkpoint(self):
        source = (ROOT / "retained_material_sales_invoice_draft_creation.py").read_text()
        hooks = (ROOT / "hooks.py").read_text()
        self.assertIn("prevent_uncontrolled_submission", source)
        self.assertIn('"before_submit"', hooks)
        self.assertIn("prevent_uncontrolled_submission", hooks)
        self.assertIn("prevent_controlled_draft_deletion", hooks)
        self.assertIn("protect_controlled_draft_integrity", source)

    def test_endpoint_has_separate_feature_flag(self):
        source = (ROOT / "material_reconciliation_ui.py").read_text()
        self.assertIn("v2_retained_material_sales_invoice_draft_creation", source)
        self.assertIn("create_retained_material_sales_invoice_draft", source)


if __name__ == "__main__":
    unittest.main()
