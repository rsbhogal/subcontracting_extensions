# Copyright (c) 2026, R.S. Bhogal and contributors
# For license information, please see license.txt

"""Controlled quantity ledger for processor material credits."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from subcontracting_extensions.material_adjustment_identity import (
	resolve_sco_component,
)


DIRECTION_BY_ENTRY_TYPE = {
	"Advance Credit": "Credit",
	"Credit Applied": "Debit",
	"Commercial Settlement": "Debit",
}

V2_RECEIPT_STRUCTURE = "V2 Itemized"
ITEM_QUANTITY_PRECISION = 6


def _get_v2_receipt_item(plr, receipt_item_key: str):
	"""Return the exact V2 receipt item identified by its stable key."""
	if plr.receipt_structure_version != V2_RECEIPT_STRUCTURE:
		frappe.throw(
			_("Receipt Item Key is valid only for a V2 Itemized receipt.")
		)
	if not receipt_item_key:
		frappe.throw(_("Receipt Item Key is required for a V2 material credit."))

	matches = [
		row
		for row in plr.get("receipt_items", [])
		if row.item_key == receipt_item_key
	]
	if len(matches) != 1:
		frappe.throw(
			_("Processor Lot Receipt {0} must contain exactly one Receipt Item {1}.").format(
				frappe.bold(plr.name),
				frappe.bold(receipt_item_key),
			)
		)
	return matches[0]


def _get_v2_credit_anchor(plr, receipt_item_key: str):
	"""Return the last positive allocation which backs one V2 receipt item."""
	_get_v2_receipt_item(plr, receipt_item_key)
	allocations = [
		row
		for row in plr.get("lot_allocations", [])
		if row.receipt_item_key == receipt_item_key
		and flt(row.allocated_accepted_qty, ITEM_QUANTITY_PRECISION) > 0
	]
	if not allocations:
		frappe.throw(
			_("Receipt Item {0} requires a positive Lot Allocation before its material credit can be recorded.").format(
				frappe.bold(receipt_item_key)
			)
		)
	return allocations[-1]


def _get_material_credit_status(
	source_qty: float,
	applied_qty: float,
) -> str:
	"""Return the source PLR status for the effective applied quantity."""
	if flt(applied_qty) <= 0:
		return "Recorded"
	if flt(applied_qty) >= flt(source_qty):
		return "Fully Applied"
	return "Partly Applied"


def credit_application_cancel_is_allowed(
	lot_docstatus: int | None,
	settlement_status: str | None,
	controlled_reversal: bool,
) -> bool:
	"""Return whether a Credit Applied PMA may pass its lot guard.

	Manual cancellation remains blocked while its Processor Lot is submitted.
	Only the server-controlled reversal transaction may cross that guard, and
	only after it has marked the settlement Reversal In Progress.
	"""
	if lot_docstatus != 1:
		return True
	return bool(
		controlled_reversal
		and settlement_status == "Reversal In Progress"
	)


ACCOUNT_IDENTITY_FIELDS = (
	"company",
	"supplier",
	"supplier_warehouse",
	"processed_item",
	"processed_item_uom",
	"principal_component",
	"account_uom",
)


class ProcessorMaterialAccountEntry(Document):
	"""Record one auditable processor material-account movement."""

	def before_validate(self) -> None:
		self._set_source_identity()
		self._set_account_direction()

	def validate(self) -> None:
		self._validate_quantity()
		self._validate_commercial_quantity()
		self._validate_source_rules()
		self._validate_lineage()
		self._validate_plr_excess_source()
		self._validate_duplicate_source()
		self._validate_against_entry()
		self._validate_target_application_capacity()
		self._validate_system_fields()

	def before_submit(self) -> None:
		"""Require submitted controlled documents before applying credit."""
		if self.entry_type != "Credit Applied":
			return

		from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.settlement_application_engine import (
			validate_credit_application_documents,
		)

		validate_credit_application_documents(self)

	def on_submit(self) -> None:
		"""Mark the source PLR credit as recorded after ledger submission."""
		if self._is_plr_excess_credit():
			self._set_plr_material_credit_status("Recorded")
		elif self.entry_type == "Credit Applied":
			self._refresh_source_credit_status()

	def before_cancel(self) -> None:
		"""Do not cancel a credit that has submitted applications."""
		if self.entry_type == "Credit Applied":
			lot_state = frappe.db.get_value(
				"Processor Lot",
				self.processor_lot,
				["docstatus", "settlement_status"],
				as_dict=True,
			)
			controlled_reversal = bool(
				self.flags.get(
					"allow_processor_settlement_reversal"
				)
			)
			if not credit_application_cancel_is_allowed(
				lot_docstatus=(
					lot_state.docstatus if lot_state else None
				),
				settlement_status=(
					lot_state.settlement_status
					if lot_state
					else None
				),
				controlled_reversal=controlled_reversal,
			):
				frappe.throw(
					_(
						"Cancel Processor Lot {0} before cancelling "
						"credit application {1}."
					).format(
						frappe.bold(self.processor_lot),
						frappe.bold(self.name),
					)
				)
			return

		if not self._is_plr_excess_credit():
			return

		self._validate_source_documents_before_cancel()

		applied_entry = frappe.db.get_value(
			"Processor Material Account Entry",
			{
				"against_entry": self.name,
				"account_direction": "Debit",
				"docstatus": 1,
			},
			"name",
		)
		if applied_entry:
			frappe.throw(
				_(
					"Processor Material Account Entry {0} cannot be "
					"cancelled because submitted application or settlement "
					"entry {1} exists against it."
				).format(
					frappe.bold(self.name),
					frappe.utils.get_link_to_form(
						"Processor Material Account Entry",
						applied_entry,
					),
				),
				title=_("Processor Credit Has Been Applied"),
			)

	def _validate_source_documents_before_cancel(self) -> None:
		"""Require downstream stock documents to be reversed first."""
		plr = frappe.get_doc(
			"Processor Lot Receipt",
			self.processor_lot_receipt,
		)
		active_documents = []

		receipt_item_key = getattr(self, "receipt_item_key", None)
		item = (
			_get_v2_receipt_item(plr, receipt_item_key)
			if receipt_item_key
			else None
		)
		stock_entry = (
			self.material_credit_stock_entry
			or (
				item.material_credit_stock_entry
				if item
				else getattr(plr, "material_credit_stock_entry", None)
			)
		)
		if stock_entry:
			stock_entry_status = frappe.db.get_value(
				"Stock Entry",
				stock_entry,
				"docstatus",
			)
			if stock_entry_status == 0:
				active_documents.append(
					_("delete Draft Stock Entry {0}").format(
						frappe.utils.get_link_to_form(
							"Stock Entry",
							stock_entry,
						)
					)
				)
			elif stock_entry_status == 1:
				active_documents.append(
					_("cancel Stock Entry {0}").format(
						frappe.utils.get_link_to_form(
							"Stock Entry",
							stock_entry,
						)
					)
				)

		scr = plr.subcontracting_receipt
		if scr:
			scr_status = frappe.db.get_value(
				"Subcontracting Receipt",
				scr,
				"docstatus",
			)
			if scr_status == 0:
				active_documents.append(
					_("delete Draft Subcontracting Receipt {0}").format(
						frappe.utils.get_link_to_form(
							"Subcontracting Receipt",
							scr,
						)
					)
				)
			elif scr_status == 1:
				active_documents.append(
					_("cancel Subcontracting Receipt {0}").format(
						frappe.utils.get_link_to_form(
							"Subcontracting Receipt",
							scr,
						)
					)
				)

		if active_documents:
			frappe.throw(
				_(
					"Processor Material Account Entry {0} cannot be cancelled. "
					"First {1}."
				).format(
					frappe.bold(self.name),
					_(" and ").join(active_documents),
				),
				title=_("Reverse Processor Stock Documents First"),
			)

	def on_cancel(self) -> None:
		"""Return the source PLR to Proposed after credit cancellation."""
		if self._is_plr_excess_credit():
			self._set_plr_material_credit_status("Proposed")
		elif self.entry_type == "Credit Applied":
			self._refresh_source_credit_status()

	def _is_plr_excess_credit(self) -> bool:
		"""Return whether this entry records an Advance Credit from a PLR."""
		return bool(
			self.entry_type == "Advance Credit"
			and self.source_event == "PLR Excess"
			and self.processor_lot_receipt
		)

	def _set_plr_material_credit_status(self, status: str) -> None:
		"""Maintain the source PLR's material-credit lifecycle status."""
		if not frappe.db.exists(
			"Processor Lot Receipt",
			self.processor_lot_receipt,
		):
			frappe.throw(
				_("Processor Lot Receipt {0} does not exist.").format(
					frappe.bold(self.processor_lot_receipt)
				)
			)

		if self.receipt_item_key:
			item_name = frappe.db.get_value(
				"Processor Lot Receipt Item",
				{
					"parent": self.processor_lot_receipt,
					"parenttype": "Processor Lot Receipt",
					"item_key": self.receipt_item_key,
				},
				"name",
			)
			if not item_name:
				frappe.throw(
					_("Receipt Item {0} does not exist on Processor Lot Receipt {1}.").format(
						frappe.bold(self.receipt_item_key),
						frappe.bold(self.processor_lot_receipt),
					)
				)
			frappe.db.set_value(
				"Processor Lot Receipt Item",
				item_name,
				"material_credit_status",
				status,
				update_modified=False,
			)
			return

		frappe.db.set_value(
			"Processor Lot Receipt",
			self.processor_lot_receipt,
			"material_credit_status",
			status,
			update_modified=False,
		)

	def _set_source_identity(self) -> None:
		"""Derive account identity from PLR, Processor Lot and SCO."""
		finished_row_hint = None
		if self.processor_lot_receipt:
			plr = frappe.get_doc(
				"Processor Lot Receipt",
				self.processor_lot_receipt,
			)
			if plr.docstatus == 2:
				frappe.throw(
					_("Processor Lot Receipt {0} is cancelled.").format(
						frappe.bold(plr.name)
					)
				)
			if plr.receipt_structure_version == V2_RECEIPT_STRUCTURE:
				anchor = _get_v2_credit_anchor(plr, self.receipt_item_key)
				self.processor_lot = anchor.processor_lot
				self.subcontracting_order = anchor.subcontracting_order
				finished_row_hint = anchor.subcontracting_order_item
			else:
				if self.receipt_item_key:
					frappe.throw(
						_("Legacy Processor Lot Receipts cannot use a Receipt Item Key.")
					)
				self.processor_lot = plr.processor_lot
				self.subcontracting_order = plr.subcontracting_order

		if self.processor_lot:
			lot = frappe.get_doc("Processor Lot", self.processor_lot)
			if lot.docstatus == 2:
				frappe.throw(
					_("Processor Lot {0} is cancelled.").format(
						frappe.bold(lot.name)
					)
				)
			self.subcontracting_order = lot.subcontracting_order

		if self.entry_type == "Credit Applied" and self.against_entry:
			source = frappe.get_doc(
				"Processor Material Account Entry", self.against_entry
			)
			self.processed_item = source.processed_item
			self.processed_item_uom = source.processed_item_uom
			self.principal_component = source.principal_component
			self.account_uom = source.account_uom

		if self.subcontracting_order:
			self._set_identity_from_sco(finished_row_hint)

	def _set_identity_from_sco(self, finished_row_hint=None) -> None:
		"""Derive account identity and its exact document-local SCO row."""
		sco = frappe.get_doc(
			"Subcontracting Order",
			self.subcontracting_order,
		)
		if sco.docstatus != 1:
			frappe.throw(
				_("Subcontracting Order {0} must be submitted.").format(
					frappe.bold(sco.name)
				)
			)

		self.company = sco.company
		self.supplier = sco.supplier
		self.supplier_warehouse = sco.supplier_warehouse

		try:
			component = resolve_sco_component(
				sco.items,
				sco.supplied_items,
				explicit=self.sco_supplied_item or None,
				finished_row=finished_row_hint,
				processed_item=self.processed_item or None,
				processed_uom=self.processed_item_uom or None,
				component_item=self.principal_component or None,
				account_uom=self.account_uom or None,
			)
		except ValueError as error:
			frappe.throw(
				_("Cannot attribute Processor Material Account entry to one SCO component: {0}.").format(
					str(error)
				)
			)
		finished = next(row for row in sco.items if row.name == component.reference_name)
		self.sco_supplied_item = component.name
		self.processed_item = finished.item_code
		self.processed_item_uom = finished.stock_uom or frappe.db.get_value(
			"Item", finished.item_code, "stock_uom"
		)
		self.principal_component = component.rm_item_code
		self.account_uom = component.stock_uom or frappe.db.get_value(
			"Item", component.rm_item_code, "stock_uom"
		)

	def _set_account_direction(self) -> None:
		"""Derive direction; it is never selected by the user."""
		if self.entry_type != "Reversal":
			self.account_direction = DIRECTION_BY_ENTRY_TYPE.get(
				self.entry_type
			)
			return

		if not self.reversal_of:
			self.account_direction = None
			return

		source_direction = frappe.db.get_value(
			"Processor Material Account Entry",
			self.reversal_of,
			"account_direction",
		)
		self.account_direction = {
			"Credit": "Debit",
			"Debit": "Credit",
		}.get(source_direction)

	def _validate_quantity(self) -> None:
		"""Keep the proof-of-concept restricted to same-UOM, 1:1 routes."""
		processed_qty = flt(
			self.processed_qty,
			self.precision("processed_qty"),
		)
		account_qty = flt(
			self.account_qty,
			self.precision("account_qty"),
		)
		if processed_qty <= 0 or account_qty <= 0:
			frappe.throw(
				_("Processed Qty and Account Qty must both be greater than zero.")
			)
		if self.processed_item_uom != self.account_uom:
			frappe.throw(
				_(
					"Processor Material Account pilot currently supports "
					"only identical Processed Item UOM and Account UOM."
				)
			)
		if processed_qty != account_qty:
			frappe.throw(
				_(
					"Processor Material Account pilot currently requires "
					"Processed Qty and Account Qty to be equal."
				)
			)

	def _validate_commercial_quantity(self) -> None:
		"""Keep the commercial leg explicit and within processed quantity."""
		commercial_qty = flt(
			self.commercial_qty,
			self.precision("commercial_qty"),
		)
		processed_qty = flt(
			self.processed_qty,
			self.precision("processed_qty"),
		)
		if commercial_qty < 0:
			frappe.throw(_("Commercially Matched Qty cannot be negative."))
		if commercial_qty > processed_qty:
			frappe.throw(
				_(
					"Commercially Matched Qty {0} cannot exceed Processed Qty {1}."
				).format(
					frappe.bold(commercial_qty),
					frappe.bold(processed_qty),
				)
			)

	def _validate_source_rules(self) -> None:
		if not self.account_direction:
			frappe.throw(
				_("Account Direction could not be derived from Entry Type.")
			)

		if self.entry_type == "Advance Credit":
			if self.source_event not in ("PLR Excess", "Opening Balance"):
				frappe.throw(
					_(
						"Advance Credit requires Source Event PLR Excess "
						"or Opening Balance."
					)
				)
			if (
				self.source_event == "PLR Excess"
				and not self.processor_lot_receipt
			):
				frappe.throw(
					_("PLR Excess requires a Processor Lot Receipt.")
				)

		elif self.entry_type == "Credit Applied":
			if self.source_event != "Processor Lot Shortage":
				frappe.throw(
					_(
						"Credit Applied requires Source Event "
						"Processor Lot Shortage."
					)
				)
			if not self.processor_lot or not self.against_entry:
				frappe.throw(
					_(
						"Credit Applied requires a Processor Lot "
						"and an Against Entry."
					)
				)

		elif self.entry_type == "Commercial Settlement":
			if (
				self.source_event != "Commercial Settlement"
				or not self.against_entry
			):
				frappe.throw(
					_(
						"Commercial Settlement requires the matching "
						"Source Event and an Against Entry."
					)
				)

		elif self.entry_type == "Reversal":
			if self.source_event != "Reversal" or not self.reversal_of:
				frappe.throw(
					_(
						"Reversal requires Source Event Reversal "
						"and a Reversal Of entry."
					)
				)

	def _validate_lineage(self) -> None:
		if self.processor_lot_receipt:
			plr = frappe.get_doc(
				"Processor Lot Receipt",
				self.processor_lot_receipt,
			)
			if plr.receipt_structure_version == V2_RECEIPT_STRUCTURE:
				anchor = _get_v2_credit_anchor(plr, self.receipt_item_key)
				if (
					anchor.processor_lot != self.processor_lot
					or anchor.subcontracting_order != self.subcontracting_order
				):
					frappe.throw(
						_("V2 Receipt Item allocation lineage is inconsistent.")
					)
			else:
				if (
					plr.processor_lot != self.processor_lot
					or plr.subcontracting_order != self.subcontracting_order
				):
					frappe.throw(
						_("Processor Lot Receipt lineage is inconsistent.")
					)

		if self.processor_lot:
			lot_sco = frappe.db.get_value(
				"Processor Lot",
				self.processor_lot,
				"subcontracting_order",
			)
			if lot_sco != self.subcontracting_order:
				frappe.throw(
					_("Processor Lot and Subcontracting Order do not match.")
				)

	def _validate_duplicate_source(self) -> None:
		"""Allow only one active PLR-excess credit for a source PLR."""
		if (
			self.entry_type != "Advance Credit"
			or self.source_event != "PLR Excess"
			or not self.processor_lot_receipt
		):
			return

		filters = {
			"entry_type": "Advance Credit",
			"source_event": "PLR Excess",
			"processor_lot_receipt": self.processor_lot_receipt,
			"docstatus": ["!=", 2],
		}
		if not self.is_new():
			filters["name"] = ["!=", self.name]

		existing_rows = frappe.get_all(
			"Processor Material Account Entry",
			filters,
			["name", "receipt_item_key"],
		)
		existing = next(
			(
				row.name
				for row in existing_rows
				if (row.receipt_item_key or "") == (self.receipt_item_key or "")
			),
			None,
		)
		if existing:
			frappe.throw(
				_(
					"Processor Material Account Entry {0} already records "
					"the excess from Processor Lot Receipt {1}{2}."
				).format(
					frappe.bold(existing),
					frappe.bold(self.processor_lot_receipt),
					(
						_(" Receipt Item {0}").format(
							frappe.bold(self.receipt_item_key)
						)
						if self.receipt_item_key
						else ""
					),
				)
			)

	def _validate_plr_excess_source(self) -> None:
		"""Prove that a PLR-excess credit exactly matches its source facts."""
		if (
			self.entry_type != "Advance Credit"
			or self.source_event != "PLR Excess"
		):
			return

		plr = frappe.get_doc(
			"Processor Lot Receipt",
			self.processor_lot_receipt,
		)

		if plr.docstatus == 2:
			frappe.throw(
				_("Processor Lot Receipt {0} is cancelled.").format(
					frappe.bold(plr.name)
				)
			)

		item = (
			_get_v2_receipt_item(plr, self.receipt_item_key)
			if plr.receipt_structure_version == V2_RECEIPT_STRUCTURE
			else plr
		)
		if not item.allow_processor_material_credit:
			frappe.throw(
				_(
					"Processor Lot Receipt {0} has not authorised a "
					"Processor Material Credit."
				).format(frappe.bold(plr.name))
			)

		if item.material_credit_status != "Proposed":
			frappe.throw(
				_(
					"Processor Lot Receipt {0} must have Material Credit "
					"Status Proposed; its current status is {1}."
				).format(
					frappe.bold(plr.name),
					frappe.bold(item.material_credit_status or _("Not Set")),
				)
			)

		credit_qty = flt(
			item.processor_material_credit_qty,
			self.precision("account_qty"),
		)
		processed_qty = flt(
			self.processed_qty,
			self.precision("processed_qty"),
		)
		account_qty = flt(
			self.account_qty,
			self.precision("account_qty"),
		)
		expected_commercial_qty = flt(
			item.material_credit_invoice_qty,
			self.precision("commercial_qty"),
		)
		if self.is_new():
			self.commercial_qty = expected_commercial_qty
		elif flt(self.commercial_qty) != expected_commercial_qty:
			frappe.throw(
				_(
					"Commercially Matched Qty must equal the source Processor "
					"Lot Receipt credit invoice quantity of {0} {1}."
				).format(
					frappe.bold(expected_commercial_qty),
					frappe.bold(item.stock_uom),
				)
			)

		if credit_qty <= 0:
			frappe.throw(
				_(
					"Processor Lot Receipt {0} has no positive Processor "
					"Material Credit Qty."
				).format(frappe.bold(plr.name))
			)

		if processed_qty != credit_qty or account_qty != credit_qty:
			frappe.throw(
				_(
					"Processed Qty and Account Qty must both equal the "
					"source Processor Lot Receipt credit of {0} {1}."
				).format(
					frappe.bold(credit_qty),
					frappe.bold(item.stock_uom),
				),
				title=_("PLR Credit Quantity Mismatch"),
			)

		if (
			self.processed_item != item.processed_item
			or self.processed_item_uom != item.stock_uom
		):
			frappe.throw(
				_(
					"Processor Material Account processed item and UOM do "
					"not match Processor Lot Receipt {0}."
				).format(frappe.bold(plr.name)),
				title=_("PLR Credit Identity Mismatch"),
			)

	def _validate_against_entry(self) -> None:
		"""Prevent cross-account use and over-application of credit."""
		if not self.against_entry:
			return
		if self.against_entry == self.name:
			frappe.throw(_("An entry cannot be applied against itself."))

		source = frappe.get_doc(
			"Processor Material Account Entry",
			self.against_entry,
		)
		if source.docstatus != 1 or source.account_direction != "Credit":
			frappe.throw(
				_(
					"Against Entry {0} must be a submitted Credit entry."
				).format(frappe.bold(source.name))
			)

		for fieldname in ACCOUNT_IDENTITY_FIELDS:
			if self.get(fieldname) != source.get(fieldname):
				frappe.throw(
					_(
						"Against Entry {0} belongs to a different "
						"processor material account ({1} differs)."
					).format(
						frappe.bold(source.name),
						self.meta.get_label(fieldname),
					)
				)

		previous_debits = frappe.get_all(
			"Processor Material Account Entry",
			filters={
				"against_entry": source.name,
				"account_direction": "Debit",
				"docstatus": 1,
				"name": ["!=", self.name or ""],
			},
			fields=["account_qty", "commercial_qty"],
		)
		previously_applied = sum(
			flt(row.account_qty) for row in previous_debits
		)
		available_qty = flt(
			flt(source.account_qty) - previously_applied,
			self.precision("account_qty"),
		)
		if flt(self.account_qty) > available_qty:
			frappe.throw(
				_(
					"Account Qty {0} exceeds available credit {1} "
					"on Against Entry {2}."
				).format(
					frappe.bold(self.account_qty),
					frappe.bold(available_qty),
					frappe.bold(source.name),
				)
			)

		if self.entry_type == "Credit Applied":
			source_unbilled_qty = max(
				flt(source.processed_qty) - flt(source.commercial_qty),
				0.0,
			)
			previously_matched_qty = sum(
				flt(row.commercial_qty) for row in previous_debits
			)
			available_unbilled_qty = max(
				source_unbilled_qty - previously_matched_qty,
				0.0,
			)
			if flt(self.commercial_qty) > available_unbilled_qty:
				frappe.throw(
					_(
						"Commercially Matched Qty {0} exceeds the unbilled "
						"credit quantity {1} available on Against Entry {2}."
					).format(
						frappe.bold(self.commercial_qty),
						frappe.bold(available_unbilled_qty),
						frappe.bold(source.name),
					)
				)

	def _validate_target_application_capacity(self) -> None:
		"""Prevent applications beyond the target lot's factual variances."""
		if self.entry_type != "Credit Applied":
			return

		from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.fact_engine import (
			get_sco_facts,
		)

		facts = get_sco_facts(self.subcontracting_order)
		physical_shortage_qty = max(
			flt(
				(facts.get("summary") or {})
				.get("physical_inventory", {})
				.get("outstanding_qty")
			),
			0.0,
		)
		commercial_variance_qty = max(
			flt(
				(facts.get("processor_lot_receipts") or {}).get(
					"total_supplier_vs_company_qty"
				)
			),
			0.0,
		)

		filters = {
			"entry_type": "Credit Applied",
			"processor_lot": self.processor_lot,
			"docstatus": ["!=", 2],
			"name": ["!=", self.name or ""],
		}
		previous = frappe.get_all(
			"Processor Material Account Entry",
			filters=filters,
			fields=["account_qty", "commercial_qty"],
		)
		remaining_physical_qty = max(
			physical_shortage_qty
			- sum(flt(row.account_qty) for row in previous),
			0.0,
		)
		remaining_commercial_qty = max(
			commercial_variance_qty
			- sum(flt(row.commercial_qty) for row in previous),
			0.0,
		)

		if flt(self.account_qty) > remaining_physical_qty:
			frappe.throw(
				_(
					"Account Qty {0} exceeds target Processor Lot shortage {1}."
				).format(
					frappe.bold(self.account_qty),
					frappe.bold(remaining_physical_qty),
				)
			)
		if flt(self.commercial_qty) > remaining_commercial_qty:
			frappe.throw(
				_(
					"Commercially Matched Qty {0} exceeds target commercial "
					"variance {1}."
				).format(
					frappe.bold(self.commercial_qty),
					frappe.bold(remaining_commercial_qty),
				)
			)

	def _refresh_source_credit_status(self) -> None:
		"""Update the source PLR after application submission or cancellation."""
		if not self.against_entry:
			return
		source = frappe.get_doc(
			"Processor Material Account Entry",
			self.against_entry,
		)
		if not source.processor_lot_receipt:
			return

		applied_qty = sum(
			flt(row.account_qty)
			for row in frappe.get_all(
				"Processor Material Account Entry",
				filters={
					"against_entry": source.name,
					"account_direction": "Debit",
					"docstatus": 1,
					"is_reversed": 0,
				},
				fields=["account_qty"],
			)
		)
		status = _get_material_credit_status(
			flt(source.account_qty),
			applied_qty,
		)
		source._set_plr_material_credit_status(status)

	def _validate_system_fields(self) -> None:
		previous_doc = self.get_doc_before_save()
		if (
			previous_doc
			and (self.sco_supplied_item or "")
			!= (previous_doc.sco_supplied_item or "")
		):
			frappe.throw(_("SCO Supplied Item is maintained by the system."))
		if (
			previous_doc
			and (self.receipt_item_key or "")
			!= (previous_doc.receipt_item_key or "")
		):
			frappe.throw(_("Receipt Item Key is maintained by the system."))
		if previous_doc and self.is_reversed != previous_doc.is_reversed:
			frappe.throw(_("Is Reversed is maintained by the system."))
		if (
			previous_doc
			and self.material_credit_stock_entry
			!= previous_doc.material_credit_stock_entry
		):
			frappe.throw(
				_("Material Credit Stock Entry is maintained by the system.")
			)
		for fieldname in (
			"application_stock_entry",
			"application_journal_entry",
		):
			if (
				previous_doc
				and self.get(fieldname) != previous_doc.get(fieldname)
			):
				frappe.throw(
					_("{0} is maintained by the system.").format(
						self.meta.get_label(fieldname)
					)
				)


@frappe.whitelist()
def get_processor_material_account_balance(
	company: str,
	supplier: str,
	supplier_warehouse: str,
	processed_item: str,
	processed_item_uom: str,
	principal_component: str,
	account_uom: str,
) -> float:
	"""Return submitted credits less submitted debits for one exact account."""
	values = {
		"company": company,
		"supplier": supplier,
		"supplier_warehouse": supplier_warehouse,
		"processed_item": processed_item,
		"processed_item_uom": processed_item_uom,
		"principal_component": principal_component,
		"account_uom": account_uom,
	}
	for fieldname, value in values.items():
		if not value:
			frappe.throw(
				_("{0} is required.").format(
					_(fieldname.replace("_", " ").title())
				)
			)

	rows = frappe.get_all(
		"Processor Material Account Entry",
		filters={**values, "docstatus": 1},
		fields=["account_direction", "account_qty"],
	)
	balance = sum(
		flt(row.account_qty)
		if row.account_direction == "Credit"
		else -flt(row.account_qty)
		if row.account_direction == "Debit"
		else 0
		for row in rows
	)
	return flt(balance, 3)
