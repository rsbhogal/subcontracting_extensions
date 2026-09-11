"""Focused J19B1D direction, revision and capability safety tests."""

import unittest

from subcontracting_extensions.component_commercial_classification import (
    _classification_entry_ready,
    _validate_event_stage,
    _validate_expected_revision,
    attach_decision_capabilities,
)


class Projection(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class CapabilityAPI:
    def get_doc(self, doctype, name):
        class Lot(Projection):
            def has_permission(self, permission):
                return True
        return Lot(docstatus=0, settlement_status="Draft")

    def get_single(self, doctype):
        return Projection(allowed_settlement_methods=[])

    def get_roles(self):
        return []


class TestCommercialDecisionConcurrency(unittest.TestCase):
    def projection(self, **changes):
        values = Projection(
            classification_revision=2,
            treatment_revision=1,
            last_decision_event="PLCD-00003",
        )
        values.update(changes)
        return values

    def test_current_projection_is_accepted(self):
        _validate_expected_revision(
            self.projection(), 2, 1, "PLCD-00003"
        )

    def test_stale_classification_revision_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "changed since"):
            _validate_expected_revision(
                self.projection(), 1, 1, "PLCD-00003"
            )

    def test_stale_treatment_revision_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "changed since"):
            _validate_expected_revision(
                self.projection(), 2, 0, "PLCD-00003"
            )

    def test_stale_last_event_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "changed since"):
            _validate_expected_revision(
                self.projection(), 2, 1, "PLCD-00002"
            )

    def test_new_scope_requires_explicit_zero_projection(self):
        _validate_expected_revision(Projection(), 0, 0, None)
        with self.assertRaisesRegex(ValueError, "changed since"):
            _validate_expected_revision(Projection(), 1, 0, None)

    def test_non_integer_revision_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must be integers"):
            _validate_expected_revision(self.projection(), "bad", 1, "PLCD-00003")

    def test_retained_fact_classification_ignores_only_policy_and_global_gate(self):
        report = {
            "commercial_review_permitted": False,
            "policy_issues": ["PROCESSOR_LOT_POLICY_DIFFERS_FROM_PURCHASE_ORDER"],
            "classification_issues": [], "material_disposition_issues": [],
            "legacy_evidence": [], "existing_documents": [],
        }
        row = {
            "commercial_decision_code": "RAW_MATERIAL_RETAINED_BY_PROCESSOR",
            "retained_material_classification_ready": True,
            "commercial_review_permitted": True,
        }
        self.assertTrue(_classification_entry_ready(report, row))

    def test_retained_fact_classification_fails_on_controlled_evidence_issue(self):
        report = {
            "classification_issues": [],
            "material_disposition_issues": ["MATERIAL_DISPOSITION_QUANTITY_STALE"],
            "legacy_evidence": [], "existing_documents": [],
        }
        row = {
            "commercial_decision_code": "RAW_MATERIAL_RETAINED_BY_PROCESSOR",
            "retained_material_classification_ready": True,
            "commercial_review_permitted": True,
        }
        self.assertFalse(_classification_entry_ready(report, row))

    def test_retained_capability_bypasses_policy_only_and_defers_treatment(self):
        row = {
            "commercial_decision_code": "RAW_MATERIAL_RETAINED_BY_PROCESSOR",
            "retained_material_classification_ready": True,
            "commercial_review_permitted": True,
            "persisted_classification": {"classification": "PROCESSOR_RESPONSIBLE",
                "variance_direction": "Shortage"},
        }
        report = {
            "processor_lot": "LOT", "commercial_review_permitted": False,
            "policy_issues": ["PROCESSOR_LOT_POLICY_DIFFERS_FROM_PURCHASE_ORDER"],
            "classification_issues": [], "material_disposition_issues": [],
            "legacy_evidence": [], "existing_documents": [],
            "components": [row], "finished_items": [],
        }
        result = attach_decision_capabilities(CapabilityAPI(), report, enabled=True)
        capability = result["components"][0]["decision_capability"]
        self.assertTrue(capability["classification_entry_available"])
        self.assertEqual(
            {choice["value"] for choice in capability["allowed_classifications"]},
            {"PROCESSOR_RESPONSIBLE", "COMPANY_RESPONSIBLE", "DISPUTED",
             "NO_COMMERCIAL_ACTION_REQUIRED"},
        )
        self.assertFalse(capability["treatment_selection_available"])
        self.assertEqual(capability["allowed_treatments"], [])

    def test_retained_treatment_endpoint_stage_is_blocked(self):
        with self.assertRaisesRegex(ValueError, "deferred beyond J19B2C"):
            _validate_event_stage("Treatment", "RAW_MATERIAL_RETAINED_BY_PROCESSOR")
        _validate_event_stage("Classification", "RAW_MATERIAL_RETAINED_BY_PROCESSOR")

    def test_only_dedicated_contract_may_open_retained_treatment_stage(self):
        _validate_event_stage(
            "Treatment", "RAW_MATERIAL_RETAINED_BY_PROCESSOR",
            allow_retained_material_treatment=True,
        )


if __name__ == "__main__":
    unittest.main()
