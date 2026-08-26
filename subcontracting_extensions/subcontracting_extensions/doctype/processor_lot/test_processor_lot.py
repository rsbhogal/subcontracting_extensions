# Copyright (c) 2026, R.S. Bhogal and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.processor_lot import (
    ProcessorLot,
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
        }

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
    def test_balanced_closure_blocks_positive_commercial_variance(
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

        with self.assertRaises(frappe.ValidationError):
            lot._validate_balanced_closure(facts)

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
