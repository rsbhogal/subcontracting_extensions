"""J19B2K pure read-only submission-readiness tests."""

import unittest

from subcontracting_extensions.retained_material_sales_invoice_submission_readiness import (
    attach_sales_invoice_submission_readiness,
)


class TestSubmissionReadiness(unittest.TestCase):
    def row(self):
        return {
            "commercial_scope_key": "SCOPE",
            "persisted_material_disposition": {
                "name": "PLMD-1", "disposition_revision": 1,
            },
            "persisted_classification": {
                "name": "PLCC-1", "classification_revision": 1,
                "treatment_revision": 1,
            },
            "retained_material_sales_invoice_draft_readiness": {
                "sales_invoice_draft_creation_event": {"name": "PLSIDC-1"},
                "lineage": {
                    "processor_lot": "LOT", "scope_key": "SCOPE",
                    "policy_reconciliation_event": "PLPRE-1",
                    "treatment_decision_event": "PLCD-2",
                },
            },
        }

    def context(self):
        return {
            "invoice_count": 1, "draft_creation_event_count": 1,
            "sales_invoice": {
                "name": "U-I/26-27/0017", "docstatus": 0,
                "custom_processor_lot_settlement": "LOT",
                "custom_invoice_number_reservation": "PLSINR-1",
                "custom_tally_reservation_confirmation": "PLSINC-1",
                "net_total": 1167000, "total_taxes_and_charges": 210060,
                "grand_total": 1377060,
            },
            "sales_invoice_items": [{
                "custom_processor_lot_scope_key": "SCOPE",
                "custom_material_disposition": "PLMD-1",
                "custom_commercial_classification": "PLCC-1",
                "custom_policy_reconciliation_event": "PLPRE-1",
                "custom_treatment_decision_event": "PLCD-2",
                "custom_disposition_revision": 1,
                "custom_classification_revision": 1,
                "custom_treatment_revision": 1,
                "warehouse": "Processor - C", "qty": 20000, "rate": 58.35,
            }],
            "draft_creation_event": {
                "name": "PLSIDC-1", "sales_invoice": "U-I/26-27/0017",
                "reservation": "PLSINR-1", "tally_confirmation": "PLSINC-1",
                "supplier_warehouse": "Processor - C", "recovery_quantity": 20000,
                "material_content_rate": 58.35, "net_total": 1167000,
                "total_taxes_and_charges": 210060, "grand_total": 1377060,
            },
            "coordination_mode": "ERPNEXT_FORECAST_WITH_TALLY_COORDINATION",
            "statutory_evidence": {
                "ewaybill_applicable": True, "ewaybill": "EWB-1",
                "einvoice_applicable": False, "vehicle_no": "PB10AB1234",
                "lr_date": "2026-09-14", "transport_details_required": True,
                "allow_blank_transport_details": False,
            },
            "stock_projection": {
                "warehouse": "Processor - C", "quantity_before": 20000,
                "projected_reduction": 20000, "quantity_after": 0,
                "stock_value_reduction": 1167000,
            },
            "projected_gl_entries": [
                {"account": "Debtors - C", "debit": 1377060, "credit": 0},
                {"account": "Sales - C", "debit": 0, "credit": 1167000},
            ],
            "linked_stock_ledger_entries": 0, "linked_gl_entries": 0,
            "settlement_started": False, "live_state_issues": [],
        }

    def assess(self, context=None):
        result = attach_sales_invoice_submission_readiness(
            {"components": [self.row()]}, context or self.context()
        )
        return result, result["components"][0][
            "retained_material_sales_invoice_submission_readiness"
        ]

    def test_complete_evidence_is_ready_but_never_authorized(self):
        result, readiness = self.assess()
        self.assertEqual(readiness["readiness_code"],
                         "SALES_INVOICE_DRAFT_READY_FOR_FUTURE_CONTROLLED_SUBMISSION")
        self.assertEqual(readiness["statutory_lead_system"], "Tally")
        self.assertEqual(readiness["blocking_issues"], [])
        for field in ("submission_authorized", "stock_posting_authorized",
                      "accounting_posting_authorized", "statutory_generation_authorized",
                      "tax_posting_authorized", "lot_closure_authorized"):
            self.assertFalse(readiness[field])
            self.assertFalse(result[field])

    def test_live_blank_tally_evidence_fails_closed(self):
        context = self.context()
        context["statutory_evidence"].update(
            ewaybill=None, vehicle_no=None, lr_date=None
        )
        _, readiness = self.assess(context)
        self.assertEqual(readiness["readiness_code"],
                         "SALES_INVOICE_DRAFT_NOT_READY_FOR_CONTROLLED_SUBMISSION")
        self.assertEqual(readiness["blocking_issues"], [
            "TALLY_STATUTORY_REFERENCE_NOT_RECORDED",
            "TALLY_VEHICLE_NUMBER_NOT_RECORDED",
            "TALLY_TRANSPORT_RECEIPT_DATE_NOT_RECORDED",
        ])

    def test_blank_override_is_not_statutory_evidence(self):
        context = self.context()
        context["statutory_evidence"].update(
            vehicle_no=None, lr_date=None, allow_blank_transport_details=True,
        )
        _, readiness = self.assess(context)
        self.assertIn("BLANK_TRANSPORT_OVERRIDE_HAS_NO_CONTROLLED_JUSTIFICATION",
                      readiness["blocking_issues"])

    def test_changed_values_and_existing_postings_fail_closed(self):
        context = self.context()
        context["sales_invoice"]["grand_total"] = 1
        context["linked_gl_entries"] = 1
        context["linked_stock_ledger_entries"] = 1
        _, readiness = self.assess(context)
        self.assertIn("CONTROLLED_SALES_INVOICE_COMMERCIAL_VALUES_CHANGED",
                      readiness["blocking_issues"])
        self.assertIn("CONTROLLED_SALES_INVOICE_ALREADY_HAS_STOCK_POSTING",
                      readiness["blocking_issues"])
        self.assertIn("CONTROLLED_SALES_INVOICE_ALREADY_HAS_ACCOUNTING_POSTING",
                      readiness["blocking_issues"])

    def test_no_draft_is_not_applicable(self):
        row = self.row()
        row["retained_material_sales_invoice_draft_readiness"] = {}
        result = attach_sales_invoice_submission_readiness(
            {"components": [row]}, {}
        )["components"][0]["retained_material_sales_invoice_submission_readiness"]
        self.assertFalse(result["applicable"])


if __name__ == "__main__":
    unittest.main()
