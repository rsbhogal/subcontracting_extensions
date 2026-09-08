"""Pure J19A1 component commercial preview tests; no Frappe or writes."""

import unittest
from copy import deepcopy

from subcontracting_extensions.component_commercial_preview import (
    build_component_commercial_preview,
)


def component(**changes):
    row = dict(sco_supplied_item="RM-A", sco_finished_item="FG-A",
        component_item="Wire", stock_uom="Kg", physical_remaining_qty=0,
        applied_credit_qty=0, evidence_consistent=True,
        material_settlement_eligible=True)
    row.update(changes)
    return row


def finished(**changes):
    row = dict(sco_finished_item="FG-A", finished_item="Drawn Wire",
        purchase_order_item="PO-A", stock_uom="Kg", company_accepted_qty=10,
        evidence_consistent=True)
    row.update(changes)
    return row


def invoice(**changes):
    row = dict(docstatus=1, is_return=0, po_detail="PO-A", qty=10,
        uom="Kg", net_rate=2.5, net_amount=25)
    row.update(changes)
    return row


def preview(components=None, finished_rows=None, context=None):
    report = dict(processor_lot="LOT-A", subcontracting_order="SCO-A",
        components=components if components is not None else [component()])
    return build_component_commercial_preview(
        report,
        finished_rows if finished_rows is not None else [finished()],
        context if context is not None else dict(
            invoice_rows=[invoice()],
            settlement_policy=dict(recover_raw_material_shortage=True,
                recover_processing_charges_on_shortage=True)),
    )


class TestComponentCommercialPreview(unittest.TestCase):
    def test_contract_is_read_only_and_never_authorizes_actions(self):
        result = preview()
        self.assertEqual(result["commercial_preview_contract_version"], "J19A1")
        self.assertFalse(result["commercial_document_creation_enabled"])
        self.assertFalse(result["commercial_document_authorized"])
        self.assertFalse(result["lot_closure_authorized"])

    def test_mixed_uoms_are_kept_in_independent_rows(self):
        result = preview(
            [component(), component(sco_supplied_item="RM-B", sco_finished_item="FG-B",
                component_item="Blank", stock_uom="Units")],
            [finished(), finished(sco_finished_item="FG-B", finished_item="Cup",
                purchase_order_item="PO-B", stock_uom="Units", company_accepted_qty=5)],
            dict(invoice_rows=[invoice(), invoice(po_detail="PO-B", qty=5,
                uom="Units", net_rate=12, net_amount=60)],
                settlement_policy=dict(recover_processing_charges_on_shortage=True)),
        )
        self.assertEqual([row["stock_uom"] for row in result["finished_items"]], ["Kg", "Units"])
        self.assertNotIn("total_qty", result)

    def test_multi_item_zero_variance_needs_no_recovery(self):
        self.assertEqual(preview()["commercial_decision_code"],
            "COMMERCIAL_REVIEW_COMPLETE_NO_RECOVERY")

    def test_unrelated_combined_invoice_row_is_ignored(self):
        result = preview(context=dict(invoice_rows=[invoice(),
            invoice(po_detail="OTHER", qty=999)], settlement_policy={}))
        row = result["finished_items"][0]
        self.assertEqual(row["supplier_invoice_qty"], 10)
        self.assertEqual(len(row["matched_invoice_rows"]), 1)

    def test_only_submitted_non_return_invoice_rows_match(self):
        result = preview(context=dict(invoice_rows=[invoice(docstatus=0),
            invoice(is_return=1), invoice()], settlement_policy={}))
        self.assertEqual(len(result["finished_items"][0]["matched_invoice_rows"]), 1)

    def test_missing_po_detail_fails_closed(self):
        row = preview(finished_rows=[finished(purchase_order_item=None)])["finished_items"][0]
        self.assertEqual(row["commercial_decision_code"], "REVIEW_FINISHED_ITEM_IDENTITY")
        self.assertFalse(row["commercial_review_permitted"])

    def test_missing_invoice_fails_closed(self):
        row = preview(context=dict(invoice_rows=[], settlement_policy={}))["finished_items"][0]
        self.assertEqual(row["commercial_decision_code"], "REVIEW_FINISHED_ITEM_INVOICING")

    def test_finished_item_uom_mismatch_fails_closed(self):
        row = preview(context=dict(invoice_rows=[invoice(uom="Units")],
            settlement_policy={}))["finished_items"][0]
        self.assertEqual(row["commercial_decision_code"], "REVIEW_FINISHED_ITEM_UOM")

    def test_under_invoicing_requires_review(self):
        row = preview(context=dict(invoice_rows=[invoice(qty=9)],
            settlement_policy={}))["finished_items"][0]
        self.assertEqual(row["commercial_decision_code"], "REVIEW_FINISHED_ITEM_INVOICING")

    def test_processing_variance_is_calculated_once_per_finished_row(self):
        result = preview(
            [component(), component(sco_supplied_item="RM-B", component_item="Oil")],
            context=dict(invoice_rows=[invoice(qty=12, net_amount=30)],
                settlement_policy=dict(recover_processing_charges_on_shortage=True)),
        )
        row = result["finished_items"][0]
        self.assertEqual(row["commercial_variance_qty"], 2)
        self.assertEqual(row["processing_recovery_amount"], 5)
        self.assertEqual(len(result["finished_items"]), 1)

    def test_processing_recovery_policy_disabled_is_explicit(self):
        row = preview(context=dict(invoice_rows=[invoice(qty=12)],
            settlement_policy=dict(recover_processing_charges_on_shortage=False)))["finished_items"][0]
        self.assertEqual(row["commercial_decision_code"], "PROCESSING_RECOVERY_POLICY_DISABLED")
        self.assertFalse(row["processing_recovery_recommended"])

    def test_conflicting_processing_rates_fail_closed(self):
        row = preview(context=dict(invoice_rows=[invoice(qty=6, net_rate=2),
            invoice(qty=6, net_rate=3)], settlement_policy=dict(
                recover_processing_charges_on_shortage=True)))["finished_items"][0]
        self.assertEqual(row["commercial_decision_code"], "REVIEW_PROCESSING_RATE_EVIDENCE")

    def test_unaccounted_component_blocks_commercial_review(self):
        result = preview([component(material_settlement_eligible=False,
            physical_remaining_qty=2)])
        self.assertEqual(result["commercial_decision_code"], "ACCOUNT_REMAINING_MATERIAL")

    def test_fully_applied_credit_is_commercially_reviewable(self):
        row = preview([component(physical_remaining_qty=2,
            applied_credit_qty=2)])["components"][0]
        self.assertEqual(row["commercial_decision_code"], "RAW_MATERIAL_CREDIT_ACCOUNTED")
        self.assertTrue(row["commercial_review_permitted"])

    def test_missing_component_identity_fails_closed(self):
        row = preview([component(sco_supplied_item=None)])["components"][0]
        self.assertEqual(row["commercial_decision_code"], "REVIEW_COMPONENT_IDENTITY")

    def test_duplicate_component_identity_fails_closed(self):
        result = preview([component(), component()])
        self.assertEqual(result["commercial_decision_code"], "REVIEW_COMMERCIAL_IDENTITY")
        self.assertEqual(
            {row["commercial_decision_code"] for row in result["components"]},
            {"DUPLICATE_COMPONENT_IDENTITY"},
        )

    def test_duplicate_finished_item_identity_fails_closed(self):
        result = preview(finished_rows=[finished(), finished()])
        self.assertEqual(result["commercial_decision_code"], "REVIEW_COMMERCIAL_IDENTITY")
        self.assertEqual(
            {row["commercial_decision_code"] for row in result["finished_items"]},
            {"DUPLICATE_FINISHED_ITEM_IDENTITY"},
        )

    def test_malformed_component_quantity_fails_closed(self):
        row = preview([component(physical_remaining_qty="invalid")])["components"][0]
        self.assertEqual(row["commercial_decision_code"], "REVIEW_COMPONENT_EVIDENCE")

    def test_negative_component_quantity_fails_closed(self):
        row = preview([component(applied_credit_qty=-1)])["components"][0]
        self.assertEqual(row["commercial_decision_code"], "REVIEW_COMPONENT_EVIDENCE")

    def test_malformed_finished_quantity_fails_closed(self):
        row = preview(finished_rows=[finished(company_accepted_qty="invalid")])["finished_items"][0]
        self.assertEqual(
            row["commercial_decision_code"],
            "REVIEW_FINISHED_ITEM_QUANTITY_EVIDENCE",
        )

    def test_non_positive_recovery_rate_fails_closed(self):
        row = preview(context=dict(invoice_rows=[invoice(qty=12, net_rate=0)],
            settlement_policy=dict(recover_processing_charges_on_shortage=True)))["finished_items"][0]
        self.assertEqual(row["commercial_decision_code"], "REVIEW_PROCESSING_RATE_EVIDENCE")

    def test_existing_document_prevents_new_treatment(self):
        result = preview(context=dict(invoice_rows=[invoice()], settlement_policy={},
            existing_documents=[dict(name="DN-1", docstatus=1)]))
        self.assertEqual(result["commercial_decision_code"],
            "COMMERCIAL_TREATMENT_ALREADY_RECORDED")

    def test_policy_issue_is_structured_and_fails_closed(self):
        result = preview(context=dict(invoice_rows=[invoice()], settlement_policy={},
            policy_issues=["POLICY_MISMATCH"]))
        self.assertEqual(result["commercial_decision_code"], "REVIEW_SETTLEMENT_POLICY")
        self.assertFalse(result["commercial_review_permitted"])

    def test_legacy_evidence_is_visible_and_non_authoritative(self):
        evidence = [dict(doctype="Processor Material Account Entry", name="PMA-1")]
        result = preview(context=dict(invoice_rows=[invoice()], settlement_policy={},
            legacy_evidence=evidence))
        self.assertEqual(result["commercial_decision_code"],
            "REVIEW_LEGACY_COMMERCIAL_EVIDENCE")
        self.assertEqual(result["legacy_evidence"], evidence)

    def test_inputs_are_not_mutated(self):
        material = dict(processor_lot="LOT-A", subcontracting_order="SCO-A",
            components=[component()])
        finished_rows = [finished()]
        context = dict(invoice_rows=[invoice()], settlement_policy={})
        before = deepcopy((material, finished_rows, context))
        build_component_commercial_preview(material, finished_rows, context)
        self.assertEqual((material, finished_rows, context), before)


if __name__ == "__main__":
    unittest.main()
