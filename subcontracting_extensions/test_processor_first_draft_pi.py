"""J9 Draft Purchase Invoice boundary tests; no documents are saved."""

import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import ANY, patch

import frappe

from subcontracting_extensions.overrides import purchase_receipt as pr_override
from subcontracting_extensions.scripts import purchase_invoice


class Record(SimpleNamespace):
	def get(self, key, default=None):
		return getattr(self, key, default)


class TestProcessorFirstDraftPI(unittest.TestCase):
	def setUp(self):
		self.pr_items = [
			Record(
				name="PR-ITEM-1", idx=1, item_code="SERVICE-A",
				purchase_order="PO-1", purchase_order_item="PO-ITEM-1",
				stock_uom="Kg", qty=500, stock_qty=500,
			),
			Record(
				name="PR-ITEM-2", idx=2, item_code="SERVICE-B",
				purchase_order="PO-2", purchase_order_item="PO-ITEM-2",
				stock_uom="Units", qty=100, stock_qty=100,
			),
		]
		self.pr = Record(
			name="PR-V2", docstatus=1, subcontracting_receipt="SCR-V2",
			posting_date=date(2026, 9, 1),
			posting_time=timedelta(hours=12), items=self.pr_items,
		)
		self.scr = Record(
			name="SCR-V2", custom_processor_lot_receipt="PLR-V2",
		)
		self.plr = Record(
			name="PLR-V2", receipt_structure_version="V2 Itemized",
			processor_first_draft_only=1, subcontracting_receipt="SCR-V2",
			purchase_receipt="PR-V2", purchase_invoice=None,
		)
		self.pi = Record(
			name=None, is_return=0,
			items=[
				Record(
					idx=1, item_code="SERVICE-A", purchase_receipt="PR-V2",
					pr_detail="PR-ITEM-1", purchase_order="PO-1",
					po_detail="PO-ITEM-1", stock_uom="Kg", qty=500, stock_qty=500,
				),
				Record(
					idx=2, item_code="SERVICE-B", purchase_receipt="PR-V2",
					pr_detail="PR-ITEM-2", purchase_order="PO-2",
					po_detail="PO-ITEM-2", stock_uom="Units", qty=100, stock_qty=100,
				),
			],
		)

	def get_doc(self, doctype, name):
		return {
			"Purchase Receipt": self.pr,
			"Subcontracting Receipt": self.scr,
			"Processor Lot Receipt": self.plr,
		}[doctype]

	def validate(self, enabled=1):
		with patch.dict(
			purchase_invoice.frappe.conf,
			{"v2_processor_first_draft_pi": enabled},
		), patch.object(
			purchase_invoice.frappe, "get_doc", side_effect=self.get_doc,
		):
			purchase_invoice.validate_processor_first_draft_purchase_invoice(
				self.pi
			)

	def test_exact_multi_po_purchase_invoice_is_accepted(self):
		self.validate()

	def test_draft_purchase_invoice_requires_j9_opt_in(self):
		with self.assertRaisesRegex(
			frappe.ValidationError, "creation is disabled"
		):
			self.validate(enabled=0)

	def test_missing_purchase_receipt_row_is_rejected(self):
		self.pi.items.pop()
		with self.assertRaisesRegex(
			frappe.ValidationError, "do not exactly match"
		):
			self.validate()

	def test_changed_quantity_is_rejected(self):
		self.pi.items[0].qty = 499
		with self.assertRaisesRegex(
			frappe.ValidationError, "does not match"
		):
			self.validate()

	def test_changed_po_lineage_is_rejected(self):
		self.pi.items[1].po_detail = "OTHER-PO-ITEM"
		with self.assertRaisesRegex(
			frappe.ValidationError, "does not match"
		):
			self.validate()

	def test_mixed_purchase_receipts_are_rejected(self):
		self.pi.items[1].purchase_receipt = "PR-OTHER"
		ordinary_pr = Record(
			name="PR-OTHER", docstatus=1, subcontracting_receipt=None, items=[],
		)

		def get_doc(doctype, name):
			if doctype == "Purchase Receipt" and name == "PR-OTHER":
				return ordinary_pr
			return self.get_doc(doctype, name)

		with patch.object(
			purchase_invoice.frappe, "get_doc", side_effect=get_doc,
		), self.assertRaisesRegex(
			frappe.ValidationError, "exactly one Purchase Receipt"
		):
			purchase_invoice.validate_processor_first_draft_purchase_invoice(
				self.pi
			)

	def test_saved_pi_cannot_replace_all_controlled_rows(self):
		self.pi.name = "PI-V2"
		self.pi.items = [
			Record(
				idx=1, item_code="OTHER-SERVICE",
				purchase_receipt="PR-OTHER", pr_detail="PR-OTHER-ITEM",
				purchase_order="PO-OTHER", po_detail="PO-OTHER-ITEM",
				stock_uom="Kg", qty=1, stock_qty=1,
			)
		]
		self.plr.purchase_invoice = "PI-V2"
		ordinary_pr = Record(
			name="PR-OTHER", docstatus=1, subcontracting_receipt=None, items=[],
		)

		def get_doc(doctype, name):
			if doctype == "Purchase Receipt" and name == "PR-OTHER":
				return ordinary_pr
			return self.get_doc(doctype, name)

		with patch.object(
			purchase_invoice.frappe, "get_doc", side_effect=get_doc,
		), patch.object(
			purchase_invoice.frappe.db, "get_value", return_value="PLR-V2",
		), self.assertRaisesRegex(
			frappe.ValidationError, "exactly one Purchase Receipt"
		):
			purchase_invoice.validate_processor_first_draft_purchase_invoice(
				self.pi
			)

	def test_existing_active_purchase_invoice_is_not_replaced(self):
		self.plr.purchase_invoice = "PI-EXISTING"
		with patch.object(
			purchase_invoice.frappe, "conf",
			frappe._dict(v2_processor_first_draft_pi=1),
		), patch.object(
			purchase_invoice.frappe, "get_doc", side_effect=self.get_doc,
		), patch.object(
			purchase_invoice.frappe.db, "get_value", return_value=0,
		), self.assertRaisesRegex(
			frappe.ValidationError, "already links"
		):
			purchase_invoice.validate_processor_first_draft_purchase_invoice(
				self.pi
			)

	def test_purchase_invoice_submission_remains_blocked(self):
		with patch.dict(
			purchase_invoice.frappe.conf,
			{"v2_processor_first_pi_submit": 0},
		), patch.object(
			purchase_invoice.frappe,
			"get_doc",
			side_effect=self.get_doc,
		), self.assertRaisesRegex(
			frappe.ValidationError,
			"submission is not enabled",
		):
			purchase_invoice.prevent_processor_first_purchase_invoice_submit(
				self.pi
			)

	def test_legacy_purchase_invoice_is_unchanged(self):
		self.pi.items[0].purchase_receipt = None
		self.pi.items[1].purchase_receipt = None
		purchase_invoice.validate_processor_first_draft_purchase_invoice(self.pi)
		purchase_invoice.prevent_processor_first_purchase_invoice_submit(self.pi)


class TestProcessorFirstDraftPIMapper(unittest.TestCase):
	def source(self):
		return Record(
			name="PR-V2", subcontracting_receipt="SCR-V2",
			posting_date=date(2026, 9, 1),
			posting_time=timedelta(hours=12),
		)

	def plr_values(self):
		return frappe._dict(
			receipt_structure_version="V2 Itemized",
			processor_first_draft_only=1,
		)

	def test_mapper_requires_separate_j9_opt_in(self):
		with patch.object(pr_override.frappe, "get_doc", return_value=self.source()), patch.object(
			pr_override, "_is_processor_lot_purchase_receipt", return_value=True,
		), patch.object(
			pr_override.frappe.db, "get_value",
			side_effect=["PLR-V2", self.plr_values()],
		), patch.object(
			pr_override.frappe, "conf",
			frappe._dict(v2_processor_first_pr_submit=1, v2_processor_first_draft_pi=0),
		), self.assertRaisesRegex(
			frappe.ValidationError, "J8 Purchase Receipt submission checkpoint"
		):
			pr_override.make_purchase_invoice("PR-V2")

	def test_mapper_uses_erpnext_then_applies_j9_controls(self):
		target = Record(
			posting_date=date(2026, 9, 3), posting_time=timedelta(hours=10),
			set_posting_time=0, items=[],
		)
		with patch.object(pr_override.frappe, "get_doc", return_value=self.source()), patch.object(
			pr_override, "_is_processor_lot_purchase_receipt", return_value=True,
		), patch.object(
			pr_override.frappe.db, "get_value",
			side_effect=["PLR-V2", self.plr_values()],
		), patch.object(
			pr_override.frappe, "conf",
			frappe._dict(v2_processor_first_draft_pi=1),
		), patch(
			"erpnext.stock.doctype.purchase_receipt.purchase_receipt.make_purchase_invoice",
			return_value=target,
		) as standard_mapper, patch.object(
			pr_override, "_set_purchase_order_payment_terms",
		), patch(
			"subcontracting_extensions.scripts.purchase_invoice."
			"validate_processor_first_draft_purchase_invoice",
		) as validate:
			result = pr_override.make_purchase_invoice("PR-V2")

		standard_mapper.assert_called_once_with(
			"PR-V2", target_doc=None, args=None
		)
		validate.assert_called_once_with(
			target, purchase_receipt=ANY
		)
		self.assertIs(result, target)
		self.assertEqual(result.posting_date, date(2026, 9, 1))
		self.assertEqual(result.posting_time, timedelta(hours=12, seconds=1))


if __name__ == "__main__":
	unittest.main()
