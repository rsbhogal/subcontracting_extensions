"""J11 read-only completion evidence; never authorizes settlement or posting.

J4 capacity/reservation arithmetic remains unchanged. Completion is deliberately
conservative: native SCO receipts outside the linked allocation evidence are
reported as unverified, not inferred from PO-wide billing percentages.
"""

from copy import deepcopy

import frappe
from frappe.utils import cint

from subcontracting_extensions.receipt_item_position import (
    build_position,
    quantity,
    read_evidence,
)


def build_completion(sco, evidence, documents):
    """Pure report builder. documents is keyed by (DocType, name)."""
    allocations, receipts, submitted_scrs, scr_items, credits = evidence
    report = deepcopy(build_position(sco, *evidence))
    items = {row.subcontracting_order_item: row for row in report["items"]}
    sources = {row.name: row for row in sco.items}
    parents = {row.name: row for row in receipts if row.docstatus != 2}
    for item in items.values():
        item.update(
            allocated_accepted_qty=item.accepted_qty,
            allocated_invoice_qty=item.invoice_qty,
            reserved_accepted_qty=item.unposted_accepted_qty,
            submitted_scr_qty=0,
            submitted_pr_qty=0,
            submitted_pi_qty=0,
            issues=[],
        )

    journeys = []
    for allocation in allocations:
        receipt = parents.get(allocation.parent)
        if receipt is None:
            continue
        key = allocation.get("subcontracting_order_item")
        if not key and len(sources) == 1:
            key = next(iter(sources))
        source = sources[key]
        item = items[key]
        journey = dict(
            processor_lot_receipt=receipt.name,
            receipt_item_key=allocation.get("receipt_item_key"),
            subcontracting_order_item=key,
            processed_item=source.item_code,
            stock_uom=source.stock_uom,
            allocated_accepted_qty=quantity(allocation.allocated_accepted_qty),
            allocated_invoice_qty=quantity(allocation.allocated_invoice_qty),
            subcontracting_receipt=receipt.get("subcontracting_receipt"),
            purchase_receipt=receipt.get("purchase_receipt"),
            purchase_invoice=receipt.get("purchase_invoice"),
            scr_verified=False, pr_verified=False, pi_verified=False,
            issues=[],
        )
        journeys.append(journey)

        def issue(code):
            journey["issues"].append(code)

        def submitted(doctype, name):
            doc = documents.get((doctype, name))
            if not doc or doc.get("docstatus") != 1:
                return None
            if doc.get("is_return"):
                return None
            if (doc.get("company"), doc.get("supplier")) != (
                sco.get("company"), sco.get("supplier")
            ):
                return None
            return doc

        scr = submitted("Subcontracting Receipt", journey["subcontracting_receipt"])
        scr_detail = allocation.get("subcontracting_receipt_item")
        if not scr:
            issue("SCR_NOT_SUBMITTED_OR_HEADER_MISMATCH")
            continue
        matches = [row for row in scr.get("items", []) if row.name == scr_detail]
        if (len(matches) != 1
                or scr.get("custom_processor_lot_receipt") != receipt.name
                or matches[0].get("subcontracting_order") != sco.name
                or matches[0].get("subcontracting_order_item") != key
                or matches[0].get("item_code") != source.item_code
                or matches[0].get("stock_uom") != source.stock_uom
                or quantity(matches[0].get("qty")) != journey["allocated_accepted_qty"]):
            issue("SCR_ROW_LINEAGE_MISMATCH")
            continue
        journey["scr_verified"] = True
        item.submitted_scr_qty = quantity(item.submitted_scr_qty + journey["allocated_accepted_qty"])

        po = documents.get(("Purchase Order", sco.get("purchase_order")))
        po_detail = source.get("purchase_order_item")
        po_rows = [row for row in (po.get("items", []) if po else []) if row.name == po_detail]
        if (not po or po.get("docstatus") != 1 or len(po_rows) != 1
                or (po.get("company"), po.get("supplier")) != (sco.get("company"), sco.get("supplier"))
                or allocation.get("purchase_order") != sco.get("purchase_order")
                or allocation.get("purchase_order_item") != po_detail
                or po_rows[0].get("stock_uom") != source.stock_uom):
            issue("PO_ROW_LINEAGE_MISMATCH")
            continue
        service = po_rows[0].get("item_code")
        pr = submitted("Purchase Receipt", journey["purchase_receipt"])
        if not pr:
            issue("PR_NOT_SUBMITTED_OR_HEADER_MISMATCH")
            continue
        pr_rows = [row for row in pr.get("items", [])
                   if row.get("subcontracting_receipt_item") == scr_detail]
        if (pr.get("subcontracting_receipt") != scr.name or len(pr_rows) != 1
                or not _commercial_row(pr_rows[0], sco.purchase_order, po_detail,
                                       service, source.stock_uom,
                                       journey["allocated_invoice_qty"], "purchase_order_item")):
            issue("PR_ROW_LINEAGE_MISMATCH")
            continue
        pr_row = pr_rows[0]
        journey["pr_verified"] = True
        journey["pr_detail"] = pr_row.name
        item.submitted_pr_qty = quantity(item.submitted_pr_qty + journey["allocated_invoice_qty"])

        pi = submitted("Purchase Invoice", journey["purchase_invoice"])
        if not pi:
            issue("PI_NOT_SUBMITTED_OR_HEADER_MISMATCH")
            continue
        pi_rows = [row for row in pi.get("items", []) if row.get("pr_detail") == pr_row.name]
        if (pi.get("update_stock") or len(pi_rows) != 1
                or pi_rows[0].get("purchase_receipt") != pr.name
                or not _commercial_row(pi_rows[0], sco.purchase_order, po_detail,
                                       service, source.stock_uom,
                                       journey["allocated_invoice_qty"], "po_detail")):
            issue("PI_ROW_LINEAGE_MISMATCH")
            continue
        journey["pi_verified"] = True
        journey["pi_detail"] = pi_rows[0].name
        item.submitted_pi_qty = quantity(item.submitted_pi_qty + journey["allocated_invoice_qty"])

    for key, item in items.items():
        own = [row for row in journeys if row["subcontracting_order_item"] == key]
        item.native_receipts_without_verified_allocation_qty = quantity(
            item.native_received_qty - item.submitted_scr_qty
        )
        item.excess_reserved_qty = quantity(max(-item.available_qty, 0))
        if item.reserved_accepted_qty:
            item.issues.append("UNPOSTED_RESERVATIONS")
        if item.excess_reserved_qty:
            item.issues.append("EXCESS_CAPACITY_COMMITMENT")
        if item.native_receipts_without_verified_allocation_qty:
            item.issues.append("NATIVE_RECEIPTS_NOT_RECONCILED_TO_ALLOCATIONS")
        if quantity(sources[key].get("returned_qty")):
            item.issues.append("RETURNS_REQUIRE_REVIEW")
        if item.credit_applied_qty:
            item.issues.append("MATERIAL_CREDIT_REQUIRES_REVIEW")
        if item.native_received_qty != item.ordered_qty:
            item.issues.append("ORDER_QUANTITY_NOT_FULLY_RECEIVED")
        if not own or any(not row["pi_verified"] for row in own):
            item.issues.append("JOURNEY_NOT_FULLY_VERIFIED")
        item.journey_complete = not item.issues
        # Completion of invoicing is not approval of a quantity variance.
        item.submitted_invoice_vs_accepted_qty = quantity(item.submitted_pi_qty - item.submitted_scr_qty)

    report.update(
        journeys=journeys,
        journey_complete=bool(items) and all(row.journey_complete for row in items.values()),
        settlement_enabled=False,
        evidence_scope="Linked allocation rows only; not a settlement authorization",
    )
    return report


def _commercial_row(row, po, detail, service, uom, qty, detail_field):
    return (row.get("purchase_order") == po
            and row.get(detail_field) == detail
            and row.get("item_code") == service
            and row.get("stock_uom") == uom
            and quantity(row.get("stock_qty")) == qty)


@frappe.whitelist()
def get_completion_position(processor_lot):
    """Opt-in diagnostic. Requires read access to every evidence document.

    Unlike the J4 UI (which redacts links), this diagnostic fails closed if any
    document is inaccessible. No hidden document identities are returned.
    """
    if not cint(frappe.conf.get("v2_processor_first_completion_facts")):
        return {"enabled": False}
    lot = frappe.get_doc("Processor Lot", processor_lot)
    lot.check_permission("read")
    sco = frappe.get_doc("Subcontracting Order", lot.subcontracting_order)
    sco.check_permission("read")
    if lot.docstatus == 2 or sco.docstatus != 1:
        frappe.throw("Completion evidence requires an active Processor Lot and submitted SCO.")
    evidence = read_evidence(lot.name, sco)
    allocations, receipts = evidence[:2]
    documents = {}
    targets = {("Purchase Order", sco.purchase_order)}
    for receipt in receipts:
        targets.add(("Processor Lot Receipt", receipt.name))
        for doctype, field in (("Subcontracting Receipt", "subcontracting_receipt"),
                               ("Purchase Receipt", "purchase_receipt"),
                               ("Purchase Invoice", "purchase_invoice")):
            if receipt.get(field):
                targets.add((doctype, receipt.get(field)))
    for doctype, name in sorted(targets):
        doc = frappe.get_doc(doctype, name)
        doc.check_permission("read")
        documents[(doctype, name)] = doc
    # J4 reads omit commercial allocation keys. Read them only for J11;
    # do not change the existing FIFO evidence tuple or capacity contract.
    keys = frappe.get_all("Processor Lot Receipt Allocation", filters={
        "processor_lot": lot.name, "parenttype": "Processor Lot Receipt",
        "parentfield": "lot_allocations", "docstatus": ["!=", 2]},
        fields=["parent", "receipt_item_key", "subcontracting_order_item",
                "purchase_order", "purchase_order_item"])
    by_key = {(row.parent, row.receipt_item_key, row.subcontracting_order_item): row for row in keys}
    for allocation in allocations:
        key = (allocation.parent, allocation.receipt_item_key, allocation.subcontracting_order_item)
        lineage = by_key.get(key)
        if lineage:
            allocation.update(purchase_order=lineage.purchase_order, purchase_order_item=lineage.purchase_order_item)
    report = build_completion(sco, evidence, documents)
    report.update(enabled=True, processor_lot=lot.name, subcontracting_order=sco.name)
    return report
