"""Pure J19B2D retained-material readiness tests; no Frappe and no writes."""

import unittest

from subcontracting_extensions.component_retained_material_readiness import (
    attach_retained_material_treatment_readiness,
)


def retained_row(**changes):
    row = {
        "commercial_decision_code": "RAW_MATERIAL_RETAINED_BY_PROCESSOR",
        "material_disposition_current": True,
        "persisted_material_disposition": {
            "disposition": "RETAINED_BY_PROCESSOR", "disposition_qty": 20000,
        },
        "persisted_classification": {"classification": "PROCESSOR_RESPONSIBLE"},
        "suggested_recovery_quantity": 20000,
        "recovery_quantity_source": "PERSISTED_FULL_RESIDUAL_MATERIAL_DISPOSITION",
        "suggested_recovery_rate": 58.35,
        "suggested_recovery_rate_source": "HISTORICAL_SEND_TO_SUBCONTRACTOR",
        "suggested_recovery_amount": 1167000,
        "dispatch_cost_issues": [],
    }
    row.update(changes)
    return row


def context(**changes):
    po_policy = {
        "recover_raw_material_shortage": True,
        "recover_processing_charges_on_shortage": True,
        "settlement_basis": "Company Accepted Quantity",
        "shortage_settlement_method": "PENDING_INVESTIGATION",
        "excess_settlement_method": "PENDING_OWNERSHIP_INVESTIGATION",
        "recovery_customer": None,
    }
    lot_policy = dict(po_policy, override_settlement_policy=False)
    values = {
        "purchase_order_policy": po_policy,
        "processor_lot_policy": lot_policy,
        "supplier_bound_customer": "Shiv Shakti Impex",
        "recovery_customer_enabled": True,
        "recovery_customer_address_ready": True,
        "supplier_warehouse": "Shiv Shakti Impex - BPL",
        "retained_scope_count": 1,
        "supplier_warehouse_actual_qty": 20000,
        "supplier_warehouse_valuation_rate": 58.35,
        "supplier_warehouse_stock_value": 1167000,
    }
    values.update(changes)
    return values


class TestRetainedMaterialTreatmentReadiness(unittest.TestCase):
    def build(self, row=None, facts=None):
        return attach_retained_material_treatment_readiness(
            {"components": [row or retained_row()]}, facts or context()
        )

    def test_live_checkpoint_is_blocked_by_method_and_customer_snapshots(self):
        facts = context()
        facts["processor_lot_policy"]["recover_raw_material_shortage"] = False
        result = self.build(facts=facts)
        readiness = result["components"][0]["retained_material_treatment_readiness"]
        self.assertEqual(readiness["readiness_code"],
                         "RETAINED_MATERIAL_TREATMENT_POLICY_NOT_READY")
        self.assertEqual(readiness["blocking_issues"], [
            "PROCESSOR_LOT_POLICY_DIFFERS_FROM_PURCHASE_ORDER",
            "SHORTAGE_SETTLEMENT_METHOD_PENDING_INVESTIGATION",
            "RECOVERY_CUSTOMER_NOT_SNAPSHOTTED_ON_PURCHASE_ORDER",
            "RECOVERY_CUSTOMER_NOT_SNAPSHOTTED_ON_PROCESSOR_LOT",
        ])
        self.assertEqual(readiness["recommended_treatment"], "SALES_INVOICE")

    def test_fully_reconciled_policy_is_ready_for_future_design_only(self):
        facts = context()
        for policy in (facts["purchase_order_policy"], facts["processor_lot_policy"]):
            policy["shortage_settlement_method"] = "SALES_INVOICE"
            policy["recovery_customer"] = "Shiv Shakti Impex"
        readiness = self.build(facts=facts)["components"][0][
            "retained_material_treatment_readiness"
        ]
        self.assertEqual(readiness["readiness_code"],
                         "RETAINED_MATERIAL_TREATMENT_READY_FOR_FUTURE_EXECUTION_DESIGN")
        self.assertEqual(readiness["blocking_issues"], [])
        self.assertTrue(readiness["policy_reconciliation_ready"])
        self.assertTrue(readiness["recovery_customer_ready"])
        self.assertFalse(readiness["commercial_execution_ready"])

    def test_stock_consequence_uses_sales_invoice_update_stock(self):
        readiness = self.build()["components"][0]["retained_material_treatment_readiness"]
        self.assertEqual(readiness["future_update_stock"], 1)
        self.assertEqual(readiness["future_source_warehouse"], "Shiv Shakti Impex - BPL")
        self.assertEqual(readiness["supplier_warehouse_qty_before"], 20000)
        self.assertEqual(readiness["projected_stock_reduction"], 20000)
        self.assertEqual(readiness["projected_supplier_warehouse_qty_after"], 0)
        self.assertEqual(readiness["projected_stock_value_reduction"], 1167000)

    def test_insufficient_live_stock_fails_closed(self):
        facts = context(supplier_warehouse_actual_qty=19999)
        readiness = self.build(facts=facts)["components"][0][
            "retained_material_treatment_readiness"
        ]
        self.assertIn("RETAINED_MATERIAL_SUPPLIER_WAREHOUSE_STOCK_NOT_READY",
                      readiness["blocking_issues"])
        self.assertFalse(readiness["stock_consequence_ready"])

    def test_shared_bin_cannot_be_claimed_by_multiple_retained_scopes(self):
        readiness = self.build(facts=context(retained_scope_count=2))["components"][0][
            "retained_material_treatment_readiness"
        ]
        self.assertIn("RETAINED_MATERIAL_STOCK_SCOPE_AMBIGUOUS",
                      readiness["blocking_issues"])
        self.assertFalse(readiness["stock_consequence_ready"])

    def test_tax_amount_is_never_invented(self):
        readiness = self.build()["components"][0]["retained_material_treatment_readiness"]
        self.assertEqual(readiness["net_material_amount_excluding_tax"], 1167000)
        self.assertIsNone(readiness["tax_amount"])
        self.assertIsNone(readiness["gross_amount"])
        self.assertEqual(readiness["tax_calculation_status"],
                         "DEFERRED_TO_STANDARD_ERPNEXT_SALES_INVOICE_TAX_RESOLUTION")

    def test_non_retained_row_is_not_applicable(self):
        row = retained_row(commercial_decision_code="NO_RAW_MATERIAL_RECOVERY")
        readiness = self.build(row=row)["components"][0][
            "retained_material_treatment_readiness"
        ]
        self.assertFalse(readiness["applicable"])
        self.assertEqual(readiness["eligible_treatments"], [])

    def test_all_authorizations_remain_false(self):
        result = self.build()
        readiness = result["components"][0]["retained_material_treatment_readiness"]
        for target in (result, readiness):
            self.assertFalse(target["commercial_document_creation_enabled"])
            self.assertFalse(target["commercial_document_authorized"])
            self.assertFalse(target["stock_document_authorized"])
            self.assertFalse(target["lot_closure_authorized"])


if __name__ == "__main__":
    unittest.main()
