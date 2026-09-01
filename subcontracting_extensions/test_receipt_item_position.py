"""Plain unittest only. All reads mocked; no fixtures/inserts/saves/commits."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from subcontracting_extensions import receipt_item_position as position
from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot_receipt import processor_lot_receipt as controller
from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot import processor_lot as lot_controller


def record(**values):
    row = SimpleNamespace(**values)
    row.get = lambda key, default=None: getattr(row, key, default)
    return row


class TestReceiptItemPosition(unittest.TestCase):
    def setUp(self):
        self.assertEqual(position.quantity(100), 100)
        self.a = record(name="SCO-A", item_code="A", stock_uom="Kg", qty=100, received_qty=0, returned_qty=0)
        self.b = record(name="SCO-B", item_code="B", stock_uom="Units", qty=50, received_qty=0, returned_qty=0)
        self.sco = record(name="SCO", items=[self.a, self.b])

    def allocation(self, parent="TRUCK-1", item="A", accepted=10, invoice=11, **extra):
        return record(parent=parent, receipt_item_key="ITEM-" + item, subcontracting_order="SCO",
            subcontracting_order_item="SCO-" + item, processed_item=item, stock_uom="Kg" if item == "A" else "Units",
            allocated_accepted_qty=accepted, allocated_invoice_qty=invoice, **extra)

    def receipt(self, name="TRUCK-1", scr=None, **extra):
        return record(name=name, docstatus=0, physical_receipt_date="2026-08-31", subcontracting_receipt=scr, **extra)

    def build(self, allocations=(), receipts=(), submitted=(), scr_items=(), credits=()):
        return position.build_position(self.sco, allocations, receipts, set(submitted), scr_items, credits)

    def test_one_truck_counts_once_per_item_and_once_for_lot(self):
        result = self.build([self.allocation(), self.allocation(item="B", accepted=3)], [self.receipt()])
        self.assertEqual(result["truck_count"], 1)
        self.assertEqual([i.receipt_count for i in result["items"]], [1, 1])
        self.assertEqual([i.accepted_qty for i in result["items"]], [10, 3])
        self.assertEqual([i.available_qty for i in result["items"]], [90, 47])
        self.assertNotIn("total_accepted_qty", result)

    def test_two_trucks_same_item_count_two(self):
        result = self.build([self.allocation(), self.allocation(parent="TRUCK-2")], [self.receipt(), self.receipt("TRUCK-2")])
        self.assertEqual(result["truck_count"], 2)
        self.assertEqual([i.receipt_count for i in result["items"]], [2, 0])

    def test_no_receipts_still_lists_all_sco_items(self):
        result = self.build()
        self.assertEqual(result["truck_count"], 0)
        self.assertEqual([i.available_qty for i in result["items"]], [100, 50])

    def test_cancelled_receipt_is_excluded(self):
        receipt = self.receipt()
        receipt.docstatus = 2
        self.assertEqual(self.build([self.allocation()], [receipt])["truck_count"], 0)

    def test_native_receipts_and_unposted_drafts_are_additive(self):
        self.a.received_qty = 20
        item = self.build([self.allocation()], [self.receipt()])["items"][0]
        self.assertEqual(item.previously_received_qty, 30)
        self.assertEqual(item.available_qty, 70)

    def test_linked_submitted_scr_is_not_counted_twice(self):
        self.a.received_qty = 20
        scr = record(name="SCR-ITEM", parent="SCR", subcontracting_order="SCO", subcontracting_order_item="SCO-A",
            item_code="A", stock_uom="Kg", qty=10)
        item = self.build([self.allocation()], [self.receipt(scr="SCR")], ["SCR"], [scr])["items"][0]
        self.assertEqual(item.available_qty, 80)
        self.assertEqual(item.accepted_qty, 10)

    def test_bad_submitted_scr_mapping_is_rejected(self):
        with self.assertRaisesRegex(frappe.ValidationError, "SCR evidence"):
            self.build([self.allocation()], [self.receipt(scr="SCR")], ["SCR"])

    def test_native_returns_and_precision(self):
        self.a.received_qty = 10.123456
        self.a.returned_qty = 0.000001
        item = self.build()["items"][0]
        self.assertEqual(item.available_qty, 89.876545)

    def test_credit_only_reduces_its_item(self):
        credit = record(processed_item="A", processed_item_uom="Kg", processed_qty=1.123456)
        result = self.build(credits=[credit])
        self.assertEqual([i.available_qty for i in result["items"]], [98.876544, 50])

    def test_unmatched_credit_is_rejected(self):
        with self.assertRaisesRegex(frappe.ValidationError, "credit does not match"):
            self.build(credits=[record(processed_item="A", processed_item_uom="Units", processed_qty=1)])

    def test_wrong_lineage_is_rejected(self):
        row = self.allocation()
        row.subcontracting_order_item = "WRONG"
        with self.assertRaisesRegex(frappe.ValidationError, "lineage"):
            self.build([row], [self.receipt()])

    def test_wrong_item_uom_is_rejected(self):
        row = self.allocation()
        row.stock_uom = "Units"
        with self.assertRaisesRegex(frappe.ValidationError, "item and UOM"):
            self.build([row], [self.receipt()])

    def test_duplicate_source_rows_rejected(self):
        self.sco.items.append(self.a)
        with self.assertRaisesRegex(frappe.ValidationError, "Duplicate or ambiguous"):
            self.build()

    def test_duplicate_allocation_rejected(self):
        with self.assertRaisesRegex(frappe.ValidationError, "Duplicate receipt"):
            self.build([self.allocation(), self.allocation()], [self.receipt()])

    def test_missing_key_allowed_only_for_legacy_single_row(self):
        row = self.allocation()
        row.subcontracting_order_item = None
        with self.assertRaisesRegex(frappe.ValidationError, "lineage"):
            self.build([row], [self.receipt()])
        self.sco.items = [self.a]
        self.assertEqual(self.build([row], [self.receipt()])["items"][0].available_qty, 90)

    def test_unconverted_uom_is_rejected(self):
        self.a.uom = "Ton"
        with self.assertRaisesRegex(frappe.ValidationError, "quantity UOM"):
            self.build()

    def test_negative_allocation_rejected(self):
        with self.assertRaisesRegex(frappe.ValidationError, "Negative allocation"):
            self.build([self.allocation(accepted=-1)], [self.receipt()])

    def test_overallocated_item_remains_visible_as_negative_capacity(self):
        item = self.build([self.allocation(accepted=101)], [self.receipt()])["items"][0]
        self.assertEqual(item.available_qty, -1)

    def test_read_evidence_excludes_current_receipt_and_cancelled_rows(self):
        with patch.object(position.frappe, "get_all", return_value=[]) as reads:
            position.read_evidence("LOT", self.sco, "CURRENT")
        filters = reads.call_args_list[0].kwargs["filters"]
        self.assertEqual(filters["parent"], ["!=", "CURRENT"])
        self.assertEqual(filters["docstatus"], ["!=", 2])
        self.assertEqual(filters["processor_lot"], "LOT")

    def test_endpoint_disabled_before_reads(self):
        with patch.object(position.frappe, "conf", frappe._dict()), patch.object(position.frappe, "get_doc") as read:
            self.assertEqual(position.get_item_position("LOT"), {"enabled": False})
            read.assert_not_called()

    def test_endpoint_checks_lot_permissions(self):
        lot = record(check_permission=Mock(side_effect=frappe.PermissionError))
        with patch.object(position.frappe, "conf", frappe._dict(v2_processor_first_draft_entry=1)), patch.object(position.frappe, "get_doc", return_value=lot):
            with self.assertRaises(frappe.PermissionError):
                position.get_item_position("LOT")

    def test_endpoint_checks_sco_permissions(self):
        lot = record(name="LOT", subcontracting_order="SCO", check_permission=Mock())
        self.sco.check_permission = Mock(side_effect=frappe.PermissionError)
        with patch.object(position.frappe, "conf", frappe._dict(v2_processor_first_draft_entry=1)), patch.object(position.frappe, "get_doc", side_effect=[lot, self.sco]):
            with self.assertRaises(frappe.PermissionError):
                position.get_item_position("LOT")

    def test_endpoint_redacts_inaccessible_document_references(self):
        lot = record(name="LOT", subcontracting_order="SCO", check_permission=Mock())
        self.sco.check_permission = Mock()
        report = self.build([self.allocation()], [self.receipt()])
        with patch.object(position.frappe, "conf", frappe._dict(v2_processor_first_draft_entry=1)), patch.object(position.frappe, "get_doc", side_effect=[lot, self.sco]), patch.object(position, "position_for_lot", return_value=report), patch.object(position.frappe, "has_permission", return_value=False):
            result = position.get_item_position("LOT")
        self.assertEqual(result["truck_count"], 1)
        self.assertEqual(result["journeys"][0]["processor_lot_receipt"], "Restricted")
        self.assertNotIn("receipt_names", result["items"][0])

    def test_multi_item_summary_updates_distinct_truck_count_not_mixed_totals(self):
        lot = record(name="LOT", subcontracting_order="SCO")
        report = self.build([self.allocation(), self.allocation(item="B")], [self.receipt()])
        with patch.object(lot_controller.frappe, "get_doc", side_effect=[lot, self.sco]), patch.object(position, "position_for_lot", return_value=report), patch.object(lot_controller.frappe.db, "set_value") as write:
            lot_controller.refresh_processor_lot_receipt_summary("LOT")
        args = write.call_args.args
        self.assertEqual(args[:2], ("Processor Lot", "LOT"))
        self.assertEqual(args[2]["receipt_count"], 1)
        self.assertEqual(args[2]["total_company_accepted_qty"], 0)
        self.assertIsNone(args[2]["receipt_stock_uom"])
        self.assertFalse(write.call_args.kwargs["update_modified"])

    def test_single_item_summary_preserves_legacy_totals(self):
        self.sco.items = [self.a]
        lot = record(name="LOT", subcontracting_order="SCO")
        receipt = record(name="TRUCK", physical_receipt_date="2026-08-31", processed_item="A", stock_uom="Kg",
            allocated_accepted_qty=10, allocated_invoice_qty=11, allocated_company_net_weight=10, allocated_supplier_net_weight=11)
        with patch.object(lot_controller.frappe, "get_doc", side_effect=[lot, self.sco]), patch.object(lot_controller, "_get_processor_lot_allocated_receipts", return_value=[receipt]), patch.object(lot_controller.frappe.db, "set_value") as write:
            lot_controller.refresh_processor_lot_receipt_summary("LOT")
        self.assertEqual(write.call_args.args[2]["total_company_accepted_qty"], 10)
        self.assertEqual(write.call_args.args[2]["receipt_stock_uom"], "Kg")

    def test_multi_item_submission_blocked_before_settlement(self):
        doc = record(_get_sco=lambda: self.sco)
        with self.assertRaisesRegex(frappe.ValidationError, "Settlement is not enabled"):
            lot_controller.ProcessorLot.before_submit(doc)

    def test_single_item_settlement_guard_is_noop(self):
        self.sco.items = [self.a]
        lot_controller._block_multi_item_settlement(self.sco)

    def test_multi_item_physical_endpoint_uses_item_contract(self):
        lot = record(name="LOT", subcontracting_order="SCO")
        with patch.object(lot_controller.frappe.db, "exists", return_value=True), patch.object(lot_controller.frappe, "get_doc", side_effect=[lot, self.sco]), patch.object(position, "get_item_position", return_value={"is_multi_item": True}) as report:
            result = lot_controller.get_processor_lot_physical_position("LOT")
        report.assert_called_once_with("LOT")
        self.assertTrue(result["is_multi_item"])

    def test_multi_item_journey_endpoint_uses_item_contract(self):
        lot = record(name="LOT", subcontracting_order="SCO")
        with patch.object(lot_controller.frappe.db, "exists", return_value=True), patch.object(lot_controller.frappe, "get_doc", side_effect=[lot, self.sco]), patch.object(position, "get_item_position", return_value={"truck_count": 1}) as report:
            result = lot_controller.get_processor_lot_receipt_journey("LOT")
        report.assert_called_once_with("LOT")
        self.assertEqual(result["item_position"]["truck_count"], 1)

    def test_two_items_in_same_lot_pass_real_allocation_validation(self):
        items = [record(item_key="ITEM-" + key, processed_item=key, stock_uom=uom, company_accepted_qty=10,
            lot_backed_qty=10, supplier_invoice_qty=11, material_credit_invoice_qty=0) for key, uom in (("A", "Kg"), ("B", "Units"))]
        allocations = []
        candidates = {}
        for index, item in enumerate(items):
            row = record(idx=index + 1, receipt_item_key=item.item_key, processor_lot="LOT", subcontracting_order="SCO",
                subcontracting_order_item="SCO-" + item.processed_item, purchase_order="PO", purchase_order_item="PO-" + item.processed_item,
                processed_item=item.processed_item, stock_uom=item.stock_uom, allocated_accepted_qty=10, allocated_invoice_qty=11)
            allocations.append(row)
            candidates[item.item_key] = [record(processor_lot="LOT", subcontracting_order="SCO", subcontracting_order_item=row.subcontracting_order_item,
                purchase_order="PO", purchase_order_item=row.purchase_order_item, lot_date="2026-08-31", lot_order_qty=100,
                previously_received_qty=0, available_qty=100)]
        doc = record(receipt_items=items, lot_allocations=allocations, _get_v2_fifo_candidates=lambda item: candidates[item.item_key])
        controller.ProcessorLotReceipt._validate_v2_allocations(doc)
        self.assertEqual([row.invoice_vs_accepted_qty for row in allocations], [1, 1])
        doc.lot_allocations.append(allocations[0])
        with self.assertRaisesRegex(frappe.ValidationError, "duplicate allocations"):
            controller.ProcessorLotReceipt._validate_v2_allocations(doc)

    def test_real_fifo_splits_one_item_across_lots_without_using_other_item_capacity(self):
        items = [record(item_key="A", processed_item="A", stock_uom="Kg", company_accepted_qty=150,
            lot_backed_qty=150, supplier_invoice_qty=151, material_credit_invoice_qty=0),
            record(item_key="B", processed_item="B", stock_uom="Units", company_accepted_qty=10,
            lot_backed_qty=10, supplier_invoice_qty=10, material_credit_invoice_qty=0)]
        def candidate(lot, key, qty):
            return record(processor_lot=lot, subcontracting_order="SCO-" + lot,
                subcontracting_order_item=lot + "-" + key, purchase_order="PO-" + lot, purchase_order_item=lot + "-PO-" + key,
                lot_date="2026-08-31", lot_order_qty=qty, previously_received_qty=0, available_qty=qty)
        candidates = {"A": [candidate("LOT1", "A", 100), candidate("LOT2", "A", 100)],
            "B": [candidate("LOT1", "B", 50)]}
        doc = record(receipt_items=items, lot_allocations=[], _get_v2_fifo_candidates=lambda item: candidates[item.item_key])
        def append(field, values):
            row = record(idx=len(doc.lot_allocations) + 1, **values)
            doc.lot_allocations.append(row)
            return row
        doc.append = append
        controller.ProcessorLotReceipt._ensure_initial_v2_allocations(doc)
        controller.ProcessorLotReceipt._validate_v2_allocations(doc)
        self.assertEqual([(r.processor_lot, r.receipt_item_key, r.allocated_accepted_qty, r.allocated_invoice_qty)
            for r in doc.lot_allocations], [("LOT1", "A", 100, 100), ("LOT2", "A", 50, 51), ("LOT1", "B", 10, 10)])
