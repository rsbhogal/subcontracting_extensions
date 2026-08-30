# Copyright (c) 2026, R S Bhogal and contributors
# For license information, please see license.txt

"""Overrides for ERPNext's Subcontracting Receipt document actions."""

import frappe
from frappe import _
from frappe.model.mapper import get_mapped_doc
from frappe.utils import flt, get_link_to_form

from subcontracting_extensions.overrides.posting_date_flow import (
	set_downstream_posting_datetime,
)


TAX_RULE_FIELDS = (
	"charge_type",
	"account_head",
	"description",
	"rate",
	"row_id",
	"included_in_print_rate",
	"included_in_paid_amount",
	"cost_center",
	"add_deduct_tax",
	"category",
)


def _tax_rule_signature(purchase_order):
	"""Return the non-calculated tax rules that a combined PR must share."""
	rules = []

	for row in purchase_order.taxes:
		if row.is_tax_withholding_account:
			continue

		rule = tuple(
			getattr(row, fieldname, None)
			for fieldname in TAX_RULE_FIELDS
		)

		# For an Actual charge, the entered amount is part of the rule. For
		# percentage/formula charges, tax_amount is calculated per document.
		if row.charge_type == "Actual":
			rule += (flt(row.tax_amount),)

		rules.append(rule)

	return tuple(rules)


def _validate_shared_taxes(po_names):
	"""Require one compatible header-tax setup across all source POs."""
	purchase_orders = [
		frappe.get_doc("Purchase Order", po_name)
		for po_name in po_names
	]
	first_po = purchase_orders[0]
	first_template = first_po.taxes_and_charges or ""
	first_rules = _tax_rule_signature(first_po)

	for purchase_order in purchase_orders[1:]:
		tax_template = purchase_order.taxes_and_charges or ""

		if tax_template != first_template:
			frappe.throw(
				_(
					"Purchase Orders {0} and {1} use different Purchase "
					"Taxes and Charges Templates ({2} and {3}). They "
					"cannot be combined into one Purchase Receipt."
				).format(
					frappe.bold(first_po.name),
					frappe.bold(purchase_order.name),
					frappe.bold(first_template or _("No Template")),
					frappe.bold(tax_template or _("No Template")),
				),
				title=_("Different Purchase Tax Templates"),
			)

		if _tax_rule_signature(purchase_order) != first_rules:
			frappe.throw(
				_(
					"Purchase Orders {0} and {1} do not have the same "
					"tax rules. Align their tax rows before combining "
					"them into one Purchase Receipt."
				).format(
					frappe.bold(first_po.name),
					frappe.bold(purchase_order.name),
				),
				title=_("Different Purchase Tax Rules"),
			)

	return first_template


def _tax_row_values(row):
	"""Copy a mapped tax row without its child-document identity fields."""
	values = row.as_dict()

	for fieldname in (
		"name",
		"owner",
		"creation",
		"modified",
		"modified_by",
		"docstatus",
		"idx",
		"parent",
		"parentfield",
		"parenttype",
		"doctype",
	):
		values.pop(fieldname, None)

	return values


def _get_plr_invoice_qty_by_purchase_order(source_doc):
	"""Return commercial receipt quantity by PO for a PLR-linked SCR.

	A Subcontracting Receipt records the physically received quantity.
	The Purchase Receipt records the supplier's commercial processing
	quantity, held in the PLR allocation's allocated_invoice_qty.

	Ordinary SCRs that are not linked to a Processor Lot Receipt retain
	the standard physical-quantity mapping.
	"""
	plr_name = source_doc.get(
		"custom_processor_lot_receipt"
	)

	if not plr_name:
		return None

	if not frappe.db.exists(
		"Processor Lot Receipt",
		plr_name,
	):
		frappe.throw(
			_(
				"Subcontracting Receipt {0} refers to missing "
				"Processor Lot Receipt {1}."
			).format(
				frappe.bold(source_doc.name),
				frappe.bold(plr_name),
			),
			title=_("Processor Lot Receipt Link Broken"),
		)

	plr = frappe.get_doc(
		"Processor Lot Receipt",
		plr_name,
	)

	if (
		plr.subcontracting_receipt
		and plr.subcontracting_receipt
		!= source_doc.name
	):
		frappe.throw(
			_(
				"Processor Lot Receipt {0} is linked to "
				"Subcontracting Receipt {1}, not {2}."
			).format(
				frappe.bold(plr.name),
				frappe.bold(
					plr.subcontracting_receipt
				),
				frappe.bold(source_doc.name),
			),
			title=_("Subcontracting Receipt Link Mismatch"),
		)

	scr_item_count_by_po = {}

	for item in source_doc.items:
		if not item.purchase_order:
			continue

		scr_item_count_by_po[item.purchase_order] = (
			scr_item_count_by_po.get(
				item.purchase_order,
				0,
			)
			+ 1
		)

	unsupported_purchase_orders = sorted(
		purchase_order
		for purchase_order, item_count
		in scr_item_count_by_po.items()
		if item_count != 1
	)

	if unsupported_purchase_orders:
		frappe.throw(
			_(
				"Processor Lot Receipt commercial quantity mapping "
				"currently requires one finished-item row per "
				"Purchase Order. Review: {0}."
			).format(
				", ".join(
					frappe.bold(purchase_order)
					for purchase_order
					in unsupported_purchase_orders
				)
			),
			title=_("Ambiguous Commercial Quantity Mapping"),
		)

	invoice_qty_by_po = {}

	for allocation in plr.lot_allocations:
		if not allocation.purchase_order:
			frappe.throw(
				_(
					"Row {0} of Processor Lot Receipt {1} "
					"has no Purchase Order."
				).format(
					allocation.idx,
					frappe.bold(plr.name),
				),
				title=_("Incomplete Lot Allocation"),
			)

		invoice_qty_by_po[
			allocation.purchase_order
		] = flt(
			invoice_qty_by_po.get(
				allocation.purchase_order,
				0,
			)
			+ flt(
				allocation.allocated_invoice_qty
			)
		)

	# The lot allocations deliberately contain only the stock-backed invoice
	# quantity. A billed Processor Material Credit is physically received and
	# valued through its separate controlled Material Receipt, but its service
	# quantity still belongs on the supplier's commercial PR/PI. Attribute that
	# billed excess to the last FIFO-backed PO represented by this SCR.
	material_credit_invoice_qty = flt(
		getattr(
			plr,
			"material_credit_invoice_qty",
			0,
		)
	)
	if material_credit_invoice_qty:
		backed_allocations = [
			allocation
			for allocation in plr.lot_allocations
			if allocation.purchase_order
			and flt(allocation.allocated_accepted_qty) > 0
		]
		if not backed_allocations:
			frappe.throw(
				_(
					"Processor Lot Receipt {0} has billed material credit "
					"but no backed Purchase Order allocation."
				).format(frappe.bold(plr.name)),
				title=_("Material Credit Purchase Order Missing"),
			)

		credit_purchase_order = (
			backed_allocations[-1].purchase_order
		)
		invoice_qty_by_po[credit_purchase_order] = flt(
			invoice_qty_by_po.get(
				credit_purchase_order,
				0,
			)
			+ material_credit_invoice_qty
		)

	scr_purchase_orders = set(
		scr_item_count_by_po
	)
	allocation_purchase_orders = set(
		invoice_qty_by_po
	)

	missing_purchase_orders = sorted(
		scr_purchase_orders
		- allocation_purchase_orders
	)
	extra_purchase_orders = sorted(
		allocation_purchase_orders
		- scr_purchase_orders
	)

	if missing_purchase_orders or extra_purchase_orders:
		details = []

		if missing_purchase_orders:
			details.append(
				_(
					"Missing PLR allocation for: {0}"
				).format(
					", ".join(
						frappe.bold(purchase_order)
						for purchase_order
						in missing_purchase_orders
					)
				)
			)

		if extra_purchase_orders:
			details.append(
				_(
					"Allocation has no SCR item for: {0}"
				).format(
					", ".join(
						frappe.bold(purchase_order)
						for purchase_order
						in extra_purchase_orders
					)
				)
			)

		frappe.throw(
			"<br>".join(details),
			title=_("PLR and SCR Purchase Orders Do Not Match"),
		)

	return invoice_qty_by_po


def _get_plr_invoice_qty_by_scr_item(source_doc):
	"""Return commercial quantity keyed by exact SCR Item identity.

	Legacy receipts retain their proven Purchase Order-level calculation. V2
	receipts resolve every allocation through its persisted SCR Item and exact
	SCO Item / PO Item lineage, so several items may safely share one PO.
	"""
	plr_name = source_doc.get("custom_processor_lot_receipt")
	if not plr_name:
		return None

	if not frappe.db.exists("Processor Lot Receipt", plr_name):
		frappe.throw(
			_("Subcontracting Receipt {0} refers to missing Processor Lot Receipt {1}.").format(
				frappe.bold(source_doc.name),
				frappe.bold(plr_name),
			),
			title=_("Processor Lot Receipt Link Broken"),
		)

	plr = frappe.get_doc("Processor Lot Receipt", plr_name)
	if plr.receipt_structure_version != "V2 Itemized":
		invoice_qty_by_po = _get_plr_invoice_qty_by_purchase_order(source_doc)
		return {
			item.name: flt(invoice_qty_by_po[item.purchase_order])
			for item in source_doc.items
			if item.purchase_order
		}

	if plr.subcontracting_receipt and plr.subcontracting_receipt != source_doc.name:
		frappe.throw(
			_("Processor Lot Receipt {0} is linked to Subcontracting Receipt {1}, not {2}.").format(
				frappe.bold(plr.name),
				frappe.bold(plr.subcontracting_receipt),
				frappe.bold(source_doc.name),
			),
			title=_("Subcontracting Receipt Link Mismatch"),
		)

	scr_items_by_name = {
		item.name: item
		for item in source_doc.items
		if item.purchase_order
	}
	invoice_qty_by_scr_item = {}

	for allocation in plr.lot_allocations or []:
		if not allocation.subcontracting_receipt_item:
			frappe.throw(
				_("Allocation row {0} has no Subcontracting Receipt Item link.").format(
					allocation.idx
				),
				title=_("SCR Item Link Missing"),
			)

		scr_item = scr_items_by_name.get(allocation.subcontracting_receipt_item)
		if not scr_item:
			frappe.throw(
				_("Allocation row {0} refers to SCR Item {1}, which is not in {2}.").format(
					allocation.idx,
					frappe.bold(allocation.subcontracting_receipt_item),
					frappe.bold(source_doc.name),
				),
				title=_("SCR Item Link Broken"),
			)

		if (
			scr_item.purchase_order_item != allocation.purchase_order_item
			or scr_item.subcontracting_order_item
			!= allocation.subcontracting_order_item
		):
			frappe.throw(
				_("Allocation row {0} and SCR Item {1} have different order-item lineage.").format(
					allocation.idx,
					frappe.bold(scr_item.name),
				),
				title=_("SCR Item Lineage Mismatch"),
			)

		if scr_item.name in invoice_qty_by_scr_item:
			frappe.throw(
				_("SCR Item {0} is represented by more than one allocation.").format(
					frappe.bold(scr_item.name)
				),
				title=_("Duplicate SCR Item Allocation"),
			)

		invoice_qty_by_scr_item[scr_item.name] = flt(
			allocation.allocated_invoice_qty
		)

	for receipt_item in plr.receipt_items or []:
		credit_invoice_qty = flt(
			receipt_item.material_credit_invoice_qty,
			6,
		)
		if credit_invoice_qty <= 0:
			continue

		backed_allocations = [
			allocation
			for allocation in plr.lot_allocations or []
			if allocation.receipt_item_key == receipt_item.item_key
			and flt(allocation.allocated_accepted_qty, 6) > 0
		]
		if not backed_allocations:
			frappe.throw(
				_("Receipt Item {0} has billed Material Credit but no positive backed allocation.").format(
					frappe.bold(receipt_item.item_key)
				),
				title=_("Material Credit Commercial Lineage Missing"),
			)

		final_allocation = backed_allocations[-1]
		scr_item_name = final_allocation.subcontracting_receipt_item
		if not scr_item_name or scr_item_name not in invoice_qty_by_scr_item:
			frappe.throw(
				_("Receipt Item {0} final allocation has no valid saved SCR Item link.").format(
					frappe.bold(receipt_item.item_key)
				),
				title=_("Material Credit Commercial Lineage Missing"),
			)

		invoice_qty_by_scr_item[scr_item_name] = flt(
			invoice_qty_by_scr_item[scr_item_name] + credit_invoice_qty,
			6,
		)

	missing_scr_items = sorted(
		set(scr_items_by_name) - set(invoice_qty_by_scr_item)
	)
	if missing_scr_items:
		frappe.throw(
			_("No PLR allocation was found for SCR Items: {0}.").format(
				", ".join(frappe.bold(name) for name in missing_scr_items)
			),
			title=_("PLR and SCR Items Do Not Match"),
		)

	return invoice_qty_by_scr_item


def _set_mapped_pr_posting_date(
	purchase_receipt,
	subcontracting_receipt,
):
	"""Default a mapped PR after its source SCR timestamp."""
	set_downstream_posting_datetime(
		purchase_receipt,
		subcontracting_receipt,
	)


@frappe.whitelist()
def make_purchase_receipt(
	source_name,
	target_doc=None,
	save=False,
	submit=False,
	notify=False,
):
	"""Create one Purchase Receipt from every PO represented in an SCR.

	ERPNext v15's standard mapper retains only the first Purchase Order found
	on the Subcontracting Receipt.  A Processor Lot Receipt can legitimately
	produce a single SCR whose finished-item rows belong to several POs, so map
	each referenced PO into the same target Purchase Receipt.
	"""
	if isinstance(source_name, str):
		source_doc = frappe.get_doc(
			"Subcontracting Receipt", source_name
		)
	else:
		source_doc = source_name

	if source_doc.is_return:
		return

	commercial_qty_by_scr_item = (
		_get_plr_invoice_qty_by_scr_item(
			source_doc
		)
	)

	po_item_details = {}
	po_names = []

	for item in source_doc.items:
		if not item.purchase_order:
			continue

		if not item.purchase_order_item:
			frappe.throw(
				_(
					"Purchase Order Item reference is missing in row {0} "
					"of Subcontracting Receipt {1}."
				).format(item.idx, source_doc.name)
			)

		if item.purchase_order_item in po_item_details:
			frappe.throw(
				_(
					"Purchase Order Item {0} is referenced more than once "
					"in Subcontracting Receipt {1}."
				).format(
					frappe.bold(item.purchase_order_item),
					frappe.bold(source_doc.name),
				),
				title=_("Duplicate Purchase Order Item"),
			)

		po_item_details[item.purchase_order_item] = {
			"purchase_order": item.purchase_order,
			"qty": (
				flt(
					commercial_qty_by_scr_item[
						item.name
					]
				)
				if commercial_qty_by_scr_item is not None
				else flt(item.qty)
			),
			"rejected_qty": flt(item.rejected_qty),
			"warehouse": item.warehouse,
			"rejected_warehouse": item.rejected_warehouse,
			"subcontracting_receipt_item": item.name,
		}

		if item.purchase_order not in po_names:
			po_names.append(item.purchase_order)

	if not po_names:
		frappe.throw(
			_(
				"Purchase Order Item reference is missing in "
				"Subcontracting Receipt {0}"
			).format(source_doc.name)
		)

	shared_tax_template = _validate_shared_taxes(po_names)

	def update_item(obj, target, source_parent):
		details = po_item_details.get(obj.name)
		if not details:
			return

		ratio = flt(obj.qty) / flt(obj.fg_item_qty)
		target.update(
			{
				"qty": ratio * details["qty"],
				"rejected_qty": ratio * details["rejected_qty"],
				"warehouse": details["warehouse"],
				"rejected_warehouse": details["rejected_warehouse"],
				"subcontracting_receipt_item": details[
					"subcontracting_receipt_item"
				],
			}
		)

	def item_belongs_to_po(po_name):
		return lambda item: (
			item.name in po_item_details
			and po_item_details[item.name]["purchase_order"] == po_name
		)

	first_po_tax_rows = []

	for index, po_name in enumerate(po_names):
		mapping = {
			"Purchase Order": {
				"doctype": "Purchase Receipt",
				"field_map": {
					"supplier_warehouse": "supplier_warehouse"
				},
				"validation": {"docstatus": ["=", 1]},
			},
			"Purchase Order Item": {
				"doctype": "Purchase Receipt Item",
				"field_map": {
					"name": "purchase_order_item",
					"parent": "purchase_order",
					"bom": "bom",
				},
				"postprocess": update_item,
				"condition": item_belongs_to_po(po_name),
			},
		}

		# Header taxes are copied once. Mapping them for every PO would create
		# duplicate charge rows on the combined Purchase Receipt.
		if index == 0:
			mapping["Purchase Taxes and Charges"] = {
				"doctype": "Purchase Taxes and Charges",
				"reset_value": True,
				"condition": lambda row: (
					not row.is_tax_withholding_account
				),
			}

		target_doc = get_mapped_doc(
			"Purchase Order",
			po_name,
			mapping,
			target_doc=target_doc,
		)

		if index == 0:
			first_po_tax_rows = [
				_tax_row_values(row)
				for row in target_doc.taxes
			]
		else:
			# Mapping another PO also maps its header fields. Keep the one
			# validated template from the first PO throughout the process.
			target_doc.taxes_and_charges = shared_tax_template

	# Additional PO mappings must not leave extra tax children behind. Restore
	# exactly the tax rows mapped from the first PO, then let ERPNext calculate
	# those rows once over the combined taxable value.
	target_doc.taxes_and_charges = shared_tax_template
	target_doc.set("taxes", [])
	for tax_values in first_po_tax_rows:
		target_doc.append("taxes", tax_values)

	target_doc.set_missing_values()
	target_doc.update(
		{
			"subcontracting_receipt": source_doc.name,
			"supplier_warehouse": source_doc.supplier_warehouse,
			"is_subcontracted": 1,
			"is_old_subcontracting_flow": 0,
			"currency": frappe.get_cached_value(
				"Company", target_doc.company, "default_currency"
			),
		}
	)
	_set_mapped_pr_posting_date(
		target_doc,
		source_doc,
	)

	target_doc.run_method(
		"calculate_taxes_and_totals"
	)

	if (save or submit) and frappe.has_permission(
		target_doc.doctype, "create"
	):
		target_doc.save()

		if submit and frappe.has_permission(
			target_doc.doctype, "submit", target_doc
		):
			try:
				target_doc.submit()
			except Exception as exc:
				target_doc.add_comment(
					"Comment",
					_("Submit Action Failed")
					+ "<br><br>"
					+ str(exc),
				)

		if notify:
			frappe.msgprint(
				_("Purchase Receipt {0} created.").format(
					get_link_to_form(
						target_doc.doctype, target_doc.name
					)
				),
				indicator="green",
				alert=True,
			)

	return target_doc
