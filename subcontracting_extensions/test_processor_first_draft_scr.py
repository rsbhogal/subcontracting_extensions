"""J5 tests use mocked mappers and database reads; no documents are saved."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from subcontracting_extensions import receipt_entry_preview
from subcontracting_extensions.scripts import subcontracting_receipt
from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot_receipt import (
    processor_lot_receipt as controller,
)


def allocation(
    idx,
    sco,
    sco_item,
    po_item,
    item,
    uom,
    qty,
):
    return frappe._dict(
        idx=idx,
        subcontracting_order=sco,
        subcontracting_order_item=sco_item,
        purchase_order_item=po_item,
        processed_item=item,
        stock_uom=uom,
        allocated_accepted_qty=qty,
    )


def mapped_item(sco_item, po_item, item, uom):
    return frappe._dict(
        subcontracting_order_item=sco_item,
        purchase_order_item=po_item,
        item_code=item,
        stock_uom=uom,
        qty=999,
        rejected_qty=7,
        branch=None,
    )


class Target:
    def __init__(self):
        self.items = []
        self.branch = None
        self.custom_processor_lot = None
        self.custom_processor_lot_receipt = None
        self.set_posting_time = 0
        self.posting_date = None


class TestProcessorFirstDraftSCR(unittest.TestCase):
    def setUp(self):
        self.allocations = [
            allocation(
                1,
                "SCO-NEW",
                "SCO-CUP",
                "PO-CUP",
                "CUP-FG",
                "Units",
                100,
            ),
            allocation(
                2,
                "SCO-OLD",
                "SCO-WIRE-OLD",
                "PO-WIRE-OLD",
                "WIRE-FG",
                "Kg",
                8590,
            ),
            allocation(
                3,
                "SCO-NEW",
                "SCO-WIRE-NEW",
                "PO-WIRE-NEW",
                "WIRE-FG",
                "Kg",
                500,
            ),
        ]
        self.plr = frappe._dict(
            name="PLR-V2",
            processor_lot=None,
            physical_receipt_date="2026-09-01",
            branch=None,
            lot_allocations=self.allocations,
        )
        self.target = Target()

    def mapper(self, source_name, target_doc):
        target_doc = target_doc or Target()
        rows = {
            "SCO-NEW": [
                mapped_item(
                    "SCO-CUP",
                    "PO-CUP",
                    "CUP-FG",
                    "Units",
                ),
                mapped_item(
                    "SCO-WIRE-NEW",
                    "PO-WIRE-NEW",
                    "WIRE-FG",
                    "Kg",
                ),
            ],
            "SCO-OLD": [
                mapped_item(
                    "SCO-WIRE-OLD",
                    "PO-WIRE-OLD",
                    "WIRE-FG",
                    "Kg",
                ),
            ],
        }
        target_doc.items.extend(rows[source_name])
        return target_doc

    def test_maps_each_sco_once_and_sizes_exact_rows(self):
        mapper = Mock(side_effect=self.mapper)
        with patch(
            "erpnext.subcontracting.doctype.subcontracting_order."
            "subcontracting_order.get_mapped_subcontracting_receipt",
            mapper,
        ):
            result = controller._make_v2_subcontracting_receipt(
                self.plr,
                None,
            )

        self.assertEqual(
            [call.args[0] for call in mapper.call_args_list],
            ["SCO-NEW", "SCO-OLD"],
        )
        quantities = {
            (
                row.subcontracting_order_item,
                row.purchase_order_item,
            ): row.qty
            for row in result.items
        }
        self.assertEqual(
            quantities,
            {
                ("SCO-CUP", "PO-CUP"): 100.0,
                ("SCO-WIRE-NEW", "PO-WIRE-NEW"): 500.0,
                ("SCO-WIRE-OLD", "PO-WIRE-OLD"): 8590.0,
            },
        )
        self.assertTrue(
            all(row.rejected_qty == 0 for row in result.items)
        )
        self.assertEqual(result.custom_processor_lot_receipt, "PLR-V2")
        self.assertEqual(result.posting_date, "2026-09-01")

    def test_duplicate_lineage_is_rejected_before_mapping(self):
        self.plr.lot_allocations.append(
            frappe._dict(self.allocations[0])
        )
        mapper = Mock()
        with patch(
            "erpnext.subcontracting.doctype.subcontracting_order."
            "subcontracting_order.get_mapped_subcontracting_receipt",
            mapper,
        ), self.assertRaisesRegex(
            frappe.ValidationError,
            "repeats SCO Item",
        ):
            controller._make_v2_subcontracting_receipt(
                self.plr,
                self.target,
            )
        mapper.assert_not_called()

    def test_unrepresented_mapped_row_is_rejected(self):
        def mapper_with_extra(source_name, target_doc):
            self.mapper(source_name, target_doc)
            if source_name == "SCO-NEW":
                target_doc.items.append(
                    mapped_item(
                        "SCO-EXTRA",
                        "PO-EXTRA",
                        "EXTRA-FG",
                        "Kg",
                    )
                )
            return target_doc

        with patch(
            "erpnext.subcontracting.doctype.subcontracting_order."
            "subcontracting_order.get_mapped_subcontracting_receipt",
            side_effect=mapper_with_extra,
        ), self.assertRaisesRegex(
            frappe.ValidationError,
            "additional open row",
        ):
            controller._make_v2_subcontracting_receipt(
                self.plr,
                self.target,
            )

    def test_draft_scr_requires_separate_site_opt_in(self):
        plr = frappe._dict(processor_first_draft_only=1)
        with patch.object(
            controller.frappe,
            "conf",
            frappe._dict(v2_processor_first_draft_scr=0),
        ), self.assertRaises(frappe.ValidationError):
            controller._block_processor_first_downstream(
                plr,
                allow_draft_scr=True,
            )

        with patch.object(
            controller.frappe,
            "conf",
            frappe._dict(v2_processor_first_draft_scr=1),
        ):
            controller._block_processor_first_downstream(
                plr,
                allow_draft_scr=True,
            )

    def test_entry_mode_reports_both_independent_flags(self):
        with patch.object(
            receipt_entry_preview.frappe,
            "conf",
            frappe._dict(
                v2_processor_first_draft_entry=1,
                v2_processor_first_draft_scr=1,
            ),
        ):
            self.assertEqual(
                receipt_entry_preview.get_entry_mode(),
                {
                    "draft_entry_enabled": True,
                    "draft_scr_enabled": True,
                },
            )

    def test_processor_first_scr_submission_remains_blocked(self):
        doc = SimpleNamespace(
            custom_processor_lot_receipt="PLR-V2"
        )
        with patch.object(
            subcontracting_receipt.frappe.db,
            "get_value",
            return_value=frappe._dict(
                receipt_structure_version="V2 Itemized",
                processor_first_draft_only=1,
            ),
        ), self.assertRaisesRegex(
            frappe.ValidationError,
            "Keep this document in Draft",
        ):
            subcontracting_receipt.prevent_processor_first_checkpoint_submit(
                doc
            )


if __name__ == "__main__":
    unittest.main()
