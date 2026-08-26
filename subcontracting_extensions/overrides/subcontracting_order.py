"""Controlled extensions for Subcontracting Order document actions."""

import frappe


@frappe.whitelist()
def make_rm_stock_entry(
	subcontract_order,
	rm_items=None,
	order_doctype="Subcontracting Order",
	target_doc=None,
):
	"""Map material to the supplier and default the SCO transaction date.

	Only Stock Entries generated from a Subcontracting Order are adjusted.
	Manual Stock Entries and Purchase Order mappings retain ERPNext behavior.
	"""
	from erpnext.controllers.subcontracting_controller import (
		make_rm_stock_entry as erpnext_make_rm_stock_entry,
	)

	stock_entry = erpnext_make_rm_stock_entry(
		subcontract_order,
		rm_items=rm_items,
		order_doctype=order_doctype,
		target_doc=target_doc,
	)

	if order_doctype != "Subcontracting Order" or not stock_entry:
		return stock_entry

	sco = frappe.get_doc("Subcontracting Order", subcontract_order)
	if not sco.transaction_date:
		return stock_entry

	stock_entry.posting_date = sco.transaction_date
	stock_entry.set_posting_time = 1
	return stock_entry
