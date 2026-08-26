# Copyright (c) 2026, Bhogals Private Limited
# For license information, please see license.txt

from __future__ import annotations

from unittest import TestCase

from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.processor_lot import (
    build_settlement_reversal_forecast,
)


class TestSettlementReversalPreview(TestCase):
    def setUp(self):
        self.facts = {
            "summary": {
                "physical_inventory": {
                    "outstanding_qty": 116.0,
                },
                "comparisons": {
                    "invoice_vs_scr_received": 135.0,
                },
            }
        }
        self.applications = [
            {
                "against_entry": "PMA-26-08-0001",
                "account_qty": 116.0,
                "commercial_qty": 116.0,
            }
        ]
        self.source_positions = {
            "PMA-26-08-0001": {
                "source_credit_qty": 170.0,
                "remaining_credit": 24.0,
            }
        }

    def test_debit_note_only_preserves_material_credit_application(self):
        forecast = build_settlement_reversal_forecast(
            facts=self.facts,
            applications=self.applications,
            source_positions=self.source_positions,
        )

        self.assertEqual(
            forecast["debit_note_only"][
                "net_physical_outstanding_qty"
            ],
            0.0,
        )
        self.assertEqual(
            forecast["debit_note_only"][
                "open_commercial_variance_qty"
            ],
            19.0,
        )
        self.assertEqual(
            forecast["debit_note_only"][
                "source_credit_remaining_qty"
            ],
            24.0,
        )

    def test_submitted_debit_note_closes_current_commercial_position(self):
        forecast = build_settlement_reversal_forecast(
            facts=self.facts,
            applications=self.applications,
            source_positions=self.source_positions,
            commercial_settlement_document_submitted=True,
        )

        self.assertEqual(
            forecast["current"]["open_commercial_variance_qty"],
            0.0,
        )
        self.assertEqual(
            forecast["debit_note_only"][
                "open_commercial_variance_qty"
            ],
            19.0,
        )

    def test_complete_reversal_restores_full_settlement_position(self):
        forecast = build_settlement_reversal_forecast(
            facts=self.facts,
            applications=self.applications,
            source_positions=self.source_positions,
        )

        self.assertEqual(
            forecast["complete_settlement"][
                "net_physical_outstanding_qty"
            ],
            116.0,
        )
        self.assertEqual(
            forecast["complete_settlement"][
                "open_commercial_variance_qty"
            ],
            135.0,
        )
        self.assertEqual(
            forecast["complete_settlement"][
                "source_credit_remaining_qty"
            ],
            140.0,
        )

    def test_no_application_leaves_gross_position_unchanged(self):
        forecast = build_settlement_reversal_forecast(
            facts=self.facts,
            applications=[],
            source_positions={},
        )

        self.assertEqual(
            forecast["current"]["net_physical_outstanding_qty"],
            116.0,
        )
        self.assertEqual(
            forecast["current"]["open_commercial_variance_qty"],
            135.0,
        )
        self.assertEqual(
            forecast["complete_settlement"][
                "source_credit_remaining_qty"
            ],
            0.0,
        )
