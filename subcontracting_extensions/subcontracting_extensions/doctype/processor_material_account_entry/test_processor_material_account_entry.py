# Copyright (c) 2026, R.S. Bhogal and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from . import processor_material_account_entry as controller


class TestProcessorMaterialAccountEntry(FrappeTestCase):
	"""Regression coverage for PLR excess-credit reversal controls."""

	def _identity_entry(self, **values):
		defaults = dict(
			subcontracting_order="SCO",
			sco_supplied_item=None,
			processed_item=None,
			processed_item_uom=None,
			principal_component=None,
			account_uom=None,
			company=None,
			supplier=None,
			supplier_warehouse=None,
		)
		defaults.update(values)
		entry = frappe._dict(defaults)
		entry._set_identity_from_sco = lambda hint=None: (
			controller.ProcessorMaterialAccountEntry._set_identity_from_sco(
				entry, hint
			)
		)
		return entry

	def _identity_sco(self, repeated_component=False):
		second_component = "Wire" if repeated_component else "Blank"
		second_uom = "Kg" if repeated_component else "Units"
		return SimpleNamespace(
			name="SCO", docstatus=1, company="Company", supplier="Supplier",
			supplier_warehouse="Processor",
			items=[
				frappe._dict(name="FG-A", item_code="Finished A", stock_uom="Kg"),
				frappe._dict(name="FG-B", item_code="Finished B", stock_uom="Units"),
			],
			supplied_items=[
				frappe._dict(name="RM-A", reference_name="FG-A", main_item_code="Finished A",
					rm_item_code="Wire", stock_uom="Kg"),
				frappe._dict(name="RM-B", reference_name="FG-B", main_item_code="Finished B",
					rm_item_code=second_component, stock_uom=second_uom),
			],
		)

	def test_identity_sco_mock_exposes_child_items_not_mapping_method(self):
		sco = self._identity_sco()
		self.assertIsInstance(sco.items, list)
		self.assertEqual([row.name for row in sco.items], ["FG-A", "FG-B"])

	def test_exact_component_is_derived_from_v2_finished_row_anchor(self):
		entry = self._identity_entry()
		with patch.object(controller.frappe, "get_doc", return_value=self._identity_sco()):
			entry._set_identity_from_sco("FG-B")
		self.assertEqual(entry.sco_supplied_item, "RM-B")
		self.assertEqual((entry.processed_item, entry.principal_component), ("Finished B", "Blank"))

	def test_target_account_identity_resolves_its_own_sco_row(self):
		entry = self._identity_entry(processed_item="Finished A", processed_item_uom="Kg",
			principal_component="Wire", account_uom="Kg")
		with patch.object(controller.frappe, "get_doc", return_value=self._identity_sco()):
			entry._set_identity_from_sco()
		self.assertEqual(entry.sco_supplied_item, "RM-A")

	def test_repeated_component_uses_finished_row_not_row_order(self):
		entry = self._identity_entry()
		with patch.object(controller.frappe, "get_doc", return_value=self._identity_sco(True)):
			entry._set_identity_from_sco("FG-B")
		self.assertEqual(entry.sco_supplied_item, "RM-B")

	def test_multi_item_identity_without_anchor_fails_closed(self):
		entry = self._identity_entry()
		with patch.object(controller.frappe, "get_doc", return_value=self._identity_sco()):
			with self.assertRaisesRegex(frappe.ValidationError, "Cannot attribute"):
				entry._set_identity_from_sco()

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
