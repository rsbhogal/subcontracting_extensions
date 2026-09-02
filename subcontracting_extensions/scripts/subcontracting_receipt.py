# Copyright (c) 2026, R S Bhogal and contributors
# For license information, please see license.txt

"""
Processor Lot lifecycle integration for Subcontracting Receipt.

This module keeps the one-to-one relationship between a Processor Lot
Receipt and its Subcontracting Receipt complete and prevents duplicate
SCRs from being created for the same truck receipt.
"""

import frappe
from frappe import _


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
	"""Keep a processor-first J5 SCR in Draft until submit support exists."""
	if not doc.custom_processor_lot_receipt:
		return

	plr = frappe.db.get_value(
		"Processor Lot Receipt",
		doc.custom_processor_lot_receipt,
		[
			"receipt_structure_version",
			"processor_first_draft_only",
		],
		as_dict=True,
	)
	if not plr:
		return

	if (
		plr.receipt_structure_version == "V2 Itemized"
		and plr.processor_first_draft_only
	):
		frappe.throw(
			_(
				"Subcontracting Receipt submission is not enabled for "
				"processor-first V2 checkpoint receipts. Keep this "
				"document in Draft."
			),
			title=_("Draft SCR Checkpoint"),
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
