"""J19B2H safety-boundary tests."""

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class TestSalesInvoiceNumberReservationContract(unittest.TestCase):
    def test_service_has_no_invoice_or_series_write(self):
        path = ROOT / "sales_invoice_number_reservation.py"
        tree = ast.parse(path.read_text())
        source = path.read_text()
        self.assertNotIn('new_doc("Sales Invoice")', source)
        self.assertNotIn('set_value("Document Naming Rule"', source)
        self.assertNotIn('UPDATE `tabSeries`', source)
        inserts = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Attribute) and node.func.attr == "insert"]
        self.assertEqual(len(inserts), 1)

    def test_endpoint_is_feature_gated(self):
        source = (ROOT / "material_reconciliation_ui.py").read_text()
        self.assertIn("v2_retained_material_invoice_number_reservation", source)
        self.assertIn("reserve_retained_material_sales_invoice_number", source)

    def test_sales_invoice_collision_guard_is_registered(self):
        source = (ROOT / "hooks.py").read_text()
        self.assertIn('"Sales Invoice": {', source)
        self.assertIn("protect_reserved_sales_invoice_number", source)


if __name__ == "__main__":
    unittest.main()
