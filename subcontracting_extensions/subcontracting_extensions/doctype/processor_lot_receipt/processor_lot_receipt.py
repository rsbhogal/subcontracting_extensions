# Copyright (c) 2026, R.S. Bhogal and contributors
# For license information, please see license.txt

"""
Processor Lot Receipt.

Represents one physical truck receipt against a Processor Lot.

This document records physical and commercial facts only. It does not make
Stock Ledger or General Ledger postings by itself. Standard ERPNext documents
such as Subcontracting Receipt, Purchase Receipt and Purchase Invoice remain
authoritative for stock and accounting.
"""

from __future__ import annotations

from datetime import time, timedelta

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, get_datetime
from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.processor_lot import (
        refresh_processor_lot_receipt_summary,
)


LEGACY_RECEIPT_STRUCTURE = "Legacy Single Item"
V2_RECEIPT_STRUCTURE = "V2 Itemized"
ITEM_QUANTITY_PRECISION = 6


def _calculate_material_credit_invoice_qty(
	invoice_qty: float,
	lot_backed_qty: float,
	credit_qty: float,
) -> float:
	"""Return the invoiced quantity belonging to a PLR excess credit."""
	return flt(
		min(
			max(flt(invoice_qty) - flt(lot_backed_qty), 0.0),
			max(flt(credit_qty), 0.0),
		),
		3,
	)


def _get_material_credit_source(plr, receipt_item_key: str | None = None):
	"""Return the authoritative legacy header or exact V2 credit item."""
	if plr.receipt_structure_version != V2_RECEIPT_STRUCTURE:
		if receipt_item_key:
			frappe.throw(
				_("Legacy Processor Lot Receipts cannot use a Receipt Item Key.")
			)
		return plr, None, 3

	if not receipt_item_key:
		frappe.throw(_("Receipt Item Key is required for a V2 material credit."))

	matches = [
		item
		for item in plr.get("receipt_items", [])
		if item.item_key == receipt_item_key
	]
	if len(matches) != 1:
		frappe.throw(
			_("Processor Lot Receipt {0} must contain exactly one Receipt Item {1}.").format(
				frappe.bold(plr.name),
				frappe.bold(receipt_item_key),
			)
		)
	return matches[0], receipt_item_key, ITEM_QUANTITY_PRECISION


def _posting_time_microseconds(value) -> int | None:
	"""Normalize Frappe Time values without losing microseconds."""
	if value is None:
		return None

	if isinstance(value, timedelta):
		return (
			(value.days * 86400 + value.seconds) * 1_000_000
			+ value.microseconds
		)

	if isinstance(value, time):
		return (
			(
				value.hour * 3600
				+ value.minute * 60
				+ value.second
			) * 1_000_000
			+ value.microsecond
		)

	text = str(value).strip()
	parts = text.split(":")
	if len(parts) != 3:
		return None

	try:
		hours = int(parts[0])
		minutes = int(parts[1])
		seconds_text = parts[2]
		if "." in seconds_text:
			seconds_part, fraction = seconds_text.split(".", 1)
		else:
			seconds_part, fraction = seconds_text, ""

		seconds = int(seconds_part)
		microseconds = int((fraction + "000000")[:6])
	except (TypeError, ValueError):
		return None

	return (
		(hours * 3600 + minutes * 60 + seconds) * 1_000_000
		+ microseconds
	)


def _posting_times_match(actual, expected) -> bool:
	actual_microseconds = _posting_time_microseconds(actual)
	expected_microseconds = _posting_time_microseconds(expected)
	if actual_microseconds is None or expected_microseconds is None:
		return str(actual) == str(expected)

	return actual_microseconds == expected_microseconds


class ProcessorLotReceipt(Document):
	"""Capture and validate one truck receipt against a Processor Lot."""

	def before_validate(self) -> None:
		"""
		Refresh controlled values and calculated quantities before validation.

		Server-side calculation remains authoritative even when values are also
		calculated immediately in the browser.
		"""

		if self.get("__v2_entry_preview"):
			frappe.throw(_("Workspace preview only. Saving is not enabled in this checkpoint."))

		self._validate_processor_lot_immutability()
		self._validate_receipt_structure_immutability()

		if self._uses_v2_item_structure():
			if self.processor_lot:
				# Preserve the existing lot-led V2 console/regression path.
				self._set_header_from_processor_lot()
			else:
				self._validate_processor_first_context()
			self._prepare_v2_receipt_items()
			self._calculate_v2_truck_differentials()
			self._calculate_v2_item_commercial_reconciliation()
			self._calculate_v2_item_lot_backing()
			self._ensure_initial_v2_allocations()
			self._sync_single_v2_item_to_legacy_header()
			return

		if not self.processor_lot:
			return

		self._set_header_from_processor_lot()
		self._set_processed_item_from_sco()
		self._calculate_net_weights()
		self._set_company_accepted_qty_from_measurement()
		self._calculate_commercial_reconciliation()
		self._calculate_processor_material_credit()
		self._ensure_initial_lot_allocation()

	def validate(self) -> None:
		"""Validate the physical and linked-document facts."""
		if self._uses_v2_item_structure():
			if not self.processor_lot:
				self._validate_processor_first_context()
			self._validate_v2_receipt_items()
			self._validate_v2_weighments()
			self._validate_v2_allocations()
			self._validate_v2_material_credit()
			self._validate_v2_linked_scr_consistency()
			return

		if not self.processor_lot:
			frappe.throw(_("Processor Lot is required."))

		# These schema requirements are now conditional for the V2 boundary.
		# Enforce the legacy requirements server-side as well as in the form.
		for fieldname, label in (
			("measurement_method", "Measurement Method"),
			("company_weighment_uom", "Company Measurement UOM"),
			("supplier_weighment_uom", "Supplier Measurement UOM"),
		):
			if not self.get(fieldname):
				frappe.throw(_("{0} is required.").format(_(label)))

		self._validate_weights()
		self._validate_lot_allocations()
		self._validate_processor_material_credit()
		self._validate_linked_scr_consistency()

	# ---------------------------------------------------------------------
	# V2 itemized receipt compatibility boundary
	# ---------------------------------------------------------------------

	def _uses_v2_item_structure(self) -> bool:
		"""Return whether this receipt uses the itemized V2 contract."""
		return self.receipt_structure_version == V2_RECEIPT_STRUCTURE

	def _validate_processor_first_context(self) -> None:
		"""Validate the opt-in, headerless V2 draft without choosing a lot.

		This checkpoint does not enable the browser form. Its existing preview
		marker remains unsaveable. Supplier invoice identity also remains PI-owned.
		"""
		if not cint(frappe.conf.get("v2_processor_first_draft_entry")):
			frappe.throw(_("Processor-first draft entry is not enabled on this site."))

		if self.subcontracting_order:
			frappe.throw(_("A processor-first receipt must not have a header Subcontracting Order."))

		previous_doc = self.get_doc_before_save()
		for fieldname, doctype in (
			("company", "Company"),
			("supplier", "Supplier"),
			("supplier_warehouse", "Warehouse"),
		):
			value = self.get(fieldname)
			if not value:
				frappe.throw(_("{0} is required for a processor-first receipt.").format(_(doctype)))
			if previous_doc and previous_doc.get(fieldname) != value:
				frappe.throw(
					_("{0} cannot be changed after this receipt has been saved.").format(_(doctype)),
					title=_("Processor Context Is Locked"),
				)
			context_doc = frappe.get_doc(doctype, value)
			context_doc.check_permission("read")
			if doctype == "Supplier" and context_doc.get("disabled"):
				frappe.throw(_("The selected Processor is disabled."))
			if doctype == "Warehouse" and (
				context_doc.company != self.company
				or context_doc.get("disabled")
				or context_doc.get("is_group")
			):
				frappe.throw(_("Supplier Warehouse must be an enabled leaf warehouse of the selected Company."))

		# Context is permission-checked here; source eligibility and the exact
		# supplier/warehouse relationship are checked afresh by the FIFO helper.
		for item in self.receipt_items or []:
			if not item.processed_item:
				frappe.throw(_("Every Receipt Item must specify a Processed Item."))
			item_doc = frappe.get_doc("Item", item.processed_item)
			item_doc.check_permission("read")
			if item_doc.get("disabled"):
				frappe.throw(_("Receipt Items cannot contain a disabled Item."))

	def _validate_receipt_structure_immutability(self) -> None:
		"""Prevent a saved legacy or V2 receipt changing data contracts."""
		previous_doc = self.get_doc_before_save()
		if not previous_doc:
			return

		if (
			previous_doc.receipt_structure_version
			!= self.receipt_structure_version
		):
			frappe.throw(
				_("Receipt Structure Version cannot be changed after creation."),
				title=_("Receipt Structure Is Locked"),
			)

		previous_keys_by_name = {
			row.name: row.item_key
			for row in previous_doc.receipt_items or []
			if row.name
		}
		for row in self.receipt_items or []:
			previous_key = previous_keys_by_name.get(row.name)
			if previous_key and previous_key != row.item_key:
				frappe.throw(
					_("Receipt Item Key cannot be changed after creation."),
					title=_("Receipt Item Key Is Locked"),
				)

	def _prepare_v2_receipt_items(self) -> None:
		"""Assign stable item keys and controlled UOM values.

		The hidden V2 tables are populated through controlled console tests in
		this checkpoint. Browser exposure is deliberately deferred until FIFO
		allocation and SCR mapping are item-aware.
		"""
		used_keys = set()
		next_number = 1

		for item in self.receipt_items or []:
			if not item.item_key:
				while f"ITEM-{next_number:03d}" in used_keys:
					next_number += 1

				item.item_key = f"ITEM-{next_number:03d}"
				next_number += 1

			if item.item_key in used_keys:
				frappe.throw(
					_("Receipt Item Key {0} occurs more than once.").format(
						frappe.bold(item.item_key)
					),
					title=_("Duplicate Receipt Item Key"),
				)

			used_keys.add(item.item_key)

			if not item.processed_item:
				continue

			stock_uom = frappe.db.get_value(
				"Item",
				item.processed_item,
				"stock_uom",
			)
			if not stock_uom:
				frappe.throw(
					_("Receipt row {0}: Item {1} has no Stock UOM.").format(
						item.idx,
						frappe.bold(item.processed_item),
					)
				)

			item.stock_uom = stock_uom
			item.company_accepted_uom = stock_uom
			item.supplier_invoice_uom = stock_uom

	def _calculate_v2_item_commercial_reconciliation(self) -> None:
		"""Calculate commercial variance independently for every item."""
		for item in self.receipt_items or []:
			accepted_qty = flt(
				item.company_accepted_qty,
				ITEM_QUANTITY_PRECISION,
			)
			invoice_qty = flt(
				item.supplier_invoice_qty,
				ITEM_QUANTITY_PRECISION,
			)

			if not invoice_qty:
				item.supplier_invoice_vs_company_qty = 0.0
				item.supplier_invoice_vs_company_percent = 0.0
				continue

			difference = flt(
				invoice_qty - accepted_qty,
				ITEM_QUANTITY_PRECISION,
			)
			item.supplier_invoice_vs_company_qty = difference
			item.supplier_invoice_vs_company_percent = (
				flt(
					difference / accepted_qty * 100,
					ITEM_QUANTITY_PRECISION,
				)
				if accepted_qty
				else 0.0
			)

	def _calculate_v2_truck_differentials(self) -> None:
		"""Derive accepted item quantities from chronological scale readings."""
		rows = list(self.item_weighments or [])
		if not rows:
			return

		items_by_key = {
			item.item_key: item
			for item in self.receipt_items or []
		}
		first_row = rows[0]
		if first_row.weighment_stage != "Arrival Loaded":
			frappe.throw(
				_("The first Item Weighment must be Arrival Loaded."),
				title=_("Invalid Weighment Sequence"),
			)

		first_row.receipt_item_key = None
		first_row.processed_item = None
		first_row.derived_unloaded_qty = 0.0
		first_row.adjustment_qty = 0.0
		first_row.accepted_qty = 0.0
		accepted_by_item = {}
		previous_weight = flt(first_row.scale_weight, 3)
		measurement_uom = first_row.measurement_uom
		previous_moment = self._v2_weighment_moment(first_row)

		for row in rows[1:]:
			current_moment = self._v2_weighment_moment(row)
			if (
				previous_moment
				and current_moment
				and current_moment < previous_moment
			):
				frappe.throw(
					_("Weighment row {0} is earlier than the preceding reading.").format(
						row.idx
					),
					title=_("Invalid Weighment Sequence"),
				)
			if row.weighment_stage == "Arrival Loaded":
				frappe.throw(
					_("Arrival Loaded may occur only as the first weighment."),
					title=_("Invalid Weighment Sequence"),
				)

			if row.measurement_uom != measurement_uom:
				frappe.throw(
					_("All truck differential readings must use UOM {0}.").format(
						frappe.bold(measurement_uom)
					),
					title=_("Mixed Weighment UOM"),
				)

			item = items_by_key.get(row.receipt_item_key)
			if not item:
				frappe.throw(
					_("Weighment row {0} must identify a valid Receipt Item.").format(
						row.idx
					),
					title=_("Weighment Item Required"),
				)

			if (
				item.measurement_method != "Weight"
				or item.measurement_basis != "Truck Differential Weight"
			):
				frappe.throw(
					_("Weighment row {0} refers to an item not measured by truck differential.").format(
						row.idx
					),
					title=_("Invalid Weighment Item"),
				)

			if item.stock_uom != measurement_uom:
				frappe.throw(
					_("Receipt Item {0} uses Stock UOM {1}, not weighment UOM {2}.").format(
						frappe.bold(item.item_key),
						frappe.bold(item.stock_uom),
						frappe.bold(measurement_uom),
					),
					title=_("Weighment UOM Does Not Match Item"),
				)

			current_weight = flt(row.scale_weight, 3)
			if current_weight > previous_weight:
				frappe.throw(
					_("Weighment row {0} exceeds the preceding scale reading.").format(
						row.idx
					),
					title=_("Invalid Weighment Sequence"),
				)

			derived_qty = flt(previous_weight - current_weight, 3)
			accepted_qty = flt(
				derived_qty + flt(row.adjustment_qty, 3),
				3,
			)
			if accepted_qty < 0:
				frappe.throw(
					_("Weighment row {0} has a negative Accepted Qty after adjustment.").format(
						row.idx
					),
					title=_("Invalid Weighment Adjustment"),
				)

			row.processed_item = item.processed_item
			row.derived_unloaded_qty = derived_qty
			row.accepted_qty = accepted_qty
			accepted_by_item[item.item_key] = flt(
				accepted_by_item.get(item.item_key, 0.0) + accepted_qty,
				ITEM_QUANTITY_PRECISION,
			)
			previous_weight = current_weight
			previous_moment = current_moment or previous_moment

		for item in self.receipt_items or []:
			if item.measurement_basis == "Truck Differential Weight":
				item.company_accepted_qty = flt(
					accepted_by_item.get(item.item_key, 0.0),
					ITEM_QUANTITY_PRECISION,
				)

	@staticmethod
	def _v2_weighment_moment(row):
		"""Return a comparable reading timestamp when its date is available."""
		if not row.weighment_date:
			return None

		return get_datetime(
			f"{row.weighment_date} {row.weighment_time or '00:00:00'}"
		)

	def _calculate_v2_item_lot_backing(self) -> None:
		"""Split every item independently between lot backing and credit."""
		for item in self.receipt_items or []:
			accepted_qty = flt(item.company_accepted_qty, ITEM_QUANTITY_PRECISION)
			invoice_qty = flt(item.supplier_invoice_qty, ITEM_QUANTITY_PRECISION)
			available_qty = flt(
				sum(
					flt(candidate.available_qty)
					for candidate in self._get_v2_fifo_candidates(item)
				),
				ITEM_QUANTITY_PRECISION,
			)

			item.lot_backed_qty = flt(
				min(accepted_qty, available_qty),
				ITEM_QUANTITY_PRECISION,
			)
			item.processor_material_credit_qty = flt(
				max(accepted_qty - available_qty, 0.0),
				ITEM_QUANTITY_PRECISION,
			)
			item.material_credit_invoice_qty = flt(
				min(
					max(invoice_qty - item.lot_backed_qty, 0.0),
					item.processor_material_credit_qty,
				),
				ITEM_QUANTITY_PRECISION,
			)

			if flt(item.processor_material_credit_qty) <= 0:
				item.material_credit_status = "Not Applicable"
			elif item.material_credit_status in (None, "", "Not Applicable"):
				item.material_credit_status = "Proposed"

	def _get_v2_fifo_candidates(self, item) -> list[frappe._dict]:
		"""Return FIFO lot capacity compatible with one receipt item."""
		processor_first = not self.processor_lot
		# The new headerless path and workspace preview share permission-aware
		# eligibility. Keep the existing lot-led V2 regression path unchanged.
		get_lots = frappe.get_list if processor_first else frappe.get_all
		processor_lots = get_lots(
			"Processor Lot",
			filters={
				"docstatus": 0,
				"company": self.company,
				"supplier": self.supplier,
				"supplier_warehouse": self.supplier_warehouse,
			},
			fields=[
				"name",
				"subcontracting_order",
				"purchase_order",
				"creation",
				"settlement_status",
			],
			order_by="creation asc, name asc",
			limit_page_length=0,
		)
		candidates = []

		for processor_lot in processor_lots:
			if processor_first and (
				not processor_lot.subcontracting_order
				or processor_lot.settlement_status in ("Completed", "Debit Note Created")
			):
				continue
			sco = frappe.get_doc(
				"Subcontracting Order",
				processor_lot.subcontracting_order,
			)
			if sco.docstatus != 1:
				continue
			if processor_first and (
				not sco.has_permission("read")
				or sco.status in ("Closed", "Completed", "Cancelled")
				or sco.company != self.company
				or sco.supplier != self.supplier
				or sco.supplier_warehouse != self.supplier_warehouse
			):
				continue

			matching_rows = [
				row
				for row in sco.items
				if row.item_code == item.processed_item
				and row.stock_uom == item.stock_uom
			]
			if not matching_rows:
				continue
			if processor_first and len(sco.items) != 1:
				frappe.throw(
					_("Subcontracting Order {0} has multiple finished-item rows. Processor-first draft entry currently supports single-item lots only.").format(frappe.bold(sco.name)),
					title=_("Multi-Item Lot Support Pending"),
				)

			if len(matching_rows) != 1:
				frappe.throw(
					_("Subcontracting Order {0} has multiple compatible rows for {1}.").format(
						frappe.bold(sco.name),
						frappe.bold(item.processed_item),
					),
					title=_("Ambiguous Subcontracting Order Item"),
				)

			sco_item = matching_rows[0]
			lot_order_qty = flt(sco_item.qty, ITEM_QUANTITY_PRECISION)
			allocated_received_qty = self._get_lot_allocated_accepted_qty(
				processor_lot.name
			)
			native_received_qty = flt(
				max(
					flt(sco_item.received_qty)
					- flt(sco_item.returned_qty),
					0.0,
				),
				ITEM_QUANTITY_PRECISION,
			)
			previously_received_qty = max(
				allocated_received_qty,
				native_received_qty,
			)
			credit_applied_qty = self._get_lot_submitted_credit_applied_qty_for_item(
				processor_lot.name,
				item.processed_item,
				item.stock_uom,
			)
			available_qty = flt(
				lot_order_qty - previously_received_qty - credit_applied_qty,
				ITEM_QUANTITY_PRECISION,
			)
			if available_qty <= 0:
				continue

			candidates.append(frappe._dict(
				processor_lot=processor_lot.name,
				subcontracting_order=sco.name,
				subcontracting_order_item=sco_item.name,
				purchase_order=processor_lot.purchase_order,
				purchase_order_item=sco_item.purchase_order_item,
				lot_date=sco.transaction_date,
				lot_order_qty=lot_order_qty,
				previously_received_qty=previously_received_qty,
				credit_applied_qty=credit_applied_qty,
				available_qty=available_qty,
				creation=processor_lot.creation,
			))

		return sorted(
			candidates,
			key=lambda row: (
				row.lot_date,
				row.creation,
				row.processor_lot,
			),
		)

	def _get_lot_submitted_credit_applied_qty_for_item(
		self,
		processor_lot: str,
		processed_item: str,
		stock_uom: str,
	) -> float:
		return flt(
			frappe.db.get_value(
				"Processor Material Account Entry",
				{
					"entry_type": "Credit Applied",
					"source_event": "Processor Lot Shortage",
					"processor_lot": processor_lot,
					"processed_item": processed_item,
					"processed_item_uom": stock_uom,
					"account_direction": "Debit",
					"docstatus": 1,
					"is_reversed": 0,
				},
				"SUM(processed_qty)",
			)
			or 0
		)
	def _sync_single_v2_item_to_legacy_header(self) -> None:
		"""Maintain legacy summary fields only for a one-item V2 receipt.

		A multi-item truck may mix UOMs, so its item quantities must never be
		summed into the legacy header fields.
		"""
		items = list(self.receipt_items or [])

		if len(items) != 1:
			self.processed_item = None
			self.stock_uom = None
			self.measurement_method = None
			self.company_accepted_qty = 0.0
			self.company_accepted_uom = None
			self.supplier_invoice_qty = 0.0
			self.supplier_invoice_uom = None
			self.supplier_invoice_vs_company_qty = 0.0
			self.supplier_invoice_vs_company_percent = 0.0
			self.lot_backed_qty = 0.0
			self.processor_material_credit_qty = 0.0
			self.material_credit_invoice_qty = 0.0
			self.material_credit_status = "Not Applicable"
			self.material_credit_stock_entry = None
			self.allow_processor_material_credit = 0
			self.material_credit_reason = None
			return

		item = items[0]
		self.processed_item = item.processed_item
		self.stock_uom = item.stock_uom
		self.measurement_method = item.measurement_method
		self.company_accepted_qty = item.company_accepted_qty
		self.company_accepted_uom = item.company_accepted_uom
		self.supplier_invoice_qty = item.supplier_invoice_qty
		self.supplier_invoice_uom = item.supplier_invoice_uom
		self.supplier_invoice_vs_company_qty = (
			item.supplier_invoice_vs_company_qty
		)
		self.supplier_invoice_vs_company_percent = (
			item.supplier_invoice_vs_company_percent
		)
		self.lot_backed_qty = item.lot_backed_qty
		self.processor_material_credit_qty = (
			item.processor_material_credit_qty
		)
		self.material_credit_invoice_qty = (
			item.material_credit_invoice_qty
		)
		self.material_credit_status = item.material_credit_status
		self.material_credit_stock_entry = item.material_credit_stock_entry
		self.allow_processor_material_credit = (
			item.allow_processor_material_credit
		)
		self.material_credit_reason = item.material_credit_reason

	def _validate_v2_receipt_items(self) -> None:
		"""Validate V2 item identity, quantity and measurement contracts."""
		if not self.receipt_items:
			frappe.throw(
				_("At least one Receipt Item is required for a V2 receipt."),
				title=_("Receipt Items Required"),
			)

		item_keys = set()
		processed_items = set()

		for item in self.receipt_items:
			row_label = _("Receipt Item row {0}").format(item.idx)

			if not item.item_key:
				frappe.throw(_("{0}: Item Key is required.").format(row_label))

			if item.item_key in item_keys:
				frappe.throw(
					_("{0}: duplicate Item Key {1}.").format(
						row_label,
						frappe.bold(item.item_key),
					)
				)
			item_keys.add(item.item_key)

			if item.processed_item in processed_items:
				frappe.throw(
					_("Processed Item {0} occurs more than once.").format(
						frappe.bold(item.processed_item)
					),
					title=_("Duplicate Receipt Item"),
				)
			processed_items.add(item.processed_item)

			if flt(item.company_accepted_qty) < 0:
				frappe.throw(_("{0}: Company Accepted Qty cannot be negative.").format(row_label))

			if flt(item.supplier_invoice_qty) < 0:
				frappe.throw(_("{0}: Supplier Invoice Qty cannot be negative.").format(row_label))

			allowed_bases = {
				"Weight": {
					"Truck Differential Weight",
					"Separate Item Weight",
					"Manual Verified Quantity",
				},
				"Count": {
					"In-house Weigh Count",
					"Direct Count",
					"Manual Verified Quantity",
				},
			}
			if item.measurement_basis not in allowed_bases.get(
				item.measurement_method,
				set(),
			):
				frappe.throw(
					_("{0}: Measurement Basis {1} is not valid for {2}.").format(
						row_label,
						frappe.bold(item.measurement_basis),
						frappe.bold(item.measurement_method),
					),
					title=_("Invalid Measurement Basis"),
				)

	def _validate_v2_weighments(self) -> None:
		"""Validate scale readings and required differential coverage."""
		items_by_key = {
			item.item_key: item
			for item in self.receipt_items or []
		}
		weighed_item_keys = set()

		for row in self.item_weighments or []:
			if flt(row.scale_weight) < 0:
				frappe.throw(
					_("Weighment row {0}: Scale Weight cannot be negative.").format(
						row.idx
					)
				)

			if not row.receipt_item_key:
				if row.weighment_stage != "Arrival Loaded":
					frappe.throw(
						_("Weighment row {0}: Receipt Item Key is required.").format(
							row.idx
						)
					)
				row.processed_item = None
				continue

			item = items_by_key.get(row.receipt_item_key)
			if not item:
				frappe.throw(
					_("Weighment row {0}: Receipt Item Key {1} does not exist.").format(
						row.idx,
						frappe.bold(row.receipt_item_key),
					),
					title=_("Invalid Weighment Item Link"),
				)

			row.processed_item = item.processed_item
			weighed_item_keys.add(item.item_key)

		for item in self.receipt_items or []:
			if (
				item.measurement_basis == "Truck Differential Weight"
				and item.item_key not in weighed_item_keys
			):
				frappe.throw(
					_("Receipt Item {0} has no truck differential weighment.").format(
						frappe.bold(item.item_key)
					),
					title=_("Item Weighment Required"),
				)

	def _ensure_initial_v2_allocations(self) -> None:
		"""Create FIFO allocations independently for each unallocated item."""
		allocated_item_keys = {
			row.receipt_item_key
			for row in self.lot_allocations or []
			if row.receipt_item_key
		}

		for item in self.receipt_items or []:
			if item.item_key in allocated_item_keys:
				continue

			accepted_remaining = flt(
				item.lot_backed_qty,
				ITEM_QUANTITY_PRECISION,
			)
			invoice_remaining = flt(
				flt(item.supplier_invoice_qty)
				- flt(item.material_credit_invoice_qty),
				ITEM_QUANTITY_PRECISION,
			)
			created_rows = []

			for candidate in self._get_v2_fifo_candidates(item):
				if accepted_remaining <= 0:
					break

				allocated_accepted_qty = min(
					accepted_remaining,
					candidate.available_qty,
				)
				allocated_invoice_qty = min(
					invoice_remaining,
					allocated_accepted_qty,
				)
				row = self.append("lot_allocations", {
					"receipt_item_key": item.item_key,
					"processor_lot": candidate.processor_lot,
					"subcontracting_order": candidate.subcontracting_order,
					"subcontracting_order_item": candidate.subcontracting_order_item,
					"purchase_order": candidate.purchase_order,
					"purchase_order_item": candidate.purchase_order_item,
					"lot_date": candidate.lot_date,
					"processed_item": item.processed_item,
					"stock_uom": item.stock_uom,
					"lot_order_qty": candidate.lot_order_qty,
					"previously_received_qty": candidate.previously_received_qty,
					"available_qty": candidate.available_qty,
					"allocated_accepted_qty": allocated_accepted_qty,
					"allocated_invoice_qty": allocated_invoice_qty,
				})
				created_rows.append(row)
				accepted_remaining = flt(
					accepted_remaining - allocated_accepted_qty,
					ITEM_QUANTITY_PRECISION,
				)
				invoice_remaining = flt(
					invoice_remaining - allocated_invoice_qty,
					ITEM_QUANTITY_PRECISION,
				)

			if created_rows and invoice_remaining > 0:
				created_rows[-1].allocated_invoice_qty = flt(
					created_rows[-1].allocated_invoice_qty + invoice_remaining,
					ITEM_QUANTITY_PRECISION,
				)

	def _validate_v2_allocations(self) -> None:
		"""Validate lineage, capacity and totals independently by item."""
		items_by_key = {
			item.item_key: item
			for item in self.receipt_items or []
		}
		rows_by_item = {}
		seen_lots = set()

		for row in self.lot_allocations or []:
			item = items_by_key.get(row.receipt_item_key)
			if not item:
				frappe.throw(
					_("Allocation row {0}: Receipt Item Key {1} does not exist.").format(
						row.idx,
						frappe.bold(row.receipt_item_key or _("blank")),
					),
					title=_("Invalid Allocation Item Link"),
				)

			if (
				row.processed_item != item.processed_item
				or row.stock_uom != item.stock_uom
			):
				frappe.throw(
					_("Allocation row {0} does not match Receipt Item {1}.").format(
						row.idx,
						frappe.bold(item.item_key),
					),
					title=_("Allocation Item Mismatch"),
				)

			if row.processor_lot in seen_lots:
				frappe.throw(
					_("Processor Lot {0} occurs more than once.").format(
						frappe.bold(row.processor_lot)
					),
					title=_("Duplicate Processor Lot Allocation"),
				)
			seen_lots.add(row.processor_lot)
			rows_by_item.setdefault(item.item_key, []).append(row)

		for item in self.receipt_items or []:
			rows = rows_by_item.get(item.item_key, [])
			candidates = {
				candidate.processor_lot: candidate
				for candidate in self._get_v2_fifo_candidates(item)
			}
			total_accepted = 0.0
			total_invoice = 0.0

			for row in rows:
				candidate = candidates.get(row.processor_lot)
				if not candidate:
					frappe.throw(
						_("Allocation row {0}: Processor Lot {1} is not eligible.").format(
							row.idx,
							frappe.bold(row.processor_lot),
						),
						title=_("Processor Lot Is Not Open"),
					)

				if (
					row.subcontracting_order_item != candidate.subcontracting_order_item
					or row.purchase_order_item != candidate.purchase_order_item
				):
					frappe.throw(
						_("Allocation row {0} has incorrect order-item lineage.").format(
							row.idx
						),
						title=_("Invalid Allocation Lineage"),
					)

				row.subcontracting_order = candidate.subcontracting_order
				row.purchase_order = candidate.purchase_order
				row.lot_date = candidate.lot_date
				row.lot_order_qty = candidate.lot_order_qty
				row.previously_received_qty = candidate.previously_received_qty
				row.available_qty = candidate.available_qty
				accepted_qty = flt(row.allocated_accepted_qty, ITEM_QUANTITY_PRECISION)
				invoice_qty = flt(row.allocated_invoice_qty, ITEM_QUANTITY_PRECISION)

				if accepted_qty < 0 or invoice_qty < 0:
					frappe.throw(
						_("Allocation row {0} quantities cannot be negative.").format(row.idx),
						title=_("Invalid Lot Allocation"),
					)
				if accepted_qty > flt(candidate.available_qty, ITEM_QUANTITY_PRECISION):
					frappe.throw(
						_("Allocation row {0} exceeds available quantity {1}.").format(
							row.idx,
							frappe.bold(candidate.available_qty),
						),
						title=_("Processor Lot Over-Allocation"),
					)

				row.invoice_vs_accepted_qty = flt(
					invoice_qty - accepted_qty,
					ITEM_QUANTITY_PRECISION,
				) if invoice_qty else 0.0
				row.allocation_status = (
					"Fully Allocated"
					if accepted_qty == flt(candidate.available_qty, ITEM_QUANTITY_PRECISION)
					else "Partly Allocated"
				)
				total_accepted += accepted_qty
				total_invoice += invoice_qty

			expected_accepted = flt(item.lot_backed_qty, ITEM_QUANTITY_PRECISION)
			expected_invoice = flt(
				flt(item.supplier_invoice_qty) - flt(item.material_credit_invoice_qty),
				ITEM_QUANTITY_PRECISION,
			)
			if flt(total_accepted, ITEM_QUANTITY_PRECISION) != expected_accepted:
				frappe.throw(
					_("Receipt Item {0}: allocated accepted quantity must equal {1}.").format(
						frappe.bold(item.item_key),
						frappe.bold(expected_accepted),
					),
					title=_("Accepted Quantity Not Fully Allocated"),
				)
			if flt(total_invoice, ITEM_QUANTITY_PRECISION) != expected_invoice:
				frappe.throw(
					_("Receipt Item {0}: allocated invoice quantity must equal {1}.").format(
						frappe.bold(item.item_key),
						frappe.bold(expected_invoice),
					),
					title=_("Invoice Quantity Not Fully Allocated"),
				)

	def _validate_v2_material_credit(self) -> None:
		"""Require per-item authority and explanation for excess receipts."""
		for item in self.receipt_items or []:
			credit_qty = flt(item.processor_material_credit_qty, ITEM_QUANTITY_PRECISION)
			credit_invoice_qty = flt(item.material_credit_invoice_qty, ITEM_QUANTITY_PRECISION)
			if credit_qty <= 0:
				if credit_invoice_qty:
					frappe.throw(
						_("Receipt Item {0} has invoice credit without physical credit.").format(
							item.item_key
						)
					)
				continue

			if not item.allow_processor_material_credit:
				frappe.throw(
					_("Receipt Item {0} requires approval for Material Credit {1} {2}.").format(
						frappe.bold(item.item_key),
						frappe.bold(credit_qty),
						frappe.bold(item.stock_uom),
					),
					title=_("Processor Material Credit Approval Required"),
				)
			if not (item.material_credit_reason or "").strip():
				frappe.throw(
					_("Receipt Item {0} requires a Material Credit Reason.").format(
						frappe.bold(item.item_key)
					),
					title=_("Material Credit Reason Required"),
				)

	def _validate_v2_linked_scr_consistency(self) -> None:
		"""Validate active SCR rows against exact V2 allocation lineage."""
		if not self.subcontracting_receipt:
			return

		scr = frappe.get_doc(
			"Subcontracting Receipt",
			self.subcontracting_receipt,
		)
		if scr.docstatus == 2:
			return
		if scr.custom_processor_lot_receipt != self.name:
			frappe.throw(
				_("Subcontracting Receipt {0} is not linked back to this receipt.").format(
					frappe.bold(scr.name)
				),
				title=_("Subcontracting Receipt Link Mismatch"),
			)

		scr_items_by_name = {
			row.name: row
			for row in scr.items
		}
		for allocation in self.lot_allocations or []:
			scr_item = scr_items_by_name.get(
				allocation.subcontracting_receipt_item
			)
			if not scr_item:
				frappe.throw(
					_("Allocation row {0} is not linked to an SCR Item.").format(
						allocation.idx
					),
					title=_("SCR Item Link Missing"),
				)

			if (
				scr_item.subcontracting_order_item
				!= allocation.subcontracting_order_item
				or scr_item.purchase_order_item
				!= allocation.purchase_order_item
			):
				frappe.throw(
					_("Allocation row {0} and SCR Item {1} have different lineage.").format(
						allocation.idx,
						frappe.bold(scr_item.name),
					),
					title=_("SCR Item Lineage Mismatch"),
				)

			if (
				scr.docstatus == 1
				and flt(scr_item.qty, ITEM_QUANTITY_PRECISION)
				!= flt(allocation.allocated_accepted_qty, ITEM_QUANTITY_PRECISION)
			):
				frappe.throw(
					_("Allocation row {0} quantity does not match submitted SCR Item {1}.").format(
						allocation.idx,
						frappe.bold(scr_item.name),
					),
					title=_("PLR and Submitted SCR Quantity Mismatch"),
				)

	def after_insert(self) -> None:
		"""Refresh every Processor Lot represented by this receipt."""
		self._refresh_affected_processor_lots()

	def on_update(self) -> None:
		"""
		Refresh the parent Processor Lot whenever this receipt changes.

		A saved Processor Lot Receipt represents physical reality. Any edit to
		its accepted quantity, dates, weighment or commercial facts must be
		reflected immediately in the parent Processor Lot summary.
		"""
		self._refresh_affected_processor_lots(
			include_previous=True,
		)

	def on_trash(self) -> None:
		"""
		Refresh the parent Processor Lot summary after this receipt is deleted.

		The refresh is queued for after commit so the deleted receipt is no
		longer included when the summary is recomputed.
		"""
		for processor_lot in self._get_affected_processor_lots():
			frappe.enqueue(
				refresh_processor_lot_receipt_summary,
				queue="short",
				enqueue_after_commit=True,
				processor_lot=processor_lot,
			)

	def _get_affected_processor_lots(
		self,
		include_previous: bool = False,
	) -> set[str]:
		"""Return header and allocation lots affected by this save."""
		processor_lots = set()

		if self.processor_lot:
			processor_lots.add(self.processor_lot)

		for allocation in self.lot_allocations or []:
			if allocation.processor_lot:
				processor_lots.add(allocation.processor_lot)

		if include_previous:
			previous_doc = self.get_doc_before_save()

			if previous_doc:
				if previous_doc.processor_lot:
					processor_lots.add(previous_doc.processor_lot)

				for allocation in previous_doc.lot_allocations or []:
					if allocation.processor_lot:
						processor_lots.add(allocation.processor_lot)

		return processor_lots

	def _refresh_affected_processor_lots(
		self,
		include_previous: bool = False,
	) -> None:
		"""Recompute summaries for every lot affected by this PLR."""
		for processor_lot in self._get_affected_processor_lots(
			include_previous=include_previous,
		):
			refresh_processor_lot_receipt_summary(processor_lot)

	# ---------------------------------------------------------------------
	# Controlled header values
	# ---------------------------------------------------------------------

	def _validate_processor_lot_immutability(self) -> None:
		"""
		Prevent a saved PLR from being moved to another Processor Lot.

		The Processor Lot controls the SCO, supplier, company, supplier
		warehouse, processed item and Stock UOM. A saved receipt must remain
		permanently attached to its original lot.
		"""
		previous_doc = self.get_doc_before_save()

		if not previous_doc:
			return

		if not self.has_value_changed("processor_lot"):
			return

		frappe.throw(
			_(
				"Processor Lot cannot be changed after Processor Lot "
				"Receipt {0} has been created."
			).format(
				frappe.bold(self.name)
			),
			title=_("Processor Lot Is Locked"),
		)

	def _get_processor_lot(self):
		"""Return the selected active Processor Lot."""
		processor_lot = frappe.get_doc(
			"Processor Lot",
			self.processor_lot,
		)

		if processor_lot.docstatus == 2:
			frappe.throw(
				_("Processor Lot {0} is cancelled.").format(
					frappe.bold(processor_lot.name)
				)
			)

		if processor_lot.docstatus != 0:
			frappe.throw(
				_(
					"Processor Lot {0} is already submitted and cannot "
					"receive another Processor Lot Receipt."
				).format(frappe.bold(processor_lot.name)),
				title=_("Processor Lot Is Closed"),
			)

		return processor_lot

	def _get_sco(self):
		"""Return the submitted SCO linked through the Processor Lot."""
		if not self.subcontracting_order:
			frappe.throw(
				_(
					"Processor Lot {0} is not linked to a Subcontracting Order."
				).format(frappe.bold(self.processor_lot))
			)

		sco = frappe.get_doc(
			"Subcontracting Order",
			self.subcontracting_order,
		)

		if sco.docstatus == 2:
			frappe.throw(
				_("Subcontracting Order {0} is cancelled.").format(
					frappe.bold(sco.name)
				)
			)

		if sco.docstatus != 1:
			frappe.throw(
				_("Subcontracting Order {0} must be submitted.").format(
					frappe.bold(sco.name)
				)
			)

		return sco

	def _set_header_from_processor_lot(self) -> None:
		"""Copy controlled header fields from the linked Processor Lot."""
		processor_lot = self._get_processor_lot()

		self.subcontracting_order = processor_lot.subcontracting_order
		self.supplier = processor_lot.supplier
		self.company = processor_lot.company
		self.supplier_warehouse = processor_lot.supplier_warehouse

	def _set_processed_item_from_sco(self) -> None:
		"""
		Set the processed item and Stock UOM from the SCO finished-item rows.

		A Processor Lot Receipt currently represents one processed item. If an
		SCO contains more than one distinct processed item, the system requires
		an explicit design decision rather than choosing one silently.
		"""
		sco = self._get_sco()

		processed_items = {}

		for row in sco.items:
			item_code = getattr(row, "item_code", None)

			if not item_code:
				continue

			stock_uom = (
				getattr(row, "stock_uom", None)
				or frappe.db.get_value(
					"Item",
					item_code,
					"stock_uom",
				)
			)

			processed_items[item_code] = stock_uom

		if not processed_items:
			frappe.throw(
				_(
					"Subcontracting Order {0} has no processed-item row."
				).format(frappe.bold(sco.name))
			)

		if len(processed_items) > 1:
			frappe.throw(
				_(
					"Subcontracting Order {0} contains multiple processed "
					"items: {1}. Processor Lot Receipt currently supports "
					"one processed item per lot."
				).format(
					frappe.bold(sco.name),
					", ".join(sorted(processed_items)),
				)
			)

		self.processed_item, self.stock_uom = next(
			iter(processed_items.items())
		)

		self.company_accepted_uom = self.stock_uom

		self.supplier_invoice_uom = self.stock_uom

	# ---------------------------------------------------------------------
	# Physical weighment
	# ---------------------------------------------------------------------

	def _calculate_net_weights(self) -> None:
		"""Calculate supplier and company net weights."""
		self.supplier_net_weight = self._net_weight(
			self.supplier_gross_weight,
			self.supplier_tare_weight,
		)

		self.company_net_weight = self._net_weight(
			self.company_gross_weight,
			self.company_tare_weight,
		)

	def _set_company_accepted_qty_from_measurement(self) -> None:
		"""
		Derive Company Accepted Qty where physical measurement and
		finished-item stock use the same UOM.

		Where the UOMs differ, retain the manually entered accepted
		finished-item quantity.

		Example
		-------
		Company Net Qty: 1,000 Kg
		Finished Item Stock UOM: Units

		The accepted Units cannot be inferred directly from Kg and must
		therefore be entered by the operator.
		"""
		if self.measurement_method != "Weight":
			return

		measurement_uom = self.company_weighment_uom
		stock_uom = self.stock_uom

		if (
			measurement_uom
			and stock_uom
			and measurement_uom == stock_uom
		):
			self.company_accepted_qty = flt(
				self.company_net_weight,
				3,
			)

	@staticmethod
	def _net_weight(gross_weight, tare_weight) -> float:
		"""
		Return net weight when either gross or tare has been entered.

		When both inputs are untouched, retain zero.
		"""
		gross_weight = flt(gross_weight)
		tare_weight = flt(tare_weight)

		if not gross_weight and not tare_weight:
			return 0.0

		return flt(gross_weight - tare_weight, 3)

	def _validate_weights(self) -> None:
		"""Prevent impossible or negative weighment values."""
		weight_fields = (
			("Supplier Gross Weight", self.supplier_gross_weight),
			("Supplier Tare Weight", self.supplier_tare_weight),
			("Company Gross Weight", self.company_gross_weight),
			("Company Tare Weight", self.company_tare_weight),
		)

		for label, value in weight_fields:
			if flt(value) < 0:
				frappe.throw(
					_("{0} cannot be negative.").format(_(label))
				)

		if flt(self.supplier_tare_weight) > flt(
			self.supplier_gross_weight
		):
			frappe.throw(
				_(
					"Supplier Tare Weight cannot exceed "
					"Supplier Gross Weight."
				)
			)

		if flt(self.company_tare_weight) > flt(
			self.company_gross_weight
		):
			frappe.throw(
				_(
					"Company Tare Weight cannot exceed "
					"Company Gross Weight."
				)
			)

	def _validate_linked_scr_consistency(self) -> None:
		"""
		Protect PLR source facts according to the linked SCR status.

		Rules
		-----
		- A broken or incorrectly linked SCR is always blocked.
		- A cancelled SCR does not restrict the PLR.
		- A Draft SCR may temporarily differ from the PLR; the form will guide
		the user to rebuild it after the PLR is saved.
		- A submitted SCR is authoritative for Stock Ledger purposes, so the
		PLR's processed item, Stock UOM and Company Accepted Qty cannot change
		until that SCR is cancelled.
		"""
		if not self.subcontracting_receipt:
			return

		scr_values = frappe.db.get_value(
			"Subcontracting Receipt",
			self.subcontracting_receipt,
			[
				"docstatus",
				"custom_processor_lot_receipt",
				"total_qty",
			],
			as_dict=True,
		)

		if not scr_values:
			frappe.throw(
				_(
					"Linked Subcontracting Receipt {0} does not exist. "
					"Correct the broken link before changing this "
					"Processor Lot Receipt."
				).format(
					frappe.bold(self.subcontracting_receipt)
				)
			)

		if (
			scr_values.custom_processor_lot_receipt
			and scr_values.custom_processor_lot_receipt != self.name
		):
			frappe.throw(
				_(
					"Subcontracting Receipt {0} is linked to another "
					"Processor Lot Receipt."
				).format(
					frappe.bold(self.subcontracting_receipt)
				)
			)

		# A cancelled SCR no longer represents an active ERP receipt.
		if scr_values.docstatus == 2:
			return

		# Any active SCR locks the physical receipt facts.
		#
		# A Draft SCR may be deleted and rebuilt through the controlled
		# workflow. A submitted SCR must first be cancelled before the PLR's
		# physical receipt facts can be corrected.

		previous_doc = self.get_doc_before_save()

		if previous_doc:
			protected_fields = (
				"measurement_method",

				"physical_receipt_date",
				"vehicle_no",
				"supplier_challan_number",
				"supplier_challan_date",

				"supplier_weighbridge",
				"supplier_weighbridge_slip_number",
				"supplier_weighment_date",
				"supplier_gross_weight",
				"supplier_tare_weight",
				"supplier_net_weight",
				"supplier_weighment_uom",

				"company_weighbridge",
				"company_weighbridge_slip_number",
				"company_weighment_date",
				"company_gross_weight",
				"company_tare_weight",
				"company_net_weight",
				"company_weighment_uom",

				"company_accepted_qty",
				"company_accepted_uom",
				"processed_item",
				"stock_uom",
			)

			changed_fields = [
				fieldname
				for fieldname in protected_fields
				if self.has_value_changed(fieldname)
			]

			if changed_fields:
				changed_labels = ", ".join(
					self.meta.get_label(fieldname)
					for fieldname in changed_fields
				)

				frappe.throw(
					_(
						"{0} cannot be changed while active "
						"Subcontracting Receipt {1} exists. Delete the "
						"Draft SCR, or cancel the submitted SCR, before "
						"correcting the physical receipt."
					).format(
						frappe.bold(changed_labels),
						frappe.utils.get_link_to_form(
							"Subcontracting Receipt",
							self.subcontracting_receipt,
						),
					),
					title=_(
						"Physical Receipt Is Locked"
					),
				)

		# A Draft SCR locks the physical receipt facts, but its quantity
		# consistency is enforced only after the SCR is submitted.
		if scr_values.docstatus == 0:
			return

		scr_qty = flt(scr_values.total_qty, 3)
		expected_scr_qty = flt(
			self.lot_backed_qty
			if flt(self.processor_material_credit_qty) > 0
			else self.company_accepted_qty,
			3,
		)

		if scr_qty != expected_scr_qty:
			frappe.throw(
				_(
					"Processor Lot Receipt stock-backed quantity {0} does not "
					"match submitted Subcontracting Receipt {1} quantity {2}. "
					"Cancel the submitted SCR before correcting this "
					"Processor Lot Receipt."
				).format(
					frappe.bold(expected_scr_qty),
					frappe.utils.get_link_to_form(
						"Subcontracting Receipt",
						self.subcontracting_receipt,
					),
					frappe.bold(scr_qty),
				),
				title=_("PLR and Submitted SCR Quantity Mismatch"),
			)

	# ---------------------------------------------------------------------
	# Commercial reconciliation
	# ---------------------------------------------------------------------

	def _calculate_commercial_reconciliation(self) -> None:
			"""
			Compare supplier invoice quantity with company accepted quantity.

			Until a supplier invoice quantity is available, the comparison remains
			zero so that the PLR does not show a misleading negative variance.
			"""
			supplier_invoice_qty = flt(self.supplier_invoice_qty)
			company_accepted_qty = flt(self.company_accepted_qty)

			if not supplier_invoice_qty:
					self.supplier_invoice_vs_company_qty = 0.0
					self.supplier_invoice_vs_company_percent = 0.0
					return

			difference = supplier_invoice_qty - company_accepted_qty

			self.supplier_invoice_vs_company_qty = flt(
					difference,
					3,
			)

			self.supplier_invoice_vs_company_percent = (
					flt(
							difference / company_accepted_qty * 100,
							3,
					)
					if company_accepted_qty
					else 0.0
			)

	# ---------------------------------------------------------------------
	# Processor Lot allocation compatibility
	# ---------------------------------------------------------------------

	def _calculate_processor_material_credit(self) -> None:
		"""Split the truck between lot-backed and processor-credit quantities."""
		accepted_qty = flt(self.company_accepted_qty, 3)
		invoice_qty = flt(self.supplier_invoice_qty, 3)
		compatible_available_qty = flt(
			sum(
				flt(candidate.available_qty)
				for candidate in self._get_fifo_allocation_candidates()
			),
			3,
		)

		self.lot_backed_qty = flt(
			min(accepted_qty, compatible_available_qty),
			3,
		)
		self.processor_material_credit_qty = flt(
			max(accepted_qty - compatible_available_qty, 0),
			3,
		)

		# Attribute the billed portion of an excess receipt to the Processor
		# Material Credit.  The lot-backed SCR can support invoice quantity only
		# up to its own physical quantity; any further invoiced quantity belongs
		# to the separately controlled material-credit receipt.
		self.material_credit_invoice_qty = (
			_calculate_material_credit_invoice_qty(
				invoice_qty=invoice_qty,
				lot_backed_qty=self.lot_backed_qty,
				credit_qty=self.processor_material_credit_qty,
			)
		)

		if flt(self.processor_material_credit_qty) <= 0:
			self.material_credit_status = "Not Applicable"
			return

		recorded_entry = (
			frappe.db.get_value(
				"Processor Material Account Entry",
				{
					"entry_type": "Advance Credit",
					"source_event": "PLR Excess",
					"processor_lot_receipt": self.name,
					"account_direction": "Credit",
					"docstatus": 1,
				},
				"name",
			)
			if self.name
			else None
		)
		self.material_credit_status = (
			"Recorded" if recorded_entry else "Proposed"
		)

	def _validate_processor_material_credit(self) -> None:
		"""Require explicit authority and explanation for an unbacked receipt."""
		credit_qty = flt(self.processor_material_credit_qty, 3)
		credit_invoice_qty = flt(self.material_credit_invoice_qty, 3)

		if credit_qty <= 0:
			if credit_invoice_qty:
				frappe.throw(
					_(
						"Material Credit Invoice Qty must be zero when no "
						"Processor Material Credit exists."
					),
					title=_("Invalid Material Credit Split"),
				)
			return

		if not self.allow_processor_material_credit:
			frappe.throw(
				_(
					"Company Accepted Qty exceeds compatible Processor Lot "
					"balances by {0} {1}. Enable Allow Processor Material "
					"Credit and record the reason to save this physical receipt."
				).format(
					frappe.bold(credit_qty),
					frappe.bold(self.stock_uom),
				),
				title=_("Processor Material Credit Approval Required"),
			)

		if not (self.material_credit_reason or "").strip():
			frappe.throw(
				_(
					"Material Credit Reason is required for the {0} {1} "
					"Processor Material Credit."
				).format(
					frappe.bold(credit_qty),
					frappe.bold(self.stock_uom),
				),
				title=_("Material Credit Reason Required"),
			)

	def _ensure_initial_lot_allocation(self) -> None:
		"""
		Populate the minimum FIFO allocation rows needed for this truck.

		Existing rows are preserved so a later save never silently replaces an
		allocation reviewed by the operator. On first population, compatible
		Processor Lots are consumed oldest-first and only as many rows as are
		needed to cover Company Accepted Qty are added.
		"""
		if self.lot_allocations:
			return

		accepted_remaining = flt(self.lot_backed_qty)
		invoice_remaining = flt(
			flt(self.supplier_invoice_qty)
			- flt(self.material_credit_invoice_qty)
		)
		candidates = self._get_fifo_allocation_candidates()

		for candidate in candidates:
			if accepted_remaining <= 0:
				break

			allocated_accepted_qty = min(
				accepted_remaining,
				candidate.available_qty,
			)
			allocated_invoice_qty = min(
				invoice_remaining,
				allocated_accepted_qty,
			)

			self.append(
				"lot_allocations",
				{
					"processor_lot": candidate.processor_lot,
					"subcontracting_order": candidate.subcontracting_order,
					"purchase_order": candidate.purchase_order,
					"lot_date": candidate.lot_date,
					"processed_item": self.processed_item,
					"stock_uom": self.stock_uom,
					"lot_order_qty": candidate.lot_order_qty,
					"previously_received_qty": candidate.previously_received_qty,
					"available_qty": candidate.available_qty,
					"allocated_accepted_qty": allocated_accepted_qty,
					"allocated_invoice_qty": allocated_invoice_qty,
				},
			)

			accepted_remaining = flt(
				accepted_remaining - allocated_accepted_qty
			)
			invoice_remaining = flt(
				invoice_remaining - allocated_invoice_qty
			)

		# Supplier invoice quantity attributed to the backed portion may exceed
		# its accepted quantity. Put that residual on the last backed row.
		if self.lot_allocations and invoice_remaining > 0:
			last_row = self.lot_allocations[-1]
			last_row.allocated_invoice_qty = flt(
				last_row.allocated_invoice_qty + invoice_remaining
			)

		if accepted_remaining > 0:
			frappe.throw(
				_(
					"Lot Backed Qty exceeds the combined available quantity "
					"of compatible Processor Lots by {0} {1}."
				).format(
					frappe.bold(accepted_remaining),
					frappe.bold(self.stock_uom),
				),
				title=_("Insufficient Compatible Lot Balance"),
			)

	def _get_fifo_allocation_candidates(self) -> list[frappe._dict]:
		"""Return open compatible Processor Lots with unsettled FIFO capacity."""
		processor_lots = frappe.get_all(
			"Processor Lot",
			filters={
				"docstatus": 0,
				"company": self.company,
				"supplier": self.supplier,
				"supplier_warehouse": self.supplier_warehouse,
			},
			fields=[
				"name",
				"subcontracting_order",
				"purchase_order",
				"creation",
			],
			order_by="creation asc, name asc",
		)

		candidates = []

		for processor_lot in processor_lots:
			sco = frappe.get_doc(
				"Subcontracting Order",
				processor_lot.subcontracting_order,
			)

			if sco.docstatus != 1:
				continue

			matching_rows = [
				row
				for row in sco.items
				if row.item_code == self.processed_item
				and row.stock_uom == self.stock_uom
			]

			if not matching_rows:
				continue

			lot_order_qty = flt(
				sum(flt(row.qty) for row in matching_rows)
			)
			allocated_received_qty = self._get_lot_allocated_accepted_qty(
				processor_lot.name
			)
			native_received_qty = flt(
				sum(
					max(
						flt(row.received_qty)
						- flt(row.returned_qty),
						0.0,
					)
					for row in matching_rows
				)
			)
			previously_received_qty = max(
				allocated_received_qty,
				native_received_qty,
			)
			credit_applied_qty = (
				self._get_lot_submitted_credit_applied_qty(
					processor_lot.name
				)
			)
			available_qty = flt(
				lot_order_qty
				- previously_received_qty
				- credit_applied_qty
			)

			if available_qty <= 0:
				continue

			candidates.append(
				frappe._dict(
					processor_lot=processor_lot.name,
					subcontracting_order=sco.name,
					purchase_order=processor_lot.purchase_order,
					lot_date=sco.transaction_date,
					lot_order_qty=lot_order_qty,
					previously_received_qty=previously_received_qty,
					credit_applied_qty=credit_applied_qty,
					available_qty=available_qty,
					creation=processor_lot.creation,
				)
			)

		return sorted(
			candidates,
			key=lambda row: (
				row.lot_date,
				row.creation,
				row.processor_lot,
			),
		)

	def _get_lot_allocated_accepted_qty(self, processor_lot: str) -> float:
		"""Return accepted quantity already allocated to one Processor Lot."""
		allocation_doctype = self.meta.get_field(
			"lot_allocations"
		).options
		filters = {
			"processor_lot": processor_lot,
			"parenttype": self.doctype,
			"parentfield": "lot_allocations",
			"docstatus": ["!=", 2],
		}

		if not self.is_new():
			filters["parent"] = ["!=", self.name]

		return flt(
			frappe.db.get_value(
				allocation_doctype,
				filters,
				"SUM(allocated_accepted_qty)",
			)
			or 0
		)

	def _get_lot_submitted_credit_applied_qty(
		self,
		processor_lot: str,
	) -> float:
		"""Return physical receipt capacity already settled by material credit."""
		return flt(
			frappe.db.get_value(
				"Processor Material Account Entry",
				{
					"entry_type": "Credit Applied",
					"source_event": "Processor Lot Shortage",
					"processor_lot": processor_lot,
					"processed_item": self.processed_item,
					"processed_item_uom": self.stock_uom,
					"account_direction": "Debit",
					"docstatus": 1,
					"is_reversed": 0,
				},
				"SUM(processed_qty)",
			)
			or 0
		)

	def _validate_lot_allocations(self) -> None:
		"""Validate allocation capacity, totals and calculated row values."""
		if not self.lot_allocations:
			frappe.throw(
				_("At least one Lot Allocation row is required."),
				title=_("Lot Allocation Required"),
			)

		precision = max(
			row.precision("allocated_accepted_qty")
			for row in self.lot_allocations
		)
		total_allocated_accepted_qty = 0.0
		total_allocated_invoice_qty = 0.0
		eligible_candidates = {
			candidate.processor_lot: candidate
			for candidate in self._get_fifo_allocation_candidates()
		}

		for row in self.lot_allocations:
			candidate = eligible_candidates.get(row.processor_lot)

			if not candidate:
				frappe.throw(
					_(
						"Row {0}: Processor Lot {1} is not eligible for a "
						"new receipt. It may be submitted, settled, fully "
						"allocated, or its remaining quantity may already "
						"be covered by Processor Material Credit."
					).format(
						row.idx,
						frappe.bold(row.processor_lot),
					),
					title=_("Processor Lot Is Not Open"),
				)

			row.lot_order_qty = candidate.lot_order_qty
			row.previously_received_qty = (
				candidate.previously_received_qty
			)
			row.available_qty = candidate.available_qty
			allocated_accepted_qty = flt(
				row.allocated_accepted_qty,
				precision,
			)
			allocated_invoice_qty = flt(
				row.allocated_invoice_qty,
				precision,
			)
			available_qty = flt(row.available_qty, precision)

			if allocated_accepted_qty < 0:
				frappe.throw(
					_("Row {0}: Allocated Accepted Qty cannot be negative.").format(
						row.idx
					),
					title=_("Invalid Lot Allocation"),
				)

			if allocated_invoice_qty < 0:
				frappe.throw(
					_("Row {0}: Allocated Invoice Qty cannot be negative.").format(
						row.idx
					),
					title=_("Invalid Lot Allocation"),
				)

			if allocated_accepted_qty > available_qty:
				frappe.throw(
					_(
						"Row {0}: Allocated Accepted Qty {1} cannot exceed "
						"Available Qty {2} for Processor Lot {3}."
					).format(
						row.idx,
						frappe.bold(allocated_accepted_qty),
						frappe.bold(available_qty),
						frappe.bold(row.processor_lot),
					),
					title=_("Processor Lot Over-Allocation"),
				)

			row.invoice_vs_accepted_qty = (
				flt(
					allocated_invoice_qty - allocated_accepted_qty,
					precision,
				)
				if allocated_invoice_qty
				else 0.0
			)
			row.allocation_status = (
				"Fully Allocated"
				if allocated_accepted_qty == available_qty
				else "Partly Allocated"
			)

			total_allocated_accepted_qty += allocated_accepted_qty
			total_allocated_invoice_qty += allocated_invoice_qty

		total_allocated_accepted_qty = flt(
			total_allocated_accepted_qty,
			precision,
		)
		total_allocated_invoice_qty = flt(
			total_allocated_invoice_qty,
			precision,
		)
		expected_allocated_accepted_qty = flt(
			flt(self.company_accepted_qty)
			- flt(self.processor_material_credit_qty),
			precision,
		)
		expected_allocated_invoice_qty = flt(
			flt(self.supplier_invoice_qty)
			- flt(self.material_credit_invoice_qty),
			precision,
		)

		if total_allocated_accepted_qty != expected_allocated_accepted_qty:
			frappe.throw(
				_(
					"Total Allocated Accepted Qty {0} must equal "
					"Lot Backed Qty {1}."
				).format(
					frappe.bold(total_allocated_accepted_qty),
					frappe.bold(expected_allocated_accepted_qty),
				),
				title=_("Accepted Quantity Not Fully Allocated"),
			)

		if total_allocated_invoice_qty != expected_allocated_invoice_qty:
			frappe.throw(
				_(
					"Total Allocated Invoice Qty {0} must equal "
					"Lot Backed Invoice Qty {1}."
				).format(
					frappe.bold(total_allocated_invoice_qty),
					frappe.bold(expected_allocated_invoice_qty),
				),
				title=_("Invoice Quantity Not Fully Allocated"),
			)

@frappe.whitelist()
def create_material_credit_record(
	processor_lot_receipt: str,
	receipt_item_key: str | None = None,
) -> dict:
	"""Create one reviewable Draft Advance Credit PMA from a credit source."""
	plr = frappe.get_doc(
		"Processor Lot Receipt",
		processor_lot_receipt,
	)
	if not frappe.has_permission(
		"Processor Lot Receipt",
		"write",
		doc=plr,
	):
		frappe.throw(_("Not permitted to update this Processor Lot Receipt."))
	if plr.docstatus == 2:
		frappe.throw(
			_("Processor Lot Receipt {0} is cancelled.").format(
				frappe.bold(plr.name)
			)
		)

	credit_source, receipt_item_key, precision = _get_material_credit_source(
		plr,
		receipt_item_key,
	)
	credit_qty = flt(
		credit_source.processor_material_credit_qty,
		precision,
	)
	if credit_qty <= 0:
		frappe.throw(
			_("Processor Lot Receipt {0} has no material credit to record.").format(
				frappe.bold(plr.name)
			)
		)
	if not credit_source.allow_processor_material_credit:
		frappe.throw(_("Processor Material Credit approval is required."))
	if not (credit_source.material_credit_reason or "").strip():
		frappe.throw(_("Processor Material Credit Reason is required."))

	existing_rows = frappe.get_all(
		"Processor Material Account Entry",
		filters={
			"entry_type": "Advance Credit",
			"source_event": "PLR Excess",
			"processor_lot_receipt": plr.name,
			"docstatus": ["!=", 2],
		},
		fields=["name", "docstatus", "receipt_item_key"],
	)
	existing = next(
		(
			row
			for row in existing_rows
			if (row.receipt_item_key or "") == (receipt_item_key or "")
		),
		None,
	)
	if existing:
		return {
			"doctype": "Processor Material Account Entry",
			"name": existing.name,
			"docstatus": existing.docstatus,
			"created": False,
			"receipt_item_key": receipt_item_key,
		}

	entry = frappe.get_doc(
		{
			"doctype": "Processor Material Account Entry",
			"posting_date": plr.physical_receipt_date,
			"entry_type": "Advance Credit",
			"source_event": "PLR Excess",
			"processor_lot_receipt": plr.name,
			"receipt_item_key": receipt_item_key,
			"processed_qty": credit_qty,
			"account_qty": credit_qty,
			"remarks": credit_source.material_credit_reason,
		}
	)
	entry.insert()

	return {
		"doctype": entry.doctype,
		"name": entry.name,
		"docstatus": entry.docstatus,
		"created": True,
		"receipt_item_key": receipt_item_key,
	}


@frappe.whitelist()
def make_subcontracting_receipt(source_name, target_doc=None):
	"""
	Create one draft Subcontracting Receipt from a Processor Lot Receipt.

	Each Lot Allocation row is mapped from its own Subcontracting Order into
	the same target SCR. ERPNext's standard SCO-to-SCR mapper remains
	authoritative for document structure, source lineage and consumption rows;
	this method restricts each mapped finished-item row to the quantity accepted
	from that allocation's Processor Lot.
	"""
	processor_lot_receipt = frappe.get_doc(
		"Processor Lot Receipt",
		source_name,
	)

	_validate_scr_creation(processor_lot_receipt)

	if processor_lot_receipt._uses_v2_item_structure():
		processor_lot_receipt._validate_v2_allocations()
		return _make_v2_subcontracting_receipt(
			processor_lot_receipt,
			target_doc,
		)

	processor_lot_receipt._validate_lot_allocations()

	from erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order import (
		get_mapped_subcontracting_receipt,
	)

	seen_processor_lots = set()

	for allocation in processor_lot_receipt.lot_allocations:
		if allocation.processor_lot in seen_processor_lots:
			frappe.throw(
				_("Processor Lot {0} occurs more than once in Lot Allocations.").format(
					frappe.bold(allocation.processor_lot)
				),
				title=_("Duplicate Processor Lot Allocation"),
			)

		seen_processor_lots.add(allocation.processor_lot)

		allocated_qty = flt(allocation.allocated_accepted_qty, 3)
		if allocated_qty <= 0:
			frappe.throw(
				_("Row {0}: Allocated Accepted Qty must be greater than zero.").format(
					allocation.idx
				),
				title=_("Invalid Lot Allocation"),
			)

		processor_lot = frappe.get_doc(
			"Processor Lot",
			allocation.processor_lot,
		)

		if processor_lot.docstatus == 2:
			frappe.throw(
				_("Processor Lot {0} is cancelled.").format(
					frappe.bold(processor_lot.name)
				)
			)

		if processor_lot.subcontracting_order != allocation.subcontracting_order:
			frappe.throw(
				_(
					"Row {0}: Subcontracting Order {1} does not belong to "
					"Processor Lot {2}."
				).format(
					allocation.idx,
					frappe.bold(allocation.subcontracting_order),
					frappe.bold(allocation.processor_lot),
				),
				title=_("Invalid Lot Allocation Lineage"),
			)

		item_count_before_mapping = (
			len(target_doc.items) if target_doc else 0
		)
		target_doc = get_mapped_subcontracting_receipt(
			allocation.subcontracting_order,
			target_doc,
		)
		new_items = target_doc.items[item_count_before_mapping:]
		matching_rows = [
			row
			for row in new_items
			if row.item_code == allocation.processed_item
			and row.stock_uom == allocation.stock_uom
		]

		if len(new_items) != 1 or len(matching_rows) != 1:
			frappe.throw(
				_(
					"Subcontracting Order {0} must map exactly one open row "
					"for processed item {1} in UOM {2}; ERPNext mapped {3} "
					"new row(s), of which {4} matched."
				).format(
					frappe.bold(allocation.subcontracting_order),
					frappe.bold(allocation.processed_item),
					frappe.bold(allocation.stock_uom),
					len(new_items),
					len(matching_rows),
				),
				title=_("Ambiguous Subcontracting Order Mapping"),
			)

		item = matching_rows[0]
		item.qty = allocated_qty
		item.rejected_qty = 0
		item.branch = target_doc.branch or processor_lot_receipt.get("branch")

	target_doc.custom_processor_lot = (
		processor_lot_receipt.processor_lot
	)
	target_doc.custom_processor_lot_receipt = (
		processor_lot_receipt.name
	)
	_set_mapped_scr_posting_date(
		target_doc,
		processor_lot_receipt,
	)

	return target_doc


def _make_v2_subcontracting_receipt(processor_lot_receipt, target_doc=None):
	"""Map V2 allocations to exact SCR rows in one target document."""
	from erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order import (
		get_mapped_subcontracting_receipt,
	)

	seen_processor_lots = set()
	for allocation in processor_lot_receipt.lot_allocations:
		if allocation.processor_lot in seen_processor_lots:
			frappe.throw(
				_("Processor Lot {0} occurs more than once.").format(
					frappe.bold(allocation.processor_lot)
				),
				title=_("Duplicate Processor Lot Allocation"),
			)
		seen_processor_lots.add(allocation.processor_lot)

		allocated_qty = flt(
			allocation.allocated_accepted_qty,
			ITEM_QUANTITY_PRECISION,
		)
		if allocated_qty <= 0:
			frappe.throw(
				_("Allocation row {0} must have a positive Accepted Qty.").format(
					allocation.idx
				),
				title=_("Invalid Lot Allocation"),
			)

		item_count_before_mapping = len(target_doc.items) if target_doc else 0
		target_doc = get_mapped_subcontracting_receipt(
			allocation.subcontracting_order,
			target_doc,
		)
		new_items = target_doc.items[item_count_before_mapping:]
		matching_rows = [
			row
			for row in new_items
			if row.subcontracting_order_item
			== allocation.subcontracting_order_item
			and row.purchase_order_item
			== allocation.purchase_order_item
		]

		if len(new_items) != 1 or len(matching_rows) != 1:
			frappe.throw(
				_(
					"Subcontracting Order {0} must map exactly one row for "
					"SCO Item {1} and PO Item {2}; ERPNext mapped {3} new "
					"row(s), of which {4} matched."
				).format(
					frappe.bold(allocation.subcontracting_order),
					frappe.bold(allocation.subcontracting_order_item),
					frappe.bold(allocation.purchase_order_item),
					len(new_items),
					len(matching_rows),
				),
				title=_("Ambiguous Subcontracting Order Mapping"),
			)

		item = matching_rows[0]
		if (
			item.item_code != allocation.processed_item
			or item.stock_uom != allocation.stock_uom
		):
			frappe.throw(
				_("Mapped SCR row does not match Allocation row {0}.").format(
					allocation.idx
				),
				title=_("Mapped SCR Item Mismatch"),
			)

		item.qty = allocated_qty
		item.rejected_qty = 0
		item.branch = target_doc.branch or processor_lot_receipt.get("branch")

	target_doc.custom_processor_lot = processor_lot_receipt.processor_lot
	target_doc.custom_processor_lot_receipt = processor_lot_receipt.name
	_set_mapped_scr_posting_date(target_doc, processor_lot_receipt)

	return target_doc


def _set_mapped_scr_posting_date(
	subcontracting_receipt,
	processor_lot_receipt,
) -> None:
	"""Default a mapped SCR to the PLR's physical receipt date."""
	if not processor_lot_receipt.physical_receipt_date:
		return

	subcontracting_receipt.set_posting_time = 1
	subcontracting_receipt.posting_date = (
		processor_lot_receipt.physical_receipt_date
	)

@frappe.whitelist()
def refresh_draft_subcontracting_receipt(
    processor_lot_receipt: str,
) -> dict:
    """
    Create or rebuild the Draft Subcontracting Receipt for one PLR.

    Rules
    -----
    - If no SCR exists, create a fresh Draft SCR.
    - If a Draft SCR exists, delete and rebuild it from the current PLR.
    - If a submitted SCR exists, stop and require cancellation first.
    - Cancelled or deleted SCR references do not block rebuilding.

    The complete SCO-to-SCR mapper is reused so finished-item rows,
    supplied-item consumption, costs and document references are rebuilt
    through ERPNext's standard logic.
    """
    if not processor_lot_receipt:
        frappe.throw(
            _("Processor Lot Receipt is required.")
        )

    if not frappe.db.exists(
        "Processor Lot Receipt",
        processor_lot_receipt,
    ):
        frappe.throw(
            _("Processor Lot Receipt {0} does not exist.").format(
                frappe.bold(processor_lot_receipt)
            )
        )

    plr = frappe.get_doc(
        "Processor Lot Receipt",
        processor_lot_receipt,
    )

    existing_scr = plr.subcontracting_receipt

    if not existing_scr:
        existing_scr = frappe.db.get_value(
            "Subcontracting Receipt",
            {
                "custom_processor_lot_receipt": plr.name,
                "docstatus": ["!=", 2],
            },
            "name",
        )

    replaced_scr = None

    if existing_scr:
        scr_docstatus = frappe.db.get_value(
            "Subcontracting Receipt",
            existing_scr,
            "docstatus",
        )

        if scr_docstatus is None:
            # Remove a stale PLR reference to a deleted SCR.
            frappe.db.set_value(
                "Processor Lot Receipt",
                plr.name,
                "subcontracting_receipt",
                None,
                update_modified=False,
            )

        elif scr_docstatus == 1:
            frappe.throw(
                _(
                    "Subcontracting Receipt {0} is submitted. "
                    "Cancel it before rebuilding the receipt from "
                    "Processor Lot Receipt {1}."
                ).format(
                    frappe.utils.get_link_to_form(
                        "Subcontracting Receipt",
                        existing_scr,
                    ),
                    frappe.bold(plr.name),
                ),
                title=_("Submitted SCR Cannot Be Rebuilt"),
            )

        elif scr_docstatus == 0:
            replaced_scr = existing_scr

            frappe.delete_doc(
                "Subcontracting Receipt",
                existing_scr,
                ignore_permissions=False,
            )

        else:
            # A cancelled SCR does not remain the active PLR document.
            frappe.db.set_value(
                "Processor Lot Receipt",
                plr.name,
                "subcontracting_receipt",
                None,
                update_modified=False,
            )

	# Reload after deleting or clearing the old SCR because lifecycle hooks
	# may have changed the maintained link on the PLR.
    plr = frappe.get_doc(
        "Processor Lot Receipt",
        processor_lot_receipt,
    )

    new_scr = make_subcontracting_receipt(
        plr.name
    )

    new_scr.insert()

    frappe.db.set_value(
        "Processor Lot Receipt",
        plr.name,
        "subcontracting_receipt",
        new_scr.name,
        update_modified=False,
    )

    return {
        "doctype": "Subcontracting Receipt",
        "name": new_scr.name,
        "docstatus": new_scr.docstatus,
        "processor_lot_receipt": plr.name,
        "processor_lot": plr.processor_lot,
        "replaced_subcontracting_receipt": replaced_scr,
        "action": (
            "Rebuilt"
            if replaced_scr
            else "Created"
        ),
        "message": (
            _(
                "Draft Subcontracting Receipt {0} was rebuilt "
                "from Processor Lot Receipt {1}."
            ).format(
                frappe.bold(new_scr.name),
                frappe.bold(plr.name),
            )
            if replaced_scr
            else _(
                "Draft Subcontracting Receipt {0} was created "
                "from Processor Lot Receipt {1}."
            ).format(
                frappe.bold(new_scr.name),
                frappe.bold(plr.name),
            )
        ),
    }

def _validate_scr_creation(processor_lot_receipt) -> None:
	"""Validate that the PLR is ready to create its SCR."""
	if processor_lot_receipt.is_new():
		frappe.throw(
			_("Please save the Processor Lot Receipt first.")
		)

	is_v2 = (
		processor_lot_receipt.receipt_structure_version
		== V2_RECEIPT_STRUCTURE
	)

	if processor_lot_receipt.docstatus == 2:
		frappe.throw(
			_("A cancelled Processor Lot Receipt cannot create an SCR.")
		)

	credit_qty = flt(processor_lot_receipt.processor_material_credit_qty, 3)
	if not is_v2 and credit_qty > 0:
		_validate_recorded_material_credit_for_scr(
			processor_lot_receipt,
			credit_qty,
		)

		if flt(processor_lot_receipt.lot_backed_qty, 3) <= 0:
			frappe.throw(
				_(
					"Processor Lot Receipt {0} has no Lot Backed Qty from "
					"which a Subcontracting Receipt can be created."
				).format(frappe.bold(processor_lot_receipt.name)),
				title=_("No Stock-Backed Quantity for SCR"),
			)

	if processor_lot_receipt.subcontracting_receipt:
		frappe.throw(
			_(
				"Subcontracting Receipt {0} is already linked to this "
				"Processor Lot Receipt."
			).format(
				frappe.bold(
					processor_lot_receipt.subcontracting_receipt
				)
			)
		)

	existing_scr = frappe.db.get_value(
		"Subcontracting Receipt",
		{
			"custom_processor_lot_receipt":
				processor_lot_receipt.name,
			"docstatus": ["!=", 2],
		},
		"name",
	)

	if existing_scr:
		frappe.throw(
			_(
				"Subcontracting Receipt {0} already exists for this "
				"Processor Lot Receipt."
			).format(
				frappe.bold(existing_scr)
			),
			title=_("Subcontracting Receipt Already Exists"),
		)

	if is_v2:
		if not processor_lot_receipt.receipt_items:
			frappe.throw(_("At least one Receipt Item is required."))

		credit_items = [
			item
			for item in processor_lot_receipt.receipt_items
			if flt(item.processor_material_credit_qty, ITEM_QUANTITY_PRECISION) > 0
		]
		for item in credit_items:
			_validate_recorded_material_credit_for_scr(
				processor_lot_receipt,
				flt(item.processor_material_credit_qty, ITEM_QUANTITY_PRECISION),
				receipt_item_key=item.item_key,
			)
			if flt(item.lot_backed_qty, ITEM_QUANTITY_PRECISION) <= 0:
				frappe.throw(
					_("Receipt Item {0} has no Lot Backed Qty from which an SCR row can be created.").format(
						frappe.bold(item.item_key)
					),
					title=_("No Stock-Backed Quantity for SCR"),
				)

		invalid_items = [
			item.item_key
			for item in processor_lot_receipt.receipt_items
			if flt(item.company_accepted_qty, ITEM_QUANTITY_PRECISION) <= 0
		]
		if invalid_items:
			frappe.throw(
				_("Company Accepted Qty must be positive for Receipt Items: {0}.").format(
					", ".join(invalid_items)
				),
				title=_("Invalid Receipt Item Quantity"),
			)
		return

	if flt(processor_lot_receipt.company_accepted_qty) <= 0:
			frappe.throw(
					_(
							"Company Accepted Qty must be greater than zero "
							"before creating a Subcontracting Receipt."
					)
			)


def _validate_recorded_material_credit_for_scr(
	processor_lot_receipt,
	credit_qty: float,
	receipt_item_key: str | None = None,
) -> None:
	"""Require an exact submitted PMA credit before mapping backed stock."""
	credit_source, receipt_item_key, precision = _get_material_credit_source(
		processor_lot_receipt,
		receipt_item_key,
	)
	credit_qty = flt(credit_qty, precision)
	if credit_source.material_credit_status != "Recorded":
		frappe.throw(
			_(
				"Processor Material Credit {0} {1} on Processor Lot "
				"Receipt {2} must be Recorded before creating its "
				"stock-backed Subcontracting Receipt."
			).format(
				frappe.bold(credit_qty),
				frappe.bold(credit_source.stock_uom),
				frappe.bold(processor_lot_receipt.name),
			),
			title=_("Processor Material Credit Is Not Recorded"),
		)

	_get_submitted_plr_material_credit(
		processor_lot_receipt,
		credit_qty,
		receipt_item_key=receipt_item_key,
	)


@frappe.whitelist()
def create_material_credit_stock_entry(
	processor_lot_receipt: str,
	receipt_item_key: str | None = None,
) -> dict:
	"""Create one linked Draft Material Receipt for a recorded PLR credit.

	The submitted, stock-backed Subcontracting Receipt is the valuation source:
	its finished-item receipt supplies the processed-item valuation rate and its
	principal-component issue supplies the component rate.  The difference is
	added through Stock Entry Additional Costs.  This method deliberately does
	not submit the resulting Stock Entry.
	"""
	if not processor_lot_receipt:
		frappe.throw(_("Processor Lot Receipt is required."))

	if not frappe.db.exists(
		"Processor Lot Receipt",
		processor_lot_receipt,
	):
		frappe.throw(
			_("Processor Lot Receipt {0} does not exist.").format(
				frappe.bold(processor_lot_receipt)
			)
		)

	plr = frappe.get_doc(
		"Processor Lot Receipt",
		processor_lot_receipt,
	)
	if plr.is_new() or plr.docstatus == 2:
		frappe.throw(
			_("A saved, active Processor Lot Receipt is required."),
			title=_("Invalid Processor Lot Receipt"),
		)

	credit_source, receipt_item_key, precision = _get_material_credit_source(
		plr,
		receipt_item_key,
	)
	credit_qty = flt(
		credit_source.processor_material_credit_qty,
		precision,
	)
	if credit_qty <= 0:
		frappe.throw(
			_("Processor Lot Receipt {0} has no material credit to receive.").format(
				frappe.bold(plr.name)
			),
			title=_("No Processor Material Credit"),
		)

	_validate_recorded_material_credit_for_scr(
		plr,
		credit_qty,
		receipt_item_key=receipt_item_key,
	)
	pma = _get_submitted_plr_material_credit(
		plr,
		credit_qty,
		receipt_item_key=receipt_item_key,
	)
	scr = _get_submitted_backed_scr(plr, receipt_item_key)
	_existing_material_credit_stock_entry(plr, pma, credit_source)

	valuation = _get_material_credit_valuation(
		plr=plr,
		pma=pma,
		scr=scr,
		receipt_item_key=receipt_item_key,
	)
	processor_liability = _get_processor_material_account(
		company=plr.company,
		account_name="Processor Material Credit Liability",
	)
	accrued_processing = _get_processor_material_account(
		company=plr.company,
		account_name="Accrued Subcontracting Charges",
	)

	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.company = plr.company
	stock_entry.stock_entry_type = "Material Receipt"
	stock_entry.purpose = "Material Receipt"
	stock_entry.set_posting_time = 1
	stock_entry.posting_date = scr.posting_date
	stock_entry.posting_time = scr.posting_time
	if stock_entry.meta.has_field("branch"):
		stock_entry.branch = getattr(scr, "branch", None) or getattr(
			plr,
			"branch",
			None,
		)
	stock_entry.remarks = _(
		"Processor Material Credit receipt for PLR {0}; quantity recorded by "
		"PMA {1}; valuation derived from submitted backed SCR {2}."
	).format(plr.name, pma.name, scr.name)

	stock_entry.append(
		"items",
		{
			"item_code": credit_source.processed_item,
			"t_warehouse": valuation["target_warehouse"],
			"qty": credit_qty,
			"basic_rate": valuation["component_rate"],
			"expense_account": processor_liability,
		},
	)

	if valuation["processing_value"] > 0:
		stock_entry.append(
			"additional_costs",
			{
				"expense_account": accrued_processing,
				"description": _(
					"Unbilled processing cost for {0} {1} Processor Material "
					"Credit from {2}"
				).format(credit_qty, credit_source.stock_uom, plr.name),
				"amount": valuation["processing_value"],
			},
		)

	stock_entry.insert()
	_created_item = stock_entry.items[0]
	if (
		stock_entry.docstatus != 0
		or flt(_created_item.qty, precision) != credit_qty
		or _created_item.item_code != credit_source.processed_item
		or _created_item.t_warehouse != valuation["target_warehouse"]
		or _created_item.expense_account != processor_liability
		or flt(_created_item.basic_rate, 6)
		!= flt(valuation["component_rate"], 6)
		or flt(_created_item.basic_amount, 2)
		!= flt(valuation["component_value"], 2)
		or flt(_created_item.additional_cost, 2)
		!= flt(valuation["processing_value"], 2)
		or not _valuation_rates_match(
			_created_item.valuation_rate,
			valuation["finished_rate"],
		)
		or flt(_created_item.amount, 2)
		!= flt(valuation["total_value"], 2)
		or flt(stock_entry.total_incoming_value, 2)
		!= flt(valuation["total_value"], 2)
		or flt(stock_entry.total_additional_costs, 2)
		!= flt(valuation["processing_value"], 2)
		or flt(stock_entry.total_amount, 2)
		!= flt(valuation["total_value"], 2)
	):
		frappe.throw(
			_("The generated Material Receipt failed its control checks."),
			title=_("Material Credit Stock Entry Control Failed"),
		)

	frappe.db.set_value(
		(
			"Processor Lot Receipt Item"
			if receipt_item_key
			else "Processor Lot Receipt"
		),
		credit_source.name if receipt_item_key else plr.name,
		"material_credit_stock_entry",
		stock_entry.name,
		update_modified=False,
	)
	frappe.db.set_value(
		"Processor Material Account Entry",
		pma.name,
		"material_credit_stock_entry",
		stock_entry.name,
		update_modified=False,
	)

	return {
		"doctype": "Stock Entry",
		"name": stock_entry.name,
		"docstatus": stock_entry.docstatus,
		"action": "Created",
		"processor_lot_receipt": plr.name,
		"receipt_item_key": receipt_item_key,
		"processor_material_account_entry": pma.name,
		"subcontracting_receipt": scr.name,
		"credit_qty": credit_qty,
		"stock_uom": credit_source.stock_uom,
		"target_warehouse": valuation["target_warehouse"],
		"component_rate": valuation["component_rate"],
		"processing_rate": valuation["processing_rate"],
		"valuation_rate": valuation["finished_rate"],
		"component_value": valuation["component_value"],
		"processing_value": valuation["processing_value"],
		"total_value": valuation["total_value"],
		"processor_material_credit_liability": processor_liability,
		"accrued_subcontracting_charges": accrued_processing,
		"message": _(
			"Draft Material Receipt {0} was created. Review it before submission."
		).format(frappe.bold(stock_entry.name)),
	}


def _get_submitted_plr_material_credit(
	plr,
	credit_qty: float,
	receipt_item_key: str | None = None,
):
	"""Return the single submitted PMA credit that exactly matches the PLR."""
	credit_source, receipt_item_key, precision = _get_material_credit_source(
		plr,
		receipt_item_key,
	)
	entries = frappe.get_all(
		"Processor Material Account Entry",
		filters={
			"entry_type": "Advance Credit",
			"source_event": "PLR Excess",
			"processor_lot_receipt": plr.name,
			"account_direction": "Credit",
			"docstatus": 1,
		},
		fields=[
			"name",
			"processed_qty",
			"account_qty",
			"account_uom",
			"processed_item",
			"principal_component",
			"material_credit_stock_entry",
			"receipt_item_key",
		],
	)
	entries = [
		entry
		for entry in entries
		if (entry.receipt_item_key or "") == (receipt_item_key or "")
	]
	if len(entries) != 1:
		frappe.throw(
			_(
				"Processor Lot Receipt {0} must have exactly one submitted "
				"Processor Material Account credit; found {1}."
			).format(frappe.bold(plr.name), len(entries)),
			title=_("Material Credit Source Is Ambiguous"),
		)

	entry = entries[0]
	if (
		flt(entry.processed_qty, precision) != flt(credit_qty, precision)
		or flt(entry.account_qty, precision) != flt(credit_qty, precision)
		or entry.account_uom != credit_source.stock_uom
		or entry.processed_item != credit_source.processed_item
		or not entry.principal_component
	):
		frappe.throw(
			_("Submitted Processor Material Account Entry {0} no longer "
			  "matches the PLR material credit facts.").format(
				frappe.bold(entry.name)
			),
			title=_("Material Credit Source Does Not Match"),
		)
	return entry


def _get_v2_backed_scr_item_names(plr, scr, receipt_item_key: str) -> set[str]:
	"""Return exact saved SCR rows linked to one V2 receipt item."""
	allocations = [
		row
		for row in plr.lot_allocations or []
		if row.receipt_item_key == receipt_item_key
		and flt(row.allocated_accepted_qty, ITEM_QUANTITY_PRECISION) > 0
	]
	if not allocations:
		frappe.throw(
			_("Receipt Item {0} has no positive backed allocations.").format(
				frappe.bold(receipt_item_key)
			)
		)
	missing_links = [row.idx for row in allocations if not row.subcontracting_receipt_item]
	if missing_links:
		frappe.throw(
			_("Receipt Item {0} has allocation rows without saved SCR Item links: {1}.").format(
				frappe.bold(receipt_item_key),
				", ".join(str(idx) for idx in missing_links),
			)
		)
	item_names = {row.subcontracting_receipt_item for row in allocations}
	scr_names = {row.name for row in scr.items}
	missing_items = sorted(item_names - scr_names)
	if missing_items:
		frappe.throw(
			_("Saved SCR Item links do not belong to Subcontracting Receipt {0}: {1}.").format(
				frappe.bold(scr.name),
				", ".join(missing_items),
			)
		)
	return item_names


def _get_submitted_backed_scr(plr, receipt_item_key: str | None = None):
	"""Return the submitted SCR proving the lot-backed receipt quantity."""
	if not plr.subcontracting_receipt:
		frappe.throw(
			_("Processor Lot Receipt {0} is not linked to an SCR.").format(
				frappe.bold(plr.name)
			),
			title=_("Submitted Backed SCR Required"),
		)

	scr = frappe.get_doc(
		"Subcontracting Receipt",
		plr.subcontracting_receipt,
	)
	if scr.docstatus != 1:
		frappe.throw(
			_("Subcontracting Receipt {0} must be submitted first.").format(
				frappe.bold(scr.name)
			),
			title=_("Submitted Backed SCR Required"),
		)
	if getattr(scr, "custom_processor_lot_receipt", None) != plr.name:
		frappe.throw(
			_("Subcontracting Receipt {0} does not belong to PLR {1}.").format(
				frappe.bold(scr.name),
				frappe.bold(plr.name),
			),
			title=_("Invalid SCR Lineage"),
		)

	credit_source, receipt_item_key, precision = _get_material_credit_source(
		plr,
		receipt_item_key,
	)
	if receipt_item_key:
		item_names = _get_v2_backed_scr_item_names(
			plr,
			scr,
			receipt_item_key,
		)
		scr_qty = flt(
			sum(flt(row.qty) for row in scr.items if row.name in item_names),
			precision,
		)
	else:
		scr_qty = flt(sum(flt(row.qty) for row in scr.items), precision)
	if scr_qty != flt(credit_source.lot_backed_qty, precision):
		frappe.throw(
			_("Submitted SCR {0} quantity {1} must equal Lot Backed Qty {2}.").format(
				frappe.bold(scr.name),
				frappe.bold(scr_qty),
				frappe.bold(flt(credit_source.lot_backed_qty, precision)),
			),
			title=_("Backed SCR Quantity Does Not Match"),
		)
	return scr


def _existing_material_credit_stock_entry(plr, pma, credit_source=None) -> None:
	"""Block duplicates and repair links which only reference deleted records."""
	credit_source = credit_source or plr
	plr_link = getattr(credit_source, "material_credit_stock_entry", None)
	pma_link = pma.material_credit_stock_entry
	if plr_link and pma_link and plr_link != pma_link:
		frappe.throw(
			_("PLR {0} and PMA {1} point to different Stock Entries.").format(
				frappe.bold(plr.name),
				frappe.bold(pma.name),
			),
			title=_("Material Credit Stock Entry Link Conflict"),
		)

	existing = plr_link or pma_link
	if not existing:
		return

	docstatus = frappe.db.get_value("Stock Entry", existing, "docstatus")
	if docstatus is not None and docstatus != 2:
		frappe.throw(
			_("Stock Entry {0} already records this Processor Material Credit.").format(
				frappe.utils.get_link_to_form("Stock Entry", existing)
			),
			title=_("Material Credit Stock Entry Already Exists"),
		)

	# Deleted or cancelled documents are not active ownership links.
	frappe.db.set_value(
		(
			"Processor Lot Receipt Item"
			if credit_source is not plr
			else "Processor Lot Receipt"
		),
		credit_source.name if credit_source is not plr else plr.name,
		"material_credit_stock_entry",
		None,
		update_modified=False,
	)
	frappe.db.set_value(
		"Processor Material Account Entry",
		pma.name,
		"material_credit_stock_entry",
		None,
		update_modified=False,
	)


def _valuation_rates_match(
	actual_rate: float,
	expected_rate: float,
) -> bool:
	"""Compare display rates while monetary amounts remain exact controls.

	ERPNext derives the stored valuation rate from currency-rounded item values,
	so harmless sub-paise differences can remain beyond currency precision.
	"""
	return flt(actual_rate, 2) == flt(expected_rate, 2)


def _get_material_credit_valuation(
	plr,
	pma,
	scr,
	receipt_item_key: str | None = None,
) -> dict:
	"""Derive component and processing rates from the submitted backed SCR."""
	credit_source, receipt_item_key, precision = _get_material_credit_source(
		plr,
		receipt_item_key,
	)
	scr_item_names = None
	supplied_item_names = None
	if receipt_item_key:
		scr_item_names = _get_v2_backed_scr_item_names(
			plr,
			scr,
			receipt_item_key,
		)
		supplied_item_names = {
			row.name
			for row in scr.supplied_items or []
			if row.reference_name in scr_item_names
			and row.rm_item_code == pma.principal_component
		}
		if not supplied_item_names:
			frappe.throw(
				_("Receipt Item {0} has no principal-component supplied rows in SCR {1}.").format(
					frappe.bold(receipt_item_key),
					frappe.bold(scr.name),
				),
				title=_("SCR Valuation Evidence Missing"),
			)
	entries = frappe.get_all(
		"Stock Ledger Entry",
		filters={
			"voucher_type": "Subcontracting Receipt",
			"voucher_no": scr.name,
			"is_cancelled": 0,
		},
		fields=[
			"item_code",
			"warehouse",
			"actual_qty",
			"stock_value_difference",
			"voucher_detail_no",
		],
	)
	finished = [
		row for row in entries
		if row.item_code == credit_source.processed_item
		and flt(row.actual_qty) > 0
		and (scr_item_names is None or row.voucher_detail_no in scr_item_names)
	]
	components = [
		row for row in entries
		if row.item_code == pma.principal_component
		and row.warehouse == plr.supplier_warehouse
		and flt(row.actual_qty) < 0
		and (
			supplied_item_names is None
			or row.voucher_detail_no in supplied_item_names
		)
	]
	if not finished or not components:
		frappe.throw(
			_("Submitted SCR {0} has no unambiguous finished-item and principal-"
			  "component Stock Ledger movements for this credit.").format(
				frappe.bold(scr.name)
			),
			title=_("SCR Valuation Evidence Missing"),
		)

	target_warehouses = {row.warehouse for row in finished}
	if len(target_warehouses) != 1:
		frappe.throw(
			_("Submitted SCR {0} received the processed item into more than one "
			  "warehouse.").format(frappe.bold(scr.name)),
			title=_("Ambiguous Target Warehouse"),
		)

	finished_qty = flt(sum(flt(row.actual_qty) for row in finished), 6)
	finished_value = flt(
		sum(flt(row.stock_value_difference) for row in finished),
		6,
	)
	component_qty = abs(
		flt(sum(flt(row.actual_qty) for row in components), 6)
	)
	component_value = abs(
		flt(sum(flt(row.stock_value_difference) for row in components), 6)
	)
	backed_qty = flt(credit_source.lot_backed_qty, precision)
	if (
		flt(finished_qty, precision) != backed_qty
		or flt(component_qty, precision) != backed_qty
		or finished_value <= 0
		or component_value <= 0
	):
		frappe.throw(
			_("SCR {0} Stock Ledger quantities or values do not match the PLR's "
			  "1:1 backed quantity of {1} {2}.").format(
				frappe.bold(scr.name),
				frappe.bold(backed_qty),
				frappe.bold(credit_source.stock_uom),
			),
			title=_("SCR Valuation Evidence Does Not Match"),
		)

	finished_rate = flt(finished_value / finished_qty, 9)
	component_rate = flt(component_value / component_qty, 9)
	processing_rate = flt(finished_rate - component_rate, 9)
	if processing_rate < 0:
		frappe.throw(
			_("SCR {0} implies a negative processing rate.").format(
				frappe.bold(scr.name)
			),
			title=_("Invalid Material Credit Valuation"),
		)

	credit_qty = flt(
		credit_source.processor_material_credit_qty,
		precision,
	)
	component_credit_value = flt(credit_qty * component_rate, 2)
	processing_value = flt(credit_qty * processing_rate, 2)
	return {
		"target_warehouse": next(iter(target_warehouses)),
		"finished_rate": finished_rate,
		"component_rate": component_rate,
		"processing_rate": processing_rate,
		"component_value": component_credit_value,
		"processing_value": processing_value,
		"total_value": flt(component_credit_value + processing_value, 2),
	}


def _get_processor_material_account(company: str, account_name: str) -> str:
	"""Return one enabled leaf account provisioned for this company."""
	accounts = frappe.get_all(
		"Account",
		filters={
			"company": company,
			"account_name": account_name,
			"is_group": 0,
			"disabled": 0,
		},
		pluck="name",
	)
	if len(accounts) != 1:
		frappe.throw(
			_("Company {0} must have exactly one enabled leaf account named {1}; "
			  "found {2}.").format(
				frappe.bold(company),
				frappe.bold(account_name),
				len(accounts),
			),
			title=_("Processor Material Account Setup Required"),
		)
	return accounts[0]


def validate_material_credit_stock_entry(doc, method=None) -> None:
	"""Protect every controlled fact on a linked material-credit receipt."""
	plr_names = frappe.get_all(
		"Processor Lot Receipt",
		filters={"material_credit_stock_entry": doc.name},
		pluck="name",
	)
	pma_names = frappe.get_all(
		"Processor Material Account Entry",
		filters={"material_credit_stock_entry": doc.name},
		pluck="name",
	)

	# Ordinary Stock Entries remain completely outside this workflow.
	if not plr_names and not pma_names:
		return

	if len(pma_names) != 1:
		frappe.throw(
			_(
				"Material Credit Stock Entry {0} must be linked to exactly one "
				"Processor Material Account Entry."
			).format(frappe.bold(doc.name)),
			title=_("Incomplete Material Credit Lineage"),
		)

	pma = frappe.get_doc(
		"Processor Material Account Entry",
		pma_names[0],
	)
	plr = frappe.get_doc(
		"Processor Lot Receipt",
		pma.processor_lot_receipt,
	)
	credit_source, receipt_item_key, precision = _get_material_credit_source(
		plr,
		pma.receipt_item_key,
	)
	credit_qty = flt(
		credit_source.processor_material_credit_qty,
		precision,
	)
	if receipt_item_key:
		if plr_names:
			frappe.throw(
				_("A V2 item-specific Stock Entry cannot be owned by the PLR header."),
				title=_("Invalid Material Credit Lineage"),
			)
		if credit_source.material_credit_stock_entry != doc.name:
			frappe.throw(
				_("Receipt Item {0} is not linked to Stock Entry {1}.").format(
					frappe.bold(receipt_item_key),
					frappe.bold(doc.name),
				),
				title=_("Incomplete Material Credit Lineage"),
			)
	else:
		if plr_names != [plr.name]:
			frappe.throw(
				_("Legacy Material Credit Stock Entry must be linked to its PLR header."),
				title=_("Incomplete Material Credit Lineage"),
			)
	if pma.processor_lot_receipt != plr.name or pma.docstatus != 1:
		frappe.throw(
			_(
				"Linked Processor Material Account Entry {0} must be a submitted "
				"credit belonging to Processor Lot Receipt {1}."
			).format(
				frappe.bold(pma.name),
				frappe.bold(plr.name),
			),
			title=_("Invalid Material Credit Lineage"),
		)

	submitted_credit = _get_submitted_plr_material_credit(
		plr,
		credit_qty,
		receipt_item_key=receipt_item_key,
	)
	if submitted_credit.name != pma.name:
		frappe.throw(
			_(
				"Stock Entry {0} is linked to PMA {1}, but PMA {2} is the "
				"submitted source credit for PLR {3}."
			).format(
				frappe.bold(doc.name),
				frappe.bold(pma.name),
				frappe.bold(submitted_credit.name),
				frappe.bold(plr.name),
			),
			title=_("Material Credit Source Conflict"),
		)

	scr = _get_submitted_backed_scr(plr, receipt_item_key)
	valuation = _get_material_credit_valuation(
		plr=plr,
		pma=pma,
		scr=scr,
		receipt_item_key=receipt_item_key,
	)
	processor_liability = _get_processor_material_account(
		company=plr.company,
		account_name="Processor Material Credit Liability",
	)
	accrued_processing = _get_processor_material_account(
		company=plr.company,
		account_name="Accrued Subcontracting Charges",
	)

	errors = []

	def require(condition: bool, message: str) -> None:
		if not condition:
			errors.append(message)

	require(
		doc.company == plr.company,
		_("Company must remain {0}.").format(frappe.bold(plr.company)),
	)
	require(
		doc.purpose == "Material Receipt",
		_("Purpose must remain Material Receipt."),
	)
	require(
		doc.stock_entry_type == "Material Receipt",
		_("Stock Entry Type must remain Material Receipt."),
	)
	require(
		bool(doc.set_posting_time),
		_("Edit Posting Date and Time must remain enabled."),
	)
	require(
		str(doc.posting_date) == str(scr.posting_date),
		_("Posting Date must remain the submitted SCR date {0}.").format(
			frappe.bold(scr.posting_date)
		),
	)
	require(
		_posting_times_match(doc.posting_time, scr.posting_time),
		_("Posting Time must remain the submitted SCR time {0}.").format(
			frappe.bold(scr.posting_time)
		),
	)
	require(
		len(doc.items) == 1,
		_("The Stock Entry must contain exactly one item row."),
	)

	if len(doc.items) == 1:
		item = doc.items[0]
		require(
			item.item_code == credit_source.processed_item,
			_("Item must remain {0}.").format(
				frappe.bold(credit_source.processed_item)
			),
		)
		require(
			not item.s_warehouse,
			_("Source Warehouse must remain blank."),
		)
		require(
			item.t_warehouse == valuation["target_warehouse"],
			_("Target Warehouse must remain {0}.").format(
				frappe.bold(valuation["target_warehouse"])
			),
		)
		require(
			flt(item.qty, precision) == credit_qty,
			_("Quantity must remain {0} {1}.").format(
				frappe.bold(credit_qty),
				frappe.bold(credit_source.stock_uom),
			),
		)
		require(
			item.expense_account == processor_liability,
			_("Difference Account must remain {0}.").format(
				frappe.bold(processor_liability)
			),
		)
		require(
			flt(item.basic_rate, 6)
			== flt(valuation["component_rate"], 6),
			_("Basic Rate must remain {0}.").format(
				frappe.bold(valuation["component_rate"])
			),
		)
		require(
			flt(item.basic_amount, 2)
			== flt(valuation["component_value"], 2),
			_("Basic Amount must remain {0}.").format(
				frappe.bold(valuation["component_value"])
			),
		)
		require(
			flt(item.additional_cost, 2)
			== flt(valuation["processing_value"], 2),
			_("Additional Cost must remain {0}.").format(
				frappe.bold(valuation["processing_value"])
			),
		)
		require(
			_valuation_rates_match(
				item.valuation_rate,
				valuation["finished_rate"],
			),
			_("Valuation Rate must remain {0}.").format(
				frappe.bold(valuation["finished_rate"])
			),
		)
		require(
			flt(item.amount, 2) == flt(valuation["total_value"], 2),
			_("Item Amount must remain {0}.").format(
				frappe.bold(valuation["total_value"])
			),
		)

	expected_cost_rows = 1 if valuation["processing_value"] > 0 else 0
	require(
		len(doc.additional_costs) == expected_cost_rows,
		_("Additional Costs must contain exactly {0} row(s).").format(
			frappe.bold(expected_cost_rows)
		),
	)
	if expected_cost_rows == 1 and len(doc.additional_costs) == 1:
		additional_cost = doc.additional_costs[0]
		require(
			additional_cost.expense_account == accrued_processing,
			_("Additional Cost account must remain {0}.").format(
				frappe.bold(accrued_processing)
			),
		)
		require(
			flt(additional_cost.amount, 2)
			== flt(valuation["processing_value"], 2),
			_("Additional Cost amount must remain {0}.").format(
				frappe.bold(valuation["processing_value"])
			),
		)

	require(
		flt(doc.total_incoming_value, 2)
		== flt(valuation["total_value"], 2),
		_("Total Incoming Value must remain {0}.").format(
			frappe.bold(valuation["total_value"])
		),
	)
	require(
		flt(doc.total_additional_costs, 2)
		== flt(valuation["processing_value"], 2),
		_("Total Additional Costs must remain {0}.").format(
			frappe.bold(valuation["processing_value"])
		),
	)
	require(
		flt(doc.total_amount, 2) == flt(valuation["total_value"], 2),
		_("Total Amount must remain {0}.").format(
			frappe.bold(valuation["total_value"])
		),
	)

	if errors:
		frappe.throw(
			_(
				"Stock Entry {0} is controlled by Processor Lot Receipt {1} "
				"and cannot be changed independently."
			).format(
				frappe.bold(doc.name),
				frappe.bold(plr.name),
			)
			+ "<ul>"
			+ "".join("<li>{0}</li>".format(error) for error in errors)
			+ "</ul>",
			title=_("Controlled Material Credit Stock Entry"),
		)


def unlink_material_credit_stock_entry(doc, method=None) -> None:
	"""Clear PLR/PMA ownership when a Draft is deleted or SE is cancelled."""
	for doctype in (
		"Processor Lot Receipt",
		"Processor Lot Receipt Item",
		"Processor Material Account Entry",
	):
		for name in frappe.get_all(
			doctype,
			filters={"material_credit_stock_entry": doc.name},
			pluck="name",
		):
			frappe.db.set_value(
				doctype,
				name,
				"material_credit_stock_entry",
				None,
				update_modified=False,
			)

# -------------------------------------------------------------------------
# Downstream document link maintenance
# -------------------------------------------------------------------------

def link_purchase_receipt(doc, method=None) -> None:
	"""
	Link a Draft or submitted Purchase Receipt to its originating PLR.

	ERPNext provides the relationship through:

		Purchase Receipt Item.subcontracting_receipt_item
			-> Subcontracting Receipt Item.parent
			-> Subcontracting Receipt.custom_processor_lot_receipt

	The PLR link is maintained automatically and is not entered by users.
	"""
	subcontracting_receipt_items = {
		row.subcontracting_receipt_item
		for row in doc.items
		if getattr(row, "subcontracting_receipt_item", None)
	}

	if not subcontracting_receipt_items:
		return

	subcontracting_receipts = frappe.get_all(
		"Subcontracting Receipt Item",
		filters={
			"name": ["in", list(subcontracting_receipt_items)],
		},
		fields=["parent"],
		pluck="parent",
	)

	for subcontracting_receipt in set(subcontracting_receipts):
		processor_lot_receipt = frappe.db.get_value(
			"Subcontracting Receipt",
			subcontracting_receipt,
			"custom_processor_lot_receipt",
		)

		if not processor_lot_receipt:
			continue

		_set_processor_lot_receipt_link(
			processor_lot_receipt=processor_lot_receipt,
			fieldname="purchase_receipt",
			linked_doctype="Purchase Receipt",
			linked_document=doc.name,
		)


def unlink_purchase_receipt(doc, method=None) -> None:
	"""
	Clear the PLR's Purchase Receipt link when this PR is deleted
	or cancelled.

	The link is cleared only where it currently points to this particular
	Purchase Receipt, so another amended document is not disturbed.
	"""
	_clear_processor_lot_receipt_link(
		fieldname="purchase_receipt",
		linked_document=doc.name,
	)


def _get_processor_lot_receipts_for_purchase_invoice(
	doc,
) -> list[str]:
	"""
	Return the Processor Lot Receipts represented by a Purchase Invoice.

	The relationship is resolved through Purchase Receipt references carried
	by the Purchase Invoice Item rows. Purchase Invoice returns and Processor
	Lot settlement Debit Notes are deliberately excluded from this commercial
	invoice-identity lifecycle.
	"""
	if doc.get("is_return"):
		return []

	purchase_receipts = {
		row.purchase_receipt
		for row in doc.items
		if getattr(row, "purchase_receipt", None)
	}

	if not purchase_receipts:
		return []

	processor_lot_receipts = frappe.get_all(
		"Processor Lot Receipt",
		filters={
			"purchase_receipt": ["in", list(purchase_receipts)],
		},
		pluck="name",
	)

	return sorted(set(processor_lot_receipts))


def validate_purchase_invoice_supplier_identity(
	doc,
	method=None,
) -> None:
	"""
	Require supplier invoice identity at its single authoritative entry point.

	For the Processor Lot workflow, bill_no and bill_date are entered only on
	the Purchase Invoice. They are copied back to read-only PLR fields after
	the Purchase Invoice is saved.
	"""
	processor_lot_receipts = (
		_get_processor_lot_receipts_for_purchase_invoice(doc)
	)

	if not processor_lot_receipts:
		return

	if len(processor_lot_receipts) != 1:
		frappe.throw(
			_(
				"Purchase Invoice {0} represents more than one "
				"Processor Lot Receipt: {1}. A controlled Purchase "
				"Invoice must represent one supplier invoice for one "
				"Processor Lot Receipt."
			).format(
				frappe.bold(doc.name or _("New Purchase Invoice")),
				frappe.bold(", ".join(processor_lot_receipts)),
			),
			title=_("Multiple Processor Lot Receipts"),
		)

	bill_no = (doc.get("bill_no") or "").strip()
	bill_date = doc.get("bill_date")

	missing_fields = []

	if not bill_no:
		missing_fields.append(_("Supplier Invoice Number"))

	if not bill_date:
		missing_fields.append(_("Supplier Invoice Date"))

	if missing_fields:
		frappe.throw(
			_(
				"{0} must be entered on Purchase Invoice {1} because "
				"it belongs to Processor Lot Receipt {2}."
			).format(
				frappe.bold(_(" and ").join(missing_fields)),
				frappe.bold(doc.name or _("New Purchase Invoice")),
				frappe.bold(processor_lot_receipts[0]),
			),
			title=_("Supplier Invoice Details Required"),
		)

	# Store a normalized number and prevent whitespace-only identities.
	doc.bill_no = bill_no


def _clear_purchase_invoice_from_processor_lot_receipt(
	processor_lot_receipt: str,
	purchase_invoice: str,
) -> None:
	"""
	Clear a PI and its copied invoice identity only when the PLR currently
	points to that same Purchase Invoice.
	"""
	current_purchase_invoice = frappe.db.get_value(
		"Processor Lot Receipt",
		processor_lot_receipt,
		"purchase_invoice",
	)

	if current_purchase_invoice != purchase_invoice:
		return

	frappe.db.set_value(
		"Processor Lot Receipt",
		processor_lot_receipt,
		{
			"purchase_invoice": None,
			"supplier_invoice_number": None,
			"supplier_invoice_date": None,
		},
		update_modified=False,
	)

	_refresh_processor_lots_for_receipt(processor_lot_receipt)


def link_purchase_invoice(doc, method=None) -> None:
	"""
	Link a Draft or submitted Purchase Invoice to its originating PLR.

	The Purchase Invoice remains authoritative for the supplier invoice number
	and date. Those values are copied to the PLR's read-only audit fields on
	every save.
	"""
	if doc.get("is_return"):
		return

	processor_lot_receipts = set(
		_get_processor_lot_receipts_for_purchase_invoice(doc)
	)

	if not processor_lot_receipts:
		return

	# Validation normally runs before this lifecycle event. Keep this explicit
	# safeguard because this function is also callable independently.
	validate_purchase_invoice_supplier_identity(doc)

	currently_linked_receipts = set(
		frappe.get_all(
			"Processor Lot Receipt",
			filters={
				"purchase_invoice": doc.name,
			},
			pluck="name",
		)
	)

	# If Purchase Receipt rows were removed from a Draft PI, do not leave a
	# stale PI identity on the formerly represented PLR.
	for processor_lot_receipt in (
		currently_linked_receipts - processor_lot_receipts
	):
		_clear_purchase_invoice_from_processor_lot_receipt(
			processor_lot_receipt=processor_lot_receipt,
			purchase_invoice=doc.name,
		)

	for processor_lot_receipt in processor_lot_receipts:
		_set_processor_lot_receipt_link(
			processor_lot_receipt=processor_lot_receipt,
			fieldname="purchase_invoice",
			linked_doctype="Purchase Invoice",
			linked_document=doc.name,
		)

		frappe.db.set_value(
			"Processor Lot Receipt",
			processor_lot_receipt,
			{
				"supplier_invoice_number": doc.bill_no,
				"supplier_invoice_date": doc.bill_date,
			},
			update_modified=False,
		)

		_refresh_processor_lots_for_receipt(processor_lot_receipt)


def unlink_purchase_invoice(doc, method=None) -> None:
	"""
	Clear the PLR's PI link and copied supplier invoice identity when the
	Purchase Invoice is cancelled or deleted.
	"""
	processor_lot_receipts = frappe.get_all(
		"Processor Lot Receipt",
		filters={
			"purchase_invoice": doc.name,
		},
		pluck="name",
	)

	for processor_lot_receipt in processor_lot_receipts:
		_clear_purchase_invoice_from_processor_lot_receipt(
			processor_lot_receipt=processor_lot_receipt,
			purchase_invoice=doc.name,
		)


def _set_processor_lot_receipt_link(
	processor_lot_receipt: str,
	fieldname: str,
	linked_doctype: str,
	linked_document: str,
) -> None:
	"""
	Set a system-maintained downstream-document link safely.

	An existing active document is not silently overwritten. A cancelled or
	deleted reference may be replaced by a newly submitted amended document.
	"""
	current_value = frappe.db.get_value(
		"Processor Lot Receipt",
		processor_lot_receipt,
		fieldname,
	)

	if current_value == linked_document:
		return

	if current_value:
		current_docstatus = frappe.db.get_value(
			linked_doctype,
			current_value,
			"docstatus",
		)

		if current_docstatus not in (None, 2):
			frappe.throw(
				_(
					"Processor Lot Receipt {0} is already linked to "
					"{1} {2}."
				).format(
					frappe.bold(processor_lot_receipt),
					linked_doctype,
					frappe.bold(current_value),
				)
			)

	frappe.db.set_value(
		"Processor Lot Receipt",
		processor_lot_receipt,
		fieldname,
		linked_document,
		update_modified=False,
	)
	_refresh_processor_lots_for_receipt(processor_lot_receipt)


def _clear_processor_lot_receipt_link(
	fieldname: str,
	linked_document: str,
) -> None:
	"""
	Clear a system-maintained PLR link only where it points to this document.
	"""
	processor_lot_receipts = frappe.get_all(
		"Processor Lot Receipt",
		filters={
			fieldname: linked_document,
		},
		fields=["name"],
		pluck="name",
	)

	for processor_lot_receipt in processor_lot_receipts:
		frappe.db.set_value(
			"Processor Lot Receipt",
			processor_lot_receipt,
			fieldname,
			None,
			update_modified=False,
		)
		_refresh_processor_lots_for_receipt(processor_lot_receipt)


def _refresh_processor_lots_for_receipt(
	processor_lot_receipt: str,
) -> None:
	"""Refresh all lots represented by one saved PLR."""
	processor_lots = set(
		frappe.get_all(
			"Processor Lot Receipt Allocation",
			filters={
				"parent": processor_lot_receipt,
				"parenttype": "Processor Lot Receipt",
				"parentfield": "lot_allocations",
			},
			pluck="processor_lot",
		)
	)
	header_lot = frappe.db.get_value(
		"Processor Lot Receipt",
		processor_lot_receipt,
		"processor_lot",
	)

	if header_lot:
		processor_lots.add(header_lot)

	for processor_lot in processor_lots:
		if processor_lot:
			refresh_processor_lot_receipt_summary(processor_lot)

@frappe.whitelist()
def get_processor_lot_receipt_context(
    processor_lot: str,
) -> dict:
    """
    Return controlled header and processed-item facts for a PLR.

    This allows the client form to display authoritative values before
    the first Save. Server-side validation remains authoritative.
    """
    if not processor_lot:
        return {}

    lot = frappe.get_doc(
        "Processor Lot",
        processor_lot,
    )

    if not lot.subcontracting_order:
        frappe.throw(
            _(
                "Processor Lot {0} has no Subcontracting Order."
            ).format(
                frappe.bold(processor_lot)
            )
        )

    sco = frappe.get_doc(
        "Subcontracting Order",
        lot.subcontracting_order,
    )

    processed_items = {}

    for row in sco.items:
        item_code = getattr(
            row,
            "item_code",
            None,
        )

        if not item_code:
            continue

        stock_uom = (
            getattr(row, "stock_uom", None)
            or frappe.db.get_value(
                "Item",
                item_code,
                "stock_uom",
            )
        )

        processed_items[item_code] = stock_uom

    if not processed_items:
        frappe.throw(
            _(
                "Subcontracting Order {0} has no processed-item row."
            ).format(
                frappe.bold(sco.name)
            )
        )

    if len(processed_items) > 1:
        frappe.throw(
            _(
                "Subcontracting Order {0} contains multiple processed "
                "items: {1}. Processor Lot Receipt currently supports "
                "one processed item per lot."
            ).format(
                frappe.bold(sco.name),
                ", ".join(
                    sorted(processed_items)
                ),
            )
        )

    processed_item, stock_uom = next(
        iter(processed_items.items())
    )

    return {
        "subcontracting_order":
            lot.subcontracting_order,
        "supplier":
            lot.supplier,
        "company":
            lot.company,
        "supplier_warehouse":
            lot.supplier_warehouse,
        "processed_item":
            processed_item,
        "stock_uom":
            stock_uom,
    }
