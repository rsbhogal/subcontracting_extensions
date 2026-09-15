"""J19B2M static atomic-submission safety contract tests."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class TestRetainedMaterialSalesInvoiceSubmission(unittest.TestCase):
    def test_service_submits_once_and_records_immutable_evidence(self):
        source = (ROOT / "retained_material_sales_invoice_submission.py").read_text()
        draft_guard = (ROOT /
            "retained_material_sales_invoice_draft_creation.py").read_text()
        self.assertEqual(source.count("invoice.submit()"), 1)
        self.assertEqual(source.count(
            'new_doc("Processor Lot Sales Invoice Submission Event")'), 1)
        self.assertNotIn(".cancel()", source)
        self.assertIn("_validate_postings", source)
        self.assertIn('lot.settlement_status = "Sales Invoice Created"', source)
        self.assertIn('doc.docstatus == 1 and controlled_submission', draft_guard)

    def test_tally_lead_and_no_physical_movement_are_preserved(self):
        source = (ROOT / "retained_material_sales_invoice_submission.py").read_text()
        self.assertIn('invoice._submitted_from_ui = 1', source)
        self.assertIn('invoice.set_posting_time = 1', source)
        self.assertIn('custom_allow_blank_ewaybill_transport_details = 1', source)
        self.assertIn('"NOT_APPLICABLE_NO_PHYSICAL_MOVEMENT"', source)
        self.assertIn('"statutory_lead_system": "Tally"', source)
        self.assertIn('"lot_closure_authorized": False', source)

    def test_gl_accounts_are_resolved_from_live_documents(self):
        source = (ROOT / "retained_material_sales_invoice_submission.py").read_text()
        for expression in (
            'invoice.get("debit_to")', 'tax.get("account_head")',
            'item.get("income_account")', 'item.get("expense_account")',
            '_warehouse_account(api, item.get("warehouse"))',
        ):
            self.assertIn(expression, source)
        self.assertNotIn('"Stock In Hand - BPL"', source)
        self.assertNotIn('"Cost of Goods Sold - BPL"', source)

    def test_endpoint_uses_a_separate_feature_flag(self):
        source = (ROOT / "material_reconciliation_ui.py").read_text()
        self.assertIn("v2_retained_material_sales_invoice_submission", source)
        self.assertIn("submit_retained_material_sales_invoice", source)

    def test_controlled_sales_invoice_status_is_not_legacy_evidence(self):
        source = (ROOT / "component_commercial_reader.py").read_text()
        self.assertIn('"Sales Invoice Created")', source)


if __name__ == "__main__":
    unittest.main()
