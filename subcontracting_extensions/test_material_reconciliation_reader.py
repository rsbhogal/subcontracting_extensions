"""J13C adapter tests using an in-memory document store; no Frappe/site needed."""

import unittest
from copy import deepcopy

from subcontracting_extensions.material_reconciliation_reader import _read_material_position


class Doc(dict):
    def __getattr__(self, key):
        # Python copy/pickle probes optional special methods with hasattr.
        # Missing protocol hooks must raise AttributeError, not return None.
        if key.startswith("__"):
            raise AttributeError(key)
        return self.get(key)

    def as_dict(self):
        return deepcopy(dict(self))

    def check_permission(self, permission):
        if self.get("denied"):
            raise PermissionError("Evidence unavailable")


class Store:
    """Only read operations exist; unexpected writes fail immediately."""
    def __init__(self):
        self.docs = {}
        self.queries = []
        self.reads = []

    def add(self, doctype, name, **values):
        doc = Doc(name=name, docstatus=1, **values)
        self.docs[doctype, name] = doc
        return doc

    def get_doc(self, doctype, name):
        self.reads.append((doctype, name))
        return self.docs[doctype, name]

    def get_all(self, doctype, filters, fields, limit_page_length):
        self.queries.append((doctype, deepcopy(filters)))
        assert limit_page_length == 0
        children = {
            "Stock Entry Detail": ("Stock Entry", "items"),
            "Subcontracting Receipt Item": ("Subcontracting Receipt", "items"),
            "Subcontracting Receipt Supplied Item": ("Subcontracting Receipt", "supplied_items"),
            "Processor Lot Receipt Allocation": ("Processor Lot Receipt", "lot_allocations"),
        }
        rows = []
        for (dt, name), doc in self.docs.items():
            if dt == doctype:
                rows.append(doc)
            if doctype in children and dt == children[doctype][0]:
                parentfield = children[doctype][1]
                rows.extend(dict(row, parent=name, parenttype=dt, parentfield=parentfield,
                                 docstatus=doc.docstatus) for row in doc.get(parentfield, []))
        def matches(row):
            for key, expected in filters.items():
                value = row.get(key)
                if isinstance(expected, list):
                    operator, expected = expected
                    if operator == "in" and value not in expected:
                        return False
                    if operator == "!=" and value == expected:
                        return False
                elif value != expected:
                    return False
            return True
        return [Doc({field: row.get(field) for field in fields}) for row in rows if matches(row)]


class TestMaterialReconciliationReader(unittest.TestCase):
    def setUp(self):
        self.api = Store()
        self.header = dict(company="Company", supplier="Supplier", supplier_warehouse="Processor")
        self.lot = self.api.add("Processor Lot", "LOT", subcontracting_order="SCO", **self.header)
        self.sco = self.api.add("Subcontracting Order", "SCO", items=[], supplied_items=[], **self.header)
        self.scr = self.api.add("Subcontracting Receipt", "SCR", items=[], supplied_items=[], **self.header)
        self.add_component("A", "Wire", "Kg", 500)
        self.add_component("B", "Blank", "Units", 100)

    def add_component(self, key, item, uom, qty):
        self.sco["items"].append(Doc(name="FG-" + key, item_code="Finished-" + key))
        self.sco["supplied_items"].append(Doc(name="RM-" + key, reference_name="FG-" + key,
            main_item_code="Finished-" + key, rm_item_code=item, stock_uom=uom,
            supplied_qty=qty, returned_qty=0, consumed_qty=qty, total_supplied_qty=qty))
        self.api.add("Stock Entry", "STE-" + key, subcontracting_order="SCO", is_return=0,
            purpose="Send to Subcontractor", items=[Doc(name="SED-" + key, sco_rm_detail="RM-" + key,
                item_code=item, stock_uom=uom, transfer_qty=qty, qty=qty / 1000,
                s_warehouse="Factory", t_warehouse="Processor")], **self.header)
        self.scr["items"].append(Doc(name="SCR-" + key, subcontracting_order="SCO",
            subcontracting_order_item="FG-" + key, item_code="Finished-" + key))
        self.scr["supplied_items"].append(Doc(name="CON-" + key, subcontracting_order="SCO",
            reference_name="SCR-" + key, rm_item_code=item, stock_uom=uom, consumed_qty=qty))

    def report(self):
        return _read_material_position(self.api, "LOT")

    def return_material(self):
        self.sco["supplied_items"][0].update(returned_qty=10, consumed_qty=490, total_supplied_qty=490)
        self.scr["supplied_items"][0]["consumed_qty"] = 490
        source = self.api.docs["Stock Entry", "STE-A"]
        row = deepcopy(source["items"][0])
        row.update(name="RETURN-ROW", transfer_qty=10, s_warehouse="Processor", t_warehouse="Factory")
        return self.api.add("Stock Entry", "RETURN", subcontracting_order="SCO", is_return=1,
                            purpose="Material Transfer", items=[row], **self.header)

    def test_document_copy_protocol_has_no_fabricated_special_hooks(self):
        doc = Doc(name="TEST", nested=[Doc(value=1)])
        self.assertFalse(hasattr(doc, "__setstate__"))
        self.assertFalse(hasattr(doc, "__deepcopy__"))
        cloned = deepcopy(doc)
        self.assertEqual(cloned, doc)
        cloned["nested"][0]["value"] = 2
        self.assertEqual(doc["nested"][0]["value"], 1)

    def test_mixed_uom_complete_and_stock_quantity(self):
        report = self.report()
        self.assertTrue(report["material_balanced"])
        self.assertEqual([r["transferred_qty"] for r in report["components"]], [500, 100])
        self.assertNotIn("total_qty", report)
        self.assertFalse(report["settlement_enabled"])

    def test_return_counted_once_despite_net_native_field(self):
        self.return_material()
        report = self.report()
        self.assertTrue(report["material_balanced"])
        self.assertEqual(report["components"][0]["supplied_qty"], 500)
        self.assertEqual(report["components"][0]["remaining_qty"], 0)
        self.assertEqual(report["components"][0]["evidenced_returned_qty"], 10)

    def test_draft_and_cancelled_returns_excluded(self):
        returned = self.return_material()
        for status in (0, 2):
            returned["docstatus"] = status
            report = self.report()
            self.assertIn("RETURN_EVIDENCE_MISMATCH", report["issues"])
            self.assertEqual(report["components"][0]["evidenced_returned_qty"], 0)

    def test_draft_and_cancelled_transfers_excluded(self):
        for status in (0, 2):
            self.api.docs["Stock Entry", "STE-A"]["docstatus"] = status
            self.assertIn("SUPPLY_EVIDENCE_MISMATCH", self.report()["issues"])

    def test_cancelled_scr_excluded(self):
        self.scr["docstatus"] = 2
        self.assertIn("CONSUMPTION_EVIDENCE_MISMATCH", self.report()["issues"])

    def test_repeated_component_uses_exact_links(self):
        self.add_component("C", "Wire", "Kg", 200)
        self.assertTrue(self.report()["material_balanced"])

    def test_invalid_explicit_component_never_falls_back(self):
        self.api.docs["Stock Entry", "STE-A"]["items"][0]["sco_rm_detail"] = "BAD"
        self.assertIn("INVALID_EXPLICIT_COMPONENT_LINK", self.report()["issues"])

    def test_wrong_header_still_discovered_by_component_link(self):
        self.api.docs["Stock Entry", "STE-A"]["subcontracting_order"] = "OTHER"
        self.assertIn("MOVEMENT_HEADER_MISMATCH", self.report()["issues"])

    def test_missing_stock_quantity_fails_without_transaction_fallback(self):
        del self.api.docs["Stock Entry", "STE-A"]["items"][0]["transfer_qty"]
        with self.assertRaises(ValueError):
            self.report()

    def test_return_flag_must_agree_with_direction(self):
        self.return_material()["is_return"] = 0
        report = self.report()
        self.assertIn("MOVEMENT_RETURN_FLAG_MISMATCH", report["issues"])
        self.assertFalse(report["material_balanced"])

    def test_wrong_warehouse_rejected(self):
        self.return_material()["items"][0]["s_warehouse"] = "Other"
        self.assertIn("UNSUPPORTED_MOVEMENT_DIRECTION", self.report()["issues"])

    def test_native_net_field_is_cross_checked(self):
        self.sco["supplied_items"][0]["total_supplied_qty"] = 400
        self.assertIn("NATIVE_NET_SUPPLY_MISMATCH", self.report()["issues"])

    def test_combined_scr_does_not_import_other_sco_consumption(self):
        self.scr["items"].append(Doc(name="OTHER-FG", subcontracting_order="OTHER",
            subcontracting_order_item="OTHER-ROW", item_code="Other"))
        self.scr["supplied_items"].append(Doc(name="OTHER-CON", subcontracting_order="OTHER",
            reference_name="OTHER-FG", rm_item_code="Wire", stock_uom="Kg", consumed_qty=999))
        self.assertTrue(self.report()["material_balanced"])
        self.assertEqual(len(self.report()["consumptions"]), 2)

    def test_orphan_consumption_is_not_silently_omitted(self):
        self.api.add("Subcontracting Receipt", "ORPHAN", items=[], supplied_items=[Doc(
            name="BAD", subcontracting_order="SCO", reference_name="MISSING", rm_item_code="Wire",
            stock_uom="Kg", consumed_qty=1)], **self.header)
        self.assertIn("CONSUMPTION_LINEAGE_MISMATCH", self.report()["issues"])

    def test_wrong_scr_header_blocks_report(self):
        self.scr["supplier"] = "Other"
        self.assertIn("SCR_HEADER_MISMATCH", self.report()["issues"])

    def test_scr_return_is_review_only(self):
        self.scr["is_return"] = 1
        self.assertIn("SCR_RETURN_REQUIRES_REVIEW", self.report()["issues"])

    def test_negative_consumption_is_review_only(self):
        self.scr["supplied_items"][0]["consumed_qty"] = -1
        self.assertIn("NEGATIVE_CONSUMPTION_REQUIRES_REVIEW", self.report()["issues"])

    def test_denied_lot_prevents_evidence_queries(self):
        self.lot["denied"] = True
        with self.assertRaises(PermissionError):
            self.report()
        self.assertEqual(self.api.queries, [])

    def test_denied_evidence_aborts_instead_of_partial_report(self):
        for key in (("Stock Entry", "STE-A"), ("Subcontracting Receipt", "SCR")):
            self.api.docs[key]["denied"] = True
            with self.assertRaises(PermissionError):
                self.report()
            del self.api.docs[key]["denied"]

    def test_lot_sco_mismatch_rejected(self):
        self.lot["supplier_warehouse"] = "Other"
        with self.assertRaises(ValueError):
            self.report()

    def test_draft_sco_rejected(self):
        self.sco["docstatus"] = 0
        with self.assertRaises(ValueError):
            self.report()

    def test_cancelled_lot_rejected(self):
        self.lot["docstatus"] = 2
        with self.assertRaises(ValueError):
            self.report()

    def test_credit_adjustments_block_without_netting(self):
        doc = self.api.add("Processor Material Account Entry", "PMA", subcontracting_order="SCO")
        for status in (0, 1):
            doc["docstatus"] = status
            report = self.report()
            expected = ("DRAFT_ADJUSTMENT_REQUIRES_REVIEW" if status == 0
                        else "ADJUSTMENT_ATTRIBUTION_REQUIRES_REVIEW")
            self.assertIn(expected, report["issues"])
            self.assertFalse(report["material_balanced"])
        doc["docstatus"] = 2
        self.assertTrue(self.report()["material_balanced"])

    def add_applied_credit(self, **changes):
        docstatus = changes.pop("docstatus", 1)
        values = dict(entry_type="Credit Applied", account_direction="Debit", account_qty=10,
            subcontracting_order="SCO", sco_supplied_item="RM-A", principal_component="Wire",
            account_uom="Kg", processor_lot="LOT", is_reversed=0)
        values.update(changes)
        doc = self.api.add("Processor Material Account Entry", "PMA", **values)
        doc["docstatus"] = docstatus
        return doc

    def test_submitted_exact_credit_is_attributed_without_changing_physical_remaining(self):
        self.sco["supplied_items"][0]["consumed_qty"] = 490
        self.scr["supplied_items"][0]["consumed_qty"] = 490
        self.add_applied_credit()
        report = self.report()
        row = report["components"][0]
        self.assertEqual(row["physical_remaining_qty"], 10)
        self.assertEqual(row["applied_credit_qty"], 10)
        self.assertEqual(row["unaccounted_remaining_qty"], 0)
        self.assertFalse(report["material_balanced"])
        self.assertTrue(report["material_accounted"])

    def test_draft_credit_remains_review_only(self):
        self.add_applied_credit(docstatus=0)
        report = self.report()
        self.assertEqual(report["components"][0]["applied_credit_qty"], 0)
        self.assertIn("DRAFT_ADJUSTMENT_REQUIRES_REVIEW", report["issues"])

    def test_reversed_submitted_credit_is_excluded(self):
        self.add_applied_credit(is_reversed=1)
        report = self.report()
        self.assertEqual(report["components"][0]["applied_credit_qty"], 0)
        self.assertTrue(report["material_balanced"])

    def test_credit_for_wrong_sco_is_discovered_by_lot_and_rejected(self):
        self.add_applied_credit(subcontracting_order="OTHER")
        self.assertIn("ADJUSTMENT_HEADER_MISMATCH", self.report()["issues"])

    def test_credit_with_invalid_explicit_component_is_not_reassigned(self):
        self.add_applied_credit(sco_supplied_item="OTHER")
        self.assertIn("INVALID_ADJUSTMENT_COMPONENT_LINK", self.report()["issues"])

    def test_credit_application_stock_entry_is_visible_but_not_physical_movement(self):
        self.sco["supplied_items"][0]["consumed_qty"] = 490
        self.scr["supplied_items"][0]["consumed_qty"] = 490
        self.add_applied_credit(application_stock_entry="APPLICATION-SE")
        self.api.add("Stock Entry", "APPLICATION-SE", subcontracting_order="SCO", supplier=None,
            company="Company", purpose="Material Issue", is_return=0, items=[Doc(name="APP-ROW",
                item_code="Wire", stock_uom="Kg", transfer_qty=10,
                s_warehouse="Processor", t_warehouse=None)])
        report = self.report()
        self.assertTrue(report["material_accounted"])
        movement = next(row for row in report["movements"] if row["parent"] == "APPLICATION-SE")
        self.assertEqual(movement["evidence_role"], "Material credit application")
        self.assertEqual(movement["processor_material_account_entry"], "PMA")
        self.assertNotIn("MOVEMENT_HEADER_MISMATCH", report["issues"])

    def test_unlinked_material_issue_remains_physical_evidence_mismatch(self):
        self.api.add("Stock Entry", "ISSUE", subcontracting_order="SCO", supplier=None,
            company="Company", purpose="Material Issue", is_return=0, items=[Doc(name="ISSUE-ROW",
                item_code="Wire", stock_uom="Kg", transfer_qty=10,
                s_warehouse="Processor", t_warehouse=None)])
        self.assertIn("MOVEMENT_HEADER_MISMATCH", self.report()["issues"])

    def test_plr_only_credit_is_discovered(self):
        self.api.add("Processor Lot Receipt", "PLR", lot_allocations=[Doc(processor_lot="LOT")])
        self.api.add("Processor Material Account Entry", "PMA", processor_lot_receipt="PLR")
        self.assertIn("ADJUSTMENT_HEADER_MISMATCH", self.report()["issues"])

    def test_debit_note_backlink_is_discovered(self):
        self.api.add("Purchase Invoice", "PI", custom_processor_lot_settlement="LOT")
        report = self.report()
        self.assertTrue(report["material_balanced"])
        self.assertEqual(report["settlement_evidence"], [
            {"doctype": "Purchase Invoice", "name": "PI", "reason": "Debit Note"}])

    def test_settlement_state_without_document_blocks(self):
        self.lot["settlement_status"] = "Completed"
        report = self.report()
        self.assertTrue(report["material_balanced"])
        self.assertEqual(report["settlement_evidence"][0]["reason"], "Settlement state")

    def test_debit_note_found_by_two_paths_is_deduplicated(self):
        self.lot["debit_note"] = "PI"
        self.api.add("Purchase Invoice", "PI", custom_processor_lot_settlement="LOT")
        report = self.report()
        self.assertEqual(report["settlement_evidence"], [
            {"doctype": "Purchase Invoice", "name": "PI", "reason": "Debit Note"}])

    def test_other_lot_same_sco_adjustments_are_not_ignored(self):
        self.api.add("Processor Lot", "LOT-2", subcontracting_order="SCO", **self.header)
        self.api.add("Processor Material Account Entry", "PMA", processor_lot="LOT-2")
        self.assertFalse(self.report()["material_balanced"])

    def test_proposed_plr_credit_blocks_before_account_entry_exists(self):
        self.api.add("Processor Lot Receipt", "PLR", lot_allocations=[Doc(processor_lot="LOT")],
                     material_credit_status="Proposed")
        self.assertFalse(self.report()["material_balanced"])

    def test_itemized_plr_credit_blocks(self):
        self.api.add("Processor Lot Receipt", "PLR", lot_allocations=[Doc(processor_lot="LOT")],
                     receipt_items=[Doc(processor_material_credit_qty=10)])
        self.assertFalse(self.report()["material_balanced"])

    def test_denied_adjustment_cannot_return_partial_report(self):
        self.api.add("Processor Material Account Entry", "PMA", processor_lot="LOT", denied=True)
        with self.assertRaises(PermissionError):
            self.report()

    def test_public_console_entry_uses_reader(self):
        from unittest.mock import patch
        from subcontracting_extensions.material_reconciliation_reader import get_material_position
        with patch.dict("sys.modules", {"frappe": self.api}):
            self.assertTrue(get_material_position("LOT")["material_balanced"])

    def test_inputs_unchanged_and_no_duplicate_parent_loads(self):
        before = deepcopy(self.api.docs)
        self.report()
        self.assertEqual(self.api.docs, before)
        self.assertEqual(len(self.api.reads), len(set(self.api.reads)))


if __name__ == "__main__":
    unittest.main()
