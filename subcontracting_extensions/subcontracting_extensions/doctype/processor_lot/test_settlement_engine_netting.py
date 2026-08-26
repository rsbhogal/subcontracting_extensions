# Copyright (c) 2026, Bhogals Private Limited
# For license information, please see license.txt

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest import TestCase

from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.settlement_engine import (
    _set_debit_note_header,
    build_net_recovery_report,
)
from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.fact_engine import (
    _build_fact_summary,
)
from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.processor_lot import (
    _submitted_credit_applications_cover_physical_shortage,
    build_material_settlement_snapshot,
)


def _gross_recovery():
    return {
        "status": "Recovery Calculated",
        "quantity": {"shortage_qty": 30.0, "shortage_uom": "Kg"},
        "raw_material": {
            "recommended": True,
            "quantity": 30.0,
            "uom": "Kg",
            "rate": 60.0,
            "amount": 1800.0,
        },
        "processing_charges": {
            "recommended": True,
            "quantity": 30.0,
            "uom": "Kg",
            "rate": 2.3,
            "amount": 69.0,
        },
        "totals": {
            "raw_material_recovery": 1800.0,
            "processing_charge_recovery": 69.0,
            "total_recovery": 1869.0,
        },
        "integrity": {
            "is_valid": True,
            "warnings": [],
            "blocking_errors": [],
        },
    }


class TestSettlementEngineNetting(TestCase):
    def test_billed_advance_credit_closes_invoice_vs_receipt_variance(self):
        summary = _build_fact_summary(
            component_totals={
                "supplied_qty": 7374,
                "consumed_qty": 7374,
                "returned_qty": 0,
                "outstanding_qty": 0,
                "stock_uom": "Kg",
            },
            material_transfer_facts={"total_transferred_qty": 7374},
            receipt_facts={
                "total_received_qty": 7374,
                "total_consumed_qty": 7374,
                "sco_received_qty": 7374,
                "sco_consumed_qty": 7374,
            },
            purchase_receipt_facts={"total_received_qty": 7500},
            commercial_facts={"total_invoice_qty": 7500},
            advance_credit_facts={"total_commercial_qty": 126},
        )

        self.assertEqual(
            summary["commercial"]["commercially_recognized_receipt_qty"],
            7500,
        )
        self.assertEqual(
            summary["comparisons"]["invoice_vs_scr_received"],
            0,
        )
        self.assertEqual(
            summary["comparisons"][
                "invoice_vs_stock_backed_scr_received"
            ],
            126,
        )

    def test_partial_credit_builds_only_residual_recovery(self):
        report = build_net_recovery_report(
            gross_recovery=_gross_recovery(),
            settlement_netting={
                "net": {
                    "physical_shortage_qty": 20.0,
                    "raw_material_recovery_qty": 20.0,
                    "raw_material_recovery": 1200.0,
                    "processing_charge_recovery_qty": 20.0,
                    "processing_charge_recovery": 46.0,
                    "total_recovery": 1246.0,
                }
            },
        )

        self.assertEqual(report["quantity"]["shortage_qty"], 20.0)
        self.assertEqual(report["raw_material"]["quantity"], 20.0)
        self.assertEqual(report["processing_charges"]["quantity"], 20.0)
        self.assertEqual(report["totals"]["total_recovery"], 1246.0)
        self.assertEqual(report["status"], "Recovery Calculated")

    def test_full_credit_suppresses_debit_note_recovery(self):
        report = build_net_recovery_report(
            gross_recovery=_gross_recovery(),
            settlement_netting={
                "net": {
                    "physical_shortage_qty": 0.0,
                    "raw_material_recovery_qty": 0.0,
                    "raw_material_recovery": 0.0,
                    "processing_charge_recovery_qty": 0.0,
                    "processing_charge_recovery": 0.0,
                    "total_recovery": 0.0,
                }
            },
        )

        self.assertFalse(report["raw_material"]["recommended"])
        self.assertFalse(report["processing_charges"]["recommended"])
        self.assertEqual(report["totals"]["total_recovery"], 0.0)
        self.assertEqual(report["status"], "No Recovery Required")

    def test_full_physical_credit_keeps_processing_only_residual(self):
        report = build_net_recovery_report(
            gross_recovery=_gross_recovery(),
            settlement_netting={
                "net": {
                    "physical_shortage_qty": 0.0,
                    "raw_material_recovery_qty": 0.0,
                    "raw_material_recovery": 0.0,
                    "processing_charge_recovery_qty": 19.0,
                    "processing_charge_recovery": 43.7,
                    "total_recovery": 43.7,
                }
            },
        )

        self.assertFalse(report["raw_material"]["recommended"])
        self.assertTrue(report["processing_charges"]["recommended"])
        self.assertEqual(report["quantity"]["shortage_qty"], 0.0)
        self.assertEqual(report["processing_charges"]["quantity"], 19.0)
        self.assertEqual(report["totals"]["total_recovery"], 43.7)
        self.assertEqual(report["status"], "Recovery Calculated")

    def test_processing_only_recovery_needs_no_material_snapshot(self):
        snapshot = build_material_settlement_snapshot(
            facts={
                "components": [
                    {
                        "outstanding_qty": 116.0,
                    }
                ]
            },
            recovery={
                "quantity": {
                    "shortage_qty": 0.0,
                },
                "raw_material": {
                    "recommended": False,
                    "rate": 59.75116,
                    "amount": 0.0,
                },
                "processing_charges": {
                    "recommended": True,
                    "quantity": 19.0,
                    "rate": 2.3,
                    "amount": 43.7,
                },
                "integrity": {
                    "is_valid": True,
                },
            },
        )

        self.assertEqual(snapshot, [])

    def test_submitted_credit_can_cover_physical_shortage_only(self):
        facts = {
            "summary": {
                "physical_inventory": {
                    "outstanding_qty": 116.0,
                }
            }
        }
        applications = [
            {
                "account_qty": 116.0,
                "commercial_qty": 116.0,
            }
        ]

        self.assertTrue(
            _submitted_credit_applications_cover_physical_shortage(
                facts=facts,
                applications=applications,
            )
        )

    def test_debit_note_header_preserves_settlement_posting_date(self):
        settlement_date = date(2026, 8, 18)
        debit_note = SimpleNamespace()
        processor_lot = SimpleNamespace(
            company="Bhogals Private Limited",
            supplier="Subash Industries",
            settlement_date=settlement_date,
            name="PLS-26-08-0013",
            cost_center="1001 - Bhogals Private Limited (Unit-1) - BPL",
            branch=None,
        )

        _set_debit_note_header(
            debit_note=debit_note,
            processor_lot=processor_lot,
        )

        self.assertEqual(debit_note.set_posting_time, 1)
        self.assertEqual(
            debit_note.posting_date,
            settlement_date,
        )
        self.assertEqual(
            debit_note.bill_date,
            settlement_date,
        )
