"""J1 processor-first boundary tests: no inserts, commits or stock documents.

Uses the real controller methods with in-memory records and mocked reads.
Database save/reload and browser behaviour are separate acceptance checkpoints.
"""

import json
import unittest
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from . import processor_lot_receipt as controller


def record(**values):
	row = SimpleNamespace(**values)
	row.get = lambda key, default=None: getattr(row, key, default)
	return row


class TestProcessorFirstDraftContext(unittest.TestCase):
	def setUp(self):
		# Initialize real rounding dependencies before mocking document reads.
		self.assertEqual(controller.flt(100, 6), 100.0)

		self.stack = ExitStack()
		self.addCleanup(self.stack.close)
		self.stack.enter_context(patch.object(
			controller.frappe, "conf",
			frappe._dict(v2_processor_first_draft_entry=1),
		))
		self.documents = {}
		for doctype, name in (
			("Company", "COMPANY"), ("Supplier", "SUPPLIER"),
			("Warehouse", "WAREHOUSE"), ("Item", "ITEM-A"),
		):
			self.documents[(doctype, name)] = record(
				name=name, company="COMPANY", disabled=0, is_group=0,
				check_permission=Mock(),
			)
		self.get_doc = self.stack.enter_context(patch.object(
			controller.frappe, "get_doc",
			side_effect=lambda doctype, name: self.documents[(doctype, name)],
		))

	def receipt(self, **values):
		defaults = dict(
			name="PLR-J1-IN-MEMORY", doctype="Processor Lot Receipt",
			processor_lot=None, subcontracting_order=None,
			receipt_structure_version=controller.V2_RECEIPT_STRUCTURE,
			company="COMPANY", supplier="SUPPLIER", supplier_warehouse="WAREHOUSE",
			receipt_items=[record(processed_item="ITEM-A", item_key="ITEM-001", name=None)],
			item_weighments=[], lot_allocations=[],
			get_doc_before_save=Mock(return_value=None),
			is_new=Mock(return_value=True),
		)
		defaults.update(values)
		result = record(**defaults)
		for name in (
			"before_validate", "validate", "_uses_v2_item_structure",
			"_validate_processor_first_context", "_validate_processor_lot_immutability",
			"_validate_receipt_structure_immutability", "_get_v2_fifo_candidates",
		):
			setattr(result, name, MethodType(getattr(controller.ProcessorLotReceipt, name), result))
		result.has_value_changed = lambda name: (
			result.get(name) != result.get_doc_before_save().get(name)
		)
		return result

	def test_context_accepts_headerless_receipt_and_checks_access(self):
		receipt = self.receipt()
		receipt._validate_processor_first_context()
		self.assertIsNone(receipt.processor_lot)
		self.assertIsNone(receipt.subcontracting_order)
		for doc in self.documents.values():
			doc.check_permission.assert_called_once_with("read")

	def test_site_opt_in_is_required(self):
		for value in (None, 0, "0"):
			with self.subTest(value=value):
				controller.frappe.conf.v2_processor_first_draft_entry = value
				with self.assertRaisesRegex(frappe.ValidationError, "not enabled"):
					self.receipt()._validate_processor_first_context()
		self.get_doc.assert_not_called()

	def test_each_context_field_is_required(self):
		for name in ("company", "supplier", "supplier_warehouse"):
			with self.subTest(field=name), self.assertRaisesRegex(frappe.ValidationError, "required"):
				self.receipt(**{name: None})._validate_processor_first_context()

	def test_header_sco_is_not_silently_accepted(self):
		with self.assertRaisesRegex(frappe.ValidationError, "header Subcontracting Order"):
			self.receipt(subcontracting_order="SCO-INJECTED")._validate_processor_first_context()

	def test_saved_context_is_immutable(self):
		for name in ("company", "supplier", "supplier_warehouse"):
			previous = self.receipt(**{name: "OTHER"})
			receipt = self.receipt(get_doc_before_save=Mock(return_value=previous))
			with self.subTest(field=name), self.assertRaisesRegex(frappe.ValidationError, "cannot be changed"):
				receipt._validate_processor_first_context()

	def test_unchanged_saved_context_is_accepted(self):
		self.receipt(get_doc_before_save=Mock(return_value=self.receipt()))._validate_processor_first_context()

	def test_saved_header_lot_cannot_be_added(self):
		receipt = self.receipt(
			processor_lot="PL-INJECTED", get_doc_before_save=Mock(return_value=self.receipt()),
		)
		with self.assertRaisesRegex(frappe.ValidationError, "cannot be changed"):
			receipt._validate_processor_lot_immutability()

	def test_saved_receipt_cannot_switch_structure(self):
		receipt = self.receipt(get_doc_before_save=Mock(return_value=self.receipt(
			receipt_structure_version=controller.LEGACY_RECEIPT_STRUCTURE,
		)))
		with self.assertRaisesRegex(frappe.ValidationError, "cannot be changed"):
			receipt._validate_receipt_structure_immutability()

	def test_warehouse_must_be_enabled_leaf_in_company(self):
		for values in ({"company": "OTHER"}, {"disabled": 1}, {"is_group": 1}):
			warehouse = self.documents[("Warehouse", "WAREHOUSE")]
			with patch.multiple(warehouse, **values), self.assertRaisesRegex(frappe.ValidationError, "leaf warehouse"):
				self.receipt()._validate_processor_first_context()

	def test_disabled_supplier_and_item_are_rejected(self):
		for key in (("Supplier", "SUPPLIER"), ("Item", "ITEM-A")):
			with patch.object(self.documents[key], "disabled", 1), self.assertRaisesRegex(frappe.ValidationError, "disabled"):
				self.receipt()._validate_processor_first_context()

	def test_missing_item_is_rejected(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Processed Item"):
			self.receipt(receipt_items=[record(processed_item=None)])._validate_processor_first_context()

	def test_context_permissions_are_not_bypassed(self):
		for key in self.documents:
			with patch.object(self.documents[key], "check_permission", side_effect=frappe.PermissionError):
				with self.subTest(document=key), self.assertRaises(frappe.PermissionError):
					self.receipt()._validate_processor_first_context()

	def test_preview_marker_still_blocks_save_before_reads(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Workspace preview only"):
			self.receipt(__v2_entry_preview=1).before_validate()
		self.get_doc.assert_not_called()

	def test_headerless_before_validate_runs_v2_pipeline(self):
		self.assert_v2_preparation(processor_lot=None, context_method="_validate_processor_first_context")

	def test_lot_led_v2_keeps_its_existing_header_path(self):
		controller.frappe.conf.v2_processor_first_draft_entry = 0
		self.assert_v2_preparation(processor_lot="PL-EXISTING", context_method="_set_header_from_processor_lot")

	def assert_v2_preparation(self, processor_lot, context_method):
		receipt = self.receipt(processor_lot=processor_lot)
		calls = []
		methods = (
			"_validate_processor_first_context", "_set_header_from_processor_lot",
			"_prepare_v2_receipt_items", "_calculate_v2_truck_differentials",
			"_calculate_v2_item_commercial_reconciliation", "_calculate_v2_item_lot_backing",
			"_ensure_initial_v2_allocations", "_sync_single_v2_item_to_legacy_header",
			"_set_processed_item_from_sco", "_calculate_net_weights",
		)
		for name in methods:
			setattr(receipt, name, Mock(side_effect=lambda name=name: calls.append(name)))
		receipt.before_validate()
		self.assertEqual(calls, [context_method, *methods[2:8]])

	def test_headerless_validate_runs_all_v2_checks(self):
		receipt = self.receipt()
		methods = (
			"_validate_v2_receipt_items", "_validate_v2_weighments", "_validate_v2_allocations",
			"_validate_v2_material_credit", "_validate_v2_linked_scr_consistency",
		)
		for name in methods:
			setattr(receipt, name, Mock())
		receipt.validate()
		for name in methods:
			getattr(receipt, name).assert_called_once_with()

	def test_legacy_still_requires_header_lot_and_measurement_fields(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Processor Lot is required"):
			self.receipt(receipt_structure_version=controller.LEGACY_RECEIPT_STRUCTURE).validate()
		for name in ("measurement_method", "company_weighment_uom", "supplier_weighment_uom"):
			values = dict(measurement_method="Count", company_weighment_uom="Kg", supplier_weighment_uom="Kg")
			values[name] = None
			with self.subTest(field=name), self.assertRaisesRegex(frappe.ValidationError, "required"):
				self.receipt(processor_lot="PL", receipt_structure_version=controller.LEGACY_RECEIPT_STRUCTURE, **values).validate()

	def fifo_fixture(self, **sco_values):
		receipt = self.receipt()
		receipt._get_lot_allocated_accepted_qty = Mock(return_value=10.123456)
		receipt._get_lot_submitted_credit_applied_qty_for_item = Mock(return_value=1)
		lot = frappe._dict(
			name="PL-OPEN", subcontracting_order="SCO-OPEN", purchase_order="PO-OPEN",
			settlement_status="Draft", creation="2026-08-10 00:00:00",
		)
		item = record(processed_item="ITEM-A", stock_uom="Kg")
		values = dict(
			name="SCO-OPEN", docstatus=1, status="Open", company="COMPANY", supplier="SUPPLIER",
			supplier_warehouse="WAREHOUSE", transaction_date=date(2026, 8, 10),
			has_permission=Mock(return_value=True),
			items=[record(name="SCO-ROW", item_code="ITEM-A", stock_uom="Kg", qty=100,
				received_qty=5, returned_qty=0, purchase_order_item="PO-ROW")],
		)
		values.update(sco_values)
		sco = record(**values)
		self.documents[("Subcontracting Order", "SCO-OPEN")] = sco
		get_list = self.stack.enter_context(patch.object(controller.frappe, "get_list", return_value=[lot]))
		get_all = self.stack.enter_context(patch.object(controller.frappe, "get_all", side_effect=AssertionError("permission bypass")))
		return receipt, item, lot, sco, get_list, get_all

	def test_fifo_is_permission_aware_and_preserves_six_decimals(self):
		receipt, item, lot, sco, get_list, get_all = self.fifo_fixture()
		candidates = receipt._get_v2_fifo_candidates(item)
		self.assertEqual(len(candidates), 1)
		self.assertEqual(candidates[0].available_qty, 88.876544)
		self.assertEqual(candidates[0].subcontracting_order_item, "SCO-ROW")
		self.assertEqual(get_list.call_args.kwargs["filters"], {
			"docstatus": 0, "company": "COMPANY", "supplier": "SUPPLIER", "supplier_warehouse": "WAREHOUSE",
		})
		self.assertEqual(get_list.call_args.kwargs["limit_page_length"], 0)
		get_all.assert_not_called()

	def test_fifo_excludes_settled_lots(self):
		receipt, item, lot, *_ = self.fifo_fixture()
		for status in ("Completed", "Debit Note Created"):
			lot.settlement_status = status
			with self.subTest(status=status):
				self.assertEqual(receipt._get_v2_fifo_candidates(item), [])
		self.get_doc.assert_not_called()

	def test_fifo_excludes_closed_unsubmitted_and_inaccessible_scos(self):
		receipt, item, lot, sco, *_ = self.fifo_fixture()
		for values in (
			{"status": "Closed"}, {"status": "Completed"}, {"status": "Cancelled"},
			{"docstatus": 0}, {"docstatus": 2}, {"has_permission": Mock(return_value=False)},
			{"company": "OTHER"}, {"supplier": "OTHER"}, {"supplier_warehouse": "OTHER"},
		):
			with self.subTest(values=values), patch.multiple(sco, **values):
				self.assertEqual(receipt._get_v2_fifo_candidates(item), [])

	def test_fifo_uses_item_position_without_lot_wide_capacity(self):
		receipt, item, lot, sco, *_ = self.fifo_fixture()
		sco.items.append(record(item_code="ITEM-B", stock_uom="Units"))
		with patch("subcontracting_extensions.receipt_item_position.position_for_lot", return_value={"items": [
			record(subcontracting_order_item="SCO-ROW", previously_received_qty=20.123456, credit_applied_qty=1),
		]}) as position:
			result = receipt._get_v2_fifo_candidates(item)
			self.assertEqual(result[0].available_qty, 78.876544)
			position.assert_called_once_with(lot, sco, None)
		receipt._get_lot_allocated_accepted_qty.assert_not_called()

	def test_fifo_rejects_duplicate_source_item_rows(self):
		receipt, item, lot, sco, *_ = self.fifo_fixture()
		sco.items.append(sco.items[0])
		with patch("subcontracting_extensions.receipt_item_position.read_evidence", return_value=([], [], set(), [], [])), self.assertRaisesRegex(frappe.ValidationError, "Duplicate or ambiguous"):
			receipt._get_v2_fifo_candidates(item)

	def test_fifo_requires_matching_item_uom_and_positive_capacity(self):
		receipt, item, lot, sco, *_ = self.fifo_fixture()
		for values in ({"item_code": "OTHER"}, {"stock_uom": "Units"}, {"received_qty": 100}):
			with self.subTest(values=values), patch.multiple(sco.items[0], **values):
				self.assertEqual(receipt._get_v2_fifo_candidates(item), [])

	def test_schema_changes_only_legacy_header_requirements(self):
		path = Path(controller.__file__).with_suffix(".json")
		fields = {row["fieldname"]: row for row in json.loads(path.read_text())["fields"]}
		for name in ("processor_lot", "measurement_method", "company_weighment_uom", "supplier_weighment_uom"):
			self.assertEqual(fields[name]["reqd"], 0)
			self.assertEqual(fields[name]["mandatory_depends_on"], "eval:doc.receipt_structure_version != 'V2 Itemized'")
		self.assertEqual(fields["physical_receipt_date"]["reqd"], 1)
		self.assertEqual(fields["supplier_invoice_number"]["read_only"], 1)
		self.assertEqual(fields["supplier_invoice_date"]["read_only"], 1)
