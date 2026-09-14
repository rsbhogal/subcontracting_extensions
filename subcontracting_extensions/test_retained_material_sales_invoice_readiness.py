"""J19B2G pure readiness tests."""

import unittest

from subcontracting_extensions.retained_material_sales_invoice_readiness import (
    attach_sales_invoice_draft_readiness,
    forecast_invoice_number,
)


class TestInvoiceForecast(unittest.TestCase):
    def test_forecast_does_not_reserve(self):
        result = forecast_invoice_number(
            "U-I/26-27/####", 16, ["U-I/26-27/0015", "U-I/26-27/0016"]
        )
        self.assertEqual(result["forecast_number"], "U-I/26-27/0017")
        self.assertEqual(result["forecast_status"], "FORECAST_ONLY_NOT_RESERVED")

    def test_counter_behind_documents_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "COUNTER_BEHIND"):
            forecast_invoice_number("U-I/26-27/####", 15, ["U-I/26-27/0016"])

    def test_missing_counter_uses_highest_unambiguous_existing_name(self):
        result = forecast_invoice_number(
            "U-I/26-27/####", None,
            ["U-I/26-27/0015", "U-I/26-27/0016"],
        )
        self.assertEqual(result["forecast_number"], "U-I/26-27/0017")
        self.assertIsNone(result["series_current"])
        self.assertEqual(
            result["forecast_source"], "HIGHEST_EXISTING_SALES_INVOICE_NAME"
        )

    def test_malformed_prefixed_document_name_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "EXISTING_NAME_INVALID"):
            forecast_invoice_number(
                "U-I/26-27/####", None, ["U-I/26-27/0016-duplicate"]
            )

    def test_invalid_series_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "SERIES_INVALID"):
            forecast_invoice_number("U-I/26-27/", 16, [])


class TestDraftReadiness(unittest.TestCase):
    def row(self):
        return {
            "commercial_scope_key": "SCOPE",
            "stock_uom": "Kg",
            "suggested_recovery_quantity": 20000,
            "suggested_recovery_rate": 58.35,
            "suggested_recovery_amount": 1167000,
            "persisted_material_disposition": {
                "name": "PLMD-1", "disposition": "RETAINED_BY_PROCESSOR",
                "disposition_revision": 1,
            },
            "persisted_classification": {
                "name": "PLCC-1", "classification": "PROCESSOR_RESPONSIBLE",
                "selected_treatment_method": "SALES_INVOICE",
                "classification_revision": 1, "treatment_revision": 1,
            },
        }

    def context(self):
        return {
            "processor_lot": "LOT", "scope_key": "SCOPE", "company": "Company",
            "customer": "Customer", "customer_address": "Address", "item_code": "Item",
            "warehouse": "Processor - C", "income_account": "Sales - C",
            "cost_center": "Main - C", "branch": "Unit 1", "decision_event": "PLCD-2",
            "policy_reconciliation_event": "PLPRE-1", "duplicate_documents": [],
            "coordination_mode": "ERPNEXT_FORECAST_WITH_TALLY_COORDINATION",
            "external_system_name": "Tally",
            "invoice_number_forecast": {
                "forecast_number": "U-I/26-27/0017",
                "forecast_status": "FORECAST_ONLY_NOT_RESERVED",
            },
        }

    def test_ready_facts_remain_non_executing(self):
        result = attach_sales_invoice_draft_readiness(
            {"components": [self.row()]}, self.context()
        )
        row = result["components"][0]["retained_material_sales_invoice_draft_readiness"]
        self.assertEqual(row["readiness_code"],
                         "SALES_INVOICE_DRAFT_FACTS_READY_FUTURE_CREATION_DEFERRED")
        self.assertEqual(row["draft_values"]["update_stock"], 1)
        self.assertEqual(row["invoice_number_forecast"]["forecast_number"],
                         "U-I/26-27/0017")
        self.assertTrue(row["tally_reservation_confirmation_required_before_draft_creation"])
        for key in (
            "commercial_execution_ready", "commercial_document_creation_enabled",
            "commercial_document_authorized", "stock_document_authorized",
            "accounting_posting_authorized", "tax_posting_authorized",
            "lot_closure_authorized",
        ):
            self.assertFalse(row[key])
            self.assertFalse(result[key])

    def test_duplicate_document_blocks(self):
        context = self.context()
        context["duplicate_documents"] = [{"parent": "SINV-1"}]
        row = attach_sales_invoice_draft_readiness(
            {"components": [self.row()]}, context
        )["components"][0]["retained_material_sales_invoice_draft_readiness"]
        self.assertIn("EXISTING_RETAINED_MATERIAL_SALES_INVOICE", row["blocking_issues"])

    def test_missing_forecast_blocks_only_transition_mode(self):
        context = self.context()
        context["invoice_number_forecast"] = None
        row = attach_sales_invoice_draft_readiness(
            {"components": [self.row()]}, context
        )["components"][0]["retained_material_sales_invoice_draft_readiness"]
        self.assertIn("TALLY_INVOICE_NUMBER_FORECAST_NOT_READY", row["blocking_issues"])

    def test_non_selected_scope_is_not_applicable(self):
        row = self.row()
        row["persisted_classification"]["selected_treatment_method"] = None
        readiness = attach_sales_invoice_draft_readiness(
            {"components": [row]}, self.context()
        )["components"][0]["retained_material_sales_invoice_draft_readiness"]
        self.assertFalse(readiness["applicable"])

    def test_matching_confirmation_projects_effective_confirmed_status(self):
        context = self.context()
        context["number_reservations"] = [{
            "name": "PLSINR-1", "reserved_invoice_number": "U-I/26-27/0017",
        }]
        context["number_reservation_confirmations"] = [{
            "name": "PLSINC-1", "reservation": "PLSINR-1",
            "reserved_invoice_number": "U-I/26-27/0017",
            "confirmation_status": "CONFIRMED",
        }]
        readiness = attach_sales_invoice_draft_readiness(
            {"components": [self.row()]}, context
        )["components"][0]["retained_material_sales_invoice_draft_readiness"]
        self.assertEqual(
            readiness["readiness_code"],
            "SALES_INVOICE_NUMBER_RESERVED_IN_TALLY_FUTURE_DRAFT_CREATION_DEFERRED",
        )
        self.assertEqual(readiness["tally_confirmation_status"], "CONFIRMED")
        self.assertFalse(readiness["tally_reservation_confirmation_available"])

    def test_confirmation_mismatch_fails_closed(self):
        context = self.context()
        context["number_reservations"] = [{
            "name": "PLSINR-1", "reserved_invoice_number": "U-I/26-27/0017",
        }]
        context["number_reservation_confirmations"] = [{
            "name": "PLSINC-1", "reservation": "OTHER",
            "reserved_invoice_number": "U-I/26-27/0017",
            "confirmation_status": "CONFIRMED",
        }]
        readiness = attach_sales_invoice_draft_readiness(
            {"components": [self.row()]}, context
        )["components"][0]["retained_material_sales_invoice_draft_readiness"]
        self.assertIn("TALLY_RESERVATION_CONFIRMATION_MISMATCH",
                      readiness["blocking_issues"])

    def test_controlled_draft_projects_submission_deferred(self):
        context = self.context()
        context["number_reservations"] = [{
            "name": "PLSINR-1", "reserved_invoice_number": "U-I/26-27/0017",
        }]
        context["number_reservation_confirmations"] = [{
            "name": "PLSINC-1", "reservation": "PLSINR-1",
            "reserved_invoice_number": "U-I/26-27/0017",
            "confirmation_status": "CONFIRMED",
        }]
        context["duplicate_documents"] = [{"parent": "U-I/26-27/0017"}]
        context["sales_invoice_draft_creation_events"] = [{
            "name": "PLSIDC-1", "sales_invoice": "U-I/26-27/0017",
            "draft_only": 1,
        }]
        readiness = attach_sales_invoice_draft_readiness(
            {"components": [self.row()]}, context
        )["components"][0]["retained_material_sales_invoice_draft_readiness"]
        self.assertEqual(readiness["readiness_code"],
                         "SALES_INVOICE_DRAFT_CREATED_SUBMISSION_DEFERRED")
        self.assertEqual(readiness["tax_calculation_status"],
                         "CALCULATED_ON_DRAFT_NOT_POSTED")
        self.assertEqual(readiness["blocking_issues"], [])


if __name__ == "__main__":
    unittest.main()
