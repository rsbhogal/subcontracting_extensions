"""Read-only, opt-in workspace trial. No inserts, saves or allocations."""

import frappe
from frappe import _
from frappe.utils import cint, flt


@frappe.whitelist()
def get_candidates(company, supplier, supplier_warehouse=None):
    if not cint(frappe.conf.get("v2_receipt_entry_preview")):
        frappe.throw(_("Receipt entry preview is not enabled on this site."))
    if not frappe.has_permission("Processor Lot Receipt", "create"):
        frappe.throw(_("Not permitted to create Processor Lot Receipts."), frappe.PermissionError)
    for doctype, name in (("Company", company), ("Supplier", supplier)):
        if not name:
            frappe.throw(_("Company and Processor are required."))
        frappe.get_doc(doctype, name).check_permission("read")

    # get_list intentionally respects user permissions; do not replace with get_all.
    lots = frappe.get_list(
        "Processor Lot",
        filters={"company": company, "supplier": supplier, "docstatus": 0},
        fields=["name", "supplier_warehouse", "subcontracting_order", "settlement_status"],
        limit_page_length=0,
    )
    eligible = {}
    warehouses = set()
    item_uoms = set()
    warnings = []
    for lot in lots:
        if lot.settlement_status in ("Completed", "Debit Note Created"):
            continue
        if not lot.subcontracting_order or not lot.supplier_warehouse:
            continue
        warehouse = frappe.get_doc("Warehouse", lot.supplier_warehouse)
        if not warehouse.has_permission("read") or warehouse.company != company:
            continue
        if warehouse.get("disabled") or warehouse.get("is_group"):
            continue
        sco = frappe.get_doc("Subcontracting Order", lot.subcontracting_order)
        if not sco.has_permission("read"):
            continue
        if sco.docstatus != 1 or sco.status in ("Closed", "Completed", "Cancelled"):
            continue
        if sco.company != company or sco.supplier != supplier:
            continue
        if sco.supplier_warehouse != lot.supplier_warehouse:
            continue
        warehouses.add(lot.supplier_warehouse)
        if lot.supplier_warehouse != supplier_warehouse:
            continue
        eligible[lot.name] = sco.name
        for row in sco.items:
            item = frappe.get_doc("Item", row.item_code)
            if item.has_permission("read") and not item.get("disabled"):
                item_uoms.add((row.item_code, row.stock_uom))

    result = {"warehouses": sorted(warehouses), "items": [], "warnings": warnings}
    if not supplier_warehouse:
        return result
    frappe.get_doc("Warehouse", supplier_warehouse).check_permission("read")
    if supplier_warehouse not in warehouses:
        warnings.append(_("No accessible open lots match this processor and warehouse."))
        return result

    # Construct an in-memory controller only; validation and save are NOT called.
    plr = frappe.get_doc({
        "doctype": "Processor Lot Receipt", "__islocal": 1,
        "receipt_structure_version": "V2 Itemized", "company": company,
        "supplier": supplier, "supplier_warehouse": supplier_warehouse,
    })
    for item_code, stock_uom in sorted(item_uoms):
        try:
            candidates = plr._get_v2_fifo_candidates(frappe._dict(
                processed_item=item_code, stock_uom=stock_uom,
            ))
        except frappe.ValidationError:
            # Do not leak references from inaccessible lots through helper errors.
            warnings.append(_("Cannot safely preview {0}: ambiguous or invalid source rows.").format(item_code))
            continue
        candidates = [row for row in candidates if row.processor_lot in eligible]
        if candidates:
            result["items"].append({
                "processed_item": item_code, "stock_uom": stock_uom,
                "available_qty": flt(sum(row.available_qty for row in candidates), 6),
                "lots": candidates,
            })
    return result
