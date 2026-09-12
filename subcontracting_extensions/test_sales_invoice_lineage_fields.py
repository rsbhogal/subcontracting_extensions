"""J19B2G schema contract tests without Frappe."""

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class TestSalesInvoiceLineageFields(unittest.TestCase):
    def test_exact_scope_lineage_fields_are_fixture_controlled(self):
        rows = json.loads((ROOT / "fixtures" / "custom_field.json").read_text())
        names = {row.get("name") for row in rows}
        expected = {
            "Sales Invoice-custom_processor_lot_settlement",
            "Sales Invoice Item-custom_processor_lot_scope_key",
            "Sales Invoice Item-custom_material_disposition",
            "Sales Invoice Item-custom_commercial_classification",
            "Sales Invoice Item-custom_policy_reconciliation_event",
            "Sales Invoice Item-custom_treatment_decision_event",
            "Sales Invoice Item-custom_disposition_revision",
            "Sales Invoice Item-custom_classification_revision",
            "Sales Invoice Item-custom_treatment_revision",
        }
        self.assertTrue(expected.issubset(names))
        by_name = {row.get("name"): row for row in rows}
        for name in expected:
            self.assertEqual(by_name[name].get("read_only"), 1)
            self.assertNotEqual(by_name[name].get("unique"), 1)

    def test_temporary_coordination_is_settings_controlled_and_off_by_default(self):
        path = (
            ROOT / "subcontracting_extensions" / "doctype" /
            "subcontracting_settlement_settings" /
            "subcontracting_settlement_settings.json"
        )
        settings = json.loads(path.read_text())
        fields = {row.get("fieldname"): row for row in settings["fields"]}
        mode = fields["sales_invoice_number_coordination_mode"]
        self.assertEqual(mode.get("default"), "DISABLED")
        self.assertIn("ERPNEXT_FORECAST_WITH_TALLY_COORDINATION", mode.get("options"))
        self.assertIn("outward_sales_invoice_series", fields)


if __name__ == "__main__":
    unittest.main()
