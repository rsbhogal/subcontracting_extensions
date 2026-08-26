# Copyright (c) 2026, R.S. Bhogal and contributors
# For license information, please see license.txt

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import date_diff, flt, getdate, today


def execute(filters=None):
	filters = frappe._dict(filters or {})
	filters.as_on_date = getdate(filters.as_on_date or today())

	columns = get_columns()
	data = get_data(filters)

	return columns, data


def get_columns():
	return [
		{
			"label": _("Processor Lot"),
			"fieldname": "processor_lot",
			"fieldtype": "Link",
			"options": "Processor Lot",
			"width": 145,
		},
		{
			"label": _("Supplier"),
			"fieldname": "supplier",
			"fieldtype": "Link",
			"options": "Supplier",
			"width": 160,
		},
		{
			"label": _("Finished Item"),
			"fieldname": "processed_item",
			"fieldtype": "Link",
			"options": "Item",
			"width": 220,
		},
		{
			"label": _("UOM"),
			"fieldname": "stock_uom",
			"fieldtype": "Link",
			"options": "UOM",
			"width": 75,
		},
		{
			"label": _("Lot Qty"),
			"fieldname": "lot_order_qty",
			"fieldtype": "Float",
			"width": 100,
		},
		{
			"label": _("Physically Accepted"),
			"fieldname": "physically_accepted_qty",
			"fieldtype": "Float",
			"width": 135,
		},
		{
			"label": _("Physical Shortfall"),
			"fieldname": "physical_shortfall_qty",
			"fieldtype": "Float",
			"width": 125,
		},
		{
			"label": _("Outstanding FG"),
			"fieldname": "outstanding_finished_qty",
			"fieldtype": "Float",
			"width": 115,
		},
		{
			"label": _("Settled Variance"),
			"fieldname": "settled_variance_qty",
			"fieldtype": "Float",
			"width": 115,
		},
		{
			"label": _("Material Credit Applied"),
			"fieldname": "material_credit_applied_qty",
			"fieldtype": "Float",
			"width": 135,
		},
		{
			"label": _("Debit Note Qty"),
			"fieldname": "debit_note_qty",
			"fieldtype": "Float",
			"width": 105,
		},
		{
			"label": _("Settlement Method"),
			"fieldname": "settlement_method",
			"fieldtype": "Data",
			"width": 220,
		},
		{
			"label": _("Receipt Count"),
			"fieldname": "receipt_count",
			"fieldtype": "Int",
			"width": 100,
		},
		{
			"label": _("Lot Start"),
			"fieldname": "lot_start_date",
			"fieldtype": "Date",
			"width": 100,
		},
		{
			"label": _("Age (Days)"),
			"fieldname": "age_days",
			"fieldtype": "Int",
			"width": 90,
		},
		{
			"label": _("Expected Delivery"),
			"fieldname": "expected_delivery_date",
			"fieldtype": "Date",
			"width": 120,
		},
		{
			"label": _("Overdue Days"),
			"fieldname": "overdue_days",
			"fieldtype": "Int",
			"width": 100,
		},
		{
			"label": _("Latest PLR"),
			"fieldname": "latest_processor_lot_receipt",
			"fieldtype": "Link",
			"options": "Processor Lot Receipt",
			"width": 135,
		},
		{
			"label": _("Latest Receipt"),
			"fieldname": "latest_receipt_date",
			"fieldtype": "Date",
			"width": 105,
		},
		{
			"label": _("Incomplete Cycles"),
			"fieldname": "incomplete_receipt_cycles",
			"fieldtype": "Int",
			"width": 115,
		},
		{
			"label": _("Pipeline Stage"),
			"fieldname": "pipeline_stage",
			"fieldtype": "Data",
			"width": 145,
		},
		{
			"label": _("Settlement"),
			"fieldname": "settlement_status",
			"fieldtype": "Data",
			"width": 120,
		},
		{
			"label": _("Debit Note"),
			"fieldname": "debit_note",
			"fieldtype": "Link",
			"options": "Purchase Invoice",
			"width": 130,
		},
		{
			"label": _("Debit Note Status"),
			"fieldname": "debit_note_status",
			"fieldtype": "Data",
			"width": 115,
		},
		{
			"label": _("Operational Status"),
			"fieldname": "operational_status",
			"fieldtype": "Data",
			"width": 155,
		},
		{
			"label": _("Next Required Action"),
			"fieldname": "next_action",
			"fieldtype": "Data",
			"width": 220,
		},
		{
			"label": _("Subcontracting Order"),
			"fieldname": "subcontracting_order",
			"fieldtype": "Link",
			"options": "Subcontracting Order",
			"width": 160,
		},
		{
			"label": _("Purchase Order"),
			"fieldname": "purchase_order",
			"fieldtype": "Link",
			"options": "Purchase Order",
			"width": 155,
		},
		{
			"label": _("Supplier Warehouse"),
			"fieldname": "supplier_warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 170,
		},
	]


def get_data(filters):
	lot_filters = {"docstatus": ["!=", 2]}

	if filters.company:
		lot_filters["company"] = filters.company

	if filters.supplier:
		lot_filters["supplier"] = filters.supplier

	lots = frappe.get_all(
		"Processor Lot",
		filters=lot_filters,
		fields=[
			"name",
			"company",
			"supplier",
			"supplier_warehouse",
			"subcontracting_order",
			"purchase_order",
			"settlement_status",
			"debit_note",
			"creation",
		],
		order_by="creation asc, name asc",
	)

	if not lots:
		return []

	sco_names = list(
		{
			row.subcontracting_order
			for row in lots
			if row.subcontracting_order
		}
	)
	lot_names = [row.name for row in lots]

	sco_map, item_rows = get_sco_details(sco_names)
	receipt_map = get_receipt_details(lot_names)
	transfer_dates = get_transfer_dates(sco_names)

	material_credit_map = get_material_credit_applications(
		lot_names
	)

	debit_note_states = get_document_states(
		"Purchase Invoice",
		[row.debit_note for row in lots if row.debit_note],
	)

	data = []

	for lot in lots:
		sco = sco_map.get(
			lot.subcontracting_order,
			frappe._dict(),
		)
		rows = item_rows.get(
			lot.subcontracting_order,
			[],
		)

		if filters.processed_item:
			rows = [
				row
				for row in rows
				if row.item_code == filters.processed_item
			]

		if not rows:
			continue

		receipts = receipt_map.get(lot.name, [])

		for item in rows:
			item_receipts = [
				row
				for row in receipts
				if (
					row.processed_item == item.item_code
					and (
						not row.stock_uom
						or row.stock_uom == item.stock_uom
					)
				)
			]

			accepted_qty = flt(
				sum(
					flt(row.allocated_accepted_qty)
					for row in item_receipts
				)
			)

			physical_shortfall_qty = max(
				flt(item.qty) - accepted_qty,
				0,
			)

			material_credit = material_credit_map.get(
				(
					lot.name,
					item.item_code,
					item.stock_uom,
				),
				frappe._dict(),
			)

			material_credit_applied_qty = min(
				flt(material_credit.get("physical_qty")),
				physical_shortfall_qty,
			)

			debit_note_state = debit_note_states.get(
				lot.debit_note
			)

			shortfall_is_settled = is_shortfall_settled(
				settlement_status=lot.settlement_status,
				debit_note_state=debit_note_state,
			)

			residual_shortfall_qty = max(
				physical_shortfall_qty
				- material_credit_applied_qty,
				0,
			)

			debit_note_qty = (
				residual_shortfall_qty
				if (
					debit_note_state
					and debit_note_state.docstatus == 1
				)
				else 0
			)

			settlement_method = get_settlement_method(
				physical_shortfall_qty=physical_shortfall_qty,
				material_credit_applied_qty=(
					material_credit_applied_qty
				),
				debit_note_qty=debit_note_qty,
				shortfall_is_settled=shortfall_is_settled,
			)

			shortfall_is_under_settlement = (
				is_shortfall_under_settlement(
					physical_shortfall_qty=physical_shortfall_qty,
					settlement_status=lot.settlement_status,
					debit_note_state=debit_note_state,
				)
			)

			outstanding_finished_qty = (
				0
				if (
					shortfall_is_settled
					or shortfall_is_under_settlement
				)
				else physical_shortfall_qty
			)

			settled_variance_qty = (
				physical_shortfall_qty
				if shortfall_is_settled
				else 0
			)

			journey = summarize_journey(item_receipts)

			lot_start_date = (
				transfer_dates.get(lot.subcontracting_order)
				or sco.get("transaction_date")
			)

			age_days = (
				max(
					date_diff(
						filters.as_on_date,
						lot_start_date,
					),
					0,
				)
				if lot_start_date
				else None
			)

			due_date = item.expected_delivery_date

			overdue_days = (
				max(
					date_diff(
						filters.as_on_date,
						due_date,
					),
					0,
				)
				if due_date
				else 0
			)

			operational_status, next_action = (
				get_operational_position(
					physical_shortfall_qty=physical_shortfall_qty,
					outstanding_finished_qty=outstanding_finished_qty,
					shortfall_is_settled=shortfall_is_settled,
					shortfall_is_under_settlement=(
						shortfall_is_under_settlement
					),
					receipt_count=len(
						{
							row.parent
							for row in item_receipts
						}
					),
					material_transferred=bool(
						transfer_dates.get(
							lot.subcontracting_order
						)
					),
					overdue_days=overdue_days,
					journey=journey,
					settlement_status=lot.settlement_status,
					debit_note_state=debit_note_state,
				)
			)

			is_complete = (
				journey.is_complete
				and outstanding_finished_qty <= 0
				and (
					physical_shortfall_qty <= 0
					or shortfall_is_settled
				)
				and lot.settlement_status == "Completed"
			)

			position = filters.position or "Outstanding"

			if position == "Outstanding" and is_complete:
				continue

			if position == "Complete" and not is_complete:
				continue

			data.append(
				{
					"processor_lot": lot.name,
					"supplier": lot.supplier,
					"processed_item": item.item_code,
					"stock_uom": item.stock_uom,
					"lot_order_qty": flt(item.qty),
					"physically_accepted_qty": accepted_qty,
					"physical_shortfall_qty": (
						physical_shortfall_qty
					),
					"outstanding_finished_qty": (
						outstanding_finished_qty
					),
					"settled_variance_qty": (
						settled_variance_qty
					),
					"material_credit_applied_qty": (
						material_credit_applied_qty
					),
					"debit_note_qty": debit_note_qty,
					"settlement_method": settlement_method,
					"receipt_count": len(
						{
							row.parent
							for row in item_receipts
						}
					),
					"lot_start_date": lot_start_date,
					"age_days": age_days,
					"expected_delivery_date": due_date,
					"overdue_days": overdue_days,
					"latest_processor_lot_receipt": (
						journey.latest_plr
					),
					"latest_receipt_date": (
						journey.latest_receipt_date
					),
					"incomplete_receipt_cycles": (
						journey.incomplete_count
					),
					"pipeline_stage": journey.stage,
					"settlement_status": (
						lot.settlement_status or "Draft"
					),
					"debit_note": lot.debit_note,
					"debit_note_status": (
						format_document_state(
							debit_note_states.get(
								lot.debit_note
							)
						)
					),
					"operational_status": operational_status,
					"next_action": next_action,
					"subcontracting_order": (
						lot.subcontracting_order
					),
					"purchase_order": lot.purchase_order,
					"supplier_warehouse": (
						lot.supplier_warehouse
					),
				}
			)

	return sorted(
		data,
		key=lambda row: (
			row["supplier"] or "",
			row["lot_start_date"] or getdate("9999-12-31"),
			row["processor_lot"],
			row["processed_item"],
		),
	)


def get_sco_details(sco_names):
	if not sco_names:
		return {}, {}

	scos = frappe.get_all(
		"Subcontracting Order",
		filters={"name": ["in", sco_names]},
		fields=[
			"name",
			"transaction_date",
			"docstatus",
		],
	)

	sco_map = {
		row.name: row
		for row in scos
	}

	item_meta = frappe.get_meta(
		"Subcontracting Order Item"
	)

	due_field = next(
		(
			fieldname
			for fieldname in (
				"schedule_date",
				"delivery_date",
				"expected_delivery_date",
			)
			if item_meta.has_field(fieldname)
		),
		None,
	)

	fields = [
		"parent",
		"item_code",
		"stock_uom",
		"qty",
		"idx",
	]

	if due_field:
		fields.append(due_field)

	items = frappe.get_all(
		"Subcontracting Order Item",
		filters={
			"parent": ["in", sco_names],
			"parenttype": "Subcontracting Order",
		},
		fields=fields,
		order_by="parent, idx",
	)

	grouped_items = {}

	for row in items:
		key = (
			row.parent,
			row.item_code,
			row.stock_uom,
		)

		due_date = (
			row.get(due_field)
			if due_field
			else None
		)

		if key not in grouped_items:
			grouped_items[key] = frappe._dict(
				parent=row.parent,
				item_code=row.item_code,
				stock_uom=row.stock_uom,
				qty=0.0,
				expected_delivery_date=due_date,
			)

		grouped_items[key].qty += flt(row.qty)

		current_due_date = (
			grouped_items[key].expected_delivery_date
		)

		if (
			due_date
			and (
				not current_due_date
				or due_date < current_due_date
			)
		):
			grouped_items[
				key
			].expected_delivery_date = due_date

	grouped = defaultdict(list)

	for row in grouped_items.values():
		grouped[row.parent].append(row)

	return sco_map, grouped


def get_receipt_details(lot_names):
	if not lot_names:
		return {}

	allocations = frappe.get_all(
		"Processor Lot Receipt Allocation",
		filters={
			"processor_lot": ["in", lot_names],
			"parenttype": "Processor Lot Receipt",
			"parentfield": "lot_allocations",
		},
		fields=[
			"processor_lot",
			"parent",
			"processed_item",
			"stock_uom",
			"allocated_accepted_qty",
			"idx",
		],
		order_by="parent, idx",
	)

	# V1 receipts identify their lot on the PLR header and have
	# no allocation rows. V1.1 receipts use allocation rows,
	# including multi-lot receipts.
	#
	# A PLR header quantity must therefore be used only when
	# that PLR has no allocation rows. Otherwise a multi-lot
	# truck would be double-counted and attributed wholly to
	# its header lot.

	direct_receipts = frappe.get_all(
		"Processor Lot Receipt",
		filters={
			"processor_lot": ["in", lot_names],
			"docstatus": ["!=", 2],
		},
		fields=[
			"name",
			"processor_lot",
			"processed_item",
			"stock_uom",
			"company_accepted_qty",
			"docstatus",
			"physical_receipt_date",
			"creation",
			"subcontracting_receipt",
			"purchase_receipt",
			"purchase_invoice",
		],
	)

	parents = list(
		{
			row.parent
			for row in allocations
		}
		| {
			row.name
			for row in direct_receipts
		}
	)

	if not parents:
		return {}

	receipts = frappe.get_all(
		"Processor Lot Receipt",
		filters={
			"name": ["in", parents],
			"docstatus": ["!=", 2],
		},
		fields=[
			"name",
			"docstatus",
			"physical_receipt_date",
			"creation",
			"subcontracting_receipt",
			"purchase_receipt",
			"purchase_invoice",
		],
	)

	receipt_map = {
		row.name: row
		for row in receipts
	}

	allocation_parents = {
		row.parent
		for row in allocations
	}

	links = {
		"scr": get_document_states(
			"Subcontracting Receipt",
			[
				row.subcontracting_receipt
				for row in receipts
				if row.subcontracting_receipt
			],
		),
		"pr": get_document_states(
			"Purchase Receipt",
			[
				row.purchase_receipt
				for row in receipts
				if row.purchase_receipt
			],
		),
		"pi": get_document_states(
			"Purchase Invoice",
			[
				row.purchase_invoice
				for row in receipts
				if row.purchase_invoice
			],
		),
	}

	grouped = defaultdict(list)

	for row in allocations:
		receipt = receipt_map.get(row.parent)

		if not receipt:
			continue

		row.update(receipt)

		row.scr_state = document_state(
			receipt.subcontracting_receipt,
			links["scr"],
		)
		row.pr_state = document_state(
			receipt.purchase_receipt,
			links["pr"],
		)
		row.pi_state = document_state(
			receipt.purchase_invoice,
			links["pi"],
		)

		grouped[row.processor_lot].append(row)

	for direct in direct_receipts:
		if direct.name in allocation_parents:
			continue

		receipt = receipt_map.get(direct.name)

		if not receipt:
			continue

		row = frappe._dict(
			processor_lot=direct.processor_lot,
			parent=direct.name,
			processed_item=direct.processed_item,
			stock_uom=direct.stock_uom,
			allocated_accepted_qty=(
				direct.company_accepted_qty
			),
			idx=0,
		)

		row.update(receipt)

		row.scr_state = document_state(
			receipt.subcontracting_receipt,
			links["scr"],
		)
		row.pr_state = document_state(
			receipt.purchase_receipt,
			links["pr"],
		)
		row.pi_state = document_state(
			receipt.purchase_invoice,
			links["pi"],
		)

		grouped[row.processor_lot].append(row)

	return grouped

def get_material_credit_applications(lot_names):
	if not lot_names:
		return {}

	rows = frappe.get_all(
		"Processor Material Account Entry",
		filters={
			"processor_lot": ["in", lot_names],
			"entry_type": "Credit Applied",
			"account_direction": "Debit",
			"docstatus": 1,
		},
		fields=[
			"processor_lot",
			"processed_item",
			"account_uom",
			"account_qty",
			"commercial_qty",
		],
	)

	result = defaultdict(
		lambda: frappe._dict(
			physical_qty=0.0,
			commercial_qty=0.0,
		)
	)

	for row in rows:
		key = (
			row.processor_lot,
			row.processed_item,
			row.account_uom,
		)

		result[key].physical_qty += flt(
			row.account_qty
		)
		result[key].commercial_qty += flt(
			row.commercial_qty
		)

	return result

def get_transfer_dates(sco_names):
	if not sco_names:
		return {}

	entries = frappe.get_all(
		"Stock Entry",
		filters={
			"subcontracting_order": ["in", sco_names],
			"purpose": "Send to Subcontractor",
			"docstatus": 1,
		},
		fields=[
			"subcontracting_order",
			"posting_date",
		],
		order_by=(
			"posting_date asc, "
			"posting_time asc, "
			"creation asc"
		),
	)

	result = {}

	for row in entries:
		result.setdefault(
			row.subcontracting_order,
			row.posting_date,
		)

	return result


def get_document_states(doctype, names):
	names = list(
		{
			name
			for name in names
			if name
		}
	)

	if not names:
		return {}

	return {
		row.name: row
		for row in frappe.get_all(
			doctype,
			filters={
				"name": ["in", names]
			},
			fields=[
				"name",
				"docstatus",
				"status",
			],
		)
	}


def document_state(name, state_map):
	if not name:
		return "not_created"

	row = state_map.get(name)

	if not row:
		return "broken_link"

	if row.docstatus == 2:
		return "cancelled"

	if row.docstatus == 1:
		return "submitted"

	return "draft"


def summarize_journey(allocations):
	if not allocations:
		return frappe._dict(
			stage=_("No Physical Receipt"),
			next_action=_(
				"Record Processor Lot Receipt"
			),
			incomplete_count=0,
			is_complete=False,
			latest_plr=None,
			latest_receipt_date=None,
		)

	unique = {}

	for row in allocations:
		unique[row.parent] = row

	receipts = sorted(
		unique.values(),
		key=lambda row: (
			str(
				row.physical_receipt_date
				or row.creation
				or ""
			),
			str(row.creation or ""),
			row.parent,
		),
	)

	progress_rows = [
		get_receipt_progress(row)
		for row in receipts
	]

	incomplete = [
		row
		for row in progress_rows
		if not row.is_complete
	]

	current = (
		incomplete[0]
		if incomplete
		else progress_rows[-1]
	)

	latest = receipts[-1]

	return frappe._dict(
		stage=current.stage,
		next_action=current.next_action,
		incomplete_count=len(incomplete),
		is_complete=not incomplete,
		latest_plr=latest.parent,
		latest_receipt_date=(
			latest.physical_receipt_date
		),
	)


def get_receipt_progress(row):
	checks = [
		(
			"scr_state",
			"broken_link",
			_("SCR Link Broken"),
			_("Review Subcontracting Receipt Link"),
		),
		(
			"scr_state",
			"cancelled",
			_("SCR Cancelled"),
			_("Create or Amend Subcontracting Receipt"),
		),
		(
			"scr_state",
			"not_created",
			_("Awaiting SCR"),
			_("Create Subcontracting Receipt"),
		),
		(
			"scr_state",
			"draft",
			_("SCR Draft"),
			_("Submit Subcontracting Receipt"),
		),
		(
			"pr_state",
			"broken_link",
			_("PR Link Broken"),
			_("Review Purchase Receipt Link"),
		),
		(
			"pr_state",
			"cancelled",
			_("PR Cancelled"),
			_("Create or Amend Purchase Receipt"),
		),
		(
			"pr_state",
			"not_created",
			_("Awaiting PR"),
			_("Create Purchase Receipt"),
		),
		(
			"pr_state",
			"draft",
			_("PR Draft"),
			_("Submit Purchase Receipt"),
		),
		(
			"pi_state",
			"broken_link",
			_("PI Link Broken"),
			_("Review Purchase Invoice Link"),
		),
		(
			"pi_state",
			"cancelled",
			_("PI Cancelled"),
			_("Create or Amend Purchase Invoice"),
		),
		(
			"pi_state",
			"not_created",
			_("Awaiting PI"),
			_("Create Purchase Invoice"),
		),
		(
			"pi_state",
			"draft",
			_("PI Draft"),
			_("Submit Purchase Invoice"),
		),
	]

	for fieldname, state, stage, action in checks:
		if row.get(fieldname) == state:
			return frappe._dict(
				stage=stage,
				next_action=action,
				is_complete=False,
			)

	return frappe._dict(
		stage=_("Commercially Complete"),
		next_action=_("Receipt Cycle Complete"),
		is_complete=True,
	)


def is_shortfall_settled(
	settlement_status,
	debit_note_state,
):
	return (
		settlement_status == "Completed"
		or bool(
			debit_note_state
			and debit_note_state.docstatus == 1
		)
	)


def is_shortfall_under_settlement(
	physical_shortfall_qty,
	settlement_status,
	debit_note_state,
):
	if physical_shortfall_qty <= 0:
		return False

	if settlement_status == "Debit Note Created":
		return (
			not debit_note_state
			or debit_note_state.docstatus == 0
		)

	return bool(
		debit_note_state
		and debit_note_state.docstatus == 0
	)


def get_operational_position(
	physical_shortfall_qty,
	outstanding_finished_qty,
	shortfall_is_settled,
	shortfall_is_under_settlement,
	receipt_count,
	material_transferred,
	overdue_days,
	journey,
	settlement_status,
	debit_note_state,
):
	if not material_transferred:
		return (
			_("Material Not Transferred"),
			_("Transfer Material to Subcontractor"),
		)

	if (
		physical_shortfall_qty > 0
		and shortfall_is_settled
	):
		return (
			_("Completed"),
			_("Completed — Shortfall Settled"),
		)

	if receipt_count == 0:
		status = (
			_("Overdue")
			if overdue_days > 0
			else _("Awaiting First Receipt")
		)

		return (
			status,
			_("Record Processor Lot Receipt"),
		)

	if not journey.is_complete:
		return (
			_("Pipeline Attention"),
			journey.next_action,
		)

	if (
		physical_shortfall_qty > 0
		and shortfall_is_under_settlement
	):
		if (
			debit_note_state
			and debit_note_state.docstatus == 0
		):
			return (
				_("Shortfall Under Settlement"),
				_("Submit Debit Note"),
			)

		return (
			_("Shortfall Under Settlement"),
			_("Complete Shortfall Settlement"),
		)

	if outstanding_finished_qty > 0:
		status = (
			_("Overdue")
			if overdue_days > 0
			else _("Partly Received")
		)

		return (
			status,
			_("Receive Pending Finished Goods"),
		)

	if (
		settlement_status == "Debit Note Created"
		and debit_note_state
		and debit_note_state.docstatus == 0
	):
		return (
			_("Receipt Complete"),
			_("Submit Debit Note"),
		)

	if settlement_status != "Completed":
		return (
			_("Receipt Complete"),
			_("Complete Processor Lot Reconciliation"),
		)

	return (
		_("Completed"),
		_("No Action Required"),
	)

def get_settlement_method(
	physical_shortfall_qty,
	material_credit_applied_qty,
	debit_note_qty,
	shortfall_is_settled,
):
	if physical_shortfall_qty <= 0:
		return ""

	parts = []

	if material_credit_applied_qty > 0:
		parts.append(
			_("{0} via Material Credit").format(
				frappe.format_value(
					material_credit_applied_qty,
					{"fieldtype": "Float"},
				)
			)
		)

	if debit_note_qty > 0:
		parts.append(
			_("{0} via Debit Note").format(
				frappe.format_value(
					debit_note_qty,
					{"fieldtype": "Float"},
				)
			)
		)

	if parts:
		return " + ".join(parts)

	if shortfall_is_settled:
		return _("Settled")

	return _("Pending Settlement")

def format_document_state(row):
	if not row:
		return ""

	return {
		0: _("Draft"),
		1: _("Submitted"),
		2: _("Cancelled"),
	}.get(
		row.docstatus,
		row.status or "",
	)
