"""Backfill one itemized V2 receipt row for every historical single-item PLR."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import flt


ITEM_KEY = "ITEM-001"
LEGACY_VERSION = "Legacy Single Item"


def execute() -> None:
	"""Run the idempotent historical PLR compatibility backfill."""
	preview = build_preview()
	if preview["blocking_issues"]:
		formatted = "<br>".join(
			f"{row['processor_lot_receipt']}: {row['problem']}"
			for row in preview["blocking_issues"]
		)
		frappe.throw(
			"Processor Lot Receipt V2 backfill cannot continue:<br>"
			+ formatted
		)

	for plan in preview["plans"]:
		_apply_plan(plan)


def build_preview() -> dict[str, Any]:
	"""Return a read-only plan for the historical single-item backfill."""
	plans = []
	blocking_issues = []

	for plr in frappe.get_all(
		"Processor Lot Receipt",
		fields=[
			"name",
			"docstatus",
			"processed_item",
			"stock_uom",
			"measurement_method",
			"company_accepted_qty",
			"company_accepted_uom",
			"supplier_invoice_qty",
			"supplier_invoice_uom",
			"supplier_gross_weight",
			"supplier_tare_weight",
			"supplier_net_weight",
			"company_gross_weight",
			"company_tare_weight",
			"company_net_weight",
			"processor_material_credit_qty",
			"material_credit_invoice_qty",
			"material_credit_status",
			"material_credit_stock_entry",
			"allow_processor_material_credit",
			"material_credit_reason",
			"receipt_structure_version",
		],
		order_by="creation asc, name asc",
	):
		try:
			plans.append(_build_plan(plr))
		except ValueError as exc:
			blocking_issues.append(
				{
					"processor_lot_receipt": plr.name,
					"problem": str(exc),
				}
			)

	return {
		"processor_lot_receipt_count": len(plans) + len(blocking_issues),
		"create_item_count": sum(
			1 for plan in plans if plan["create_item"]
		),
		"existing_item_count": sum(
			1 for plan in plans if not plan["create_item"]
		),
		"allocation_rows_to_link": sum(
			len(plan["allocation_names_to_link"])
			for plan in plans
		),
		"plrs_to_version": sum(
			1 for plan in plans if plan["set_structure_version"]
		),
		"blocking_issues": blocking_issues,
		"plans": plans,
	}


def _build_plan(plr: frappe._dict) -> dict[str, Any]:
	if not plr.processed_item:
		raise ValueError("Processed Item is missing")
	if not plr.stock_uom:
		raise ValueError("Stock UOM is missing")
	if plr.receipt_structure_version not in (None, "", LEGACY_VERSION):
		raise ValueError(
			f"Receipt Structure Version is already {plr.receipt_structure_version}"
		)

	allocations = frappe.get_all(
		"Processor Lot Receipt Allocation",
		filters={
			"parent": plr.name,
			"parenttype": "Processor Lot Receipt",
			"parentfield": "lot_allocations",
		},
		fields=[
			"name",
			"idx",
			"processed_item",
			"stock_uom",
			"allocated_accepted_qty",
			"allocated_invoice_qty",
			"receipt_item_key",
		],
		order_by="idx asc",
	)

	for allocation in allocations:
		if allocation.processed_item and allocation.processed_item != plr.processed_item:
			raise ValueError(
				f"Allocation {allocation.name} has a different Processed Item"
			)
		if allocation.stock_uom and allocation.stock_uom != plr.stock_uom:
			raise ValueError(
				f"Allocation {allocation.name} has a different Stock UOM"
			)
		if allocation.receipt_item_key not in (None, "", ITEM_KEY):
			raise ValueError(
				f"Allocation {allocation.name} is linked to "
				f"{allocation.receipt_item_key}"
			)

	existing_items = frappe.get_all(
		"Processor Lot Receipt Item",
		filters={
			"parent": plr.name,
			"parenttype": "Processor Lot Receipt",
			"parentfield": "receipt_items",
		},
		fields=["name", "item_key", "processed_item", "stock_uom"],
		order_by="idx asc",
	)
	if len(existing_items) > 1:
		raise ValueError("More than one Receipt Item already exists")
	if existing_items:
		existing = existing_items[0]
		if existing.item_key != ITEM_KEY:
			raise ValueError(
				f"Existing Receipt Item uses key {existing.item_key}"
			)
		if existing.processed_item != plr.processed_item:
			raise ValueError("Existing Receipt Item has a different item")
		if existing.stock_uom != plr.stock_uom:
			raise ValueError("Existing Receipt Item has a different Stock UOM")

	measurement_method = _resolve_measurement_method(plr)
	accepted_qty, accepted_source = _resolve_accepted_qty(plr, allocations)
	invoice_qty, invoice_source = _resolve_invoice_qty(plr, allocations)
	lot_backed_qty = flt(
		sum(flt(row.allocated_accepted_qty) for row in allocations),
		6,
	)
	if not allocations:
		lot_backed_qty = flt(
			accepted_qty - flt(plr.processor_material_credit_qty),
			6,
		)
	if lot_backed_qty < 0:
		raise ValueError("Inferred Lot Backed Qty is negative")

	variance_qty = flt(invoice_qty - accepted_qty, 6)
	variance_percent = (
		flt(variance_qty / accepted_qty * 100, 6)
		if accepted_qty
		else 0.0
	)

	return {
		"processor_lot_receipt": plr.name,
		"create_item": not existing_items,
		"set_structure_version": not plr.receipt_structure_version,
		"allocation_names_to_link": [
			row.name
			for row in allocations
			if not row.receipt_item_key
		],
		"accepted_source": accepted_source,
		"invoice_source": invoice_source,
		"item_values": {
			"item_key": ITEM_KEY,
			"processed_item": plr.processed_item,
			"stock_uom": plr.stock_uom,
			"legacy_direct_attribution": 0 if allocations else 1,
			"measurement_method": measurement_method,
			"measurement_basis": (
				"In-house Weigh Count"
				if measurement_method == "Count"
				else "Truck Differential Weight"
			),
			"company_accepted_qty": accepted_qty,
			"company_accepted_uom": (
				plr.company_accepted_uom or plr.stock_uom
			),
			"supplier_invoice_qty": invoice_qty,
			"supplier_invoice_uom": (
				plr.supplier_invoice_uom or plr.stock_uom
			),
			"supplier_invoice_vs_company_qty": variance_qty,
			"supplier_invoice_vs_company_percent": variance_percent,
			"lot_backed_qty": lot_backed_qty,
			"processor_material_credit_qty": flt(
				plr.processor_material_credit_qty,
				6,
			),
			"material_credit_invoice_qty": flt(
				plr.material_credit_invoice_qty,
				6,
			),
			"material_credit_status": (
				plr.material_credit_status
				or "Not Applicable"
			),
			"material_credit_stock_entry": plr.material_credit_stock_entry,
			"allow_processor_material_credit": (
				plr.allow_processor_material_credit
			),
			"material_credit_reason": plr.material_credit_reason,
		},
	}


def _resolve_measurement_method(plr: frappe._dict) -> str:
	if plr.measurement_method in ("Weight", "Count"):
		return plr.measurement_method

	has_weight_facts = any(
		flt(value)
		for value in (
			plr.supplier_gross_weight,
			plr.supplier_tare_weight,
			plr.supplier_net_weight,
			plr.company_gross_weight,
			plr.company_tare_weight,
			plr.company_net_weight,
		)
	)
	if has_weight_facts:
		return "Weight"
	if plr.stock_uom == "Units":
		return "Count"
	raise ValueError("Measurement Method cannot be inferred")


def _resolve_accepted_qty(
	plr: frappe._dict,
	allocations: list[frappe._dict],
) -> tuple[float, str]:
	if allocations:
		return (
			flt(
				sum(flt(row.allocated_accepted_qty) for row in allocations)
				+ flt(plr.processor_material_credit_qty),
				6,
			),
			"Lot Allocations and Material Credit",
		)
	if flt(plr.company_accepted_qty):
		return flt(plr.company_accepted_qty, 6), "PLR Header"

	scr_qty = _submitted_total_qty(
		"Subcontracting Receipt",
		plr.name,
		"subcontracting_receipt",
	)
	if scr_qty is not None:
		return scr_qty, "Submitted Subcontracting Receipt"
	return 0.0, "PLR Header Zero"


def _resolve_invoice_qty(
	plr: frappe._dict,
	allocations: list[frappe._dict],
) -> tuple[float, str]:
	if allocations:
		return (
			flt(
				sum(flt(row.allocated_invoice_qty) for row in allocations)
				+ flt(plr.material_credit_invoice_qty),
				6,
			),
			"Lot Allocations and Material Credit",
		)
	if flt(plr.supplier_invoice_qty):
		return flt(plr.supplier_invoice_qty, 6), "PLR Header"

	for doctype, fieldname in (
		("Purchase Invoice", "purchase_invoice"),
		("Purchase Receipt", "purchase_receipt"),
	):
		qty = _submitted_total_qty(doctype, plr.name, fieldname)
		if qty is not None:
			return qty, f"Submitted {doctype}"
	return 0.0, "PLR Header Zero"


def _submitted_total_qty(
	doctype: str,
	plr_name: str,
	link_field: str,
) -> float | None:
	document_name = frappe.db.get_value(
		"Processor Lot Receipt",
		plr_name,
		link_field,
	)
	if not document_name:
		return None
	values = frappe.db.get_value(
		doctype,
		document_name,
		["docstatus", "total_qty"],
		as_dict=True,
	)
	if not values or values.docstatus != 1:
		return None
	return flt(values.total_qty, 6)


def _apply_plan(plan: dict[str, Any]) -> None:
	if plan["create_item"]:
		item = frappe.get_doc(
			{
				"doctype": "Processor Lot Receipt Item",
				"parent": plan["processor_lot_receipt"],
				"parenttype": "Processor Lot Receipt",
				"parentfield": "receipt_items",
				"idx": 1,
				**plan["item_values"],
			}
		)
		item.db_insert()

	for allocation_name in plan["allocation_names_to_link"]:
		frappe.db.set_value(
			"Processor Lot Receipt Allocation",
			allocation_name,
			"receipt_item_key",
			ITEM_KEY,
			update_modified=False,
		)

	if plan["set_structure_version"]:
		frappe.db.set_value(
			"Processor Lot Receipt",
			plan["processor_lot_receipt"],
			"receipt_structure_version",
			LEGACY_VERSION,
			update_modified=False,
		)
