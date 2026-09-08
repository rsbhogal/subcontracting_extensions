# Copyright (c) 2026, R S Bhogal and contributors
# For license information, please see license.txt

"""
Purchase Order business rules for Bhogals.

This module contains Purchase Order validations that are specific to
Bhogals' business processes and are independent of Activity Based Costing.

Current responsibilities
------------------------
- Enforce the single-item policy for subcontracted Purchase Orders.
"""

import frappe
from frappe import _
from frappe.utils import cint

from subcontracting_extensions.settlement_method_policy import (
    SettlementMethodPolicyError,
    get_default_method,
    get_method_contract,
)


def validate(doc, method=None):
    """
    Validate Bhogals-specific Purchase Order business rules.

    This function is called through the Purchase Order ``validate``
    document event configured in ``hooks.py``.
    """
    validate_subcontracted_purchase_order_item_count(doc)
    validate_subcontracting_routes(doc)
    validate_commercial_settlement_defaults(doc)


def _settlement_method_rows():
    return frappe.get_single("Subcontracting Settlement Settings").get(
        "allowed_settlement_methods"
    ) or []


def _value(doc, fieldname):
    return doc.get(fieldname) if hasattr(doc, "get") else getattr(doc, fieldname, None)


def validate_commercial_settlement_defaults(
    doc,
    *,
    method_rows=None,
    db_get_value=None,
    throw=None,
):
    """Default and validate J19B1B policy without authorizing execution."""
    if not doc.is_subcontracted:
        return
    rows = _settlement_method_rows() if method_rows is None else method_rows
    read_value = db_get_value or frappe.db.get_value
    reject = throw or frappe.throw
    try:
        if not _value(doc, "custom_shortage_settlement_method"):
            doc.custom_shortage_settlement_method = get_default_method(
                rows, "Shortage"
            )["method_code"]
        if not _value(doc, "custom_excess_settlement_method"):
            doc.custom_excess_settlement_method = get_default_method(
                rows, "Excess"
            )["method_code"]
        shortage = get_method_contract(
            rows, doc.custom_shortage_settlement_method, "Shortage"
        )
        get_method_contract(rows, doc.custom_excess_settlement_method, "Excess")
    except SettlementMethodPolicyError as error:
        reject(_(str(error)), title=_("Invalid Settlement Method"))

    bound_customer = (
        read_value("Supplier", doc.supplier, "custom_recovery_customer")
        if doc.supplier else None
    )
    selected_customer = _value(doc, "custom_recovery_customer")
    if not selected_customer and bound_customer:
        doc.custom_recovery_customer = bound_customer
        selected_customer = bound_customer
    if selected_customer and selected_customer != bound_customer:
        reject(
            _("Recovery Customer must match the authoritative Customer bound to Supplier {0}.").format(
                frappe.bold(doc.supplier)
            ),
            title=_("Recovery Customer Mismatch"),
        )
    if shortage["requires_customer"] and not selected_customer:
        reject(
            _("Recovery Customer is required for Sales Invoice settlement treatment."),
            title=_("Recovery Customer Required"),
        )
    if selected_customer:
        disabled = read_value("Customer", selected_customer, "disabled")
        if disabled is None:
            reject(_("Recovery Customer {0} does not exist.").format(
                frappe.bold(selected_customer)
            ))
        if cint(disabled):
            reject(_("Recovery Customer {0} is disabled.").format(
                frappe.bold(selected_customer)
            ))

def validate_subcontracting_routes(doc):
    """
    Validate the Processing Route selected on every subcontracted PO row.

    The selected route must be active and must match the row's Service Item
    and Finished Good. Normal Purchase Orders are unaffected.
    """
    if not doc.is_subcontracted:
        return

    for row in doc.items or []:
        route_name = row.get("custom_processing_route")

        if not route_name:
            frappe.throw(
                _(
                    "Row {0}: Processing Route is required for subcontracted "
                    "Service Item {1} and Finished Good {2}."
                ).format(
                    row.idx,
                    frappe.bold(row.item_code),
                    frappe.bold(row.fg_item or ""),
                ),
                title=_("Processing Route Required"),
            )

        route = frappe.db.get_value(
            "Subcontracting Route",
            route_name,
            [
                "is_active",
                "finished_item",
                "service_item",
                "manufacturing_bom",
                "finished_goods_target_warehouse",
            ],
            as_dict=True,
        )

        if not route:
            frappe.throw(
                _("Row {0}: Processing Route {1} does not exist.").format(
                    row.idx,
                    frappe.bold(route_name),
                ),
                title=_("Invalid Processing Route"),
            )

        if not route.is_active:
            frappe.throw(
                _("Row {0}: Processing Route {1} is inactive.").format(
                    row.idx,
                    frappe.bold(route_name),
                ),
                title=_("Inactive Processing Route"),
            )

        if route.finished_item != row.fg_item:
            frappe.throw(
                _(
                    "Row {0}: Processing Route {1} is for Finished Item {2}, "
                    "not {3}."
                ).format(
                    row.idx,
                    frappe.bold(route_name),
                    frappe.bold(route.finished_item),
                    frappe.bold(row.fg_item or ""),
                ),
                title=_("Processing Route Mismatch"),
            )

        if route.service_item != row.item_code:
            frappe.throw(
                _(
                    "Row {0}: Processing Route {1} is for Service Item {2}, "
                    "not {3}."
                ).format(
                    row.idx,
                    frappe.bold(route_name),
                    frappe.bold(route.service_item),
                    frappe.bold(row.item_code),
                ),
                title=_("Processing Route Mismatch"),
            )

        if cint(frappe.conf.get("v2_processor_first_draft_entry")):
            row.warehouse = _get_v2_route_target_warehouse(
                route_name=route_name,
                company=doc.company,
                route=route,
            )


def _get_v2_route_target_warehouse(route_name, company, route=None):
    """Return one controlled V2 route destination after company checks."""
    route = route or frappe.db.get_value(
        "Subcontracting Route",
        route_name,
        ["is_active", "finished_goods_target_warehouse"],
        as_dict=True,
    )
    warehouse_name = route and route.get("finished_goods_target_warehouse")

    if not warehouse_name:
        frappe.throw(
            _("Processing Route {0} has no Finished Goods Target Warehouse.").format(
                frappe.bold(route_name)
            ),
            title=_("Route Target Warehouse Required"),
        )

    warehouse = frappe.db.get_value(
        "Warehouse",
        warehouse_name,
        ["company", "is_group", "disabled"],
        as_dict=True,
    )
    if not warehouse:
        frappe.throw(_("Target Warehouse {0} does not exist.").format(
            frappe.bold(warehouse_name)
        ))
    if warehouse.company != company:
        frappe.throw(_("Target Warehouse {0} belongs to {1}, not PO Company {2}.").format(
            frappe.bold(warehouse_name), frappe.bold(warehouse.company), frappe.bold(company)
        ))
    if warehouse.is_group or warehouse.disabled:
        frappe.throw(_("Target Warehouse {0} must be an enabled leaf warehouse.").format(
            frappe.bold(warehouse_name)
        ))
    return warehouse_name


@frappe.whitelist()
def get_v2_route_targets(route_names, company):
    """Permission-aware browser hint; PO validation remains authoritative."""
    if not cint(frappe.conf.get("v2_processor_first_draft_entry")):
        return {"enabled": False, "targets": {}}
    names = frappe.parse_json(route_names) if isinstance(route_names, str) else route_names
    if not isinstance(names, list) or len(names) > 200:
        frappe.throw(_("Expected at most 200 Processing Routes."))
    targets = {}
    for route_name in dict.fromkeys(name for name in names if name):
        route_doc = frappe.get_doc("Subcontracting Route", route_name)
        route_doc.check_permission("read")
        targets[route_name] = _get_v2_route_target_warehouse(
            route_name, company, route_doc
        )
    return {"enabled": True, "targets": targets}

def validate_subcontracted_purchase_order_item_count(doc):
    """
    Enforce the Bhogals single-item subcontracting policy.

    A subcontracted Purchase Order represents one entrusted material and
    one processing operation. The related Subcontracting Order, Processor
    Lot, receipt journey and settlement therefore operate as one processing
    stream.

    Normal Purchase Orders are unaffected.
    """

    if not doc.is_subcontracted:
        return

    item_count = len(doc.items or [])

    if item_count <= 1:
        return

    if cint(frappe.conf.get("v2_processor_first_draft_entry")):
        seen_finished_rows = set()

        for row in doc.items or []:
            identity = (
                row.get("fg_item"),
                row.get("stock_uom"),
            )

            if identity in seen_finished_rows:
                frappe.throw(
                    _(
                        "Rows contain the Finished Item {0} more than once "
                        "with Stock UOM {1}. Duplicate source rows are not "
                        "supported in this V2 receipt-draft checkpoint."
                    ).format(
                        frappe.bold(identity[0] or _("blank")),
                        frappe.bold(identity[1] or _("blank")),
                    ),
                    title=_("Ambiguous Finished Item Rows"),
                )

            seen_finished_rows.add(identity)

        return

    frappe.throw(
        _(
            "A subcontracted Purchase Order may contain only one item row.<br><br>"
            "<b>Bhogals Subcontracting Policy:</b> Each subcontracted Purchase "
            "Order represents one entrusted material and one processing "
            "operation.<br><br>"
            "Please create separate Purchase Orders for different materials "
            "or processing operations."
        ),
        title=_("Multiple Items Not Permitted"),
    )
