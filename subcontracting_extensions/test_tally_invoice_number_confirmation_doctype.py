import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class TestTallyConfirmationDocType(unittest.TestCase):
    def test_unique_confirmation_and_safety_fields(self):
        path = (ROOT / "subcontracting_extensions" / "doctype" /
                "processor_lot_sales_invoice_number_confirmation" /
                "processor_lot_sales_invoice_number_confirmation.json")
        doc = json.loads(path.read_text())
        fields = {row["fieldname"]: row for row in doc["fields"]}
        self.assertEqual(fields["reservation"].get("unique"), 1)
        self.assertEqual(fields["reserved_invoice_number"].get("unique"), 1)
        self.assertEqual(fields["confirmation_attested"].get("reqd"), 1)
        for name in ("commercial_document_creation_enabled", "commercial_document_authorized",
                     "stock_document_authorized", "accounting_posting_authorized",
                     "tax_posting_authorized", "lot_closure_authorized"):
            self.assertEqual(fields[name].get("default"), "0")
            self.assertEqual(fields[name].get("read_only"), 1)


if __name__ == "__main__":
    unittest.main()
