"""J4 item-scoped receipt facts. Reads only; never creates masters/documents.

The SCO finished-row name is the capacity identity. Truck counts are distinct
PLR names, both per finished row and for the whole lot. No mixed-item total.
"""

import math

import frappe
from frappe import _
from frappe.utils import cint, flt


def quantity(value):
    result = flt(value, 6)
    if not math.isfinite(result):
        frappe.throw(_("Non-finite receipt quantity."))
    return result


def finished_rows(sco):
    rows = list(sco.get("items") or [])
    seen = set()
    names = set()
    for row in rows:
        identity = (row.item_code, row.stock_uom)
        if not row.name or row.name in names or identity in seen:
            frappe.throw(_("Duplicate or ambiguous SCO finished-item rows are not supported yet."))
        if not all(identity):
            frappe.throw(_("Every SCO finished row needs an Item and Stock UOM."))
        if row.get("uom") and row.uom != row.stock_uom:
            frappe.throw(_("Receipt entry currently requires SCO quantity UOM to equal Stock UOM."))
        seen.add(identity)
        names.add(row.name)
    return rows


def read_evidence(lot_name, sco, exclude_receipt=None):
    """Read scoped allocation evidence, including drafts not yet in ERP stock.

    Excluding the edited PLR prevents it consuming its own capacity. Other
    users' active allocations must still count, even if not visible to them.
    Public callers must first check access to the lot and SCO.
    """
    filters = {"processor_lot": lot_name, "parenttype": "Processor Lot Receipt",
               "parentfield": "lot_allocations", "docstatus": ["!=", 2]}
    if exclude_receipt:
        filters["parent"] = ["!=", exclude_receipt]
    allocations = frappe.get_all("Processor Lot Receipt Allocation", filters=filters,
        fields=["parent", "receipt_item_key", "subcontracting_order", "subcontracting_order_item",
                "subcontracting_receipt_item", "processed_item", "stock_uom",
                "allocated_accepted_qty", "allocated_invoice_qty"])
    parents = sorted({row.parent for row in allocations})
    receipts = frappe.get_all("Processor Lot Receipt", filters={"name": ["in", parents], "docstatus": ["!=", 2]},
        fields=["name", "docstatus", "physical_receipt_date", "subcontracting_receipt", "purchase_receipt", "purchase_invoice"]) if parents else []
    scr_names = sorted({row.subcontracting_receipt for row in receipts if row.subcontracting_receipt})
    submitted = frappe.get_all("Subcontracting Receipt", filters={"name": ["in", scr_names], "docstatus": 1},
        fields=["name"]) if scr_names else []
    submitted_names = [row.name for row in submitted]
    scr_items = frappe.get_all("Subcontracting Receipt Item", filters={"parent": ["in", submitted_names]},
        fields=["name", "parent", "subcontracting_order", "subcontracting_order_item", "item_code", "stock_uom", "qty"]) if submitted_names else []
    credits = frappe.get_all("Processor Material Account Entry", filters={
        "entry_type": "Credit Applied", "source_event": "Processor Lot Shortage", "processor_lot": lot_name,
        "account_direction": "Debit", "docstatus": 1, "is_reversed": 0},
        fields=["processed_item", "processed_item_uom", "processed_qty"])
    return allocations, receipts, set(submitted_names), scr_items, credits


def build_position(sco, allocations, receipts, submitted_scrs, scr_items, credits):
    """Pure aggregation, suitable for tests without a database or fixtures."""
    rows = finished_rows(sco)
    by_name = {row.name: row for row in rows}
    receipts_by_name = {row.name: row for row in receipts if row.docstatus != 2}
    items = {row.name: frappe._dict(
        subcontracting_order_item=row.name, processed_item=row.item_code, stock_uom=row.stock_uom,
        ordered_qty=quantity(row.qty), native_received_qty=quantity(max(quantity(row.received_qty) - quantity(row.returned_qty), 0)),
        accepted_qty=0, invoice_qty=0, unposted_accepted_qty=0, credit_applied_qty=0,
        receipt_count=0, receipt_names=[], last_receipt_date=None,
    ) for row in rows}
    counts = {row.name: set() for row in rows}
    seen = set()
    journeys = []
    for allocation in allocations:
        receipt = receipts_by_name.get(allocation.parent)
        if not receipt:
            continue
        key = allocation.get("subcontracting_order_item")
        # Legacy allocation keys may be absent only for unambiguous single-row SCOs.
        if not key and len(rows) == 1:
            key = rows[0].name
        row = by_name.get(key)
        if not row or allocation.get("subcontracting_order") not in (None, "", sco.name):
            frappe.throw(_("An existing allocation has missing or invalid SCO item lineage."))
        if (allocation.processed_item, allocation.stock_uom) != (row.item_code, row.stock_uom):
            frappe.throw(_("An existing allocation does not match its SCO item and UOM."))
        identity = (receipt.name, key)
        if identity in seen:
            frappe.throw(_("Duplicate receipt allocations exist for the same SCO item row."))
        seen.add(identity)
        item = items[key]
        accepted = quantity(allocation.allocated_accepted_qty)
        invoiced = quantity(allocation.allocated_invoice_qty)
        if min(accepted, invoiced) < 0:
            frappe.throw(_("Negative allocation quantities are not supported in this draft checkpoint."))
        recognised = receipt.subcontracting_receipt in submitted_scrs
        if recognised:
            matches = [r for r in scr_items if r.parent == receipt.subcontracting_receipt
                and r.subcontracting_order == sco.name and r.subcontracting_order_item == key
                and (not allocation.get("subcontracting_receipt_item") or r.name == allocation.subcontracting_receipt_item)]
            if len(matches) != 1 or matches[0].item_code != row.item_code or matches[0].stock_uom != row.stock_uom or quantity(matches[0].qty) != accepted:
                frappe.throw(_("Submitted SCR evidence does not match its receipt allocation; reconcile it before proceeding."))
        else:
            item.unposted_accepted_qty = quantity(item.unposted_accepted_qty + accepted)
        item.accepted_qty = quantity(item.accepted_qty + accepted)
        item.invoice_qty = quantity(item.invoice_qty + invoiced)
        counts[key].add(receipt.name)
        day = receipt.get("physical_receipt_date")
        if day and (not item.last_receipt_date or str(day) > str(item.last_receipt_date)):
            item.last_receipt_date = day
        journeys.append(dict(processor_lot_receipt=receipt.name, receipt_item_key=allocation.get("receipt_item_key"),
            subcontracting_order_item=key, processed_item=row.item_code, stock_uom=row.stock_uom,
            accepted_qty=accepted, invoice_qty=invoiced, physical_receipt_date=day,
            subcontracting_receipt=receipt.subcontracting_receipt,
            purchase_receipt=receipt.get("purchase_receipt"), purchase_invoice=receipt.get("purchase_invoice")))
    by_identity = {(item.processed_item, item.stock_uom): item for item in items.values()}
    for credit in credits:
        item = by_identity.get((credit.processed_item, credit.processed_item_uom))
        if not item:
            frappe.throw(_("An applied credit does not match a unique SCO item and UOM."))
        credit_qty = quantity(credit.processed_qty)
        if credit_qty < 0:
            frappe.throw(_("An applied credit has a negative quantity; reconcile it before proceeding."))
        item.credit_applied_qty = quantity(item.credit_applied_qty + credit_qty)
    for key, item in items.items():
        item.receipt_names = sorted(counts[key])
        item.receipt_count = len(counts[key])
        item.invoice_vs_accepted_qty = quantity(item.invoice_qty - item.accepted_qty)
        item.previously_received_qty = quantity(item.native_received_qty + item.unposted_accepted_qty)
        item.available_qty = quantity(item.ordered_qty - item.previously_received_qty - item.credit_applied_qty)
        item.plr_balance_qty = quantity(item.ordered_qty - item.accepted_qty)
    truck_names = set().union(*counts.values()) if counts else set()
    return dict(is_multi_item=len(rows) > 1, items=list(items.values()), truck_count=len(truck_names),
        journeys=sorted(journeys, key=lambda r: (str(r["physical_receipt_date"] or ""), r["processor_lot_receipt"], r["subcontracting_order_item"])))


def position_for_lot(lot, sco, exclude_receipt=None):
    finished_rows(sco)  # Reject ambiguity before reading transaction evidence.
    return build_position(sco, *read_evidence(lot.name, sco, exclude_receipt))


@frappe.whitelist()
def get_item_position(processor_lot):
    if not cint(frappe.conf.get("v2_processor_first_draft_entry")):
        return {"enabled": False}
    lot = frappe.get_doc("Processor Lot", processor_lot)
    lot.check_permission("read")
    sco = frappe.get_doc("Subcontracting Order", lot.subcontracting_order)
    sco.check_permission("read")
    # J14 opt-in uses evidence panels for single-item lots too.
    use_item_panels = bool(cint(frappe.conf.get("v2_processor_first_material_facts")))
    if len(sco.items) <= 1 and not use_item_panels:
        return {"enabled": True, "is_multi_item": False}
    report = position_for_lot(lot, sco)
    # Aggregate lot balances include all reservations. Document references in
    # the browser, however, must respect each document's own read permissions.
    for item in report["items"]:
        item.pop("receipt_names", None)
    for field, doctype in (("processor_lot_receipt", "Processor Lot Receipt"),
                           ("subcontracting_receipt", "Subcontracting Receipt"),
                           ("purchase_receipt", "Purchase Receipt"),
                           ("purchase_invoice", "Purchase Invoice")):
        names = sorted({row[field] for row in report["journeys"] if row.get(field)})
        if not names:
            continue
        visible = set()
        if frappe.has_permission(doctype, "read"):
            visible = {row.name for row in frappe.get_list(doctype, filters={"name": ["in", names]},
                fields=["name"], limit_page_length=0)}
        for row in report["journeys"]:
            if row.get(field) and row[field] not in visible:
                row[field] = _("Restricted")
                if field == "processor_lot_receipt":
                    row["receipt_item_key"] = None
    report.update(enabled=True, processor_lot=lot.name, subcontracting_order=sco.name,
                  use_item_panels=use_item_panels)
    return report
