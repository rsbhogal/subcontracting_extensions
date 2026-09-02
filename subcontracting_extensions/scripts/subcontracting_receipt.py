# Copyright (c) 2026, R S Bhogal and contributors
# For license information, please see license.txt

"""
Processor Lot lifecycle integration for Subcontracting Receipt.

This module keeps the one-to-one relationship between a Processor Lot
Receipt and its Subcontracting Receipt complete and prevents duplicate
SCRs from being created for the same truck receipt.
"""

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime


V2_STRUCTURE = "V2 Itemized"
V2_QUANTITY_PRECISION = 6


def before_insert(doc, method=None):
	"""
	Carry Processor Lot references into an amended SCR.

	The custom reference fields are configured as No Copy, so an amended
	Subcontracting Receipt must recover them explicitly from the cancelled
	source document.
	"""
	if not doc.amended_from:
		return

	if (
		doc.custom_processor_lot
		and doc.custom_processor_lot_receipt
	):
		return

	references = frappe.db.get_value(
		"Subcontracting Receipt",
		doc.amended_from,
		[
			"custom_processor_lot",
			"custom_processor_lot_receipt",
		],
		as_dict=True,
	)

	if not references:
		return

	doc.custom_processor_lot = references.custom_processor_lot
	doc.custom_processor_lot_receipt = (
		references.custom_processor_lot_receipt
	)


def validate(doc, method=None):
	"""
	Prevent more than one active SCR for the same Processor Lot Receipt.
	"""
	if not doc.custom_processor_lot_receipt:
		return

	existing_scr = frappe.db.get_value(
		"Subcontracting Receipt",
		{
			"custom_processor_lot_receipt":
				doc.custom_processor_lot_receipt,
			"name": ["!=", doc.name],
			"docstatus": ["!=", 2],
		},
		"name",
	)

	if existing_scr:
		frappe.throw(
			_(
				"Processor Lot Receipt {0} is already linked to "
				"Subcontracting Receipt {1}."
			).format(
				frappe.bold(doc.custom_processor_lot_receipt),
				frappe.bold(existing_scr),
			),
			title=_("Duplicate Subcontracting Receipt"),
		)


def prevent_processor_first_checkpoint_submit(doc, method=None):
	"""Gate and validate submission of a processor-first V2 SCR."""
	if not doc.custom_processor_lot_receipt:
		return

	plr_values = frappe.db.get_value(
		"Processor Lot Receipt",
		doc.custom_processor_lot_receipt,
		[
			"receipt_structure_version",
			"processor_first_draft_only",
		],
		as_dict=True,
	)
	if not plr_values:
		return

	if not (
		plr_values.receipt_structure_version == V2_STRUCTURE
		and plr_values.processor_first_draft_only
	):
		return

	if not cint(frappe.conf.get("v2_processor_first_scr_submit")):
		frappe.throw(
			_(
				"Subcontracting Receipt submission is not enabled for "
				"processor-first V2 receipts on this site. Keep this "
				"document in Draft until the submission checkpoint is enabled."
			),
			title=_("V2 SCR Submission Disabled"),
		)

	if cint(
		frappe.db.get_single_value(
			"Buying Settings",
			"auto_create_purchase_receipt",
		)
	):
		frappe.throw(
			_(
				"Disable Auto Create Purchase Receipt in Buying Settings "
				"before submitting this processor-first V2 receipt. Purchase "
				"Receipt creation is not enabled at this checkpoint."
			),
			title=_("Automatic Purchase Receipt Must Be Disabled"),
		)

	plr = frappe.get_doc(
		"Processor Lot Receipt",
		doc.custom_processor_lot_receipt,
	)
	_align_after_referenced_material_transfers(doc)
	_validate_processor_first_v2_submit(doc, plr)


def _align_after_referenced_material_transfers(doc):
	"""Post one second after the latest submitted referenced Stock Entry."""
	stock_entry_names = sorted(
		{
			row.link_name
			for row in doc.get("doc_references") or []
			if row.link_doctype == "Stock Entry" and row.link_name
		}
	)
	if not stock_entry_names:
		return

	stock_entries = frappe.get_all(
		"Stock Entry",
		filters={
			"name": ["in", stock_entry_names],
			"docstatus": 1,
		},
		fields=["name", "posting_date", "posting_time"],
	)
	resolved_names = {row.name for row in stock_entries}
	invalid_names = sorted(set(stock_entry_names) - resolved_names)
	if invalid_names:
		frappe.throw(
			_("Referenced Stock Entries must exist and be submitted: {0}.").format(
				", ".join(frappe.bold(name) for name in invalid_names)
			),
			title=_("Invalid Material Transfer Reference"),
		)

	latest_transfer = max(
		get_datetime(f"{row.posting_date} {row.posting_time or '00:00:00'}")
		for row in stock_entries
	)
	scr_moment = get_datetime(
		f"{doc.posting_date} {doc.posting_time or '00:00:00'}"
	)
	if scr_moment > latest_transfer:
		return

	aligned_moment = latest_transfer + timedelta(seconds=1)
	doc.set_posting_time = 1
	doc.posting_date = aligned_moment.date()
	doc.posting_time = aligned_moment.time()


def _validate_processor_first_v2_submit(doc, plr):
	"""Require exact PLR, SCR Item and consumed-material correspondence."""
	if doc.get("is_return"):
		frappe.throw(
			_("Processor-first V2 checkpoint receipts cannot be returns."),
			title=_("V2 SCR Return Not Enabled"),
		)

	if plr.subcontracting_receipt != doc.name:
		frappe.throw(
			_("Processor Lot Receipt {0} is not linked to Subcontracting Receipt {1}.").format(
				frappe.bold(plr.name),
				frappe.bold(doc.name),
			),
			title=_("Subcontracting Receipt Link Mismatch"),
		)

	for fieldname, label in (
		("company", _("Company")),
		("supplier", _("Supplier")),
		("supplier_warehouse", _("Supplier Warehouse")),
	):
		if doc.get(fieldname) != plr.get(fieldname):
			frappe.throw(
				_("{0} does not match Processor Lot Receipt {1}.").format(
					label,
					frappe.bold(plr.name),
				),
				title=_("Processor Receipt Context Mismatch"),
			)

	allocations = list(plr.lot_allocations or [])
	if not allocations:
		frappe.throw(
			_("Processor Lot Receipt {0} has no backed allocations.").format(
				frappe.bold(plr.name)
			),
			title=_("Lot Allocations Required"),
		)

	scr_items = {
		row.name: row
		for row in doc.get("items") or []
	}
	linked_item_names = set()
	accepted_by_receipt_item = {}

	for allocation in allocations:
		item_name = allocation.subcontracting_receipt_item
		if not item_name or item_name not in scr_items:
			frappe.throw(
				_("Allocation row {0} has no valid saved SCR Item link.").format(
					allocation.idx
				),
				title=_("SCR Item Link Missing"),
			)
		if item_name in linked_item_names:
			frappe.throw(
				_("SCR Item {0} is linked to more than one allocation.").format(
					frappe.bold(item_name)
				),
				title=_("Duplicate SCR Item Allocation"),
			)
		linked_item_names.add(item_name)
		item = scr_items[item_name]

		expected = {
			"subcontracting_order": allocation.subcontracting_order,
			"subcontracting_order_item": allocation.subcontracting_order_item,
			"purchase_order": allocation.purchase_order,
			"purchase_order_item": allocation.purchase_order_item,
			"item_code": allocation.processed_item,
			"stock_uom": allocation.stock_uom,
		}
		for fieldname, value in expected.items():
			if item.get(fieldname) != value:
				frappe.throw(
					_("Allocation row {0} and SCR Item {1} differ in {2}.").format(
						allocation.idx,
						frappe.bold(item.name),
						frappe.bold(fieldname),
					),
					title=_("SCR Item Lineage Mismatch"),
				)

		if flt(item.qty, V2_QUANTITY_PRECISION) != flt(
			allocation.allocated_accepted_qty,
			V2_QUANTITY_PRECISION,
		):
			frappe.throw(
				_("Allocation row {0} quantity does not match SCR Item {1}.").format(
					allocation.idx,
					frappe.bold(item.name),
				),
				title=_("PLR and SCR Quantity Mismatch"),
			)

		po_item = frappe.db.get_value(
			"Purchase Order Item",
			allocation.purchase_order_item,
			["parent", "warehouse"],
			as_dict=True,
		)
		if (
			not po_item
			or po_item.parent != allocation.purchase_order
			or item.warehouse != po_item.warehouse
		):
			frappe.throw(
				_("SCR Item {0} target warehouse does not match its Purchase Order row.").format(
					frappe.bold(item.name)
				),
				title=_("SCR Target Warehouse Mismatch"),
			)

		key = allocation.receipt_item_key
		accepted_by_receipt_item[key] = flt(
			accepted_by_receipt_item.get(key, 0)
			+ flt(allocation.allocated_accepted_qty),
			V2_QUANTITY_PRECISION,
		)

	extra_items = sorted(set(scr_items) - linked_item_names)
	if extra_items:
		frappe.throw(
			_("SCR contains rows not backed by this PLR: {0}.").format(
				", ".join(frappe.bold(name) for name in extra_items)
			),
			title=_("Unbacked SCR Items"),
		)

	receipt_item_keys = set()
	for receipt_item in plr.receipt_items or []:
		receipt_item_keys.add(receipt_item.item_key)
		expected_backed_qty = flt(
			flt(receipt_item.company_accepted_qty)
			- flt(receipt_item.processor_material_credit_qty),
			V2_QUANTITY_PRECISION,
		)
		if accepted_by_receipt_item.get(receipt_item.item_key, 0) != expected_backed_qty:
			frappe.throw(
				_("Receipt Item {0} backed quantity does not match its SCR allocations.").format(
					frappe.bold(receipt_item.item_key)
				),
				title=_("Receipt Item Quantity Mismatch"),
			)

	if set(accepted_by_receipt_item) != receipt_item_keys:
		frappe.throw(
			_("PLR receipt items and allocation item keys do not match."),
			title=_("Receipt Item Allocation Mismatch"),
		)

	_material_rows = list(doc.get("supplied_items") or [])
	if not _material_rows:
		frappe.throw(
			_("Consumed Items are required before submitting this receipt."),
			title=_("Consumed Items Required"),
		)

	referenced_items = set()
	for material in _material_rows:
		if material.reference_name not in scr_items:
			frappe.throw(
				_("Consumed Item row {0} has no valid finished-item reference.").format(
					material.idx
				),
				title=_("Consumed Item Lineage Missing"),
			)
		referenced_items.add(material.reference_name)
		if flt(material.required_qty, V2_QUANTITY_PRECISION) <= 0:
			frappe.throw(
				_("Consumed Item row {0} must have a positive Required Qty.").format(
					material.idx
				),
				title=_("Invalid Consumed Item Quantity"),
			)
		if flt(material.consumed_qty, V2_QUANTITY_PRECISION) != flt(
			material.required_qty,
			V2_QUANTITY_PRECISION,
		):
			frappe.throw(
				_("Consumed Item row {0}: Consumed Qty must equal Required Qty at this checkpoint.").format(
					material.idx
				),
				title=_("Consumed Item Quantity Mismatch"),
			)

	missing_materials = sorted(set(scr_items) - referenced_items)
	if missing_materials:
		frappe.throw(
			_("SCR Items have no consumed-material rows: {0}.").format(
				", ".join(frappe.bold(name) for name in missing_materials)
			),
			title=_("Consumed Item Lineage Missing"),
		)


def after_insert(doc, method=None):
	"""
	Write the newly saved SCR reference back into its Processor Lot Receipt.
	"""
	if not doc.custom_processor_lot_receipt:
		return

	processor_lot_receipt = frappe.db.get_value(
		"Processor Lot Receipt",
		doc.custom_processor_lot_receipt,
		[
			"name",
			"processor_lot",
			"subcontracting_receipt",
		],
		as_dict=True,
	)

	if not processor_lot_receipt:
		frappe.throw(
			_(
				"Processor Lot Receipt {0} could not be found."
			).format(
				frappe.bold(doc.custom_processor_lot_receipt)
			)
		)

	if (
		doc.custom_processor_lot
		and processor_lot_receipt.processor_lot
		and doc.custom_processor_lot
		!= processor_lot_receipt.processor_lot
	):
		frappe.throw(
			_(
				"Processor Lot mismatch: SCR refers to {0}, while "
				"Processor Lot Receipt {1} belongs to {2}."
			).format(
				frappe.bold(doc.custom_processor_lot),
				frappe.bold(processor_lot_receipt.name),
				frappe.bold(
					processor_lot_receipt.processor_lot
				),
			)
		)

	linked_scr = processor_lot_receipt.subcontracting_receipt

	if linked_scr and linked_scr != doc.name:
		linked_docstatus = frappe.db.get_value(
			"Subcontracting Receipt",
			linked_scr,
			"docstatus",
		)

		if linked_docstatus != 2:
			frappe.throw(
				_(
					"Processor Lot Receipt {0} is already linked "
					"to Subcontracting Receipt {1}."
				).format(
					frappe.bold(processor_lot_receipt.name),
					frappe.bold(linked_scr),
				),
				title=_("Duplicate Subcontracting Receipt"),
			)

	frappe.db.set_value(
		"Processor Lot Receipt",
		processor_lot_receipt.name,
		"subcontracting_receipt",
		doc.name,
	)

	_link_v2_allocations_to_scr_items(
		doc,
		processor_lot_receipt.name,
	)


def _link_v2_allocations_to_scr_items(doc, processor_lot_receipt):
	"""Persist exact SCR Item links on V2 allocation rows."""
	structure_version = frappe.db.get_value(
		"Processor Lot Receipt",
		processor_lot_receipt,
		"receipt_structure_version",
	)
	if structure_version != "V2 Itemized":
		return

	allocations = frappe.get_all(
		"Processor Lot Receipt Allocation",
		filters={
			"parent": processor_lot_receipt,
			"parenttype": "Processor Lot Receipt",
			"parentfield": "lot_allocations",
		},
		fields=[
			"name",
			"idx",
			"subcontracting_order_item",
			"purchase_order_item",
		],
		order_by="idx asc",
	)
	scr_items_by_lineage = {}
	for item in doc.items:
		key = (
			item.subcontracting_order_item,
			item.purchase_order_item,
		)
		if key in scr_items_by_lineage:
			frappe.throw(
				_("SCR contains duplicate order-item lineage {0} / {1}.").format(
					frappe.bold(key[0]),
					frappe.bold(key[1]),
				),
				title=_("Ambiguous SCR Item Lineage"),
			)
		scr_items_by_lineage[key] = item

	for allocation in allocations:
		key = (
			allocation.subcontracting_order_item,
			allocation.purchase_order_item,
		)
		scr_item = scr_items_by_lineage.get(key)
		if not scr_item:
			frappe.throw(
				_("Allocation row {0} has no matching SCR Item.").format(
					allocation.idx
				),
				title=_("SCR Item Link Missing"),
			)

		frappe.db.set_value(
			"Processor Lot Receipt Allocation",
			allocation.name,
			"subcontracting_receipt_item",
			scr_item.name,
			update_modified=False,
		)


def on_trash(doc, method=None):
	"""
	Clear the PLR reference when its Draft SCR is deleted.

	Frappe does not permit deleting a submitted SCR directly, so this
	primarily handles abandoned Draft documents.
	"""
	if not doc.custom_processor_lot_receipt:
		return

	linked_scr = frappe.db.get_value(
		"Processor Lot Receipt",
		doc.custom_processor_lot_receipt,
		"subcontracting_receipt",
	)

	if linked_scr != doc.name:
		return

	frappe.db.set_value(
		"Processor Lot Receipt Allocation",
		{
			"parent": doc.custom_processor_lot_receipt,
			"parenttype": "Processor Lot Receipt",
			"parentfield": "lot_allocations",
		},
		"subcontracting_receipt_item",
		None,
		update_modified=False,
	)

	frappe.db.set_value(
		"Processor Lot Receipt",
		doc.custom_processor_lot_receipt,
		"subcontracting_receipt",
		None,
	)
