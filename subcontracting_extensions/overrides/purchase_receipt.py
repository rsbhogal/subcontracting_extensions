# Copyright (c) 2026, R S Bhogal and contributors
# For license information, please see license.txt

"""Overrides for ERPNext's Purchase Receipt document actions."""

import frappe
from frappe import _
from frappe.utils import cint

from subcontracting_extensions.overrides.posting_date_flow import (
	set_downstream_posting_datetime,
)


def _is_processor_lot_purchase_receipt(purchase_receipt) -> bool:
	"""Return whether a PR belongs to the controlled PLR receipt journey."""
	if not purchase_receipt.subcontracting_receipt:
		return False

	return bool(
		frappe.db.get_value(
			"Subcontracting Receipt",
			purchase_receipt.subcontracting_receipt,
			"custom_processor_lot_receipt",
		)
	)


def _set_mapped_pi_posting_date(
	purchase_invoice,
	purchase_receipt,
) -> None:
	"""Default a mapped PI after its source PR timestamp."""
	set_downstream_posting_datetime(
		purchase_invoice,
		purchase_receipt,
	)


def _get_purchase_order_payment_terms(purchase_invoice) -> str | None:
	"""Return the one contractual PO template represented on a mapped PI."""
	purchase_orders = {
		row.purchase_order
		for row in purchase_invoice.items
		if row.purchase_order
	}
	if not purchase_orders:
		return None

	templates = {
		row.payment_terms_template
		for row in frappe.get_all(
			"Purchase Order",
			filters={"name": ["in", sorted(purchase_orders)]},
			fields=["name", "payment_terms_template"],
		)
		if row.payment_terms_template
	}
	if len(templates) > 1:
		frappe.throw(
			_(
				"Linked Purchase Orders use different Payment Terms Templates: {0}. "
				"Create separate Purchase Invoices or align the contractual terms."
			).format(", ".join(sorted(templates))),
			title=_("Conflicting Purchase Order Payment Terms"),
		)

	return next(iter(templates), None)


def _set_purchase_order_payment_terms(purchase_invoice) -> None:
	"""Apply the linked PO template and rebuild the mapped PI schedule."""
	template = _get_purchase_order_payment_terms(purchase_invoice)
	if not template:
		return

	purchase_invoice.payment_terms_template = template
	purchase_invoice.set("payment_schedule", [])
	purchase_invoice.set_payment_schedule()


@frappe.whitelist()
def make_purchase_invoice(
	source_name,
	target_doc=None,
	args=None,
):
	"""Map a PI and carry forward the controlled PR posting date."""
	from erpnext.stock.doctype.purchase_receipt.purchase_receipt import (
		make_purchase_invoice as erpnext_make_purchase_invoice,
	)

	purchase_receipt = frappe.get_doc(
		"Purchase Receipt",
		source_name,
	)
	if _is_processor_lot_purchase_receipt(purchase_receipt):
		plr_name = frappe.db.get_value(
			"Subcontracting Receipt",
			purchase_receipt.subcontracting_receipt,
			"custom_processor_lot_receipt",
		)
		plr = frappe.db.get_value(
			"Processor Lot Receipt",
			plr_name,
			["receipt_structure_version", "processor_first_draft_only"],
			as_dict=True,
		)
		if plr and plr.receipt_structure_version == "V2 Itemized" and plr.processor_first_draft_only:
			if not cint(frappe.conf.get("v2_processor_first_draft_pi")):
				checkpoint = (
					"J8 Purchase Receipt submission checkpoint"
					if frappe.conf.get("v2_processor_first_pr_submit")
					else "J7 Draft Purchase Receipt checkpoint"
				)
				frappe.throw(
					_("Purchase Invoice creation is not enabled at the {0}.").format(checkpoint),
					title=_("V2 Purchase Invoice Checkpoint"),
				)
	purchase_invoice = erpnext_make_purchase_invoice(
		source_name,
		target_doc=target_doc,
		args=args,
	)

	if _is_processor_lot_purchase_receipt(purchase_receipt):
		_set_mapped_pi_posting_date(
			purchase_invoice,
			purchase_receipt,
		)
		_set_purchase_order_payment_terms(purchase_invoice)
		from subcontracting_extensions.scripts.purchase_invoice import (
			validate_processor_first_draft_purchase_invoice,
		)

		validate_processor_first_draft_purchase_invoice(
			purchase_invoice,
			purchase_receipt=purchase_receipt,
		)

	return purchase_invoice
