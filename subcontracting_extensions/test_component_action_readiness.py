"""J17 pure component action-readiness tests; no Frappe or document writes."""

import unittest
from copy import deepcopy

from subcontracting_extensions.component_action_readiness import (
    assess_component_action_readiness,
)


def component(**changes):
    row = dict(
        sco_supplied_item="SCO-RM-A",
        sco_finished_item="SCO-FG-A",
        component_item="Wire",
        stock_uom="Kg",
        evidence_consistent=True,
        material_settlement_eligible=True,
    )
    row.update(changes)
    return row


def report(*rows):
    return dict(
        processor_lot="LOT-A",
        subcontracting_order="SCO-A",
        components=list(rows),
        evidence_consistent=True,
        settlement_enabled=False,
    )


class TestComponentActionReadiness(unittest.TestCase):
    def test_exact_component_identity_is_preserved(self):
        result = assess_component_action_readiness(report(component()), can_write=True)
        row = result["components"][0]
        self.assertEqual(
            row["component_action_identity"],
            {
                "processor_lot": "LOT-A",
                "subcontracting_order": "SCO-A",
                "sco_supplied_item": "SCO-RM-A",
                "sco_finished_item": "SCO-FG-A",
                "component_item": "Wire",
                "stock_uom": "Kg",
            },
        )
        self.assertTrue(row["component_action_identity_complete"])

    def test_same_item_rows_remain_distinct_by_exact_sco_row(self):
        result = assess_component_action_readiness(
            report(
                component(sco_supplied_item="SCO-RM-A"),
                component(sco_supplied_item="SCO-RM-B", sco_finished_item="SCO-FG-B"),
            ),
            can_write=True,
        )
        identities = [
            row["component_action_identity"] for row in result["components"]
        ]
        self.assertNotEqual(identities[0], identities[1])

    def test_mixed_uoms_have_independent_identity_and_no_total(self):
        result = assess_component_action_readiness(
            report(
                component(),
                component(
                    sco_supplied_item="SCO-RM-B",
                    sco_finished_item="SCO-FG-B",
                    component_item="Blank",
                    stock_uom="Units",
                ),
            ),
            can_write=True,
        )
        self.assertEqual(
            [row["component_action_identity"]["stock_uom"] for row in result["components"]],
            ["Kg", "Units"],
        )
        self.assertNotIn("total_qty", result)

    def test_write_permission_allows_review_but_never_an_action(self):
        row = assess_component_action_readiness(
            report(component()), can_write=True
        )["components"][0]
        self.assertTrue(row["commercial_review_permitted"])
        self.assertEqual(row["component_action_code"], "AWAIT_COMMERCIAL_POLICY")
        self.assertFalse(row["component_action_available"])
        self.assertFalse(row["commercial_document_authorized"])
        self.assertFalse(row["lot_closure_authorized"])

    def test_read_only_user_cannot_enter_future_commercial_review(self):
        row = assess_component_action_readiness(
            report(component()), can_write=False
        )["components"][0]
        self.assertFalse(row["commercial_review_permitted"])
        self.assertEqual(row["component_action_code"], "READ_ONLY_ACCESS")

    def test_inconsistent_evidence_fails_closed_before_permission(self):
        row = assess_component_action_readiness(
            report(component(evidence_consistent=False)), can_write=True
        )["components"][0]
        self.assertEqual(row["component_action_code"], "REVIEW_COMPONENT_EVIDENCE")
        self.assertFalse(row["commercial_review_permitted"])

    def test_unaccounted_material_fails_closed(self):
        row = assess_component_action_readiness(
            report(component(material_settlement_eligible=False)), can_write=True
        )["components"][0]
        self.assertEqual(row["component_action_code"], "ACCOUNT_REMAINING_MATERIAL")
        self.assertFalse(row["commercial_review_permitted"])

    def test_missing_exact_identity_fails_closed(self):
        row = assess_component_action_readiness(
            report(component(sco_supplied_item=None)), can_write=True
        )["components"][0]
        self.assertEqual(row["component_action_code"], "REVIEW_COMPONENT_IDENTITY")
        self.assertFalse(row["component_action_identity_complete"])

    def test_policy_and_every_mutating_outcome_remain_disabled(self):
        result = assess_component_action_readiness(report(component()), can_write=True)
        self.assertEqual(result["commercial_policy_status"], "DEFERRED")
        self.assertFalse(result["commercial_document_creation_enabled"])
        self.assertFalse(result["commercial_document_authorized"])
        self.assertFalse(result["lot_closure_authorized"])

    def test_inputs_are_unchanged(self):
        source = report(component())
        before = deepcopy(source)
        assess_component_action_readiness(source, can_write=True)
        self.assertEqual(source, before)


if __name__ == "__main__":
    unittest.main()
