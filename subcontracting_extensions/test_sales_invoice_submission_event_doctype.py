"""J19B2M immutable submission-event schema/controller tests."""

import json
from pathlib import Path
import unittest


class TestSalesInvoiceSubmissionEventDocType(unittest.TestCase):
    def setUp(self):
        folder = (Path(__file__).parent / "subcontracting_extensions" / "doctype" /
                  "processor_lot_sales_invoice_submission_event")
        self.schema = json.loads((folder /
            "processor_lot_sales_invoice_submission_event.json").read_text())
        self.controller = (folder /
            "processor_lot_sales_invoice_submission_event.py").read_text()

    def test_identity_postings_and_attestation_are_required(self):
        fields = {row["fieldname"]: row for row in self.schema["fields"]}
        for name in (
            "sales_invoice", "processor_lot", "scope_key",
            "statutory_evidence_confirmation", "reason", "submission_confirmed",
            "submitted_by", "submitted_at", "stock_ledger_snapshot",
            "gl_entry_snapshot",
        ):
            self.assertEqual(fields[name].get("reqd"), 1)
            self.assertEqual(fields[name].get("read_only"), 1)

    def test_event_is_immutable_and_cannot_close_lot(self):
        self.assertIn("are immutable", self.controller)
        self.assertIn("permanent audit evidence", self.controller)
        self.assertIn("cannot authorize lot closure", self.controller)


if __name__ == "__main__":
    unittest.main()
