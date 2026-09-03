"""J7 Draft Purchase Receipt boundary tests; no documents are saved."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from subcontracting_extensions.scripts import purchase_receipt
from subcontracting_extensions.overrides import subcontracting_receipt as scr_override


class Record(SimpleNamespace):
    def get(self, key, default=None):
        return getattr(self, key, default)


class TestProcessorFirstDraftPR(unittest.TestCase):
    def setUp(self):
        self.scr_item = Record(
            name="SCR-ITEM",
            warehouse="TARGET-WH",
        )
        self.scr = Record(
            name="MAT-SCR-V2",
            docstatus=1,
            custom_processor_lot_receipt="PLR-V2",
            items=[self.scr_item],
        )
        self.allocation = Record(
            subcontracting_receipt_item="SCR-ITEM",
            purchase_order="PO-1",
            purchase_order_item="PO-ITEM-1",
            stock_uom="Kg",
            allocated_invoice_qty=25,
        )
        self.plr = Record(
            name="PLR-V2",
            receipt_structure_version="V2 Itemized",
            processor_first_draft_only=1,
            subcontracting_receipt="MAT-SCR-V2",
            purchase_receipt=None,
            lot_allocations=[self.allocation],
        )
        self.pr = Record(
            name="PR-V2",
            subcontracting_receipt="MAT-SCR-V2",
            items=[Record(
                idx=1,
                subcontracting_receipt_item="SCR-ITEM",
                purchase_order="PO-1",
                purchase_order_item="PO-ITEM-1",
                warehouse="TARGET-WH",
                stock_uom="Kg",
                stock_qty=25,
            )],
        )

    def get_doc(self, doctype, name):
        return self.scr if doctype == "Subcontracting Receipt" else self.plr

    def test_exact_draft_purchase_receipt_is_accepted(self):
        with patch.object(
            purchase_receipt.frappe,
            "conf",
            frappe._dict(v2_processor_first_draft_pr=1),
        ), patch.object(
            purchase_receipt.frappe,
            "get_doc",
            side_effect=self.get_doc,
        ):
            purchase_receipt.validate_processor_first_draft_purchase_receipt(
                self.pr
            )

    def test_commercial_quantity_mismatch_is_rejected(self):
        self.pr.items[0].stock_qty = 24
        with patch.object(
            purchase_receipt.frappe,
            "conf",
            frappe._dict(v2_processor_first_draft_pr=1),
        ), patch.object(
            purchase_receipt.frappe,
            "get_doc",
            side_effect=self.get_doc,
        ), self.assertRaisesRegex(
            frappe.ValidationError,
            "does not match its allocation",
        ):
            purchase_receipt.validate_processor_first_draft_purchase_receipt(
                self.pr
            )

    def test_purchase_receipt_submission_requires_j8_opt_in(self):
        with patch.object(
            purchase_receipt.frappe,
            "conf",
            frappe._dict(v2_processor_first_pr_submit=0),
        ), patch.object(
            purchase_receipt.frappe,
            "get_doc",
            side_effect=self.get_doc,
        ), self.assertRaisesRegex(
            frappe.ValidationError,
            "submission is not enabled",
        ):
            purchase_receipt.prevent_processor_first_purchase_receipt_submit(
                self.pr
            )

    def test_legacy_purchase_receipt_is_unchanged(self):
        self.pr.subcontracting_receipt = None
        purchase_receipt.validate_processor_first_draft_purchase_receipt(
            self.pr
        )
        purchase_receipt.prevent_processor_first_purchase_receipt_submit(
            self.pr
        )

    def test_mapper_allows_draft_but_rejects_direct_submit(self):
        source = Record(
            name="MAT-SCR-V2",
            docstatus=1,
            custom_processor_lot_receipt="PLR-V2",
        )
        plr_values = frappe._dict(
            receipt_structure_version="V2 Itemized",
            processor_first_draft_only=1,
            purchase_receipt=None,
        )
        with patch.object(
            scr_override.frappe,
            "conf",
            frappe._dict(v2_processor_first_draft_pr=1),
        ), patch.object(
            scr_override.frappe.db,
            "get_value",
            return_value=plr_values,
        ):
            scr_override._block_processor_first_v2_purchase_receipt(
                source
            )
            with self.assertRaisesRegex(
                frappe.ValidationError,
                "submission is not enabled",
            ):
                scr_override._block_processor_first_v2_purchase_receipt(
                    source,
                    submit=True,
                )


if __name__ == "__main__":
    unittest.main()
