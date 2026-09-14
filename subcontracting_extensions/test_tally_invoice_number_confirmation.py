"""J19B2I safety-boundary tests."""

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class TestTallyInvoiceNumberConfirmationContract(unittest.TestCase):
    def test_service_only_inserts_confirmation_evidence(self):
        path = ROOT / "tally_invoice_number_confirmation.py"
        source = path.read_text()
        tree = ast.parse(source)
        self.assertNotIn('new_doc("Sales Invoice")', source)
        self.assertNotIn('set_value("Document Naming Rule"', source)
        self.assertNotIn("UPDATE `tabSeries`", source)
        inserts = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Attribute) and node.func.attr == "insert"]
        self.assertEqual(len(inserts), 1)
        self.assertIn('new_doc("Processor Lot Sales Invoice Number Confirmation")', source)

    def test_endpoint_is_separately_feature_gated(self):
        source = (ROOT / "material_reconciliation_ui.py").read_text()
        self.assertIn("v2_retained_material_tally_reservation_confirmation", source)
        self.assertIn("confirm_retained_material_invoice_number_reserved_in_tally", source)

    def test_confirmation_requires_attestation_and_disabled_mode_fails_closed(self):
        source = (ROOT / "tally_invoice_number_confirmation.py").read_text()
        self.assertIn("confirmation_attested", source)
        self.assertIn("Transitional Tally coordination is not enabled", source)
        self.assertIn('!= MODE', source)


if __name__ == "__main__":
    unittest.main()
