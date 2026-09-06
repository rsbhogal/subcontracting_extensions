"""J11 evidence tests. No records are inserted, saved or submitted."""

import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from subcontracting_extensions import receipt_completion as completion


class Record(SimpleNamespace):
    def get(self, key, default=None):
        return getattr(self, key, default)

    def update(self, **values):
        self.__dict__.update(values)


def record(**values):
    return Record(**values)


class TestReceiptCompletion(unittest.TestCase):
    def setUp(self):
        self.assertEqual(completion.quantity(499), 499)
        self.sco = record(name="SCO", purchase_order="PO", company="Company", supplier="Supplier", items=[])
        self.allocations = []
        self.scr_rows = []
        self.documents = {}
        for doctype, name in (("Subcontracting Receipt", "SCR"), ("Purchase Receipt", "PR"),
                              ("Purchase Invoice", "PI"), ("Purchase Order", "PO")):
            self.documents[(doctype, name)] = record(name=name, docstatus=1,
                company="Company", supplier="Supplier", items=[])
        self.scr = self.documents[("Subcontracting Receipt", "SCR")]
        self.scr.custom_processor_lot_receipt = "PLR"
        self.pr = self.documents[("Purchase Receipt", "PR")]
        self.pr.subcontracting_receipt = "SCR"
        self.pi = self.documents[("Purchase Invoice", "PI")]
        for key, uom, qty in (("A", "Kg", 500), ("B", "Units", 100)):
            self.sco.items.append(record(name="SCO-" + key, item_code="FG-" + key,
                stock_uom=uom, qty=qty, received_qty=qty, returned_qty=0, purchase_order_item="PO-" + key))
            self.allocations.append(record(parent="PLR", receipt_item_key=key,
                subcontracting_order="SCO", subcontracting_order_item="SCO-" + key,
                subcontracting_receipt_item="SCR-" + key, purchase_order="PO", purchase_order_item="PO-" + key,
                processed_item="FG-" + key, stock_uom=uom, allocated_accepted_qty=qty, allocated_invoice_qty=qty))
            scr_row = record(name="SCR-" + key, parent="SCR", subcontracting_order="SCO",
                subcontracting_order_item="SCO-" + key, item_code="FG-" + key, stock_uom=uom, qty=qty)
            self.scr_rows.append(scr_row)
            self.scr.items.append(deepcopy(scr_row))
            self.documents[("Purchase Order", "PO")].items.append(record(name="PO-" + key,
                item_code="SERVICE-" + key, stock_uom=uom))
            self.pr.items.append(record(name="PR-" + key, subcontracting_receipt_item="SCR-" + key,
                purchase_order="PO", purchase_order_item="PO-" + key,
                item_code="SERVICE-" + key, stock_uom=uom, stock_qty=qty))
            self.pi.items.append(record(name="PI-" + key, purchase_receipt="PR", pr_detail="PR-" + key,
                purchase_order="PO", po_detail="PO-" + key, item_code="SERVICE-" + key, stock_uom=uom, stock_qty=qty))
        self.receipts = [record(name="PLR", docstatus=0, subcontracting_receipt="SCR",
            purchase_receipt="PR", purchase_invoice="PI", physical_receipt_date="2026-09-01",
            receipt_structure_version="V2 Itemized")]
        self.submitted = {"SCR"}

    def build(self):
        return completion.build_completion(self.sco,
            (self.allocations, self.receipts, self.submitted, self.scr_rows, []), self.documents)

    def test_complete_mixed_uom_items_remain_separate(self):
        result = self.build()
        self.assertTrue(result["journey_complete"])
        self.assertFalse(result["settlement_enabled"])
        self.assertEqual([row.submitted_pi_qty for row in result["items"]], [500, 100])
        self.assertNotIn("total_qty", result)

    def test_draft_invoice_is_not_recognized(self):
        self.pi.docstatus = 0
        result = self.build()
        self.assertFalse(result["journey_complete"])
        self.assertEqual(result["items"][0].submitted_pi_qty, 0)
        self.assertEqual(result["items"][0].submitted_pr_qty, 500)

    def test_cancelled_invoice_is_not_recognized(self):
        self.pi.docstatus = 2
        self.assertFalse(self.build()["journey_complete"])

    def test_quantity_change_blocks_only_affected_item(self):
        self.pi.items[0].stock_qty = 499
        result = self.build()
        self.assertEqual([row.journey_complete for row in result["items"]], [False, True])
        self.assertIn("PI_ROW_LINEAGE_MISMATCH", result["journeys"][0]["issues"])

    def test_po_lineage_change_is_not_recognized(self):
        self.pr.items[0].purchase_order_item = "WRONG"
        self.assertFalse(self.build()["journeys"][0]["pr_verified"])

    def test_wrong_service_item_is_not_recognized(self):
        self.pr.items[0].item_code = "FG-A"
        self.assertFalse(self.build()["journeys"][0]["pr_verified"])

    def test_duplicate_pi_reference_is_not_recognized(self):
        self.pi.items.append(deepcopy(self.pi.items[0]))
        self.assertFalse(self.build()["journeys"][0]["pi_verified"])

    def test_wrong_supplier_is_not_recognized(self):
        self.pi.supplier = "OTHER"
        self.assertFalse(self.build()["journey_complete"])

    def test_wrong_scr_backlink_is_not_recognized(self):
        self.scr.custom_processor_lot_receipt = "OTHER"
        self.assertFalse(self.build()["journeys"][0]["scr_verified"])

    def test_legacy_evidence_is_classified_without_weakening_verification(self):
        self.receipts[0].receipt_structure_version = "Legacy Single Item"
        self.scr.custom_processor_lot_receipt = "OTHER"
        result = self.build()
        self.assertFalse(result["journey_complete"])
        self.assertTrue(result["legacy_evidence_only"])
        self.assertTrue(result["journeys"][0]["legacy_evidence"])
        self.assertIn("SCR_ROW_LINEAGE_MISMATCH", result["journeys"][0]["issues"])

    def test_v2_mismatch_is_not_classified_as_legacy(self):
        self.scr.custom_processor_lot_receipt = "OTHER"
        result = self.build()
        self.assertFalse(result["legacy_evidence_only"])
        self.assertFalse(result["journeys"][0]["legacy_evidence"])

    def test_return_invoice_is_not_recognized(self):
        self.pi.is_return = 1
        self.assertFalse(self.build()["journey_complete"])

    def test_stock_updating_invoice_is_not_recognized(self):
        self.pi.update_stock = 1
        self.assertFalse(self.build()["journey_complete"])

    def test_excess_draft_reservation_is_preserved(self):
        extra = deepcopy(self.allocations[0])
        extra.update(parent="DRAFT", allocated_accepted_qty=201, allocated_invoice_qty=205,
                     subcontracting_receipt_item=None)
        self.allocations.append(extra)
        self.receipts.append(record(name="DRAFT", docstatus=0, subcontracting_receipt=None))
        item = self.build()["items"][0]
        self.assertEqual(item.native_received_qty, 500)
        self.assertEqual(item.submitted_scr_qty, 500)
        self.assertEqual(item.reserved_accepted_qty, 201)
        self.assertEqual(item.previously_received_qty, 701)
        self.assertEqual(item.available_qty, -201)
        self.assertEqual(item.excess_reserved_qty, 201)
        self.assertFalse(item.journey_complete)

    def test_native_receipts_outside_allocations_are_not_inferred(self):
        self.sco.items[0].qty = 10000
        self.sco.items[0].received_qty = 10000
        item = self.build()["items"][0]
        self.assertEqual(item.native_receipts_without_verified_allocation_qty, 9500)
        self.assertFalse(item.journey_complete)

    def test_cancelled_draft_reservation_is_excluded(self):
        self.receipts.append(record(name="DRAFT", docstatus=2, subcontracting_receipt=None))
        extra = deepcopy(self.allocations[0])
        extra.parent = "DRAFT"
        self.allocations.append(extra)
        self.assertTrue(self.build()["journey_complete"])

    def test_builder_does_not_mutate_input_documents(self):
        before = deepcopy((self.sco, self.allocations, self.receipts, self.documents))
        self.build()
        self.assertEqual(before, (self.sco, self.allocations, self.receipts, self.documents))

    def test_wrong_uom_blocks_only_affected_item(self):
        self.pi.items[1].stock_uom = "Kg"
        self.assertEqual([row.journey_complete for row in self.build()["items"]], [True, False])

    def test_draft_pr_cannot_be_bypassed_by_submitted_pi(self):
        self.pr.docstatus = 0
        result = self.build()
        self.assertEqual(result["items"][0].submitted_pi_qty, 0)
        self.assertFalse(result["journey_complete"])

    def test_commercial_variance_is_disclosed_not_settled(self):
        self.allocations[0].allocated_invoice_qty = 505
        self.pr.items[0].stock_qty = 505
        self.pi.items[0].stock_qty = 505
        result = self.build()
        self.assertTrue(result["journey_complete"])
        self.assertEqual(result["items"][0].submitted_invoice_vs_accepted_qty, 5)
        self.assertFalse(result["settlement_enabled"])

    def endpoint_context(self):
        from unittest.mock import Mock
        lot = record(name="LOT", docstatus=0, subcontracting_order="SCO", check_permission=Mock())
        self.sco.docstatus = 1
        self.sco.check_permission = Mock()
        docs = dict(self.documents)
        docs[("Processor Lot", "LOT")] = lot
        docs[("Subcontracting Order", "SCO")] = self.sco
        docs[("Processor Lot Receipt", "PLR")] = record(name="PLR")
        for doc in docs.values():
            doc.check_permission = Mock()
        return docs

    def test_enabled_endpoint_loads_and_checks_all_documents(self):
        docs = self.endpoint_context()
        with patch.dict(frappe.conf, {"v2_processor_first_completion_facts": 1}), \
                patch.object(frappe, "get_doc", side_effect=lambda dt, name: docs[(dt, name)]), \
                patch.object(frappe, "get_all", return_value=self.allocations), \
                patch.object(completion, "read_evidence", return_value=(
                    deepcopy(self.allocations), self.receipts, self.submitted, self.scr_rows, [])):
            result = completion.get_completion_position("LOT")
        self.assertTrue(result["journey_complete"])
        for doc in docs.values():
            doc.check_permission.assert_called_once_with("read")

    def test_inaccessible_invoice_prevents_evidence_disclosure(self):
        docs = self.endpoint_context()
        docs[("Purchase Invoice", "PI")].check_permission.side_effect = frappe.PermissionError
        with patch.dict(frappe.conf, {"v2_processor_first_completion_facts": 1}), \
                patch.object(frappe, "get_doc", side_effect=lambda dt, name: docs[(dt, name)]), \
                patch.object(completion, "read_evidence", return_value=(
                    self.allocations, self.receipts, self.submitted, self.scr_rows, [])):
            with self.assertRaises(frappe.PermissionError):
                completion.get_completion_position("LOT")

    def test_endpoint_disabled_without_reads(self):
        with patch.dict(frappe.conf, {"v2_processor_first_completion_facts": 0}), \
                patch.object(frappe, "get_doc") as read:
            self.assertEqual(completion.get_completion_position("LOT"), {"enabled": False})
        read.assert_not_called()

    def test_endpoint_checks_lot_permissions_before_evidence(self):
        from unittest.mock import Mock
        lot = record(check_permission=Mock(side_effect=frappe.PermissionError))
        with patch.dict(frappe.conf, {"v2_processor_first_completion_facts": 1}), \
                patch.object(frappe, "get_doc", return_value=lot), \
                patch.object(completion, "read_evidence") as read:
            with self.assertRaises(frappe.PermissionError):
                completion.get_completion_position("LOT")
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
