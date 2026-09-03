"""Processor-first V2 checkpoint controls for Purchase Receipt."""

import frappe
from frappe import _
from frappe.utils import cint, flt

from subcontracting_extensions.overrides.posting_date_flow import (
	set_downstream_posting_datetime,
)


PRECISION = 6


def _checkpoint_context(doc):
	if not doc.get("subcontracting_receipt"):
		return None
	scr = frappe.get_doc("Subcontracting Receipt", doc.subcontracting_receipt)
	if not scr.get("custom_processor_lot_receipt"):
		return None
	plr = frappe.get_doc(
		"Processor Lot Receipt",
		scr.custom_processor_lot_receipt,
	)
	if not (
		plr.receipt_structure_version == "V2 Itemized"
		and plr.processor_first_draft_only
	):
		return None
	return scr, plr


def validate_processor_first_draft_purchase_receipt(doc, method=None):
	"""Require an exact commercial-row image while the PR remains Draft."""
	context = _checkpoint_context(doc)
	if not context:
		return
	if not cint(frappe.conf.get("v2_processor_first_draft_pr")):
		frappe.throw(
			_("Draft Purchase Receipt creation is disabled for processor-first V2 receipts."),
			title=_("V2 Draft Purchase Receipt Disabled"),
		)
	scr, plr = context
	if scr.docstatus != 1:
		frappe.throw(_("The linked Subcontracting Receipt must be submitted."))
	if plr.subcontracting_receipt != scr.name:
		frappe.throw(_("The Processor Lot Receipt and Subcontracting Receipt links do not match."))
	if plr.purchase_receipt and plr.purchase_receipt != doc.name:
		status = frappe.db.get_value("Purchase Receipt", plr.purchase_receipt, "docstatus")
		if status != 2:
			frappe.throw(
				_("Processor Lot Receipt already links to Purchase Receipt {0}.").format(
					frappe.bold(plr.purchase_receipt)
				),
				title=_("Purchase Receipt Already Exists"),
			)

	scr_items = {row.name: row for row in scr.items}
	expected = {}
	for allocation in plr.lot_allocations or []:
		name = allocation.subcontracting_receipt_item
		if not name or name not in scr_items or name in expected:
			frappe.throw(_("PLR allocations do not identify unique saved SCR Items."))
		expected[name] = allocation

	actual = {}
	for row in doc.items or []:
		name = row.subcontracting_receipt_item
		if not name or name in actual:
			frappe.throw(_("Each Purchase Receipt row requires one unique SCR Item reference."))
		actual[name] = row

	if set(actual) != set(expected):
		frappe.throw(
			_("Purchase Receipt rows do not exactly match the PLR commercial allocations."),
			title=_("Purchase Receipt Commercial Lineage Mismatch"),
		)

	for name, allocation in expected.items():
		row = actual[name]
		scr_item = scr_items[name]
		if (
			row.purchase_order != allocation.purchase_order
			or row.purchase_order_item != allocation.purchase_order_item
			or row.warehouse != scr_item.warehouse
			or row.stock_uom != allocation.stock_uom
			or flt(row.stock_qty, PRECISION)
			!= flt(allocation.allocated_invoice_qty, PRECISION)
		):
			frappe.throw(
				_("Purchase Receipt row {0} does not match its allocation quantity, warehouse or lineage.").format(
					row.idx
				),
				title=_("Purchase Receipt Commercial Row Mismatch"),
			)


def prevent_processor_first_purchase_receipt_submit(doc, method=None):
	context = _checkpoint_context(doc)
	if not context:
		return
	if not cint(frappe.conf.get("v2_processor_first_pr_submit")):
		frappe.throw(
			_("Purchase Receipt submission is not enabled at the J7 Draft PR checkpoint."),
			title=_("Draft Purchase Receipt Checkpoint"),
		)

	# Revalidate the complete image at the last event before ERPNext submits it.
	# This protects against a Draft row being changed after it was first mapped.
	validate_processor_first_draft_purchase_receipt(doc)
	scr, _plr = context
	set_downstream_posting_datetime(doc, scr)
