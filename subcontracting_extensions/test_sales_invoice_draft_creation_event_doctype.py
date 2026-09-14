import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class TestDraftCreationEventDocType(unittest.TestCase):
    def test_unique_lineage_and_non_posting_defaults(self):
        path = (ROOT / "subcontracting_extensions" / "doctype" /
                "processor_lot_sales_invoice_draft_creation_event" /
                "processor_lot_sales_invoice_draft_creation_event.json")
        doc = json.loads(path.read_text())
        fields = {row["fieldname"]: row for row in doc["fields"]}
        for name in ("sales_invoice", "scope_key", "reservation", "tally_confirmation"):
            self.assertEqual(fields[name].get("unique"), 1)
        for name in ("submission_authorized", "stock_posting_authorized",
                     "accounting_posting_authorized", "tax_posting_authorized",
                     "lot_closure_authorized"):
            self.assertEqual(fields[name].get("default"), "0")
            self.assertEqual(fields[name].get("read_only"), 1)


if __name__ == "__main__":
    unittest.main()
