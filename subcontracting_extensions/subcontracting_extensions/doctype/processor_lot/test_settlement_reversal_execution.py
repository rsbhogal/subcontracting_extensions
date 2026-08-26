# Copyright (c) 2026, Bhogals Private Limited
# For license information, please see license.txt

from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase

from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.processor_lot import (
    REVERSAL_SCOPE_COMPLETE,
    REVERSAL_SCOPE_DEBIT_NOTE_ONLY,
    build_settlement_reversal_sequence,
    resolve_cancelled_workflow_state,
    settlement_reversal_sco_temporary_status,
)
from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.settlement_application_engine import (
    processor_lot_accepts_credit_application,
)
from subcontracting_extensions.subcontracting_extensions.doctype.processor_material_account_entry.processor_material_account_entry import (
    credit_application_cancel_is_allowed,
)


class TestSettlementReversalExecution(TestCase):
    def setUp(self):
        self.applications = [
            {
                "name": "PMA-APP-1",
                "application_journal_entry": "JE-1",
                "application_stock_entry": "SE-1",
            }
        ]

    def test_debit_note_only_sequence_stops_after_debit_note(self):
        sequence = build_settlement_reversal_sequence(
            REVERSAL_SCOPE_DEBIT_NOTE_ONLY,
            "DN-1",
            self.applications,
        )

        self.assertEqual(
            [(row["doctype"], row["name"]) for row in sequence],
            [("Purchase Invoice", "DN-1")],
        )

    def test_complete_sequence_respects_dependency_order(self):
        sequence = build_settlement_reversal_sequence(
            REVERSAL_SCOPE_COMPLETE,
            "DN-1",
            self.applications,
        )

        self.assertEqual(
            [(row["doctype"], row["name"]) for row in sequence],
            [
                ("Purchase Invoice", "DN-1"),
                ("Processor Material Account Entry", "PMA-APP-1"),
                ("Journal Entry", "JE-1"),
                ("Stock Entry", "SE-1"),
            ],
        )

    def test_submitted_application_cancel_requires_controlled_window(self):
        self.assertFalse(
            credit_application_cancel_is_allowed(1, "Completed", False)
        )
        self.assertFalse(
            credit_application_cancel_is_allowed(
                1,
                "Reversal In Progress",
                False,
            )
        )
        self.assertTrue(
            credit_application_cancel_is_allowed(
                1,
                "Reversal In Progress",
                True,
            )
        )

    def test_draft_lot_keeps_existing_application_cancel_behavior(self):
        self.assertTrue(
            credit_application_cancel_is_allowed(0, "Draft", False)
        )

    def test_reopened_submitted_lot_accepts_new_credit_application(self):
        self.assertTrue(
            processor_lot_accepts_credit_application(1, "Reopened")
        )
        self.assertFalse(
            processor_lot_accepts_credit_application(1, "Completed")
        )

    def test_draft_lot_still_accepts_credit_application(self):
        self.assertTrue(
            processor_lot_accepts_credit_application(0, "Draft")
        )

    def test_unsupported_scope_is_rejected(self):
        with self.assertRaises(ValueError):
            build_settlement_reversal_sequence(
                "PMA Only",
                "DN-1",
                self.applications,
            )

    def test_closed_sco_uses_completed_cancellation_window(self):
        self.assertEqual(
            settlement_reversal_sco_temporary_status("Closed"),
            "Completed",
        )
        self.assertEqual(
            settlement_reversal_sco_temporary_status("Completed"),
            "Completed",
        )

    def test_unique_cancelled_workflow_state_is_resolved(self):
        states = [
            SimpleNamespace(state="Draft", doc_status="0"),
            SimpleNamespace(state="Submitted", doc_status="1"),
            SimpleNamespace(state="Cancelled", doc_status="2"),
        ]

        self.assertEqual(
            resolve_cancelled_workflow_state(states),
            "Cancelled",
        )

    def test_missing_cancelled_workflow_state_is_rejected(self):
        states = [
            SimpleNamespace(state="Draft", doc_status="0"),
            SimpleNamespace(state="Submitted", doc_status="1"),
        ]

        with self.assertRaises(ValueError):
            resolve_cancelled_workflow_state(states)

    def test_ambiguous_cancelled_workflow_state_is_rejected(self):
        states = [
            SimpleNamespace(state="Cancelled", doc_status="2"),
            SimpleNamespace(state="Voided", doc_status="2"),
        ]

        with self.assertRaises(ValueError):
            resolve_cancelled_workflow_state(states)
