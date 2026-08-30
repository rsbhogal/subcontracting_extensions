# Copyright (c) 2026, R.S. Bhogal and Contributors
# See license.txt

from datetime import date, time, timedelta
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import call, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from . import processor_lot_receipt as controller


class TestProcessorLotReceipt(FrappeTestCase):
	"""Regression coverage for controlled Processor Material Credit stock."""

	def _v2_plr(self, items=None, allocations=None, weighments=None):
		plr = frappe.get_doc({
			"doctype": "Processor Lot Receipt",
			"receipt_structure_version": controller.V2_RECEIPT_STRUCTURE,
			"receipt_items": items or [],
			"lot_allocations": allocations or [],
			"item_weighments": weighments or [],
		})
		return plr

	def test_legacy_receipt_does_not_enter_v2_path(self):
		plr = frappe.get_doc({
			"doctype": "Processor Lot Receipt",
			"receipt_structure_version": controller.LEGACY_RECEIPT_STRUCTURE,
		})
		self.assertFalse(plr._uses_v2_item_structure())

	def test_v2_commercial_reconciliation_is_item_specific(self):
		plr = self._v2_plr(items=[
			{
				"item_key": "ITEM-001",
				"processed_item": "ITEM-A",
				"measurement_method": "Weight",
				"measurement_basis": "Separate Item Weight",
				"company_accepted_qty": 100.123456,
				"supplier_invoice_qty": 101.123456,
			},
			{
				"item_key": "ITEM-002",
				"processed_item": "ITEM-B",
				"measurement_method": "Count",
				"measurement_basis": "Direct Count",
				"company_accepted_qty": 20,
				"supplier_invoice_qty": 18,
			},
		])

		plr._calculate_v2_item_commercial_reconciliation()

		self.assertEqual(
			plr.receipt_items[0].supplier_invoice_vs_company_qty,
			1,
		)
		self.assertEqual(
			plr.receipt_items[1].supplier_invoice_vs_company_qty,
			-2,
		)

	def test_multi_item_v2_does_not_aggregate_header_quantities(self):
		plr = self._v2_plr(items=[
			{
				"item_key": "ITEM-001",
				"processed_item": "ITEM-A",
				"stock_uom": "Kg",
				"measurement_method": "Weight",
				"measurement_basis": "Separate Item Weight",
				"company_accepted_qty": 100,
			},
			{
				"item_key": "ITEM-002",
				"processed_item": "ITEM-B",
				"stock_uom": "Units",
				"measurement_method": "Count",
				"measurement_basis": "Direct Count",
				"company_accepted_qty": 20,
			},
		])
		plr.company_accepted_qty = 120

		plr._sync_single_v2_item_to_legacy_header()

		self.assertEqual(plr.company_accepted_qty, 0)
		self.assertIsNone(plr.stock_uom)
		self.assertIsNone(plr.processed_item)

	def test_v2_allocation_requires_existing_item_key(self):
		plr = self._v2_plr(
			items=[{
				"item_key": "ITEM-001",
				"processed_item": "ITEM-A",
				"stock_uom": "Kg",
				"measurement_method": "Weight",
				"measurement_basis": "Separate Item Weight",
			}],
			allocations=[{
				"receipt_item_key": "ITEM-999",
				"processed_item": "ITEM-A",
				"stock_uom": "Kg",
				"processor_lot": "LOT-A",
				"allocated_accepted_qty": 1,
			}],
		)

		with self.assertRaises(frappe.ValidationError):
			plr._validate_v2_allocation_links()

	def test_equivalent_posting_time_representations_match(self):
		time_delta = timedelta(
			seconds=36123,
			microseconds=129842,
		)
		self.assertTrue(
			controller._posting_times_match(
				time_delta,
				time(10, 2, 3, 129842),
			)
		)
		self.assertTrue(
			controller._posting_times_match(
				time_delta,
				"10:02:03.129842",
			)
		)

	def test_different_posting_time_is_rejected(self):
		self.assertFalse(
			controller._posting_times_match(
				timedelta(
					seconds=36123,
					microseconds=129842,
				),
				"10:02:03.129843",
			)
		)

	def test_mixed_receipt_attributes_billed_excess_to_material_credit(self):
		self.assertEqual(
			controller._calculate_material_credit_invoice_qty(
				invoice_qty=3500,
				lot_backed_qty=3374,
				credit_qty=126,
			),
			126,
		)

	def test_material_credit_invoice_qty_is_capped_by_credit(self):
		self.assertEqual(
			controller._calculate_material_credit_invoice_qty(
				invoice_qty=3600,
				lot_backed_qty=3374,
				credit_qty=126,
			),
			126,
		)

	def test_unbilled_excess_keeps_zero_material_credit_invoice_qty(self):
		self.assertEqual(
			controller._calculate_material_credit_invoice_qty(
				invoice_qty=3374,
				lot_backed_qty=3374,
				credit_qty=126,
			),
			0,
		)

	def test_create_material_credit_record_builds_reviewable_draft(self):
		plr = SimpleNamespace(
			name="PLR-TEST-CREDIT",
			docstatus=0,
			processor_material_credit_qty=160,
			allow_processor_material_credit=1,
			material_credit_reason="Supplier Supply Excess",
			physical_receipt_date=date(2026, 7, 18),
		)
		entry = SimpleNamespace(
			doctype="Processor Material Account Entry",
			name="PMA-TEST-CREDIT",
			docstatus=0,
			insert=lambda: None,
		)

		with patch.object(
			controller.frappe,
			"get_doc",
			side_effect=[plr, entry],
		) as get_doc, patch.object(
			controller.frappe,
			"has_permission",
			return_value=True,
		), patch.object(
			controller.frappe.db,
			"get_value",
			return_value=None,
		):
			result = controller.create_material_credit_record(plr.name)

		entry_values = get_doc.call_args_list[1].args[0]
		self.assertEqual(entry_values["entry_type"], "Advance Credit")
		self.assertEqual(entry_values["source_event"], "PLR Excess")
		self.assertEqual(entry_values["processed_qty"], 160)
		self.assertEqual(entry_values["account_qty"], 160)
		self.assertEqual(entry_values["remarks"], "Supplier Supply Excess")
		self.assertEqual(result["name"], "PMA-TEST-CREDIT")
		self.assertTrue(result["created"])

	def test_create_material_credit_record_reuses_existing_draft(self):
		plr = SimpleNamespace(
			name="PLR-TEST-CREDIT",
			docstatus=0,
			processor_material_credit_qty=160,
			allow_processor_material_credit=1,
			material_credit_reason="Supplier Supply Excess",
		)
		existing = SimpleNamespace(
			name="PMA-EXISTING",
			docstatus=0,
		)

		with patch.object(
			controller.frappe,
			"get_doc",
			return_value=plr,
		), patch.object(
			controller.frappe,
			"has_permission",
			return_value=True,
		), patch.object(
			controller.frappe.db,
			"get_value",
			return_value=existing,
		):
			result = controller.create_material_credit_record(plr.name)

		self.assertEqual(result["name"], "PMA-EXISTING")
		self.assertFalse(result["created"])

	def test_valuation_rate_accepts_sub_paise_rounding(self):
		self.assertTrue(
			controller._valuation_rates_match(61.669997, 61.67)
		)
		self.assertFalse(
			controller._valuation_rates_match(61.66, 61.67)
		)

	def test_fifo_candidates_are_limited_to_draft_processor_lots(self):
		receipt = SimpleNamespace(
			company="Test Company",
			supplier="Test Supplier",
			supplier_warehouse="Test Supplier Warehouse",
			processed_item="Processed Item",
			stock_uom="Kg",
			_get_lot_allocated_accepted_qty=lambda processor_lot: 10,
			_get_lot_submitted_credit_applied_qty=lambda processor_lot: 0,
		)
		processor_lot = SimpleNamespace(
			name="PL-OPEN",
			subcontracting_order="SCO-OPEN",
			purchase_order="PO-OPEN",
			creation=date(2026, 8, 10),
		)
		sco = SimpleNamespace(
			name="SCO-OPEN",
			docstatus=1,
			transaction_date=date(2026, 8, 10),
			items=[
				SimpleNamespace(
					item_code="Processed Item",
					stock_uom="Kg",
					qty=100,
				)
			],
		)

		with patch.object(
			controller.frappe,
			"get_all",
			return_value=[processor_lot],
		) as get_all, patch.object(
			controller.frappe,
			"get_doc",
			return_value=sco,
		):
			candidates = (
				controller.ProcessorLotReceipt.
				_get_fifo_allocation_candidates(receipt)
			)

		self.assertEqual(
			get_all.call_args.kwargs["filters"]["docstatus"],
			0,
		)
		self.assertEqual(len(candidates), 1)
		self.assertEqual(candidates[0].processor_lot, "PL-OPEN")
		self.assertEqual(candidates[0].available_qty, 90)

	def test_fifo_capacity_deducts_submitted_credit_applied_qty(self):
		receipt = SimpleNamespace(
			company="Test Company",
			supplier="Test Supplier",
			supplier_warehouse="Test Supplier Warehouse",
			processed_item="Processed Item",
			stock_uom="Kg",
			_get_lot_allocated_accepted_qty=lambda processor_lot: 10,
			_get_lot_submitted_credit_applied_qty=lambda processor_lot: 30,
		)
		processor_lot = SimpleNamespace(
			name="PL-OPEN",
			subcontracting_order="SCO-OPEN",
			purchase_order="PO-OPEN",
			creation=date(2026, 8, 10),
		)
		sco = SimpleNamespace(
			name="SCO-OPEN",
			docstatus=1,
			transaction_date=date(2026, 8, 10),
			items=[
				SimpleNamespace(
					item_code="Processed Item",
					stock_uom="Kg",
					qty=100,
				)
			],
		)

		with patch.object(
			controller.frappe,
			"get_all",
			return_value=[processor_lot],
		), patch.object(
			controller.frappe,
			"get_doc",
			return_value=sco,
		):
			candidates = (
				controller.ProcessorLotReceipt.
				_get_fifo_allocation_candidates(receipt)
			)

		self.assertEqual(len(candidates), 1)
		self.assertEqual(candidates[0].credit_applied_qty, 30)
		self.assertEqual(candidates[0].available_qty, 60)

	def test_credit_applied_capacity_uses_processed_quantity(self):
		receipt = SimpleNamespace(
			processed_item="Processed Item",
			stock_uom="Kg",
		)

		with patch.object(
			controller.frappe.db,
			"get_value",
			return_value=30,
		) as get_value:
			quantity = (
				controller.ProcessorLotReceipt.
				_get_lot_submitted_credit_applied_qty(
					receipt,
					"PL-TARGET",
				)
			)

		self.assertEqual(quantity, 30)
		self.assertEqual(
			get_value.call_args.args[2],
			"SUM(processed_qty)",
		)
		self.assertEqual(
			get_value.call_args.args[1]["docstatus"],
			1,
		)
		self.assertEqual(
			get_value.call_args.args[1]["is_reversed"],
			0,
		)

	def _controlled_facts(self):
		posting_date = date(2026, 8, 14)
		posting_time = timedelta(
			seconds=24702,
			microseconds=792652,
		)
		plr = SimpleNamespace(
			name="PLR-TEST-CREDIT",
			company="Test Company",
			processed_item="Processed Item",
			stock_uom="Kg",
			processor_material_credit_qty=170,
			lot_backed_qty=3900,
			supplier_warehouse="Processor Warehouse",
			subcontracting_receipt="SCR-TEST-BACKED",
		)
		pma = SimpleNamespace(
			name="PMA-TEST-CREDIT",
			docstatus=1,
			processor_lot_receipt=plr.name,
			principal_component="Principal Component",
		)
		scr = SimpleNamespace(
			name="SCR-TEST-BACKED",
			posting_date=posting_date,
			posting_time=posting_time,
		)
		valuation = {
			"target_warehouse": "Finished Goods Warehouse",
			"finished_rate": 62.3,
			"component_rate": 60.0,
			"processing_rate": 2.3,
			"component_value": 10200.0,
			"processing_value": 391.0,
			"total_value": 10591.0,
		}
		item = SimpleNamespace(
			item_code=plr.processed_item,
			s_warehouse=None,
			t_warehouse=valuation["target_warehouse"],
			qty=170,
			expense_account="Processor Material Credit Liability - TEST",
			basic_rate=60,
			basic_amount=10200,
			additional_cost=391,
			valuation_rate=62.3,
			amount=10591,
		)
		additional_cost = SimpleNamespace(
			expense_account="Accrued Subcontracting Charges - TEST",
			amount=391,
		)
		stock_entry = SimpleNamespace(
			name="STE-TEST-CREDIT",
			company=plr.company,
			purpose="Material Receipt",
			stock_entry_type="Material Receipt",
			set_posting_time=1,
			posting_date=posting_date,
			posting_time=posting_time,
			items=[item],
			additional_costs=[additional_cost],
			total_incoming_value=10591,
			total_additional_costs=391,
			total_amount=10591,
		)
		return plr, pma, scr, valuation, stock_entry

	def _validation_patches(self, plr, pma, scr, valuation):
		real_get_all = controller.frappe.get_all
		real_get_doc = controller.frappe.get_doc

		def get_all(doctype, *args, **kwargs):
			controlled_names = {
				"Processor Lot Receipt": [plr.name],
				"Processor Material Account Entry": [pma.name],
			}
			if isinstance(doctype, str) and doctype in controlled_names:
				return controlled_names[doctype]

			return real_get_all(doctype, *args, **kwargs)

		def get_doc(doctype, name=None, *args, **kwargs):
			controlled_docs = {
				"Processor Lot Receipt": plr,
				"Processor Material Account Entry": pma,
			}
			if isinstance(doctype, str) and doctype in controlled_docs:
				return controlled_docs[doctype]

			return real_get_doc(doctype, name, *args, **kwargs)

		def get_account(company, account_name):
			return {
				"Processor Material Credit Liability":
					"Processor Material Credit Liability - TEST",
				"Accrued Subcontracting Charges":
					"Accrued Subcontracting Charges - TEST",
			}[account_name]

		return (
			patch.object(
				controller.frappe,
				"get_all",
				side_effect=get_all,
			),
			patch.object(
				controller.frappe,
				"get_doc",
				side_effect=get_doc,
			),
			patch.object(
				controller,
				"_get_submitted_plr_material_credit",
				return_value=SimpleNamespace(name=pma.name),
			),
			patch.object(
				controller,
				"_get_submitted_backed_scr",
				return_value=scr,
			),
			patch.object(
				controller,
				"_get_material_credit_valuation",
				return_value=valuation,
			),
			patch.object(
				controller,
				"_get_processor_material_account",
				side_effect=get_account,
			),
		)

	def test_material_credit_valuation_is_derived_from_backed_scr(self):
		plr = SimpleNamespace(
			processed_item="Processed Item",
			supplier_warehouse="Processor Warehouse",
			lot_backed_qty=3900,
			processor_material_credit_qty=170,
			stock_uom="Kg",
		)
		pma = SimpleNamespace(
			principal_component="Principal Component",
		)
		scr = SimpleNamespace(name="SCR-TEST-BACKED")
		ledger_rows = [
			SimpleNamespace(
				item_code="Processed Item",
				warehouse="Finished Goods Warehouse",
				actual_qty=3900,
				stock_value_difference=242970,
			),
			SimpleNamespace(
				item_code="Principal Component",
				warehouse="Processor Warehouse",
				actual_qty=-3900,
				stock_value_difference=-234000,
			),
		]

		with patch.object(
			controller.frappe,
			"get_all",
			return_value=ledger_rows,
		):
			valuation = controller._get_material_credit_valuation(
				plr=plr,
				pma=pma,
				scr=scr,
			)

		self.assertEqual(
			valuation["target_warehouse"],
			"Finished Goods Warehouse",
		)
		self.assertEqual(valuation["component_rate"], 60.0)
		self.assertEqual(valuation["processing_rate"], 2.3)
		self.assertEqual(valuation["finished_rate"], 62.3)
		self.assertEqual(valuation["component_value"], 10200.0)
		self.assertEqual(valuation["processing_value"], 391.0)
		self.assertEqual(valuation["total_value"], 10591.0)

	def test_valid_controlled_material_credit_stock_entry(self):
		plr, pma, scr, valuation, stock_entry = (
			self._controlled_facts()
		)
		patches = self._validation_patches(
			plr,
			pma,
			scr,
			valuation,
		)

		with ExitStack() as stack:
			for controlled_patch in patches:
				stack.enter_context(controlled_patch)

			controller.validate_material_credit_stock_entry(
				stock_entry
			)

	def test_controlled_stock_entry_rejects_quantity_change(self):
		plr, pma, scr, valuation, stock_entry = (
			self._controlled_facts()
		)
		stock_entry.items[0].qty = 171
		patches = self._validation_patches(
			plr,
			pma,
			scr,
			valuation,
		)

		with ExitStack() as stack:
			for controlled_patch in patches:
				stack.enter_context(controlled_patch)

			throw = stack.enter_context(
				patch.object(controller.frappe, "throw")
			)
			controller.validate_material_credit_stock_entry(
				stock_entry
			)

		self.assertEqual(stock_entry.items[0].qty, 171)
		throw.assert_called_once()
		message = throw.call_args.args[0]
		self.assertIn("Quantity must remain", message)
		self.assertIn("170.0", message)
		self.assertIn("Kg", message)

	def test_duplicate_active_material_credit_stock_entry_is_blocked(self):
		plr = SimpleNamespace(
			name="PLR-TEST-CREDIT",
			material_credit_stock_entry="STE-TEST-CREDIT",
		)
		pma = SimpleNamespace(
			name="PMA-TEST-CREDIT",
			material_credit_stock_entry="STE-TEST-CREDIT",
		)

		with patch.object(
			controller.frappe.db,
			"get_value",
			return_value=0,
		):
			with self.assertRaisesRegex(
				frappe.ValidationError,
				"already records this Processor Material Credit",
			):
				controller._existing_material_credit_stock_entry(
					plr,
					pma,
				)

	def test_unlink_clears_matching_plr_and_pma_links(self):
		stock_entry = SimpleNamespace(name="STE-TEST-CREDIT")

		with patch.object(
			controller.frappe,
			"get_all",
			side_effect=[
				["PLR-TEST-CREDIT"],
				["PMA-TEST-CREDIT"],
			],
		), patch.object(
			controller.frappe.db,
			"set_value",
		) as set_value:
			controller.unlink_material_credit_stock_entry(
				stock_entry
			)

		self.assertEqual(
			set_value.call_args_list,
			[
				call(
					"Processor Lot Receipt",
					"PLR-TEST-CREDIT",
					"material_credit_stock_entry",
					None,
					update_modified=False,
				),
				call(
					"Processor Material Account Entry",
					"PMA-TEST-CREDIT",
					"material_credit_stock_entry",
					None,
					update_modified=False,
				),
			],
		)

	def _purchase_invoice(
		self,
		*,
		is_return=0,
		bill_no="3215",
		bill_date_value=date(2026, 7, 28),
	):
		"""Return an in-memory PI suitable for lifecycle tests."""
		purchase_invoice = SimpleNamespace(
			name="PI-TEST-COMMERCIAL",
			is_return=is_return,
			bill_no=bill_no,
			bill_date=bill_date_value,
			items=[
				SimpleNamespace(
					purchase_receipt="PR-TEST-COMMERCIAL",
				),
			],
		)
		purchase_invoice.get = lambda fieldname, default=None: getattr(
			purchase_invoice,
			fieldname,
			default,
		)
		return purchase_invoice

	def test_controlled_purchase_invoice_requires_supplier_identity(self):
		purchase_invoice = self._purchase_invoice(
			bill_no=" ",
			bill_date_value=None,
		)

		with patch.object(
			controller,
			"_get_processor_lot_receipts_for_purchase_invoice",
			return_value=["PLR-TEST-COMMERCIAL"],
		):
			with self.assertRaisesRegex(
				frappe.ValidationError,
				"Supplier Invoice Number and Supplier Invoice Date",
			):
				controller.validate_purchase_invoice_supplier_identity(
					purchase_invoice
				)

	def test_purchase_invoice_backfills_plr_supplier_identity(self):
		purchase_invoice = self._purchase_invoice()

		with patch.object(
			controller,
			"_get_processor_lot_receipts_for_purchase_invoice",
			return_value=["PLR-TEST-COMMERCIAL"],
		), patch.object(
			controller.frappe,
			"get_all",
			return_value=[],
		), patch.object(
			controller,
			"_set_processor_lot_receipt_link",
		) as set_link, patch.object(
			controller.frappe.db,
			"set_value",
		) as set_value, patch.object(
			controller,
			"_refresh_processor_lots_for_receipt",
		) as refresh:
			controller.link_purchase_invoice(purchase_invoice)

		set_link.assert_called_once_with(
			processor_lot_receipt="PLR-TEST-COMMERCIAL",
			fieldname="purchase_invoice",
			linked_doctype="Purchase Invoice",
			linked_document="PI-TEST-COMMERCIAL",
		)
		set_value.assert_called_once_with(
			"Processor Lot Receipt",
			"PLR-TEST-COMMERCIAL",
			{
				"supplier_invoice_number": "3215",
				"supplier_invoice_date": date(2026, 7, 28),
			},
			update_modified=False,
		)
		refresh.assert_called_once_with("PLR-TEST-COMMERCIAL")

	def test_purchase_invoice_return_is_ignored(self):
		purchase_invoice = self._purchase_invoice(
			is_return=1,
			bill_no=None,
			bill_date_value=None,
		)

		with patch.object(
			controller.frappe,
			"get_all",
		) as get_all, patch.object(
			controller.frappe.db,
			"set_value",
		) as set_value:
			controller.validate_purchase_invoice_supplier_identity(
				purchase_invoice
			)
			controller.link_purchase_invoice(purchase_invoice)

		get_all.assert_not_called()
		set_value.assert_not_called()

	def test_unlink_purchase_invoice_clears_identity(self):
		purchase_invoice = self._purchase_invoice()

		with patch.object(
			controller.frappe,
			"get_all",
			return_value=["PLR-TEST-COMMERCIAL"],
		), patch.object(
			controller.frappe.db,
			"get_value",
			return_value="PI-TEST-COMMERCIAL",
		), patch.object(
			controller.frappe.db,
			"set_value",
		) as set_value, patch.object(
			controller,
			"_refresh_processor_lots_for_receipt",
		) as refresh:
			controller.unlink_purchase_invoice(purchase_invoice)

		set_value.assert_called_once_with(
			"Processor Lot Receipt",
			"PLR-TEST-COMMERCIAL",
			{
				"purchase_invoice": None,
				"supplier_invoice_number": None,
				"supplier_invoice_date": None,
			},
			update_modified=False,
		)
		refresh.assert_called_once_with("PLR-TEST-COMMERCIAL")

	def test_cancelled_purchase_invoice_link_can_be_replaced(self):
		with patch.object(
			controller.frappe.db,
			"get_value",
			side_effect=[
				"PI-TEST-CANCELLED",
				2,
			],
		), patch.object(
			controller.frappe.db,
			"set_value",
		) as set_value, patch.object(
			controller,
			"_refresh_processor_lots_for_receipt",
		) as refresh:
			controller._set_processor_lot_receipt_link(
				processor_lot_receipt="PLR-TEST-COMMERCIAL",
				fieldname="purchase_invoice",
				linked_doctype="Purchase Invoice",
				linked_document="PI-TEST-AMENDED",
			)

		set_value.assert_called_once_with(
			"Processor Lot Receipt",
			"PLR-TEST-COMMERCIAL",
			"purchase_invoice",
			"PI-TEST-AMENDED",
			update_modified=False,
		)
		refresh.assert_called_once_with("PLR-TEST-COMMERCIAL")
