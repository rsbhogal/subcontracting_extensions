"""J16 pure material eligibility tests; no Frappe or document writes."""

import unittest
from copy import deepcopy

from subcontracting_extensions.material_settlement_eligibility import assess_material_settlement


def component(**changes):
    row = dict(component_item="Wire", stock_uom="Kg", physical_remaining_qty=0,
        applied_credit_qty=0, unaccounted_remaining_qty=0, evidence_consistent=True, issues=[])
    row.update(changes)
    return row


class TestMaterialSettlementEligibility(unittest.TestCase):
    def report(self, *rows, consistent=True):
        return dict(components=list(rows), evidence_consistent=consistent, issues=[], settlement_enabled=False)

    def test_physically_reconciled_component_needs_no_material_action(self):
        result = assess_material_settlement(self.report(component()))
        self.assertTrue(result["material_settlement_eligible"])
        self.assertEqual(result["components"][0]["material_next_action"], "NONE_PHYSICALLY_RECONCILED")
        self.assertEqual(result["material_next_action"], "REVIEW_RECEIPT_AND_INVOICING")

    def test_submitted_credit_can_account_without_erasing_physical_balance(self):
        result = assess_material_settlement(self.report(component(
            physical_remaining_qty=116, applied_credit_qty=116, unaccounted_remaining_qty=0)))
        row = result["components"][0]
        self.assertTrue(row["material_settlement_eligible"])
        self.assertEqual(row["material_next_action"], "NONE_CREDIT_ACCOUNTED")
        self.assertEqual(row["physical_remaining_qty"], 116)

    def test_partial_credit_requires_component_action(self):
        result = assess_material_settlement(self.report(component(
            physical_remaining_qty=10, applied_credit_qty=4, unaccounted_remaining_qty=6)))
        self.assertFalse(result["material_settlement_eligible"])
        self.assertEqual(result["material_next_action"], "ACCOUNT_REMAINING_MATERIAL")

    def test_mixed_uom_components_keep_independent_actions(self):
        result = assess_material_settlement(self.report(component(), component(component_item="Blank",
            stock_uom="Units", physical_remaining_qty=2, unaccounted_remaining_qty=2)))
        self.assertEqual([row["material_settlement_eligible"] for row in result["components"]], [True, False])
        self.assertNotIn("total_qty", result)

    def test_inconsistent_evidence_fails_closed(self):
        result = assess_material_settlement(self.report(component(evidence_consistent=False), consistent=False))
        self.assertEqual(result["material_next_action"], "REVIEW_MATERIAL_EVIDENCE")
        self.assertFalse(result["material_settlement_eligible"])

    def test_over_credit_fails_closed(self):
        result = assess_material_settlement(self.report(component(
            physical_remaining_qty=5, applied_credit_qty=6, unaccounted_remaining_qty=-1)))
        self.assertEqual(result["components"][0]["material_next_action"], "REVIEW_COMPONENT_EVIDENCE")

    def test_empty_components_are_not_eligible(self):
        self.assertFalse(assess_material_settlement(self.report())["material_settlement_eligible"])

    def test_unsupported_zero_balance_with_credit_fails_closed(self):
        result = assess_material_settlement(self.report(component(applied_credit_qty=1)))
        self.assertEqual(result["components"][0]["material_next_action"], "REVIEW_COMPONENT_EVIDENCE")

    def test_inputs_are_unchanged(self):
        report = self.report(component(physical_remaining_qty=10, unaccounted_remaining_qty=10))
        before = deepcopy(report)
        assess_material_settlement(report)
        self.assertEqual(report, before)


if __name__ == "__main__":
    unittest.main()
