"""Opt-in workspace lookup and in-memory draft review. No writes here."""

import math
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

    result = {
        "warehouses": sorted(warehouses), "items": [], "warnings": warnings,
        "draft_entry_enabled": bool(cint(frappe.conf.get("v2_processor_first_draft_entry"))),
    }
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


@frappe.whitelist()
def get_entry_mode():
    """Read the site opt-in; this does not confer document permissions."""
    return {"draft_entry_enabled": bool(cint(frappe.conf.get("v2_processor_first_draft_entry")))}


HEADER_INPUTS = (
    "company", "supplier", "supplier_warehouse", "physical_receipt_date",
    "vehicle_no", "supplier_challan_number", "supplier_challan_date", "remarks",
)
ITEM_INPUTS = (
    "name", "item_key", "processed_item", "measurement_method", "measurement_basis",
    "company_accepted_qty", "supplier_invoice_qty", "remarks",
)
WEIGHMENT_INPUTS = (
    "name", "weighment_stage", "weighment_date", "weighment_time", "weighbridge",
    "slip_number", "scale_weight", "measurement_uom", "receipt_item_key",
    "adjustment_qty", "adjustment_reason", "remarks",
)


def assert_draft_unlinked(doc):
    """Do not permit J2 to modify a receipt with downstream activity."""
    if cint(doc.docstatus) != 0 or any(doc.get(field) for field in (
        "subcontracting_receipt", "purchase_receipt", "purchase_invoice",
    )):
        frappe.throw(_("J2 can edit only drafts without downstream documents."))
    if doc.is_new():
        return
    # Reverse links also protect against missing/stale header links.
    for doctype, field in (
        ("Subcontracting Receipt", "custom_processor_lot_receipt"),
        ("Processor Material Account Entry", "processor_lot_receipt"),
    ):
        if frappe.db.exists(doctype, {field: doc.name, "docstatus": ["!=", 2]}):
            frappe.throw(_("This receipt already has downstream activity; J2 cannot edit it."))


def _input_rows(rows, fields):
    if not isinstance(rows, list) or len(rows) > 200:
        frappe.throw(_("Expected at most 200 input rows."))
    result = []
    for row in rows:
        if not isinstance(row, dict):
            frappe.throw(_("Invalid receipt input row."))
        result.append({field: row.get(field) for field in fields if field in row})
    return result


def _check_quantities(items, weighments):
    for rows, fields in (
        (items, ("company_accepted_qty", "supplier_invoice_qty")),
        (weighments, ("scale_weight", "adjustment_qty")),
    ):
        for row in rows:
            for field in fields:
                try:
                    number = float(row.get(field) or 0)
                except (ValueError, TypeError):
                    frappe.throw(_("Quantities must be numeric."))
                if not math.isfinite(number):
                    frappe.throw(_("Quantities must be finite numbers."))
                if field != "adjustment_qty" and number < 0:
                    frappe.throw(_("Quantities cannot be negative."))


@frappe.whitelist()
def review_draft(payload):
    """Recalculate FIFO in memory from whitelisted user inputs; never save.

    Existing receipt identity/context is checked before applying inputs.
    Save subsequently runs the controller again against fresh lot capacity.
    """
    if not cint(frappe.conf.get("v2_processor_first_draft_entry")):
        frappe.throw(_("Processor-first draft entry is not enabled on this site."))
    data = frappe.parse_json(payload) if isinstance(payload, str) else payload
    if not isinstance(data, dict):
        frappe.throw(_("Invalid draft payload."))
    if data.get("__v2_entry_preview"):
        frappe.throw(_("Workspace previews cannot be saved; reopen from Receive from Processor."))

    name = data.get("name")
    if name:
        doc = frappe.get_doc("Processor Lot Receipt", name)
        doc.check_permission("write")
        if doc.receipt_structure_version != "V2 Itemized" or doc.processor_lot:
            frappe.throw(_("Only processor-first V2 drafts can use this action."))
        if str(doc.modified) != str(data.get("modified")):
            frappe.throw(_("This receipt changed since it was opened. Reload before reviewing allocations."))
        for field in ("company", "supplier", "supplier_warehouse"):
            if doc.get(field) != data.get(field):
                frappe.throw(_("Saved processor context cannot be changed."))
    else:
        if not frappe.has_permission("Processor Lot Receipt", "create"):
            frappe.throw(_("Not permitted to create Processor Lot Receipts."), frappe.PermissionError)
        doc = frappe.get_doc({"doctype": "Processor Lot Receipt", "__islocal": 1,
            "receipt_structure_version": "V2 Itemized", "docstatus": 0})

    assert_draft_unlinked(doc)
    items = _input_rows(data.get("receipt_items"), ITEM_INPUTS)
    weighments = _input_rows(data.get("item_weighments", []), WEIGHMENT_INPUTS)
    _check_quantities(items, weighments)
    if name:
        old_items = {row.name: (row.item_key, row.processed_item) for row in doc.receipt_items}
        if len(items) != len(old_items) or {
            row.get("name"): (row.get("item_key"), row.get("processed_item")) for row in items
        } != old_items:
            frappe.throw(_("J2 keeps the selected items and their keys fixed after the first save."))
        old_weighments = {row.name for row in doc.item_weighments}
        names = [row.get("name") for row in weighments if row.get("name")]
        if len(names) != len(set(names)) or set(names) - old_weighments:
            frappe.throw(_("Invalid saved weighment row identity."))
    for row in weighments:
        if row.get("weighment_stage") not in ("Arrival Loaded", "After Unloading"):
            frappe.throw(_("J2 supports Arrival Loaded and After Unloading readings only."))
        if not row.get("weighment_date") or not row.get("measurement_uom"):
            frappe.throw(_("Every weighment needs a Date and Scale UOM."))
        if flt(row.get("adjustment_qty")) and not (row.get("adjustment_reason") or "").strip():
            frappe.throw(_("Explain every non-zero weighment adjustment."))
    for field in HEADER_INPUTS:
        doc.set(field, data.get(field))
    for field, rows in (("receipt_items", items), ("item_weighments", weighments)):
        if not name:
            for row in rows:
                row.pop("name", None)
        doc.set(field, rows)
    doc.set("lot_allocations", [])
    if not doc.physical_receipt_date:
        frappe.throw(_("Physical Receipt Date is required."))
    # No lifecycle update/insert hooks, fixture generation or database writes.
    doc.before_validate()
    doc.validate()
    return {field: [row.as_dict() for row in doc.get(field)] for field in (
        "receipt_items", "item_weighments", "lot_allocations",
    )}
