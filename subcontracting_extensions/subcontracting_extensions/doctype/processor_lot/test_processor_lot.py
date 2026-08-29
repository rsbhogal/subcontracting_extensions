# Copyright (c) 2026, R.S. Bhogal and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime

from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.processor_lot import (
    ProcessorLot,
)
from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot import (
    settlement_recommendation,
)


class TestProcessorLot(FrappeTestCase):
    def _make_lot(self):
        lot = ProcessorLot(
            {
                "doctype": "Processor Lot",
                "name": "TEST-PL",
            }
        )
        return lot

    def _make_balanced_facts(
        self,
        invoice_vs_scr_received=0.0,
        recover_processing_charges_on_shortage=True,
    ):
        return {
            "summary": {
                "physical_inventory": {
                    "outstanding_qty": 0.0,
                    "scr_received_qty": 100.0,
                },
                "comparisons": {
                    "transfer_vs_sco_supplied": 0.0,
                    "scr_received_vs_sco_received": 0.0,
                    "scr_consumed_vs_sco_consumed": 0.0,
                    "purchase_receipt_vs_invoice": 0.0,
                    "invoice_vs_scr_received":
                        invoice_vs_scr_received,
                },
            },
            "integrity": {
                "blocking_errors": [],
            },
            "settlement_policy": {
                "policy_available": True,
                "policy_source": "Processor Lot",
                "purchase_order": "TEST-PO",
                "recover_raw_material_shortage": True,
                "recover_processing_charges_on_shortage": (
                    recover_processing_charges_on_shortage
                ),
                "settlement_basis": "Company Accepted Quantity",
            },
        }

    def _make_submission_with_saved_override(
        self,
        current_reason=None,
    ):
        saved_reason = "Management waived minor recovery."
        timestamp = "2026-08-28 12:29:53.302512"
        shared_values = {
            "doctype": "Processor Lot",
            "name": "TEST-PL",
            "override_settlement_policy": 1,
            "recover_raw_material_shortage": 1,
            "recover_processing_charges_on_shortage": 0,
            "settlement_basis": "Company Accepted Quantity",
            "settlement_remarks": None,
            "settlement_policy_source": "Overridden",
            "overridden_by": "Administrator",
            "debit_note": None,
        }

        saved_lot = ProcessorLot(
            {
                **shared_values,
                "docstatus": 0,
                "settlement_policy_override_reason": saved_reason,
                "settlement_policy_overridden_on": get_datetime(
                    timestamp
                ),
            }
        )
        submitting_lot = ProcessorLot(
            {
                **shared_values,
                "docstatus": 1,
                "settlement_policy_override_reason": (
                    current_reason
                    if current_reason is not None
                    else saved_reason
                ),
                "settlement_policy_overridden_on": timestamp,
            }
        )
        submitting_lot._doc_before_save = saved_lot
        return submitting_lot

    def test_commercial_only_recommends_processing_recovery_only(self):
        facts = self._make_balanced_facts(
            invoice_vs_scr_received=30.0,
        )

        recommendation = settlement_recommendation.recommend_settlement(
            facts,
            "commercial_variance_only",
        )

        self.assertTrue(recommendation["integrity"]["is_valid"])
        self.assertFalse(
            recommendation["recommend_recover_raw_material"]
        )
        self.assertTrue(
            recommendation[
                "recommend_recover_processing_charges"
            ]
        )
        self.assertEqual(
            recommendation["classification_label"],
            "Commercial Variance Only",
        )
        reasoning_codes = {
            row["code"] for row in recommendation["reasoning"]
        }
        self.assertIn(
            "RAW_MATERIAL_NOT_APPLICABLE",
            reasoning_codes,
        )
        self.assertNotIn("RAW_MATERIAL_POLICY", reasoning_codes)

    def test_commercial_only_blocks_when_physical_balance_remains(self):
        facts = self._make_balanced_facts(
            invoice_vs_scr_received=30.0,
        )
        facts["summary"]["physical_inventory"][
            "outstanding_qty"
        ] = 1.0

        recommendation = settlement_recommendation.recommend_settlement(
            facts,
            "commercial_variance_only",
        )

        blocking_codes = {
            row["code"]
            for row in recommendation["integrity"]["blocking_errors"]
        }
        self.assertIn(
            "PHYSICAL_OUTSTANDING_QTY_REMAINS",
            blocking_codes,
        )

    def test_commercial_only_blocks_without_positive_variance(self):
        facts = self._make_balanced_facts(
            invoice_vs_scr_received=0.0,
        )

        recommendation = settlement_recommendation.recommend_settlement(
            facts,
            "commercial_variance_only",
        )

        blocking_codes = {
            row["code"]
            for row in recommendation["integrity"]["blocking_errors"]
        }
        self.assertIn(
            "COMMERCIAL_VARIANCE_NOT_POSITIVE",
            blocking_codes,
        )

    def test_unchanged_override_timestamp_type_allows_submission(self):
        lot = self._make_submission_with_saved_override()

        lot._validate_settlement_policy_override()

    def test_override_change_during_submission_is_blocked(self):
        lot = self._make_submission_with_saved_override(
            current_reason="Changed during submission.",
        )

        with self.assertRaises(frappe.ValidationError):
            lot._validate_settlement_policy_override()

    @patch(
        "subcontracting_extensions.subcontracting_extensions.doctype.processor_lot."
        "processor_lot.get_processor_lot_receipt_journey"
    )
    def test_balanced_closure_allows_negative_commercial_variance(
        self,
        mock_journey,
    ):
        mock_journey.return_value = {
            "summary": {
                "receipt_count": 1,
                "completed_count": 1,
                "pending_count": 0,
            }
        }

        lot = self._make_lot()

        facts = self._make_balanced_facts(
            invoice_vs_scr_received=-130.0,
        )

        lot._validate_balanced_closure(facts)

    @patch(
        "subcontracting_extensions.subcontracting_extensions.doctype.processor_lot."
        "processor_lot.get_processor_lot_receipt_journey"
    )
    def test_commercial_only_closure_requires_submitted_debit_note(
        self,
        mock_journey,
    ):
        mock_journey.return_value = {
            "summary": {
                "receipt_count": 1,
                "completed_count": 1,
                "pending_count": 0,
            }
        }

        lot = self._make_lot()

        facts = self._make_balanced_facts(
            invoice_vs_scr_received=130.0,
        )

        with patch.object(
            lot,
            "_validate_submitted_settlement_document",
        ) as mock_validate_debit_note:
            lot._validate_balanced_closure(facts)

        mock_validate_debit_note.assert_called_once_with()

    @patch(
        "subcontracting_extensions.subcontracting_extensions.doctype.processor_lot."
        "processor_lot.get_processor_lot_receipt_journey"
    )
    def test_commercial_only_closure_allows_policy_waiver(
        self,
        mock_journey,
    ):
        mock_journey.return_value = {
            "summary": {
                "receipt_count": 1,
                "completed_count": 1,
                "pending_count": 0,
            }
        }

        lot = self._make_lot()

        facts = self._make_balanced_facts(
            invoice_vs_scr_received=130.0,
            recover_processing_charges_on_shortage=False,
        )

        with patch.object(
            lot,
            "_validate_submitted_settlement_document",
        ) as mock_validate_debit_note:
            lot._validate_balanced_closure(facts)

        mock_validate_debit_note.assert_not_called()

    @patch(
        "subcontracting_extensions.subcontracting_extensions.doctype.processor_lot."
        "processor_lot.get_processor_lot_receipt_journey"
    )
    def test_balanced_closure_blocks_incomplete_receipt_journey(
        self,
        mock_journey,
    ):
        mock_journey.return_value = {
            "summary": {
                "receipt_count": 1,
                "completed_count": 0,
                "pending_count": 1,
            }
        }

        lot = self._make_lot()

        facts = self._make_balanced_facts(
            invoice_vs_scr_received=-130.0,
        )

        with self.assertRaises(frappe.ValidationError):
            lot._validate_balanced_closure(facts)
