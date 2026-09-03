"""Processor-first V2 checkpoint controls for Purchase Invoice."""

import frappe
from frappe import _
from frappe.utils import cint, flt


PRECISION = 6


def _checkpoint_context(doc, purchase_receipt=None):
	if doc.get("is_return"):
		return None

	purchase_receipts = {
		row.purchase_receipt
		for row in (doc.items or [])
		if row.get("purchase_receipt")
	}
	contexts = {}
	for pr_name in purchase_receipts:
		pr = (
			purchase_receipt
			if purchase_receipt and purchase_receipt.name == pr_name
			else frappe.get_doc("Purchase Receipt", pr_name)
		)
		if not pr.get("subcontracting_receipt"):
			continue
		scr = frappe.get_doc("Subcontracting Receipt", pr.subcontracting_receipt)
		if not scr.get("custom_processor_lot_receipt"):
			continue
		plr = frappe.get_doc(
			"Processor Lot Receipt", scr.custom_processor_lot_receipt
		)
		if (
			plr.receipt_structure_version == "V2 Itemized"
			and plr.processor_first_draft_only
		):
			contexts[plr.name] = (pr, scr, plr)

	# Once a Draft PI is saved, its PLR back-link remains authoritative even
	# if every controlled item row is removed or replaced before the next save.
	if doc.get("name"):
		linked_plr_name = frappe.db.get_value(
			"Processor Lot Receipt",
			{"purchase_invoice": doc.name},
			"name",
		)
		if linked_plr_name and linked_plr_name not in contexts:
			plr = frappe.get_doc("Processor Lot Receipt", linked_plr_name)
			if (
				plr.receipt_structure_version == "V2 Itemized"
				and plr.processor_first_draft_only
				and plr.purchase_receipt
			):
				pr = frappe.get_doc("Purchase Receipt", plr.purchase_receipt)
				scr = frappe.get_doc(
					"Subcontracting Receipt", pr.subcontracting_receipt
				)
				contexts[plr.name] = (pr, scr, plr)

	if not contexts:
		return None
	context = next(iter(contexts.values())) if len(contexts) == 1 else None
	if (
		len(purchase_receipts) != 1
		or context is None
		or next(iter(purchase_receipts)) != context[0].name
	):
		frappe.throw(
			_("A processor-first V2 Purchase Invoice must represent exactly one Purchase Receipt."),
			title=_("Purchase Invoice Receipt Lineage Mismatch"),
		)

	return context


def validate_processor_first_draft_purchase_invoice(
	doc,
	method=None,
	*,
	purchase_receipt=None,
):
	"""Require an exact PR commercial image while the PI remains Draft."""
	context = _checkpoint_context(doc, purchase_receipt=purchase_receipt)
	if not context:
		return
	if not cint(frappe.conf.get("v2_processor_first_draft_pi")):
		frappe.throw(
			_("Draft Purchase Invoice creation is disabled for processor-first V2 receipts."),
			title=_("V2 Draft Purchase Invoice Disabled"),
		)

	pr, scr, plr = context
	if pr.docstatus != 1:
		frappe.throw(_("The linked Purchase Receipt must be submitted."))
	if plr.purchase_receipt != pr.name or plr.subcontracting_receipt != scr.name:
		frappe.throw(_("The Processor Lot Receipt journey links do not match."))
	if plr.purchase_invoice and plr.purchase_invoice != doc.get("name"):
		status = frappe.db.get_value(
			"Purchase Invoice", plr.purchase_invoice, "docstatus"
		)
		if status != 2:
			frappe.throw(
				_("Processor Lot Receipt already links to Purchase Invoice {0}.").format(
					frappe.bold(plr.purchase_invoice)
				),
				title=_("Purchase Invoice Already Exists"),
			)

	expected = {row.name: row for row in (pr.items or [])}
	actual = {}
	for row in doc.items or []:
		pr_detail = row.get("pr_detail")
		if not pr_detail or pr_detail in actual:
			frappe.throw(_("Each Purchase Invoice row requires one unique Purchase Receipt Item reference."))
		actual[pr_detail] = row

	if set(actual) != set(expected):
		frappe.throw(
			_("Purchase Invoice rows do not exactly match the Purchase Receipt rows."),
			title=_("Purchase Invoice Commercial Lineage Mismatch"),
		)

	for pr_detail, source in expected.items():
		row = actual[pr_detail]
		if (
			row.purchase_receipt != pr.name
			or row.purchase_order != source.purchase_order
			or row.po_detail != source.purchase_order_item
			or row.item_code != source.item_code
			or row.stock_uom != source.stock_uom
			or flt(row.qty, PRECISION) != flt(source.qty, PRECISION)
			or flt(row.stock_qty, PRECISION) != flt(source.stock_qty, PRECISION)
		):
			frappe.throw(
				_("Purchase Invoice row {0} does not match its Purchase Receipt quantity, item, UOM or lineage.").format(
					row.idx
				),
				title=_("Purchase Invoice Commercial Row Mismatch"),
			)


def prevent_processor_first_purchase_invoice_submit(doc, method=None):
	if not _checkpoint_context(doc):
		return
	frappe.throw(
		_("Purchase Invoice submission is not enabled at the J9 Draft Purchase Invoice checkpoint."),
		title=_("Draft Purchase Invoice Checkpoint"),
	)
