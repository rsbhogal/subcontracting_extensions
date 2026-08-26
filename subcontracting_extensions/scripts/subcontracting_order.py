# Copyright (c) 2026, R S Bhogal and contributors
# For license information, please see license.txt

"""
Subcontracting Order workflow integration.

A submitted Subcontracting Order must have exactly one Processor Lot.
The Processor Lot becomes the operational control document for all subsequent
truck receipts and final lot reconciliation.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

def validate_reserve_warehouses(
    doc: Document,
    method: str | None = None,
) -> None:
    """
    Validate the row-level Reserve Warehouse of every supplied component.

    The SCO header field "Set Reserve Warehouse" is only a bulk-fill
    convenience. The authoritative source warehouse is the
    reserve_warehouse stored on each supplied-item row.

    This validation does not automatically change warehouses. Where the
    selected warehouse lacks sufficient stock, it reports warehouses that
    currently hold enough stock for that specific component.
    """
    if doc.doctype != "Subcontracting Order":
        return

    for row in doc.get("supplied_items") or []:
        _validate_supplied_item_reserve_warehouse(
            doc=doc,
            row=row,
        )


def _validate_supplied_item_reserve_warehouse(
    doc: Document,
    row: Document,
) -> None:
    """Validate one supplied component's selected Reserve Warehouse."""
    item_code = row.rm_item_code
    reserve_warehouse = row.reserve_warehouse
    required_qty = flt(row.required_qty)

    if not item_code:
        return

    if not reserve_warehouse:
        frappe.throw(
            _(
                "Row {0}: Reserve Warehouse is required for supplied "
                "component {1}."
            ).format(
                row.idx,
                frappe.bold(item_code),
            ),
            title=_("Reserve Warehouse Required"),
        )

    warehouse = frappe.db.get_value(
        "Warehouse",
        reserve_warehouse,
        [
            "company",
            "is_group",
            "disabled",
        ],
        as_dict=True,
    )

    if not warehouse:
        frappe.throw(
            _(
                "Row {0}: Reserve Warehouse {1} does not exist."
            ).format(
                row.idx,
                frappe.bold(reserve_warehouse),
            ),
            title=_("Invalid Reserve Warehouse"),
        )

    if warehouse.company != doc.company:
        frappe.throw(
            _(
                "Row {0}: Reserve Warehouse {1} belongs to company {2}, "
                "not {3}."
            ).format(
                row.idx,
                frappe.bold(reserve_warehouse),
                frappe.bold(warehouse.company),
                frappe.bold(doc.company),
            ),
            title=_("Invalid Reserve Warehouse"),
        )

    if warehouse.is_group:
        frappe.throw(
            _(
                "Row {0}: Reserve Warehouse {1} is a group warehouse "
                "and cannot issue stock."
            ).format(
                row.idx,
                frappe.bold(reserve_warehouse),
            ),
            title=_("Invalid Reserve Warehouse"),
        )

    if warehouse.disabled:
        frappe.throw(
            _(
                "Row {0}: Reserve Warehouse {1} is disabled."
            ).format(
                row.idx,
                frappe.bold(reserve_warehouse),
            ),
            title=_("Invalid Reserve Warehouse"),
        )

    if reserve_warehouse == doc.supplier_warehouse:
        frappe.throw(
            _(
                "Row {0}: Reserve Warehouse must differ from Supplier "
                "Warehouse for supplied component {1}."
            ).format(
                row.idx,
                frappe.bold(item_code),
            ),
            title=_("Invalid Reserve Warehouse"),
        )

    available_qty = flt(
        frappe.db.get_value(
            "Bin",
            {
                "item_code": item_code,
                "warehouse": reserve_warehouse,
            },
            "actual_qty",
        )
    )

    if available_qty >= required_qty:
        return

    alternative_bins = frappe.db.sql(
        """
        SELECT
            bin.warehouse,
            bin.actual_qty
        FROM `tabBin` bin
        INNER JOIN `tabWarehouse` warehouse
            ON warehouse.name = bin.warehouse
        WHERE
            bin.item_code = %(item_code)s
            AND bin.actual_qty >= %(required_qty)s
            AND warehouse.company = %(company)s
            AND warehouse.is_group = 0
            AND warehouse.disabled = 0
            AND bin.warehouse != %(supplier_warehouse)s
        ORDER BY
            bin.actual_qty DESC,
            bin.warehouse ASC
        """,
        {
            "item_code": item_code,
            "required_qty": required_qty,
            "company": doc.company,
            "supplier_warehouse":
                doc.supplier_warehouse or "",
        },
        as_dict=True,
    )

    suggestion = ""

    if alternative_bins:
        alternatives = ", ".join(
            _("{0} ({1})").format(
                frappe.bold(bin_row.warehouse),
                frappe.bold(
                    _("{0} available").format(
                        flt(bin_row.actual_qty)
                    )
                ),
            )
            for bin_row in alternative_bins
        )

        suggestion = _(
            " Warehouse(s) currently holding sufficient stock: {0}."
        ).format(alternatives)

    frappe.throw(
        _(
            "Row {0}: Supplied component {1} requires {2} {3}, but "
            "Reserve Warehouse {4} has only {5} {3}.{6}"
        ).format(
            row.idx,
            frappe.bold(item_code),
            frappe.bold(required_qty),
            row.stock_uom or "",
            frappe.bold(reserve_warehouse),
            frappe.bold(available_qty),
            suggestion,
        ),
        title=_("Reserve Warehouse Requires Correction"),
    )

def apply_processing_routes(
    doc: Document,
    method: str | None = None,
) -> None:
    """
    Apply each source PO row's selected Processing Route to the Draft SCO.

    The route-selected Manufacturing BOM overrides ERPNext's default-BOM
    choice on the corresponding Subcontracting Order finished-item row.

    After applying all route BOMs, rebuild the supplied-item rows from
    those BOMs.
    """
    if doc.doctype != "Subcontracting Order":
        return

    if doc.docstatus != 0:
        return

    for row in doc.get("items") or []:
        if not row.purchase_order_item:
            continue

        po_item = frappe.db.get_value(
            "Purchase Order Item",
            row.purchase_order_item,
            [
                "custom_processing_route",
                "item_code",
                "fg_item",
            ],
            as_dict=True,
        )

        if not po_item or not po_item.custom_processing_route:
            continue

        route = frappe.db.get_value(
            "Subcontracting Route",
            po_item.custom_processing_route,
            [
                "is_active",
                "finished_item",
                "service_item",
                "manufacturing_bom",
            ],
            as_dict=True,
        )

        if not route or not route.is_active:
            frappe.throw(
                _(
                    "Processing Route {0} linked to Purchase Order Item {1} "
                    "is missing or inactive."
                ).format(
                    frappe.bold(po_item.custom_processing_route),
                    frappe.bold(row.purchase_order_item),
                )
            )

        if route.finished_item != row.item_code:
            frappe.throw(
                _(
                    "Processing Route {0} is for Finished Item {1}, "
                    "but SCO row {2} contains {3}."
                ).format(
                    frappe.bold(po_item.custom_processing_route),
                    frappe.bold(route.finished_item),
                    row.idx,
                    frappe.bold(row.item_code),
                )
            )

        if route.service_item != po_item.item_code:
            frappe.throw(
                _(
                    "Processing Route {0} is for Service Item {1}, "
                    "but Purchase Order Item {2} contains {3}."
                ).format(
                    frappe.bold(po_item.custom_processing_route),
                    frappe.bold(route.service_item),
                    frappe.bold(row.purchase_order_item),
                    frappe.bold(po_item.item_code),
                )
            )

        row.bom = route.manufacturing_bom

    # Mapped, unsaved SCO child rows may not yet have names. ERPNext uses
    # the finished-row name as supplied_item.reference_name, so assign a
    # temporary local name before regenerating supplied materials.
    for row in doc.get("items") or []:
        if not row.name:
            row.name = frappe.generate_hash(length=10)

        if not flt(getattr(row, "conversion_factor", None)):
            row.conversion_factor = 1.0


def _apply_route_component_warehouses(
    doc: Document,
) -> None:
    """
    Apply Route Component source warehouses to SCO supplied-item rows.

    Matching chain:
        SCO supplied row.reference_name
        → SCO finished-item row.name
        → Purchase Order Item
        → Processing Route
        → Route Component BOM Detail
    """
    finished_rows_by_name = {
        row.name: row
        for row in doc.get("items") or []
        if row.name
    }

    route_warehouses_by_finished_row = {}

    for finished_row in doc.get("items") or []:
        purchase_order_item = finished_row.purchase_order_item

        if not purchase_order_item:
            continue

        processing_route = frappe.db.get_value(
            "Purchase Order Item",
            purchase_order_item,
            "custom_processing_route",
        )

        if not processing_route:
            frappe.throw(
                _(
                    "Purchase Order Item {0} has no Processing Route."
                ).format(
                    frappe.bold(purchase_order_item),
                ),
                title=_("Processing Route Missing"),
            )

        route = frappe.get_doc(
            "Subcontracting Route",
            processing_route,
        )

        route_warehouses_by_finished_row[finished_row.name] = {
            component.bom_detail: component.source_warehouse
            for component in route.get("route_components") or []
        }

    for supplied_row in doc.get("supplied_items") or []:
        reference_name = supplied_row.reference_name

        finished_row = finished_rows_by_name.get(
            reference_name
        )

        if not finished_row:
            frappe.throw(
                _(
                    "Unable to identify the SCO finished-item row for "
                    "supplied component {0}. Reference Name: {1}."
                ).format(
                    frappe.bold(supplied_row.rm_item_code),
                    frappe.bold(reference_name or ""),
                ),
                title=_("Route Component Mapping Failed"),
            )

        route_warehouses = (
            route_warehouses_by_finished_row.get(
                finished_row.name,
                {},
            )
        )

        source_warehouse = route_warehouses.get(
            supplied_row.bom_detail_no
        )

        if not source_warehouse:
            frappe.throw(
                _(
                    "No Source Warehouse is configured for supplied "
                    "component {0}, BOM Detail {1}, against Processing "
                    "Route selected for Purchase Order Item {2}."
                ).format(
                    frappe.bold(supplied_row.rm_item_code),
                    frappe.bold(
                        supplied_row.bom_detail_no or ""
                    ),
                    frappe.bold(
                        finished_row.purchase_order_item or ""
                    ),
                ),
                title=_("Route Component Warehouse Missing"),
            )

        supplied_row.reserve_warehouse = source_warehouse

def apply_processing_route_warehouses(
    doc: Document,
    method: str | None = None,
) -> None:
    """
    Apply route-defined Source Warehouses after ERPNext has regenerated
    the SCO supplied-item rows during standard validation.
    """
    if doc.doctype != "Subcontracting Order":
        return

    if doc.docstatus == 2:
        return

    _apply_route_component_warehouses(doc)

def ensure_processor_lot(
    doc: Document,
    method: str | None = None,
) -> str:
    """
    Return the Processor Lot belonging to the submitted SCO.

    Create it when none exists. The function is intentionally idempotent so
    retries cannot create another Processor Lot for the same SCO.
    """
    if doc.doctype != "Subcontracting Order":
        return ""

    if doc.docstatus != 1:
        return ""

    existing_lot = frappe.db.get_value(
        "Processor Lot",
        {"subcontracting_order": doc.name},
        "name",
    )

    if existing_lot:
        return existing_lot

    processor_lot = frappe.get_doc(
        {
            "doctype": "Processor Lot",
            "subcontracting_order": doc.name,
        }
    )

    # This is a system-driven document required by the SCO workflow.
    processor_lot.insert(ignore_permissions=True)

    frappe.msgprint(
        _("Processor Lot {0} has been created automatically.").format(
            frappe.utils.get_link_to_form(
                "Processor Lot",
                processor_lot.name,
            )
        ),
        title=_("Processor Lot Created"),
        indicator="green",
    )

    return processor_lot.name