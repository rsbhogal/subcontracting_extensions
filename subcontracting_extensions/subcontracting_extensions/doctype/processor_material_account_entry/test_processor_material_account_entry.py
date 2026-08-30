# Copyright (c) 2026, R.S. Bhogal and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from . import processor_material_account_entry as controller


class TestProcessorMaterialAccountEntry(FrappeTestCase):
	"""Regression coverage for PLR excess-credit reversal controls."""

	def test_material_credit_status_lifecycle(self):
		self.assertEqual(
			controller._get_material_credit_status(170.0, 0.0),
			"Recorded",
		)
		self.assertEqual(
			controller._get_material_credit_status(170.0, 30.0),
			"Partly Applied",
		)
		self.assertEqual(
			controller._get_material_credit_status(170.0, 170.0),
			"Fully Applied",
		)

	def test_v2_credit_anchor_is_last_positive_item_allocation(self):
		plr = frappe._dict(
			name="PLR-TEST-V2",
			receipt_structure_version=controller.V2_RECEIPT_STRUCTURE,
			receipt_items=[
				frappe._dict(item_key="ITEM-001"),
			],
			lot_allocations=[
				frappe._dict(
					receipt_item_key="ITEM-001",
					processor_lot="PL-001",
					subcontracting_order="SCO-001",
					allocated_accepted_qty=5,
				),
				frappe._dict(
					receipt_item_key="ITEM-001",
					processor_lot="PL-002",
					subcontracting_order="SCO-002",
					allocated_accepted_qty=7,
				),
			],
		)

		anchor = controller._get_v2_credit_anchor(plr, "ITEM-001")

		self.assertEqual(anchor.processor_lot, "PL-002")
		self.assertEqual(anchor.subcontracting_order, "SCO-002")

	def test_v2_credit_requires_positive_backed_allocation(self):
		plr = frappe._dict(
			name="PLR-TEST-V2",
			receipt_structure_version=controller.V2_RECEIPT_STRUCTURE,
			receipt_items=[
				frappe._dict(item_key="ITEM-001"),
			],
			lot_allocations=[],
		)

		with self.assertRaisesRegex(
			frappe.ValidationError,
			"requires a positive Lot Allocation",
		):
			controller._get_v2_credit_anchor(plr, "ITEM-001")

	def _entry(self):
		return SimpleNamespace(
			name="PMA-TEST-CREDIT",
			processor_lot_receipt="PLR-TEST-CREDIT",
			material_credit_stock_entry="STE-TEST-CREDIT",
		)

	def test_credit_cancellation_requires_stock_documents_reversed(self):
		entry = self._entry()
		plr = SimpleNamespace(
			name="PLR-TEST-CREDIT",
			material_credit_stock_entry="STE-TEST-CREDIT",
			subcontracting_receipt="SCR-TEST-BACKED",
		)

		def get_value(doctype, name, fieldname):
			return {
				"Stock Entry": 1,
				"Subcontracting Receipt": 1,
			}[doctype]

		with patch.object(
			controller.frappe,
			"get_doc",
			return_value=plr,
		), patch.object(
			controller.frappe.db,
			"get_value",
			side_effect=get_value,
		):
			with self.assertRaisesRegex(
				frappe.ValidationError,
				"cancel Stock Entry.*cancel Subcontracting Receipt",
			):
				(
					controller.ProcessorMaterialAccountEntry
					._validate_source_documents_before_cancel(entry)
				)

	def test_credit_cancellation_allows_reversed_stock_documents(self):
		entry = self._entry()
		plr = SimpleNamespace(
			name="PLR-TEST-CREDIT",
			material_credit_stock_entry="STE-TEST-CREDIT",
			subcontracting_receipt="SCR-TEST-BACKED",
		)

		with patch.object(
			controller.frappe,
			"get_doc",
			return_value=plr,
		), patch.object(
			controller.frappe.db,
			"get_value",
			return_value=2,
		):
			(
				controller.ProcessorMaterialAccountEntry
				._validate_source_documents_before_cancel(entry)
			)
