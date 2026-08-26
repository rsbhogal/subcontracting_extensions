# Copyright (c) 2026, Bhogals Private Limited
# For license information, please see license.txt

from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase

from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.settlement_netting import (
    _summarize_used_credit_quantities,
    calculate_settlement_netting,
)


def _facts(shortage_qty=30.0, commercial_variance_qty=30.0):
    return {
        "summary": {
            "physical_inventory": {"outstanding_qty": shortage_qty},
            "comparisons": {
                "invoice_vs_scr_received": commercial_variance_qty,
            },
        }
    }


def _gross(shortage_qty=30.0):
    raw_rate = 58.93598628479226
    processing_rate = 2.3
    return {
        "quantity": {"shortage_qty": shortage_qty, "shortage_uom": "Kg"},
        "raw_material": {"recommended": True, "rate": raw_rate},
        "processing_charges": {
            "recommended": True,
            "rate": processing_rate,
        },
        "totals": {
            "raw_material_recovery": shortage_qty * raw_rate,
            "processing_charge_recovery": shortage_qty * processing_rate,
            "total_recovery": shortage_qty * (raw_rate + processing_rate),
        },
    }


def _credit(qty=170.0, unbilled_qty=170.0, name="PMA-1"):
    return {
        "entry": name,
        "available_qty": qty,
        "commercial_unbilled_qty": unbilled_qty,
    }


class TestSettlementNetting(TestCase):
    def test_used_credit_tracks_commercial_qty_independently(self):
        used = _summarize_used_credit_quantities(
            [
                SimpleNamespace(
                    against_entry="PMA-1",
                    processed_qty=30.0,
                    account_qty=30.0,
                    commercial_qty=10.0,
                )
            ]
        )

        self.assertEqual(used["PMA-1"]["processed_qty"], 30.0)
        self.assertEqual(used["PMA-1"]["account_qty"], 30.0)
        self.assertEqual(used["PMA-1"]["commercial_qty"], 10.0)

    def test_current_case_is_fully_netted(self):
        result = calculate_settlement_netting(
            facts=_facts(),
            gross_recovery=_gross(),
            credits=[_credit()],
        )

        self.assertEqual(
            result["material_credit"]["proposed_applied_qty"],
            30.0,
        )
        self.assertEqual(
            result["material_credit"]["commercially_matched_qty"],
            30.0,
        )
        self.assertEqual(result["net"]["physical_shortage_qty"], 0.0)
        self.assertEqual(result["net"]["commercial_variance_qty"], 0.0)
        self.assertEqual(result["net"]["total_recovery"], 0.0)
        self.assertEqual(
            result["recommended_action"],
            "Apply Processor Material Credit",
        )

    def test_partial_credit_leaves_residual_debit_note(self):
        result = calculate_settlement_netting(
            facts=_facts(),
            gross_recovery=_gross(),
            credits=[_credit(qty=10.0, unbilled_qty=10.0)],
        )

        self.assertEqual(
            result["material_credit"]["proposed_applied_qty"],
            10.0,
        )
        self.assertEqual(result["net"]["physical_shortage_qty"], 20.0)
        self.assertEqual(result["net"]["commercial_variance_qty"], 20.0)
        self.assertAlmostEqual(
            result["net"]["total_recovery"],
            20.0 * (58.93598628479226 + 2.3),
        )
        self.assertEqual(
            result["recommended_action"],
            "Apply Processor Material Credit and Create Draft Debit Note",
        )

    def test_billed_credit_nets_physical_but_not_commercial_variance(self):
        result = calculate_settlement_netting(
            facts=_facts(),
            gross_recovery=_gross(),
            credits=[_credit(qty=30.0, unbilled_qty=0.0)],
        )

        self.assertEqual(result["net"]["physical_shortage_qty"], 0.0)
        self.assertEqual(result["net"]["commercial_variance_qty"], 30.0)
        self.assertEqual(result["net"]["raw_material_recovery"], 0.0)
        self.assertAlmostEqual(
            result["net"]["processing_charge_recovery"],
            69.0,
        )

    def test_no_credit_preserves_gross_recovery(self):
        result = calculate_settlement_netting(
            facts=_facts(),
            gross_recovery=_gross(),
            credits=[],
        )

        self.assertEqual(
            result["net"]["physical_shortage_qty"],
            30.0,
        )
        self.assertAlmostEqual(
            result["net"]["total_recovery"],
            _gross()["totals"]["total_recovery"],
        )
        self.assertEqual(
            result["recommended_action"],
            "Create Draft Debit Note",
        )