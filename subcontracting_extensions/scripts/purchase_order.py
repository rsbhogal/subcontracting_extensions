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


def validate(doc, method=None):
    """
    Validate Bhogals-specific Purchase Order business rules.

    This function is called through the Purchase Order ``validate``
    document event configured in ``hooks.py``.
    """
    validate_subcontracted_purchase_order_item_count(doc)
    validate_subcontracting_routes(doc)

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