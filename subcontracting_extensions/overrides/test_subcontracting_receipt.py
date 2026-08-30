# Copyright (c) 2026, R S Bhogal and contributors
# See license.txt

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from subcontracting_extensions.overrides import subcontracting_receipt as controller


class AttributeObject(SimpleNamespace):
	"""Small in-memory object with Frappe-like field access."""

	def get(self, fieldname, default=None):
		return getattr(self, fieldname, default)

	def update(self, values):
		for fieldname, value in values.items():
			setattr(self, fieldname, value)

	def as_dict(self):
		return vars(self).copy()


class FakePurchaseReceipt(AttributeObject):
	"""In-memory Purchase Receipt used without database persistence."""

	def __init__(self):
		super().__init__(
			doctype="Purchase Receipt",
			name=None,
			company="Test Company",
			taxes=[],
			items=[],
			taxes_and_charges=None,
			saved=False,
			calculated=False,
			net_total=0,
		)

	def set(self, fieldname, value):
		setattr(self, fieldname, value)

	def append(self, fieldname, values):
		row = AttributeObject(**values)
		getattr(self, fieldname).append(row)
		return row

	def set_missing_values(self):
		return

	def run_method(self, method):
		if method != "calculate_taxes_and_totals":
			return

		self.calculated = True

		for item in self.items:
			item.amount = item.qty * item.rate
			item.net_amount = item.amount

		self.net_total = sum(
			item.net_amount for item in self.items
		)

	def save(self):
		self.saved = True


class TestSubcontractingReceiptPurchaseReceiptMapping(
	FrappeTestCase
):
	"""Regression coverage for PLR commercial-quantity PR mapping."""

	def _source_scr(self):
		return AttributeObject(
			name="SCR-TEST-COMMERCIAL",
			is_return=0,
			custom_processor_lot_receipt="PLR-TEST-COMMERCIAL",
			posting_date=date(2026, 8, 15),
			posting_time=timedelta(hours=10),
			supplier_warehouse="Processor Warehouse - TEST",
			items=[
				AttributeObject(
					name="SCR-ITEM-TEST",
					idx=1,
					purchase_order="PO-TEST-COMMERCIAL",
					purchase_order_item="PO-ITEM-TEST",
					qty=3900,
					rejected_qty=0,
					warehouse="Finished Goods Warehouse - TEST",
					rejected_warehouse=None,
				),
			],
		)

	def _fake_get_mapped_doc(
		self,
		source_doctype,
		source_name,
		mapping,
		target_doc=None,
	):
		target_doc = target_doc or FakePurchaseReceipt()

		source_po_item = AttributeObject(
			name="PO-ITEM-TEST",
			parent="PO-TEST-COMMERCIAL",
			qty=26580,
			fg_item_qty=26580,
			bom="BOM-TEST",
		)

		item_mapping = mapping["Purchase Order Item"]

		if item_mapping["condition"](source_po_item):
			target_item = AttributeObject(
				item_code="Processing Service Item",
				rate=2.3,
				qty=26580,
				amount=61134,
				net_amount=61134,
			)

			item_mapping["postprocess"](
				source_po_item,
				target_item,
				None,
			)
			target_doc.items.append(target_item)

		return target_doc

	def _map_purchase_receipt(self, commercial_qty_by_scr_item):
		source_scr = self._source_scr()

		with patch.object(
			controller,
			"_get_plr_invoice_qty_by_scr_item",
			return_value=commercial_qty_by_scr_item,
		), patch.object(
			controller,
			"_validate_shared_taxes",
			return_value="GST TEST",
		), patch.object(
			controller,
			"get_mapped_doc",
			side_effect=self._fake_get_mapped_doc,
		), patch.object(
			controller.frappe,
			"get_cached_value",
			return_value="INR",
		):
			return controller.make_purchase_receipt(
				source_scr,
				save=False,
				submit=False,
				notify=False,
			)

	def test_plr_invoice_quantity_is_aggregated_by_purchase_order(self):
		source_scr = self._source_scr()
		plr = AttributeObject(
			name="PLR-TEST-COMMERCIAL",
			subcontracting_receipt="SCR-TEST-COMMERCIAL",
			lot_allocations=[
				AttributeObject(
					idx=1,
					purchase_order="PO-TEST-COMMERCIAL",
					allocated_invoice_qty=4030,
				),
			],
		)

		with patch.object(
			controller.frappe.db,
			"exists",
			return_value=True,
		), patch.object(
			controller.frappe,
			"get_doc",
			return_value=plr,
		):
			result = (
				controller
				._get_plr_invoice_qty_by_purchase_order(
					source_scr
				)
			)

		self.assertEqual(
			result,
			{"PO-TEST-COMMERCIAL": 4030.0},
		)

	def test_v2_billed_credit_is_added_to_each_items_final_scr_row(self):
		source_scr = self._source_scr()
		source_scr.items[0].subcontracting_order_item = "SCO-A1"
		source_scr.items.extend([
			AttributeObject(
				name="SCR-ITEM-A2",
				idx=2,
				purchase_order="PO-TEST-COMMERCIAL",
				purchase_order_item="PO-A2",
				subcontracting_order_item="SCO-A2",
			),
			AttributeObject(
				name="SCR-ITEM-B1",
				idx=3,
				purchase_order="PO-TEST-COMMERCIAL",
				purchase_order_item="PO-B1",
				subcontracting_order_item="SCO-B1",
			),
		])
		plr = AttributeObject(
			name="PLR-TEST-COMMERCIAL",
			receipt_structure_version="V2 Itemized",
			subcontracting_receipt="SCR-TEST-COMMERCIAL",
			receipt_items=[
				AttributeObject(
					item_key="ITEM-A",
					material_credit_invoice_qty=5,
				),
				AttributeObject(
					item_key="ITEM-B",
					material_credit_invoice_qty=2,
				),
			],
			lot_allocations=[
				AttributeObject(
					idx=1,
					receipt_item_key="ITEM-A",
					subcontracting_receipt_item="SCR-ITEM-TEST",
					purchase_order_item="PO-ITEM-TEST",
					subcontracting_order_item="SCO-A1",
					allocated_accepted_qty=10,
					allocated_invoice_qty=10,
				),
				AttributeObject(
					idx=2,
					receipt_item_key="ITEM-A",
					subcontracting_receipt_item="SCR-ITEM-A2",
					purchase_order_item="PO-A2",
					subcontracting_order_item="SCO-A2",
					allocated_accepted_qty=20,
					allocated_invoice_qty=20,
				),
				AttributeObject(
					idx=3,
					receipt_item_key="ITEM-B",
					subcontracting_receipt_item="SCR-ITEM-B1",
					purchase_order_item="PO-B1",
					subcontracting_order_item="SCO-B1",
					allocated_accepted_qty=30,
					allocated_invoice_qty=30,
				),
			],
		)

		with patch.object(
			controller.frappe.db,
			"exists",
			return_value=True,
		), patch.object(
			controller.frappe,
			"get_doc",
			return_value=plr,
		):
			result = controller._get_plr_invoice_qty_by_scr_item(source_scr)

		self.assertEqual(result, {
			"SCR-ITEM-TEST": 10.0,
			"SCR-ITEM-A2": 25.0,
			"SCR-ITEM-B1": 32.0,
		})

	def test_billed_material_credit_is_added_to_last_backed_po(self):
		source_scr = self._source_scr()
		plr = AttributeObject(
			name="PLR-TEST-COMMERCIAL",
			subcontracting_receipt="SCR-TEST-COMMERCIAL",
			material_credit_invoice_qty=9,
			lot_allocations=[
				AttributeObject(
					idx=1,
					purchase_order="PO-TEST-COMMERCIAL",
					allocated_accepted_qty=1000,
					allocated_invoice_qty=1000,
				),
			],
		)

		with patch.object(
			controller.frappe.db,
			"exists",
			return_value=True,
		), patch.object(
			controller.frappe,
			"get_doc",
			return_value=plr,
		):
			result = (
				controller
				._get_plr_invoice_qty_by_purchase_order(
					source_scr
				)
			)

		self.assertEqual(
			result,
			{"PO-TEST-COMMERCIAL": 1009.0},
		)

	def test_ordinary_scr_has_no_commercial_quantity_override(self):
		source_scr = self._source_scr()
		source_scr.custom_processor_lot_receipt = None

		with patch.object(
			controller.frappe.db,
			"exists",
		) as exists:
			result = (
				controller
				._get_plr_invoice_qty_by_purchase_order(
					source_scr
				)
			)

		self.assertIsNone(result)
		exists.assert_not_called()

	def test_plr_linked_scr_maps_commercial_quantity_and_amount(self):
		purchase_receipt = self._map_purchase_receipt(
			{
				"SCR-ITEM-TEST": 4030,
			}
		)

		self.assertEqual(len(purchase_receipt.items), 1)

		item = purchase_receipt.items[0]

		self.assertEqual(item.qty, 4030)
		self.assertEqual(item.rate, 2.3)
		self.assertEqual(item.amount, 9269)
		self.assertEqual(item.net_amount, 9269)
		self.assertEqual(purchase_receipt.net_total, 9269)
		self.assertTrue(purchase_receipt.calculated)
		self.assertFalse(purchase_receipt.saved)

	def test_v2_commercial_quantity_is_keyed_by_exact_scr_item(self):
		source_scr = self._source_scr()
		source_scr.items.append(
			AttributeObject(
				name="SCR-ITEM-SECOND",
				idx=2,
				purchase_order="PO-TEST-COMMERCIAL",
				purchase_order_item="PO-ITEM-SECOND",
				subcontracting_order_item="SCO-ITEM-SECOND",
				qty=20,
				rejected_qty=0,
				warehouse="Finished Goods Warehouse - TEST",
				rejected_warehouse=None,
			)
		)
		source_scr.items[0].subcontracting_order_item = "SCO-ITEM-TEST"

		plr = AttributeObject(
			name="PLR-TEST-COMMERCIAL",
			receipt_structure_version="V2 Itemized",
			subcontracting_receipt="SCR-TEST-COMMERCIAL",
			receipt_items=[],
			lot_allocations=[
				AttributeObject(
					idx=1,
					subcontracting_receipt_item="SCR-ITEM-TEST",
					purchase_order_item="PO-ITEM-TEST",
					subcontracting_order_item="SCO-ITEM-TEST",
					allocated_invoice_qty=49,
				),
				AttributeObject(
					idx=2,
					subcontracting_receipt_item="SCR-ITEM-SECOND",
					purchase_order_item="PO-ITEM-SECOND",
					subcontracting_order_item="SCO-ITEM-SECOND",
					allocated_invoice_qty=18,
				),
			],
		)

		with patch.object(
			controller.frappe.db,
			"exists",
			return_value=True,
		), patch.object(
			controller.frappe,
			"get_doc",
			return_value=plr,
		):
			result = controller._get_plr_invoice_qty_by_scr_item(
				source_scr
			)

		self.assertEqual(
			result,
			{
				"SCR-ITEM-TEST": 49.0,
				"SCR-ITEM-SECOND": 18.0,
			},
		)

	def test_ordinary_scr_retains_physical_quantity_mapping(self):
		purchase_receipt = self._map_purchase_receipt(None)

		self.assertEqual(len(purchase_receipt.items), 1)

		item = purchase_receipt.items[0]

		self.assertEqual(item.qty, 3900)
		self.assertEqual(item.rate, 2.3)
		self.assertEqual(item.amount, 8970)
		self.assertEqual(item.net_amount, 8970)
		self.assertEqual(purchase_receipt.net_total, 8970)
		self.assertTrue(purchase_receipt.calculated)
		self.assertFalse(purchase_receipt.saved)
