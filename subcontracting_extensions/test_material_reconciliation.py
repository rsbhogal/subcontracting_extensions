"""J13A pure fixtures: never connects to a site or changes records."""

import unittest
from copy import deepcopy

from subcontracting_extensions.material_reconciliation import reconcile_material


class TestMaterialReconciliation(unittest.TestCase):
    def setUp(self):
        self.sco = dict(name="SCO", company="Company", supplier="Supplier",
            supplier_warehouse="Processor", items=[], supplied_items=[])
        self.movements, self.receipts, self.consumptions = [], [], []
        self.add_component("A", "Wire", "Kg", 500)
        self.add_component("B", "Blank", "Units", 100)

    def add_component(self, key, material, uom, qty):
        self.sco["items"].append(dict(name="FG-" + key, item_code="Finished-" + key))
        self.sco["supplied_items"].append(dict(name="RM-" + key, reference_name="FG-" + key,
            main_item_code="Finished-" + key, rm_item_code=material, stock_uom=uom,
            supplied_qty=qty, consumed_qty=qty, returned_qty=0))
        self.movements.append(dict(name="STE-" + key, parent="STE", docstatus=1,
            subcontracting_order="SCO", company="Company", supplier="Supplier",
            item_code=material, stock_uom=uom, stock_qty=qty, s_warehouse="Factory", t_warehouse="Processor"))
        self.receipts.append(dict(name="SCR-" + key, parent="SCR", docstatus=1,
            subcontracting_order="SCO", subcontracting_order_item="FG-" + key, item_code="Finished-" + key))
        self.consumptions.append(dict(name="CON-" + key, parent="SCR", docstatus=1,
            subcontracting_order="SCO", reference_name="SCR-" + key,
            rm_item_code=material, stock_uom=uom, consumed_qty=qty))

    def report(self, adjustments=()):
        return reconcile_material(self.sco, self.movements, self.receipts, self.consumptions, adjustments)

    def return_material(self, qty=10, docstatus=1):
        self.sco["supplied_items"][0].update(consumed_qty=500-qty, returned_qty=qty)
        self.consumptions[0]["consumed_qty"] = 500-qty
        row = deepcopy(self.movements[0])
        row.update(name="RETURN-A", parent="RETURN", stock_qty=qty, docstatus=docstatus,
                   s_warehouse="Processor", t_warehouse="Factory")
        self.movements.append(row)

    def test_balanced_mixed_uoms_have_no_combined_quantity(self):
        report = self.report()
        self.assertTrue(report["material_balanced"])
        self.assertEqual([r["transferred_qty"] for r in report["components"]], [500, 100])
        self.assertNotIn("total_qty", report)
        self.assertFalse(report["settlement_enabled"])

    def test_remaining_material_is_not_automatically_recovery(self):
        self.sco["supplied_items"][0]["consumed_qty"] = 490
        self.consumptions[0]["consumed_qty"] = 490
        report = self.report()
        self.assertTrue(report["evidence_consistent"])
        self.assertFalse(report["material_balanced"])
        self.assertEqual(report["components"][0]["remaining_qty"], 10)
        self.assertIn("MATERIAL_BALANCE_REMAINS", report["issues"])
        self.assertFalse(report["settlement_enabled"])

    def test_submitted_return_reconciles_without_consumption_inflation(self):
        self.return_material()
        report = self.report()
        self.assertTrue(report["material_balanced"])
        self.assertEqual(report["components"][0]["evidenced_consumed_qty"], 490)
        self.assertEqual(report["components"][0]["evidenced_returned_qty"], 10)

    def test_draft_return_does_not_clear_native_return(self):
        self.return_material(docstatus=0)
        self.assertIn("RETURN_EVIDENCE_MISMATCH", self.report()["issues"])

    def test_cancelled_return_does_not_clear_native_return(self):
        self.return_material(docstatus=2)
        self.assertIn("RETURN_EVIDENCE_MISMATCH", self.report()["issues"])

    def test_wrong_return_warehouse_is_not_accepted(self):
        self.return_material()
        self.movements[-1]["s_warehouse"] = "Other Processor"
        self.assertIn("UNSUPPORTED_MOVEMENT_DIRECTION", self.report()["issues"])

    def test_over_return_preserves_negative_balance(self):
        self.return_material()
        self.sco["supplied_items"][0]["returned_qty"] = 11
        self.movements[-1]["stock_qty"] = 11
        report = self.report()
        self.assertEqual(report["components"][0]["remaining_qty"], -1)
        self.assertIn("NEGATIVE_MATERIAL_BALANCE", report["issues"])

    def test_duplicate_component_identity_rejected(self):
        row = deepcopy(self.sco["supplied_items"][0])
        row["name"] = "DIFFERENT-NAME"
        self.sco["supplied_items"].append(row)
        with self.assertRaisesRegex(ValueError, "duplicate component"):
            self.report()

    def test_same_component_for_two_finished_items_is_ambiguous_transfer(self):
        self.add_component("C", "Wire", "Kg", 200)
        report = self.report()
        self.assertIn("AMBIGUOUS_TRANSFER_ATTRIBUTION", report["issues"])
        self.assertEqual(report["components"][0]["transferred_qty"], 0)
        self.assertEqual(report["components"][2]["transferred_qty"], 0)
        self.assertEqual(report["components"][0]["evidenced_consumed_qty"], 500)
        self.assertEqual(report["components"][2]["evidenced_consumed_qty"], 200)

    def test_explicit_links_disambiguate_repeated_component(self):
        self.add_component("C", "Wire", "Kg", 200)
        for movement, component in zip(self.movements, self.sco["supplied_items"]):
            movement["sco_rm_detail"] = component["name"]
        report = self.report()
        self.assertTrue(report["material_balanced"])
        self.assertEqual([r["transferred_qty"] for r in report["components"]], [500, 100, 200])

    def test_invalid_explicit_link_never_uses_unique_item_fallback(self):
        self.movements[0]["sco_rm_detail"] = "OTHER-SCO-ROW"
        report = self.report()
        self.assertIn("INVALID_EXPLICIT_COMPONENT_LINK", report["issues"])
        self.assertEqual(report["components"][0]["transferred_qty"], 0)
        self.assertFalse(report["material_balanced"])

    def test_explicit_link_to_wrong_item_is_rejected(self):
        self.movements[0]["sco_rm_detail"] = "RM-B"
        report = self.report()
        self.assertIn("EXPLICIT_COMPONENT_ITEM_UOM_MISMATCH", report["issues"])
        self.assertEqual(report["components"][0]["transferred_qty"], 0)
        self.assertFalse(report["components"][1]["evidence_consistent"])

    def test_explicit_link_with_wrong_uom_is_rejected(self):
        self.movements[0].update(sco_rm_detail="RM-A", stock_uom="Units")
        self.assertIn("EXPLICIT_COMPONENT_ITEM_UOM_MISMATCH", self.report()["issues"])

    def test_explicit_link_cannot_override_wrong_sco_header(self):
        self.movements[0].update(sco_rm_detail="RM-A", subcontracting_order="OTHER")
        report = self.report()
        self.assertIn("MOVEMENT_HEADER_MISMATCH", report["issues"])
        self.assertEqual(report["components"][0]["transferred_qty"], 0)

    def test_empty_explicit_link_preserves_unique_legacy_matching(self):
        for row in self.movements:
            row["sco_rm_detail"] = ""
        self.assertTrue(self.report()["material_balanced"])

    def test_explicit_return_link_disambiguates_repeated_component(self):
        self.add_component("C", "Wire", "Kg", 200)
        for movement, component in zip(self.movements, self.sco["supplied_items"]):
            movement["sco_rm_detail"] = component["name"]
        self.return_material()
        report = self.report()
        self.assertTrue(report["material_balanced"])
        self.assertEqual(report["components"][0]["evidenced_returned_qty"], 10)
        self.assertEqual(report["components"][2]["evidenced_returned_qty"], 0)

    def test_invalid_explicit_return_link_is_not_silently_reassigned(self):
        self.return_material()
        self.movements[-1]["sco_rm_detail"] = "OTHER-SCO-ROW"
        report = self.report()
        self.assertIn("INVALID_EXPLICIT_COMPONENT_LINK", report["issues"])
        self.assertEqual(report["components"][0]["evidenced_returned_qty"], 0)

    def test_duplicate_movement_is_not_counted_twice(self):
        self.movements.append(deepcopy(self.movements[0]))
        with self.assertRaisesRegex(ValueError, "duplicate movement"):
            self.report()

    def test_duplicate_consumption_is_not_counted_twice(self):
        self.consumptions.append(deepcopy(self.consumptions[0]))
        with self.assertRaisesRegex(ValueError, "duplicate consumption"):
            self.report()

    def test_consumption_uses_scr_row_not_just_item_code(self):
        self.consumptions[0]["reference_name"] = "WRONG"
        self.assertIn("CONSUMPTION_LINEAGE_MISMATCH", self.report()["issues"])

    def test_same_scr_can_contain_another_lot_without_cross_attribution(self):
        self.consumptions[0]["subcontracting_order"] = "OTHER-SCO"
        self.assertIn("CONSUMPTION_LINEAGE_MISMATCH", self.report()["issues"])

    def test_wrong_movement_supplier_is_rejected(self):
        self.movements[0]["supplier"] = "Other"
        self.assertIn("MOVEMENT_HEADER_MISMATCH", self.report()["issues"])

    def test_stock_qty_not_transaction_qty_controls_reconciliation(self):
        self.movements[0].update(qty=0.5, uom="Ton", stock_qty=500)
        self.assertTrue(self.report()["material_balanced"])

    def test_missing_stock_qty_does_not_fall_back_to_qty(self):
        self.movements[0]["qty"] = 500
        del self.movements[0]["stock_qty"]
        with self.assertRaises(ValueError):
            self.report()

    def test_nonfinite_qty_rejected(self):
        self.movements[0]["stock_qty"] = float("nan")
        with self.assertRaises(ValueError):
            self.report()

    def test_small_balances_are_not_clamped_away(self):
        self.sco["supplied_items"][0]["consumed_qty"] = "499.999999"
        self.consumptions[0]["consumed_qty"] = "499.999999"
        self.assertEqual(self.report()["components"][0]["remaining_qty"], 0.000001)

    def test_adjustments_are_not_applied_to_multiple_component_rows(self):
        report = self.report(adjustments=[{"component_item": "Wire", "account_qty": 10}])
        self.assertIn("ADJUSTMENT_ATTRIBUTION_REQUIRES_REVIEW", report["issues"])
        self.assertFalse(report["material_balanced"])

    def test_no_components_cannot_report_balanced(self):
        self.sco["supplied_items"] = []
        self.assertFalse(self.report()["material_balanced"])

    def test_input_documents_are_unchanged(self):
        before = deepcopy((self.sco, self.movements, self.receipts, self.consumptions))
        self.report()
        self.assertEqual(before, (self.sco, self.movements, self.receipts, self.consumptions))


if __name__ == "__main__":
    unittest.main()
