"""J19B2L immutable event schema/controller tests."""

import json
from pathlib import Path
import unittest


class TestStatutoryEvidenceConfirmationDocType(unittest.TestCase):
    def setUp(self):
        folder = (Path(__file__).parent / "subcontracting_extensions" / "doctype" /
                  "processor_lot_sales_invoice_statutory_evidence_confirmation")
        self.schema = json.loads((folder /
            "processor_lot_sales_invoice_statutory_evidence_confirmation.json").read_text())
        self.controller = (folder /
            "processor_lot_sales_invoice_statutory_evidence_confirmation.py").read_text()

    def test_identity_and_attestation_are_required(self):
        fields = {row["fieldname"]: row for row in self.schema["fields"]}
        for name in ("sales_invoice", "scope_key", "draft_creation_event",
                     "evidence_outcome", "confirmation_attested", "reason",
                     "confirmed_by", "confirmed_at", "statutory_evidence_snapshot"):
            self.assertEqual(fields[name].get("reqd"), 1)
            self.assertEqual(fields[name].get("read_only"), 1)

    def test_event_is_immutable_and_cannot_authorize(self):
        self.assertIn("are immutable", self.controller)
        self.assertIn("permanent audit evidence", self.controller)
        self.assertIn("cannot authorize execution", self.controller)


if __name__ == "__main__":
    unittest.main()
