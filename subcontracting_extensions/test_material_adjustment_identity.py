"""J15A pure component-attribution tests; no Frappe or records."""
import unittest

from subcontracting_extensions.material_adjustment_identity import resolve_sco_component


class TestMaterialAdjustmentIdentity(unittest.TestCase):
    def setUp(self):
        self.items = [
            dict(name="FG-A", item_code="Finished A", stock_uom="Kg"),
            dict(name="FG-B", item_code="Finished B", stock_uom="Units"),
        ]
        self.rows = [
            dict(name="RM-A", reference_name="FG-A", main_item_code="Finished A",
                 rm_item_code="Wire", stock_uom="Kg"),
            dict(name="RM-B", reference_name="FG-B", main_item_code="Finished B",
                 rm_item_code="Blank", stock_uom="Units"),
        ]

    def resolve(self, **values):
        return resolve_sco_component(self.items, self.rows, **values)

    def test_v2_allocation_finished_row_resolves_exact_component(self):
        self.assertEqual(self.resolve(finished_row="FG-B")["name"], "RM-B")

    def test_account_identity_resolves_target_sco_row(self):
        self.assertEqual(self.resolve(processed_item="Finished A", processed_uom="Kg",
            component_item="Wire", account_uom="Kg")["name"], "RM-A")

    def test_explicit_row_is_validated_against_all_identity_fields(self):
        with self.assertRaisesRegex(ValueError, "no match"):
            self.resolve(explicit="RM-A", processed_item="Finished B", processed_uom="Units",
                         component_item="Blank", account_uom="Units")

    def test_missing_explicit_row_never_falls_back(self):
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.resolve(explicit="MISSING", component_item="Wire", account_uom="Kg")

    def test_repeated_component_is_disambiguated_by_finished_row(self):
        self.rows[1].update(rm_item_code="Wire", stock_uom="Kg")
        self.assertEqual(self.resolve(finished_row="FG-A", component_item="Wire",
                                      account_uom="Kg")["name"], "RM-A")

    def test_repeated_component_without_finished_identity_is_ambiguous(self):
        self.rows[1].update(rm_item_code="Wire", stock_uom="Kg")
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.resolve(component_item="Wire", account_uom="Kg")

    def test_duplicate_finished_row_is_rejected(self):
        self.items.append(dict(self.items[0]))
        with self.assertRaisesRegex(ValueError, "duplicate SCO finished"):
            self.resolve()

    def test_missing_finished_row_identity_is_rejected(self):
        self.items[0]["name"] = ""
        with self.assertRaisesRegex(ValueError, "Missing or duplicate SCO finished"):
            self.resolve()

    def test_duplicate_component_row_is_rejected(self):
        self.rows.append(dict(self.rows[0]))
        with self.assertRaisesRegex(ValueError, "duplicate SCO component"):
            self.resolve()

    def test_missing_component_row_identity_is_rejected(self):
        self.rows[0]["name"] = None
        with self.assertRaisesRegex(ValueError, "Missing or duplicate SCO component"):
            self.resolve()

    def test_invalid_finished_lineage_is_rejected(self):
        self.rows[0]["main_item_code"] = "Wrong"
        with self.assertRaisesRegex(ValueError, "lineage is invalid"):
            self.resolve(explicit="RM-A")

    def test_no_matching_identity_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "no match"):
            self.resolve(component_item="Other", account_uom="Kg")


if __name__ == "__main__":
    unittest.main()
