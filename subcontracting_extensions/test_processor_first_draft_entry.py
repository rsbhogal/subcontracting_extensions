"""J2 service-boundary tests. All documents and database calls are mocked."""

import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from subcontracting_extensions import receipt_entry_preview as entry
from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot_receipt import (
    processor_lot_receipt as controller,
)


class Record(SimpleNamespace):
    def get(self, key, default=None):
        return getattr(self, key, default)

    def as_dict(self):
        return dict(vars(self))


class FakeReceipt(Record):
    def set(self, fieldname, value):
        if fieldname in ("receipt_items", "item_weighments", "lot_allocations"):
            value = [row if isinstance(row, Record) else Record(**row) for row in value]
        setattr(self, fieldname, value)


class TestProcessorFirstDraftEntry(unittest.TestCase):
    def setUp(self):
        self.assertEqual(controller.flt(100, 6), 100.0)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(
            entry.frappe, "conf", frappe._dict(
                v2_receipt_entry_preview=1,
                v2_processor_first_draft_entry=1,
            ),
        ))
        self.stack.enter_context(patch.object(entry.frappe.db, "exists", return_value=False))
        self.stack.enter_context(patch.object(entry.frappe, "has_permission", return_value=True))
        self.new_doc = self.receipt(is_new=True)
        self.saved_doc = self.receipt(is_new=False)
        self.stack.enter_context(patch.object(
            entry.frappe, "get_doc", side_effect=lambda *args, **kwargs: (
                self.new_doc if len(args) == 1 and isinstance(args[0], dict) else self.saved_doc
            ),
        ))

    def receipt(self, is_new):
        items = [] if is_new else [Record(
            name="ITEM-ROW-1", item_key="ITEM-001", processed_item="ITEM-A",
            stock_uom="Kg", measurement_method="Count", measurement_basis="Direct Count",
            company_accepted_qty=50, supplier_invoice_qty=49,
        )]
        doc = FakeReceipt(
            name=None if is_new else "PLR-J2", modified="2026-08-31 01:02:03.000001",
            docstatus=0, receipt_structure_version="V2 Itemized", processor_lot=None,
            company="COMPANY", supplier="SUPPLIER", supplier_warehouse="WAREHOUSE",
            physical_receipt_date="2026-08-31", vehicle_no=None,
            supplier_challan_number=None, supplier_challan_date=None, remarks=None,
            subcontracting_receipt=None, purchase_receipt=None, purchase_invoice=None,
            receipt_items=items, item_weighments=[], lot_allocations=[],
            check_permission=Mock(), is_new=Mock(return_value=is_new),
            insert=Mock(side_effect=AssertionError("review inserted a document")),
            save=Mock(side_effect=AssertionError("review saved a document")),
            submit=Mock(side_effect=AssertionError("review submitted a document")),
        )

        def before_validate():
            for index, item in enumerate(doc.receipt_items, 1):
                item.item_key = item.get("item_key") or f"ITEM-{index:03d}"
                item.stock_uom = "Kg"
                item.company_accepted_uom = "Kg"
                item.supplier_invoice_uom = "Kg"
                item.supplier_invoice_vs_company_qty = (
                    float(item.supplier_invoice_qty) - float(item.company_accepted_qty)
                )
                item.lot_backed_qty = float(item.company_accepted_qty)
                item.processor_material_credit_qty = 0
            doc.lot_allocations = [Record(
                receipt_item_key=item.item_key, processor_lot="PL-OPEN",
                subcontracting_order="SCO-OPEN", subcontracting_order_item="SCO-ROW",
                purchase_order="PO-OPEN", purchase_order_item="PO-ROW", lot_date="2026-08-01",
                processed_item=item.processed_item, stock_uom="Kg", available_qty=100,
                lot_order_qty=100, previously_received_qty=0,
                allocated_accepted_qty=item.company_accepted_qty,
                allocated_invoice_qty=item.supplier_invoice_qty,
            ) for item in doc.receipt_items]

        doc.before_validate = Mock(side_effect=before_validate)
        doc.validate = Mock()
        return doc

    def payload(self, saved=False):
        return {
            "name": "PLR-J2" if saved else None,
            "modified": self.saved_doc.modified if saved else None,
            "company": "COMPANY", "supplier": "SUPPLIER", "supplier_warehouse": "WAREHOUSE",
            "physical_receipt_date": "2026-08-31", "vehicle_no": "PB00TEST",
            "supplier_challan_number": "CH-1", "supplier_challan_date": "2026-08-31",
            "remarks": "Draft reference",
            "receipt_items": [{
                **({"name": "ITEM-ROW-1"} if saved else {}),
                "item_key": "ITEM-001", "processed_item": "ITEM-A",
                "measurement_method": "Count", "measurement_basis": "Direct Count",
                "company_accepted_qty": 50.123456, "supplier_invoice_qty": 49.123456,
            }],
            "item_weighments": [],
        }

    def test_new_review_recalculates_without_database_write(self):
        result = entry.review_draft(self.payload())
        self.assertEqual(result["receipt_items"][0]["company_accepted_qty"], 50.123456)
        self.assertEqual(result["lot_allocations"][0]["allocated_accepted_qty"], 50.123456)
        self.new_doc.before_validate.assert_called_once_with()
        self.new_doc.validate.assert_called_once_with()
        self.new_doc.insert.assert_not_called()
        self.new_doc.save.assert_not_called()
        self.new_doc.submit.assert_not_called()

    def test_saved_review_checks_permission_and_staleness(self):
        entry.review_draft(self.payload(saved=True))
        self.saved_doc.check_permission.assert_called_once_with("write")
        stale = self.payload(saved=True)
        stale["modified"] = "stale"
        with self.assertRaisesRegex(frappe.ValidationError, "changed since"):
            entry.review_draft(stale)

    def test_saved_context_and_item_identity_are_immutable(self):
        for field in ("company", "supplier", "supplier_warehouse"):
            data = self.payload(saved=True)
            data[field] = "OTHER"
            with self.subTest(field=field), self.assertRaisesRegex(frappe.ValidationError, "context"):
                entry.review_draft(data)
        for values in (
            {"name": "OTHER"}, {"item_key": "ITEM-999"}, {"processed_item": "OTHER"},
        ):
            data = self.payload(saved=True)
            data["receipt_items"][0].update(values)
            with self.subTest(values=values), self.assertRaisesRegex(frappe.ValidationError, "selected items"):
                entry.review_draft(data)

    def test_untrusted_system_fields_are_not_copied(self):
        data = self.payload()
        data.update({"doctype": "Stock Entry", "docstatus": 1, "processor_lot": "PL-INJECTED",
                     "subcontracting_receipt": "SCR-INJECTED", "owner": "Administrator"})
        data["receipt_items"][0].update({"stock_uom": "Hacked", "lot_backed_qty": 999})
        entry.review_draft(data)
        self.assertIsNone(self.new_doc.processor_lot)
        self.assertIsNone(self.new_doc.subcontracting_receipt)
        self.assertEqual(self.new_doc.receipt_items[0].stock_uom, "Kg")
        self.assertEqual(self.new_doc.receipt_items[0].lot_backed_qty, 50.123456)

    def test_feature_gate_and_create_permission_are_enforced(self):
        entry.frappe.conf.v2_processor_first_draft_entry = 0
        with self.assertRaisesRegex(frappe.ValidationError, "not enabled"):
            entry.review_draft(self.payload())
        entry.frappe.conf.v2_processor_first_draft_entry = 1
        with patch.object(entry.frappe, "has_permission", return_value=False):
            with self.assertRaises(frappe.PermissionError):
                entry.review_draft(self.payload())

    def test_preview_legacy_and_linked_documents_are_rejected(self):
        data = self.payload()
        data["__v2_entry_preview"] = 1
        with self.assertRaisesRegex(frappe.ValidationError, "previews"):
            entry.review_draft(data)
        self.saved_doc.receipt_structure_version = "Legacy Single Item"
        with self.assertRaisesRegex(frappe.ValidationError, "Only processor-first"):
            entry.review_draft(self.payload(saved=True))
        self.saved_doc.receipt_structure_version = "V2 Itemized"
        self.saved_doc.purchase_receipt = "PR-1"
        with self.assertRaisesRegex(frappe.ValidationError, "downstream"):
            entry.review_draft(self.payload(saved=True))

    def test_reverse_downstream_links_are_checked(self):
        with patch.object(entry.frappe.db, "exists", return_value=True):
            with self.assertRaisesRegex(frappe.ValidationError, "downstream activity"):
                entry.review_draft(self.payload(saved=True))

    def test_quantities_must_be_numeric_finite_and_non_negative(self):
        for value in ("bad", float("inf"), float("nan"), -1):
            data = self.payload()
            data["receipt_items"][0]["company_accepted_qty"] = value
            with self.subTest(value=value), self.assertRaises(frappe.ValidationError):
                entry.review_draft(data)

    def test_row_limit_and_payload_shape_are_bounded(self):
        with self.assertRaisesRegex(frappe.ValidationError, "Invalid draft payload"):
            entry.review_draft([])
        data = self.payload()
        data["receipt_items"] = data["receipt_items"] * 201
        with self.assertRaisesRegex(frappe.ValidationError, "at most 200"):
            entry.review_draft(data)

    def test_weighment_contract_is_bounded(self):
        base = {"weighment_stage": "Arrival Loaded", "weighment_date": "2026-08-31",
                "scale_weight": 1000, "measurement_uom": "Kg", "adjustment_qty": 0}
        for change, message in (
            ({"weighment_stage": "Other"}, "supports"),
            ({"weighment_date": None}, "Date"),
            ({"measurement_uom": None}, "Scale UOM"),
            ({"adjustment_qty": 1}, "Explain"),
        ):
            data = self.payload()
            data["item_weighments"] = [{**base, **change}]
            with self.subTest(change=change), self.assertRaisesRegex(frappe.ValidationError, message):
                entry.review_draft(data)

    def test_saved_weighment_identity_cannot_be_injected_or_duplicated(self):
        self.saved_doc.item_weighments = [Record(name="WEIGH-1")]
        base = {"weighment_stage": "Arrival Loaded", "weighment_date": "2026-08-31",
                "scale_weight": 1000, "measurement_uom": "Kg", "adjustment_qty": 0}
        for names in (("OTHER",), ("WEIGH-1", "WEIGH-1")):
            data = self.payload(saved=True)
            data["item_weighments"] = [{**base, "name": name} for name in names]
            with self.subTest(names=names), self.assertRaisesRegex(frappe.ValidationError, "identity"):
                entry.review_draft(data)

    def test_get_entry_mode_exposes_only_boolean(self):
        self.assertEqual(entry.get_entry_mode(), {"draft_entry_enabled": True})
        entry.frappe.conf.v2_processor_first_draft_entry = 0
        self.assertEqual(entry.get_entry_mode(), {"draft_entry_enabled": False})

    def test_allocation_review_does_not_echo_unknown_parent_fields(self):
        result = entry.review_draft(self.payload())
        self.assertEqual(set(result), {"receipt_items", "item_weighments", "lot_allocations"})


class TestProcessorFirstDraftDownstreamGuards(unittest.TestCase):
    def test_checkpoint_requires_positive_fully_backed_quantities(self):
        doc = Record(receipt_items=[Record(company_accepted_qty=1, processor_material_credit_qty=0)])
        with patch.object(entry, "assert_draft_unlinked") as guard:
            controller.ProcessorLotReceipt._validate_processor_first_draft_checkpoint(doc)
            guard.assert_called_once_with(doc)
            for accepted, credit in ((0, 0), (-1, 0), (1, 1)):
                doc.receipt_items[0].company_accepted_qty = accepted
                doc.receipt_items[0].processor_material_credit_qty = credit
                with self.subTest(accepted=accepted, credit=credit), self.assertRaises(frappe.ValidationError):
                    controller.ProcessorLotReceipt._validate_processor_first_draft_checkpoint(doc)

    def test_downstream_entry_points_stop_on_the_j2_marker(self):
        doc = Record(processor_first_draft_only=1)
        with patch.object(controller.frappe, "get_doc", return_value=doc), patch.object(
            controller.frappe.db, "exists", return_value=True,
        ):
            for function in (
                controller.create_material_credit_record,
                controller.create_material_credit_stock_entry,
                controller.refresh_draft_subcontracting_receipt,
                controller.make_subcontracting_receipt,
            ):
                with self.subTest(function=function.__name__), self.assertRaisesRegex(frappe.ValidationError, "Downstream document"):
                    function("PLR-J2")

    def test_guard_allows_legacy_and_blocks_j2(self):
        controller._block_processor_first_downstream(Record(processor_first_draft_only=0))
        with self.assertRaisesRegex(frappe.ValidationError, "Downstream document"):
            controller._block_processor_first_downstream(Record(processor_first_draft_only=1))

    def test_scr_validation_stops_before_document_lookup(self):
        with self.assertRaisesRegex(frappe.ValidationError, "Downstream document"):
            controller._validate_scr_creation(Record(processor_first_draft_only=1))
