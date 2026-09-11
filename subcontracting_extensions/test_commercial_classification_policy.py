"""Pure J19B1C classification identity and compatibility tests."""

import unittest

from subcontracting_extensions.commercial_classification_policy import (
    CommercialClassificationError,
    allowed_classifications_for_evidence,
    canonical_scope,
    derive_variance_direction,
    make_event_key,
    make_scope_key,
    validate_classification,
    validate_classification_for_evidence,
    validate_reason,
    validate_treatment,
)


class TestCommercialClassificationPolicy(unittest.TestCase):
    def raw(self, **changes):
        row = dict(scope_type="Raw Material", processor_lot="LOT",
            sco_supplied_item="RM-A", sco_finished_item="FG-A",
            component_item="WIRE", stock_uom="Kg")
        row.update(changes)
        return row

    def finished(self, **changes):
        row = dict(scope_type="Finished Item", processor_lot="LOT",
            sco_finished_item="FG-A", purchase_order_item="PO-A",
            stock_uom="Kg")
        row.update(changes)
        return row

    def test_raw_identity_includes_component_and_uom(self):
        self.assertNotEqual(make_scope_key(self.raw()),
                            make_scope_key(self.raw(stock_uom="Units")))

    def test_finished_identity_does_not_include_descriptive_uom(self):
        self.assertEqual(make_scope_key(self.finished()),
                         make_scope_key(self.finished(stock_uom="Units")))

    def test_finished_identity_is_not_duplicated_per_component(self):
        self.assertEqual(make_scope_key(self.finished(component_item="A")),
                         make_scope_key(self.finished(component_item="B")))

    def test_raw_scope_rejects_missing_finished_lineage(self):
        with self.assertRaisesRegex(CommercialClassificationError, "sco_finished_item"):
            make_scope_key(self.raw(sco_finished_item=None))

    def test_finished_scope_clears_raw_only_fields(self):
        row = canonical_scope(self.finished(sco_supplied_item="WRONG", component_item="WRONG"))
        self.assertIsNone(row["sco_supplied_item"])
        self.assertIsNone(row["component_item"])

    def test_classification_is_direction_specific(self):
        self.assertEqual(validate_classification("Shortage", "PROCESSOR_RESPONSIBLE"),
                         "PROCESSOR_RESPONSIBLE")
        with self.assertRaises(CommercialClassificationError):
            validate_classification("Excess", "PROCESSOR_RESPONSIBLE")

    def test_no_action_evidence_rejects_liability_classification(self):
        self.assertEqual(
            validate_classification_for_evidence(
                "NO_RAW_MATERIAL_RECOVERY",
                "Shortage",
                "NO_COMMERCIAL_ACTION_REQUIRED",
            ),
            "NO_COMMERCIAL_ACTION_REQUIRED",
        )
        with self.assertRaisesRegex(CommercialClassificationError, "permits only"):
            validate_classification_for_evidence(
                "NO_PROCESSING_RECOVERY",
                "Shortage",
                "PROCESSOR_RESPONSIBLE",
            )

    def test_recovery_evidence_does_not_prejudge_responsibility(self):
        self.assertEqual(
            validate_classification_for_evidence(
                "PROCESSING_RECOVERY_RECOMMENDED",
                "Shortage",
                "DISPUTED",
            ),
            "DISPUTED",
        )

    def test_excess_requires_ownership_compatible_method(self):
        self.assertEqual(validate_treatment("Excess", "SUPPLIER_OWNED",
                         "PURCHASE_SUPPLIER_OWNED_EXCESS"),
                         "PURCHASE_SUPPLIER_OWNED_EXCESS")
        with self.assertRaises(CommercialClassificationError):
            validate_treatment("Excess", "COMPANY_OWNED",
                               "PURCHASE_SUPPLIER_OWNED_EXCESS")

    def test_unresolved_excess_allows_only_pending_method(self):
        self.assertEqual(validate_treatment("Excess", "MIXED_OR_UNRESOLVED",
                         "PENDING_OWNERSHIP_INVESTIGATION"),
                         "PENDING_OWNERSHIP_INVESTIGATION")
        with self.assertRaises(CommercialClassificationError):
            validate_treatment("Excess", "MIXED_OR_UNRESOLVED",
                               "RETURN_OR_REJECT_EXCESS")

    def test_company_responsible_shortage_has_no_recovery_method(self):
        with self.assertRaises(CommercialClassificationError):
            validate_treatment("Shortage", "COMPANY_RESPONSIBLE",
                               "COMMERCIAL_WAIVER")

    def test_waiver_is_treatment_for_processor_responsibility(self):
        self.assertEqual(validate_treatment("Shortage", "PROCESSOR_RESPONSIBLE",
                         "COMMERCIAL_WAIVER"), "COMMERCIAL_WAIVER")

    def test_reason_is_mandatory_and_trimmed(self):
        self.assertEqual(validate_reason("  reviewed evidence  "), "reviewed evidence")
        with self.assertRaises(CommercialClassificationError):
            validate_reason("  ")

    def test_event_key_is_stable_and_sequence_specific(self):
        scope = make_scope_key(self.raw())
        self.assertEqual(make_event_key(scope, 1), make_event_key(scope, 1))
        self.assertNotEqual(make_event_key(scope, 1), make_event_key(scope, 2))

    def test_direction_is_derived_from_authoritative_evidence(self):
        self.assertEqual(derive_variance_direction(
            "PROCESSING_RECOVERY_RECOMMENDED"), "Shortage")
        with self.assertRaisesRegex(CommercialClassificationError, "does not prove"):
            derive_variance_direction("REVIEW_COMPONENT_EVIDENCE")

    def test_no_action_evidence_exposes_only_no_action_classification(self):
        self.assertEqual(
            allowed_classifications_for_evidence("NO_RAW_MATERIAL_RECOVERY"),
            [{"value": "NO_COMMERCIAL_ACTION_REQUIRED",
              "label": "No Commercial Action Required"}],
        )

    def test_recovery_evidence_exposes_only_shortage_choices(self):
        values = {row["value"] for row in allowed_classifications_for_evidence(
            "PROCESSING_RECOVERY_RECOMMENDED")}
        self.assertIn("PROCESSOR_RESPONSIBLE", values)
        self.assertNotIn("COMPANY_OWNED", values)

    def test_retained_material_proves_shortage_without_prejudging_responsibility(self):
        self.assertEqual(
            derive_variance_direction("RAW_MATERIAL_RETAINED_BY_PROCESSOR"),
            "Shortage",
        )
        values = {row["value"] for row in allowed_classifications_for_evidence(
            "RAW_MATERIAL_RETAINED_BY_PROCESSOR")}
        self.assertEqual(values, {
            "PROCESSOR_RESPONSIBLE", "COMPANY_RESPONSIBLE", "DISPUTED",
            "NO_COMMERCIAL_ACTION_REQUIRED",
        })
        self.assertNotIn("PENDING_INVESTIGATION", values)

    def test_retained_material_rejects_pending_classification(self):
        with self.assertRaisesRegex(CommercialClassificationError, "retained material"):
            validate_classification_for_evidence(
                "RAW_MATERIAL_RETAINED_BY_PROCESSOR",
                "Shortage",
                "PENDING_INVESTIGATION",
            )


if __name__ == "__main__":
    unittest.main()
