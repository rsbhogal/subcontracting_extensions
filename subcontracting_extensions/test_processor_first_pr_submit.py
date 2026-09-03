"""J8 Purchase Receipt submission boundary tests; no documents are saved."""

import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from subcontracting_extensions.scripts import purchase_receipt


class Record(SimpleNamespace):
	def get(self, key, default=None):
		return getattr(self, key, default)


class TestProcessorFirstPRSubmit(unittest.TestCase):
	def setUp(self):
		self.scr_item = Record(name="SCR-ITEM", warehouse="TARGET-WH")
		self.scr = Record(
			name="MAT-SCR-V2",
			docstatus=1,
			custom_processor_lot_receipt="PLR-V2",
			items=[self.scr_item],
			posting_date=date(2026, 9, 1),
			posting_time=timedelta(hours=12),
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
			purchase_receipt="PR-V2",
			lot_allocations=[self.allocation],
		)
		self.pr = Record(
			name="PR-V2",
			subcontracting_receipt="MAT-SCR-V2",
			posting_date=date(2026, 9, 1),
			posting_time=timedelta(hours=11),
			set_posting_time=1,
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

	def flags(self, submit):
		return frappe._dict(
			v2_processor_first_draft_pr=1,
			v2_processor_first_pr_submit=submit,
		)

	def test_submission_requires_separate_site_opt_in(self):
		with patch.object(purchase_receipt.frappe, "conf", self.flags(0)), patch.object(
			purchase_receipt.frappe, "get_doc", side_effect=self.get_doc
		), self.assertRaisesRegex(frappe.ValidationError, "submission is not enabled"):
			purchase_receipt.prevent_processor_first_purchase_receipt_submit(self.pr)

	def test_exact_purchase_receipt_can_submit_and_moves_after_scr(self):
		with patch.object(purchase_receipt.frappe, "conf", self.flags(1)), patch.object(
			purchase_receipt.frappe, "get_doc", side_effect=self.get_doc
		):
			purchase_receipt.prevent_processor_first_purchase_receipt_submit(self.pr)

		self.assertEqual(self.pr.posting_date, self.scr.posting_date)
		self.assertEqual(self.pr.posting_time, self.scr.posting_time + timedelta(seconds=1))
		self.assertEqual(self.pr.set_posting_time, 1)

	def test_later_purchase_receipt_timestamp_is_not_moved_back(self):
		self.pr.posting_time = timedelta(hours=13)
		with patch.object(purchase_receipt.frappe, "conf", self.flags(1)), patch.object(
			purchase_receipt.frappe, "get_doc", side_effect=self.get_doc
		):
			purchase_receipt.prevent_processor_first_purchase_receipt_submit(self.pr)

		self.assertEqual(self.pr.posting_time, timedelta(hours=13))

	def test_changed_draft_row_is_rejected_at_submit(self):
		self.pr.items[0].stock_qty = 24
		with patch.object(purchase_receipt.frappe, "conf", self.flags(1)), patch.object(
			purchase_receipt.frappe, "get_doc", side_effect=self.get_doc
		), self.assertRaisesRegex(frappe.ValidationError, "does not match its allocation"):
			purchase_receipt.prevent_processor_first_purchase_receipt_submit(self.pr)

	def test_legacy_purchase_receipt_keeps_existing_submit_path(self):
		self.pr.subcontracting_receipt = None
		with patch.object(purchase_receipt.frappe, "conf", self.flags(0)):
			purchase_receipt.prevent_processor_first_purchase_receipt_submit(self.pr)


if __name__ == "__main__":
	unittest.main()
