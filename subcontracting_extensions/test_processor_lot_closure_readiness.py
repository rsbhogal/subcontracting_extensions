"""Pure J19B2N whole-lot closure-readiness tests."""

import unittest

from subcontracting_extensions.processor_lot_closure_readiness import (
    attach_processor_lot_closure_readiness,
)


class TestProcessorLotClosureReadiness(unittest.TestCase):
    def report(self):
        sle = [{"name": "SLE-1"}]
        gl = [{"name": "GL-1"}, {"name": "GL-2"}, {"name": "GL-3"},
              {"name": "GL-4"}, {"name": "GL-5"}, {"name": "GL-6"}]
        return {
            "receipt_completion": {
                "journey_complete": True,
                "items": [{
                    "subcontracting_order_item": "FG-1",
                    "processed_item": "Finished",
                    "stock_uom": "Kg",
                    "ordered_qty": 100,
                    "submitted_scr_qty": 100,
                    "submitted_pr_qty": 100,
                    "submitted_pi_qty": 100,
                    "journey_complete": True,
                    "issues": [],
                }],
            },
            "finished_items": [{
                "sco_finished_item": "FG-1",
                "finished_item": "Finished",
                "stock_uom": "Kg",
                "commercial_decision_code": "COMMERCIAL_REVIEW_COMPLETE_NO_RECOVERY",
                "commercial_variance_qty": 0,
            }],
            "components": [{
                "commercial_scope_key": "SCOPE",
                "sco_supplied_item": "RM-1",
                "component_item": "Raw Material",
                "stock_uom": "Kg",
                "physical_remaining_qty": 20,
                "persisted_material_disposition": {
                    "disposition": "RETAINED_BY_PROCESSOR",
                },
                "persisted_classification": {
                    "classification": "PROCESSOR_RESPONSIBLE",
                    "selected_treatment_method": "SALES_INVOICE",
                },
                "retained_material_sales_invoice_submission_readiness": {
                    "readiness_code": "SALES_INVOICE_SUBMITTED_LOT_CLOSURE_DEFERRED",
                    "blocking_issues": [],
                    "sales_invoice": "SI-1",
                    "submission_event": {
                        "name": "EVENT-1", "sales_invoice": "SI-1",
                        "processor_lot": "LOT", "scope_key": "SCOPE",
                        "lot_closure_authorized": 0,
                    },
                    "posted_stock_ledger_entries": sle,
                    "posted_gl_entries": gl,
                },
            }],
            "policy_issues": [],
            "classification_issues": [],
            "legacy_evidence": [],
        }

    def context(self):
        return {
            "processor_lot": {
                "name": "LOT", "docstatus": 0,
                "settlement_status": "Sales Invoice Created",
            },
            "sales_invoice_posting_evidence": {
                "SCOPE": {
                    "sales_invoice_docstatus": 1,
                    "lineage_matches": True,
                    "commercial_values_match": True,
                    "stock_ledger_entry_count": 1,
                    "gl_entry_count": 6,
                    "live_stock_ledger_entries": [{"name": "SLE-1"}],
                    "live_gl_entries": [
                        {"name": "GL-1"}, {"name": "GL-2"}, {"name": "GL-3"},
                        {"name": "GL-4"}, {"name": "GL-5"}, {"name": "GL-6"},
                    ],
                    "supplier_warehouse_qty": 0,
                    "supplier_warehouse_stock_value": 0,
                },
            },
            "collection_status": "OUTSTANDING",
            "user_can_submit_processor_lot": True,
            "enabled": False,
        }

    def test_complete_scopes_are_ready_but_never_authorized(self):
        result = attach_processor_lot_closure_readiness(
            self.report(), self.context()
        )["processor_lot_closure_readiness"]
        self.assertEqual(
            result["readiness_code"],
            "PROCESSOR_LOT_READY_FOR_FUTURE_CONTROLLED_CLOSURE",
        )
        self.assertEqual(result["blocking_issues"], [])
        self.assertFalse(result["closure_action_visible"])
        self.assertFalse(result["closure_authorized"])
        self.assertEqual(result["collection_status"], "OUTSTANDING")
        self.assertFalse(result["collection_blocks_lot_closure"])

    def test_live_lot_blocks_on_unfinished_receipt_and_commercial_evidence(self):
        report = self.report()
        report["receipt_completion"].update(journey_complete=False)
        report["receipt_completion"]["items"][0].update(
            submitted_scr_qty=0, submitted_pr_qty=0, submitted_pi_qty=0,
            journey_complete=False,
            issues=["ORDER_QUANTITY_NOT_FULLY_RECEIVED", "JOURNEY_NOT_FULLY_VERIFIED"],
        )
        report["finished_items"][0]["commercial_decision_code"] = (
            "REVIEW_FINISHED_ITEM_COMMERCIAL_EVIDENCE"
        )
        result = attach_processor_lot_closure_readiness(
            report, self.context()
        )["processor_lot_closure_readiness"]
        self.assertEqual(result["readiness_code"], "PROCESSOR_LOT_CLOSURE_NOT_READY")
        self.assertEqual(result["blocking_issues"], [
            "FINISHED_ITEM_ORDER_QUANTITY_NOT_FULLY_RECEIVED",
            "FINISHED_ITEM_RECEIPT_JOURNEY_NOT_FULLY_VERIFIED",
            "FINISHED_ITEM_COMMERCIAL_EVIDENCE_NOT_READY",
        ])
        component = result["component_scopes"][0]
        self.assertTrue(component["resolved"])
        self.assertEqual(component["resolution_code"],
                         "RETAINED_MATERIAL_RECOVERY_COMPLETE")

    def test_physical_balance_requires_exact_controlled_posting_evidence(self):
        context = self.context()
        context["sales_invoice_posting_evidence"]["SCOPE"]["gl_entry_count"] = 5
        result = attach_processor_lot_closure_readiness(
            self.report(), context
        )["processor_lot_closure_readiness"]
        self.assertIn("COMPONENT_POSITIVE_RESIDUAL_NOT_CONTROLLED",
                      result["blocking_issues"])
        self.assertFalse(result["component_scopes"][0]["resolved"])

    def test_receivable_does_not_block_operational_closure(self):
        context = self.context()
        context["collection_status"] = "OVERDUE"
        result = attach_processor_lot_closure_readiness(
            self.report(), context
        )["processor_lot_closure_readiness"]
        self.assertEqual(result["blocking_issues"], [])
        self.assertFalse(result["collection_blocks_lot_closure"])

    def test_feature_flag_never_exposes_a_closure_action(self):
        context = self.context()
        context["enabled"] = True
        result = attach_processor_lot_closure_readiness(
            self.report(), context
        )["processor_lot_closure_readiness"]
        self.assertFalse(result["closure_action_visible"])
        self.assertFalse(result["closure_authorized"])

    def test_legacy_evidence_fails_closed(self):
        report = self.report()
        report["legacy_evidence"] = [{"doctype": "Purchase Invoice", "name": "DN"}]
        result = attach_processor_lot_closure_readiness(
            report, self.context()
        )["processor_lot_closure_readiness"]
        self.assertIn("LEGACY_SETTLEMENT_EVIDENCE_REQUIRES_REVIEW",
                      result["blocking_issues"])


if __name__ == "__main__":
    unittest.main()
