# Copyright (c) 2026, Bhogals Private Limited
# For license information, please see license.txt

from __future__ import annotations

from unittest import TestCase

from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.settlement_application_engine import (
    _allocate_commercial_match_quantities,
    classify_credit_application_bundle,
    credit_application_sco_temporary_status,
    is_credit_application_journal_submission_allowed,
)


class TestSettlementApplicationEngine(TestCase):
    def test_journal_submission_sequence_is_enforced_by_docstatus(self):
        self.assertFalse(
            is_credit_application_journal_submission_allowed(0)
        )
        self.assertTrue(
            is_credit_application_journal_submission_allowed(1)
        )
        self.assertFalse(
            is_credit_application_journal_submission_allowed(2)
        )
        self.assertFalse(
            is_credit_application_journal_submission_allowed(None)
        )

    def test_bundle_submission_order_is_stock_then_journal_then_pma(self):
        self.assertEqual(
            classify_credit_application_bundle(0, 0, 0),
            {
                "state": "Pending",
                "next_doctype": "Stock Entry",
            },
        )
        self.assertEqual(
            classify_credit_application_bundle(0, 1, 0),
            {
                "state": "Pending",
                "next_doctype": "Journal Entry",
            },
        )
        self.assertEqual(
            classify_credit_application_bundle(0, 1, 1),
            {
                "state": "Pending",
                "next_doctype": "Processor Material Account Entry",
            },
        )
        self.assertEqual(
            classify_credit_application_bundle(1, 1, 1),
            {
                "state": "Complete",
                "next_doctype": None,
            },
        )

    def test_bundle_with_missing_or_cancelled_document_is_broken(self):
        self.assertEqual(
            classify_credit_application_bundle(
                0,
                None,
                0,
                has_stock_entry=False,
            )["state"],
            "Broken",
        )
        self.assertEqual(
            classify_credit_application_bundle(0, 2, 0)["state"],
            "Broken",
        )

    def test_reopened_lot_uses_completed_sco_submission_window(self):
        self.assertEqual(
            credit_application_sco_temporary_status(
                lot_docstatus=1,
                settlement_status="Reopened",
                sco_status="Closed",
            ),
            "Completed",
        )
        self.assertIsNone(
            credit_application_sco_temporary_status(
                lot_docstatus=1,
                settlement_status="Completed",
                sco_status="Closed",
            )
        )

    def test_commercial_allocation_skips_prior_application_qty(self):
        rows = [
            {
                "processor_lot_receipt": "PLR-1",
                "supplier_invoice_vs_company_qty": 10.0,
            },
            {
                "processor_lot_receipt": "PLR-2",
                "supplier_invoice_vs_company_qty": 20.0,
            },
        ]

        allocations, remaining = _allocate_commercial_match_quantities(
            receipt_rows=rows,
            required_qty=10.0,
            skip_qty=15.0,
        )

        self.assertEqual(len(allocations), 1)
        self.assertEqual(
            allocations[0][0]["processor_lot_receipt"],
            "PLR-2",
        )
        self.assertEqual(allocations[0][1], 10.0)
        self.assertEqual(remaining, 0.0)

    def test_commercial_allocation_reports_unmatched_remainder(self):
        rows = [
            {
                "processor_lot_receipt": "PLR-1",
                "supplier_invoice_vs_company_qty": 10.0,
            }
        ]

        allocations, remaining = _allocate_commercial_match_quantities(
            receipt_rows=rows,
            required_qty=8.0,
            skip_qty=5.0,
        )

        self.assertEqual(allocations[0][1], 5.0)
        self.assertEqual(remaining, 3.0)
