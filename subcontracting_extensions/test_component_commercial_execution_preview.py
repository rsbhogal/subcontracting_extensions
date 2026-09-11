"""Pure J19B2A dispatch-cost preview tests; no Frappe and no writes."""

import unittest

from subcontracting_extensions.component_commercial_execution_preview import (
    attach_commercial_execution_preview,
)


def component(**changes):
    row = {
        "sco_supplied_item": "RM-A",
        "sco_finished_item": "FG-A",
        "component_item": "Forged Cup",
        "stock_uom": "Units",
        "persisted_classification": None,
    }
    row.update(changes)
    return row


def movement(name, qty, rate, **changes):
    row = {
        "name": name,
        "parent": "STE-" + name,
        "docstatus": 1,
        "is_return": False,
        "purpose": "Send to Subcontractor",
        "movement_direction": "Send to Subcontractor",
        "evidence_role": "Physical transfer or return",
        "subcontracting_order": "SCO",
        "company": "Company",
        "supplier": "Supplier",
        "sco_rm_detail": "RM-A",
        "item_code": "Forged Cup",
        "stock_uom": "Units",
        "stock_qty": qty,
        "basic_rate": rate,
        "basic_amount": qty * rate if rate is not None else None,
        "s_warehouse": "Factory",
        "t_warehouse": "Processor",
    }
    row.update(changes)
    return row


class TestComponentCommercialExecutionPreview(unittest.TestCase):
    identity = {
        "subcontracting_order": "SCO",
        "company": "Company",
        "supplier": "Supplier",
        "supplier_warehouse": "Processor",
    }

    def build(self, rows, **changes):
        preview = {"components": [component(**changes)]}
        return attach_commercial_execution_preview(preview, rows, self.identity)

    def test_single_dispatch_provides_historical_cost_suggestion(self):
        result = self.build([movement("A", 100, 42)])
        row = result["components"][0]
        self.assertEqual(result["commercial_execution_preview_contract_version"], "J19B2A")
        self.assertEqual(row["suggested_recovery_rate"], 42)
        self.assertEqual(row["dispatch_cost_quantity"], 100)
        self.assertEqual(row["dispatch_cost_amount"], 4200)
        self.assertEqual(row["suggested_recovery_rate_source"],
                         "HISTORICAL_SEND_TO_SUBCONTRACTOR")

    def test_multiple_dispatches_use_weighted_historical_rate(self):
        row = self.build([movement("A", 600, 40), movement("B", 400, 43)])["components"][0]
        self.assertAlmostEqual(row["suggested_recovery_rate"], 41.2)
        self.assertEqual(len(row["dispatch_cost_evidence"]), 2)

    def test_purchase_provenance_is_not_required_for_manufactured_component(self):
        row = self.build([movement("A", 10, 63.75)])["components"][0]
        self.assertEqual(row["suggested_recovery_rate"], 63.75)
        self.assertNotIn("purchase_invoice", row["dispatch_cost_evidence"][0])

    def test_amount_can_supply_rate_when_basic_rate_is_missing(self):
        row = self.build([movement("A", 8, None, basic_amount=100)])["components"][0]
        self.assertEqual(row["suggested_recovery_rate"], 12.5)

    def test_wrong_component_identity_fails_closed(self):
        row = self.build([movement("A", 10, 5, item_code="Other")])["components"][0]
        self.assertIsNone(row["suggested_recovery_rate"])
        self.assertIn("DISPATCH_COST_IDENTITY_MISMATCH", row["dispatch_cost_issues"])

    def test_return_and_material_credit_movements_are_not_rate_sources(self):
        rows = [
            movement("RETURN", 2, 9, is_return=True,
                     movement_direction="Return from Subcontractor",
                     s_warehouse="Processor", t_warehouse="Factory"),
            movement("CREDIT", 2, 99,
                     evidence_role="Material credit application"),
        ]
        row = self.build(rows)["components"][0]
        self.assertIsNone(row["suggested_recovery_rate"])
        self.assertEqual(row["dispatch_cost_issues"], ["NO_EXACT_DISPATCH_COST_EVIDENCE"])

    def test_mismatched_recorded_amount_fails_closed(self):
        row = self.build([movement("A", 10, 5, basic_amount=49)])["components"][0]
        self.assertIsNone(row["suggested_recovery_rate"])
        self.assertIn("DISPATCH_COST_AMOUNT_MISMATCH", row["dispatch_cost_issues"])

    def test_sales_invoice_still_requires_authoritative_recovery_quantity(self):
        persisted = {"classification": "PROCESSOR_RESPONSIBLE",
                     "selected_treatment_method": "SALES_INVOICE"}
        row = self.build([movement("A", 10, 5)], persisted_classification=persisted)[
            "components"][0]
        self.assertEqual(row["commercial_execution_readiness_code"],
                         "DEFINE_COMMERCIAL_RECOVERY_QUANTITY")
        self.assertIsNone(row["suggested_recovery_quantity"])
        self.assertFalse(row["commercial_execution_ready"])

    def test_unaccounted_balance_requires_disposition_before_classification(self):
        row = self.build([movement("A", 10, 5)],
                         unaccounted_remaining_qty=10)["components"][0]
        self.assertEqual(row["commercial_execution_readiness_code"],
                         "DEFINE_MATERIAL_DISPOSITION")
        self.assertEqual(
            row["suggested_recovery_rate_basis"],
            "Historical material-content cost carried by the exact Send to "
            "Subcontractor row(s); excludes ABC processing, consumable, labour, "
            "machine, overhead, and pending subcontracting costs",
        )
        self.assertFalse(row["commercial_execution_ready"])

    def test_exact_retained_disposition_advances_only_to_classification(self):
        row = self.build(
            [movement("A", 10, 5)],
            unaccounted_remaining_qty=10,
            material_disposition_current=True,
            persisted_material_disposition={
                "disposition": "RETAINED_BY_PROCESSOR", "disposition_qty": 10,
            },
        )["components"][0]
        self.assertEqual(row["commercial_execution_readiness_code"],
                         "PERSIST_COMMERCIAL_CLASSIFICATION")
        self.assertEqual(row["commercial_decision_code"],
                         "RAW_MATERIAL_RETAINED_BY_PROCESSOR")
        self.assertTrue(row["commercial_review_permitted"])
        self.assertTrue(row["retained_material_classification_ready"])
        self.assertEqual(row["suggested_recovery_quantity"], 10)
        self.assertEqual(row["recovery_quantity_source"],
                         "PERSISTED_FULL_RESIDUAL_MATERIAL_DISPOSITION")
        self.assertEqual(row["suggested_recovery_amount"], 50)
        self.assertFalse(row["commercial_execution_ready"])

    def test_retained_processor_classification_still_defers_treatment(self):
        row = self.build(
            [movement("A", 10, 5)],
            unaccounted_remaining_qty=10,
            material_disposition_current=True,
            persisted_material_disposition={
                "disposition": "RETAINED_BY_PROCESSOR", "disposition_qty": 10,
            },
            persisted_classification={"classification": "PROCESSOR_RESPONSIBLE"},
        )["components"][0]
        self.assertEqual(row["commercial_execution_readiness_code"],
                         "COMMERCIAL_TREATMENT_DEFERRED_J19B2C")
        self.assertFalse(row["commercial_execution_ready"])

    def test_every_mutating_outcome_remains_disabled(self):
        result = self.build([movement("A", 10, 5)])
        row = result["components"][0]
        for target in (result, row):
            self.assertFalse(target["commercial_document_creation_enabled"])
            self.assertFalse(target["commercial_document_authorized"])
            self.assertFalse(target["lot_closure_authorized"])


if __name__ == "__main__":
    unittest.main()
