"""J6 submission checks are isolated unit tests; no documents are saved."""

import unittest
from datetime import date, time
from unittest.mock import patch

import frappe

from subcontracting_extensions.overrides import subcontracting_receipt as receipt_override
from subcontracting_extensions.scripts import subcontracting_receipt


def allocation(idx, key, scr_item, item, uom, qty, po_item):
    return frappe._dict(
        idx=idx,
        receipt_item_key=key,
        subcontracting_receipt_item=scr_item,
        subcontracting_order="SCO-1",
        subcontracting_order_item=f"SCO-{idx}",
        purchase_order="PO-1",
        purchase_order_item=po_item,
        processed_item=item,
        stock_uom=uom,
        allocated_accepted_qty=qty,
    )


def scr_item(name, idx, item, uom, qty, po_item, warehouse):
    return frappe._dict(
        name=name,
        idx=idx,
        subcontracting_order="SCO-1",
        subcontracting_order_item=f"SCO-{idx}",
        purchase_order="PO-1",
        purchase_order_item=po_item,
        item_code=item,
        stock_uom=uom,
        qty=qty,
        warehouse=warehouse,
    )


class TestProcessorFirstSCRSubmit(unittest.TestCase):
    def setUp(self):
        self.doc = frappe._dict(
            name="MAT-SCR-V2",
            custom_processor_lot_receipt="PLR-V2",
            company="COMPANY",
            supplier="SUPPLIER",
            supplier_warehouse="SUPPLIER-WH",
            is_return=0,
            items=[
                scr_item("SCR-CUP", 1, "CUP", "Units", 100, "PO-CUP", "CUP-WH"),
                scr_item("SCR-WIRE", 2, "WIRE", "Kg", 500, "PO-WIRE", "WIRE-WH"),
            ],
            supplied_items=[
                frappe._dict(
                    idx=1,
                    reference_name="SCR-CUP",
                    required_qty=100,
                    consumed_qty=100,
                ),
                frappe._dict(
                    idx=2,
                    reference_name="SCR-WIRE",
                    required_qty=500,
                    consumed_qty=500,
                ),
            ],
        )
        self.plr = frappe._dict(
            name="PLR-V2",
            receipt_structure_version="V2 Itemized",
            processor_first_draft_only=1,
            subcontracting_receipt="MAT-SCR-V2",
            company="COMPANY",
            supplier="SUPPLIER",
            supplier_warehouse="SUPPLIER-WH",
            receipt_items=[
                frappe._dict(
                    item_key="ITEM-CUP",
                    company_accepted_qty=100,
                    processor_material_credit_qty=0,
                ),
                frappe._dict(
                    item_key="ITEM-WIRE",
                    company_accepted_qty=500,
                    processor_material_credit_qty=0,
                ),
            ],
            lot_allocations=[
                allocation(1, "ITEM-CUP", "SCR-CUP", "CUP", "Units", 100, "PO-CUP"),
                allocation(2, "ITEM-WIRE", "SCR-WIRE", "WIRE", "Kg", 500, "PO-WIRE"),
            ],
        )

    def po_item(self, doctype, name, fields, as_dict=False):
        warehouses = {"PO-CUP": "CUP-WH", "PO-WIRE": "WIRE-WH"}
        return frappe._dict(parent="PO-1", warehouse=warehouses[name])

    def test_opted_in_submission_validates_exact_rows_and_materials(self):
        def get_value(doctype, name, fields, as_dict=False):
            if doctype == "Processor Lot Receipt":
                return frappe._dict(
                    receipt_structure_version="V2 Itemized",
                    processor_first_draft_only=1,
                )
            return self.po_item(doctype, name, fields, as_dict)

        with patch.object(
            subcontracting_receipt.frappe,
            "conf",
            frappe._dict(v2_processor_first_scr_submit=1),
        ), patch.object(
            subcontracting_receipt.frappe.db,
            "get_value",
            side_effect=get_value,
        ), patch.object(
            subcontracting_receipt.frappe.db,
            "get_single_value",
            return_value=0,
        ), patch.object(
            subcontracting_receipt.frappe,
            "get_doc",
            return_value=self.plr,
        ):
            subcontracting_receipt.prevent_processor_first_checkpoint_submit(
                self.doc
            )

    def test_wrong_item_warehouse_is_rejected(self):
        self.doc["items"][0].warehouse = "WRONG-WH"
        with patch.object(
            subcontracting_receipt.frappe.db,
            "get_value",
            side_effect=self.po_item,
        ), self.assertRaisesRegex(
            frappe.ValidationError,
            "target warehouse",
        ):
            subcontracting_receipt._validate_processor_first_v2_submit(
                self.doc,
                self.plr,
            )

    def test_partial_consumption_is_rejected(self):
        self.doc["supplied_items"][1].consumed_qty = 499
        with patch.object(
            subcontracting_receipt.frappe.db,
            "get_value",
            side_effect=self.po_item,
        ), self.assertRaisesRegex(
            frappe.ValidationError,
            "Consumed Qty must equal Required Qty",
        ):
            subcontracting_receipt._validate_processor_first_v2_submit(
                self.doc,
                self.plr,
            )

    def test_auto_purchase_receipt_setting_blocks_submit(self):
        with patch.object(
            subcontracting_receipt.frappe,
            "conf",
            frappe._dict(v2_processor_first_scr_submit=1),
        ), patch.object(
            subcontracting_receipt.frappe.db,
            "get_value",
            return_value=frappe._dict(
                receipt_structure_version="V2 Itemized",
                processor_first_draft_only=1,
            ),
        ), patch.object(
            subcontracting_receipt.frappe.db,
            "get_single_value",
            return_value=1,
        ), self.assertRaisesRegex(
            frappe.ValidationError,
            "Disable Auto Create Purchase Receipt",
        ):
            subcontracting_receipt.prevent_processor_first_checkpoint_submit(
                self.doc
            )

    def test_scr_moves_one_second_after_latest_material_transfer(self):
        self.doc.update(
            {
                "posting_date": date(2026, 9, 1),
                "posting_time": time(8, 32, 33),
                "set_posting_time": 1,
                "doc_references": [
                    frappe._dict(
                        link_doctype="Stock Entry",
                        link_name="MAT-STE-OLD",
                    ),
                    frappe._dict(
                        link_doctype="Stock Entry",
                        link_name="MAT-STE-LATEST",
                    ),
                ],
            }
        )
        with patch.object(
            subcontracting_receipt.frappe,
            "get_all",
            return_value=[
                frappe._dict(
                    name="MAT-STE-OLD",
                    posting_date=date(2026, 7, 28),
                    posting_time=time(6, 56, 51),
                ),
                frappe._dict(
                    name="MAT-STE-LATEST",
                    posting_date=date(2026, 9, 1),
                    posting_time=time(11, 13, 23, 804280),
                ),
            ],
        ):
            subcontracting_receipt._align_after_referenced_material_transfers(
                self.doc
            )

        self.assertEqual(self.doc.posting_date, date(2026, 9, 1))
        self.assertEqual(
            self.doc.posting_time,
            time(11, 13, 24, 804280),
        )

    def test_later_scr_timestamp_is_not_moved_back(self):
        self.doc.update(
            {
                "posting_date": date(2026, 9, 1),
                "posting_time": time(12, 0, 0),
                "set_posting_time": 1,
                "doc_references": [
                    frappe._dict(
                        link_doctype="Stock Entry",
                        link_name="MAT-STE-1",
                    )
                ],
            }
        )
        with patch.object(
            subcontracting_receipt.frappe,
            "get_all",
            return_value=[
                frappe._dict(
                    name="MAT-STE-1",
                    posting_date=date(2026, 9, 1),
                    posting_time=time(11, 13, 23),
                )
            ],
        ):
            subcontracting_receipt._align_after_referenced_material_transfers(
                self.doc
            )

        self.assertEqual(self.doc.posting_time, time(12, 0, 0))

    def test_manual_purchase_receipt_is_blocked(self):
        with patch.object(
            receipt_override.frappe.db,
            "get_value",
            return_value=frappe._dict(
                receipt_structure_version="V2 Itemized",
                processor_first_draft_only=1,
            ),
        ), self.assertRaisesRegex(
            frappe.ValidationError,
            "Purchase Receipt creation is not enabled",
        ):
            receipt_override._block_processor_first_v2_purchase_receipt(
                self.doc
            )

    def test_legacy_receipt_keeps_existing_submit_path(self):
        with patch.object(
            subcontracting_receipt.frappe.db,
            "get_value",
            return_value=frappe._dict(
                receipt_structure_version="Legacy Header",
                processor_first_draft_only=0,
            ),
        ):
            subcontracting_receipt.prevent_processor_first_checkpoint_submit(
                self.doc
            )


if __name__ == "__main__":
    unittest.main()
