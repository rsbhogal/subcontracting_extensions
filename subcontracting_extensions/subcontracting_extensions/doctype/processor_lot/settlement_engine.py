# Copyright (c) 2026, Bhogals Private Limited
# For license information, please see license.txt

"""
Settlement Engine.

Purpose
-------
Executes an approved Processor Lot settlement action through standard
ERPNext documents.

The Settlement Engine does not trust quantities, rates or amounts supplied
by the browser or stored in editable Processor Lot child rows.

Immediately before creating a settlement document, it rebuilds the complete
authoritative decision chain:

    Fact Engine
        ↓
    Recommendation Engine
        ↓
    Recovery Calculator
        ↓
    Settlement Engine

The engine never posts Stock Ledger or General Ledger entries directly.
All inventory and accounting effects are produced through standard ERPNext
documents.

Current capability
------------------
Recover Processor Shortage

Creates one standard draft Purchase Invoice Debit Note containing, when
recommended:

- a stock-return row for raw-material shortage; and
- a non-stock return row for recoverable processing charges.

Future capabilities
-------------------
- Accepted Process Loss
- Components Returned
- Pending Investigation
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

import frappe
from frappe import _
from frappe.utils import flt

from erpnext.controllers.accounts_controller import (
    get_taxes_and_charges,
)

from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.fact_engine import (
    apply_processor_lot_settlement_policy,
    get_sco_facts,
)
from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.recovery_calculator import (
    calculate_recovery,
)
from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.settlement_netting import (
    build_settlement_netting,
)
from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.settlement_recommendation import (
    recommend_settlement,
)

if TYPE_CHECKING:
    from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.processor_lot import (
        ProcessorLot,
    )


def create_shortage_debit_note(
    processor_lot: "ProcessorLot",
    business_classification: str,
):
    """
    Create and insert a standard draft Purchase Invoice Debit Note.

    All commercial values are regenerated server-side immediately before
    document creation.

    Parameters
    ----------
    processor_lot:
        Fresh Processor Lot document loaded from the database.

    business_classification:
        Business reality selected by the user in the reconciliation wizard.

    Returns
    -------
    dict
        Settlement result containing:

        - inserted draft Purchase Invoice Debit Note;
        - authoritative Fact Engine output;
        - settlement recommendation; and
        - authoritative Recovery Calculator output.
    """
    _validate_processor_lot(
        processor_lot=processor_lot,
        business_classification=business_classification,
    )

    _prevent_duplicate_debit_note(processor_lot.name)

    facts = get_sco_facts(
        processor_lot.subcontracting_order
    )
    facts = apply_processor_lot_settlement_policy(
        facts,
        processor_lot,
    )

    recommendation = recommend_settlement(
        facts,
        business_classification,
    )

    gross_recovery = calculate_recovery(
        facts,
        recommendation,
    )

    settlement_netting = build_settlement_netting(
        subcontracting_order=processor_lot.subcontracting_order,
        facts=facts,
        gross_recovery=gross_recovery,
    )
    proposed_applications = (
        (settlement_netting.get("material_credit") or {}).get(
            "applications"
        )
        or []
    )
    if proposed_applications:
        frappe.throw(
            _(
                "Apply and submit the proposed Processor Material Credit "
                "before creating a residual Debit Note."
            ),
            title=_("Processor Material Credit Available"),
        )
    recovery = build_net_recovery_report(
        gross_recovery=gross_recovery,
        settlement_netting=settlement_netting,
    )

    _validate_recovery_report(
        processor_lot=processor_lot,
        facts=facts,
        recovery=recovery,
    )

    debit_note = frappe.new_doc("Purchase Invoice")

    _set_debit_note_header(
        debit_note=debit_note,
        processor_lot=processor_lot,
    )

    _append_recovery_items(
        debit_note=debit_note,
        processor_lot=processor_lot,
        facts=facts,
        recovery=recovery,
    )

    debit_note.remarks = build_shortage_debit_note_remarks(
        processor_lot=processor_lot,
        business_classification=business_classification,
        recovery=recovery,
    )

    # Populate standard ERPNext defaults such as accounts, conversion rates,
    # GST context, item descriptions and stock-accounting properties.
    debit_note.set_missing_values()

    # Header Set Warehouse may be copied by ERPNext into non-stock
    # service rows during set_missing_values(). A processing-service
    # recovery must never create a stock movement.
    for item in debit_note.items:
        is_stock_item = frappe.db.get_value(
            "Item",
            item.item_code,
            "is_stock_item",
        )

        if not is_stock_item:
            item.warehouse = ""

    # set_missing_values() may reset the posting date to the current date.
    # Restore the settlement date selected on the Processor Lot.
    debit_note.posting_date = processor_lot.settlement_date
    debit_note.bill_date = processor_lot.settlement_date

    _load_purchase_taxes(debit_note)

    # Use ERPNext's standard accounts controller for tax and total
    # calculations. The Settlement Engine performs no parallel accounting
    # calculation.
    debit_note.calculate_taxes_and_totals()

    debit_note.insert()

    return {
        "debit_note": debit_note,
        "facts": facts,
        "recommendation": recommendation,
        "recovery": recovery,
        "gross_recovery": gross_recovery,
        "settlement_netting": settlement_netting,
    }


def build_net_recovery_report(
    gross_recovery: dict[str, Any],
    settlement_netting: dict[str, Any],
) -> dict[str, Any]:
    """Return a Recovery Calculator-shaped report for only the residual."""
    report = deepcopy(gross_recovery or {})
    net = (settlement_netting or {}).get("net") or {}
    raw_material = report.get("raw_material") or {}
    processing_charges = report.get("processing_charges") or {}

    raw_qty = max(flt(net.get("raw_material_recovery_qty"), 3), 0.0)
    processing_qty = max(
        flt(net.get("processing_charge_recovery_qty"), 3),
        0.0,
    )
    raw_amount = max(flt(net.get("raw_material_recovery")), 0.0)
    processing_amount = max(
        flt(net.get("processing_charge_recovery")),
        0.0,
    )
    total_recovery = max(flt(net.get("total_recovery")), 0.0)

    raw_material.update(
        {
            "recommended": bool(
                raw_material.get("recommended") and raw_qty > 0
            ),
            "quantity": raw_qty,
            "amount": raw_amount,
            "calculation": _build_residual_calculation(
                qty=raw_qty,
                uom=raw_material.get("uom"),
                rate=raw_material.get("rate"),
                amount=raw_amount,
            ),
        }
    )
    processing_charges.update(
        {
            "recommended": bool(
                processing_charges.get("recommended")
                and processing_qty > 0
            ),
            "quantity": processing_qty,
            "amount": processing_amount,
            "calculation": _build_residual_calculation(
                qty=processing_qty,
                uom=processing_charges.get("uom"),
                rate=processing_charges.get("rate"),
                amount=processing_amount,
            ),
        }
    )

    report["raw_material"] = raw_material
    report["processing_charges"] = processing_charges
    report["quantity"] = {
        "shortage_qty": max(
            flt(net.get("physical_shortage_qty"), 3),
            0.0,
        ),
        "shortage_uom": (
            (gross_recovery.get("quantity") or {}).get("shortage_uom")
        ),
    }
    report["totals"] = {
        "raw_material_recovery": raw_amount,
        "processing_charge_recovery": processing_amount,
        "total_recovery": total_recovery,
    }
    report["status"] = (
        "Recovery Calculated"
        if total_recovery > 0
        else "No Recovery Required"
    )
    report["next_step"] = (
        "Review residual recovery and create draft settlement document"
        if total_recovery > 0
        else "No Debit Note is required"
    )
    report["settlement_netting"] = settlement_netting
    return report


def _build_residual_calculation(
    qty: float,
    uom: str | None,
    rate: float,
    amount: float,
) -> str:
    quantity = _format_number(qty)
    if uom:
        quantity = f"{quantity} {uom}"
    return _("{0} × {1} = {2}").format(
        quantity,
        _format_number(rate),
        _format_number(amount),
    )


def _validate_processor_lot(
    processor_lot: "ProcessorLot",
    business_classification: str,
) -> None:
    """Validate the minimum document state required for settlement."""
    if not processor_lot:
        frappe.throw(_("Processor Lot is required."))

    if processor_lot.is_new():
        frappe.throw(
            _(
                "Save the Processor Lot before creating a Debit Note."
            )
        )

    if processor_lot.docstatus == 2:
        frappe.throw(
            _("A cancelled Processor Lot cannot create a Debit Note.")
        )

    if not processor_lot.subcontracting_order:
        frappe.throw(
            _("Subcontracting Order is required.")
        )

    if not business_classification:
        frappe.throw(
            _("Business Classification is required.")
        )

    if not processor_lot.company:
        frappe.throw(_("Company is required."))

    if not processor_lot.supplier:
        frappe.throw(_("Supplier is required."))

    if not processor_lot.supplier_warehouse:
        frappe.throw(
            _("Supplier Warehouse is required.")
        )

    if not processor_lot.settlement_date:
        frappe.throw(
            _("Settlement Date is required.")
        )

    if not processor_lot.cost_center:
        frappe.throw(_("Cost Center is required."))

    if not processor_lot.branch:
        frappe.throw(_("Branch is required."))


def _prevent_duplicate_debit_note(
    processor_lot_name: str,
) -> None:
    """
    Prevent more than one active Debit Note for a Processor Lot.

    Cancelled Purchase Invoices do not block creation of a replacement.
    """
    existing = frappe.db.get_value(
        "Purchase Invoice",
        {
            "custom_processor_lot_settlement": (
                processor_lot_name
            ),
            "docstatus": ["!=", 2],
        },
        "name",
    )

    if not existing:
        return

    frappe.throw(
        _(
            "Purchase Invoice {0} already exists for Processor Lot {1}."
        ).format(
            frappe.utils.get_link_to_form(
                "Purchase Invoice",
                existing,
            ),
            frappe.bold(processor_lot_name),
        )
    )


def _validate_recovery_report(
    processor_lot: "ProcessorLot",
    facts: dict[str, Any],
    recovery: dict[str, Any],
) -> None:
    """Block document creation when the recovery report is unsafe."""
    integrity = recovery.get("integrity") or {}
    blocking_errors = (
        integrity.get("blocking_errors") or []
    )

    if blocking_errors:
        messages = []

        for error in blocking_errors:
            message = error.get("message")

            if message:
                messages.append(str(message))

        frappe.throw(
            "<br>".join(messages)
            or _(
                "The Recovery Calculator reported a blocking error."
            ),
            title=_("Recovery Calculation Blocked"),
        )

    if recovery.get("status") != "Recovery Calculated":
        frappe.throw(
            _(
                "The Recovery Calculator did not recommend a commercial "
                "recovery for this Processor Lot."
            )
        )

    raw_material = recovery.get("raw_material") or {}
    processing_charges = (
        recovery.get("processing_charges") or {}
    )

    if not (
        raw_material.get("recommended")
        or processing_charges.get("recommended")
    ):
        frappe.throw(
            _(
                "Neither raw-material recovery nor processing-charge "
                "recovery is recommended."
            )
        )

    total_recovery = flt(
        (recovery.get("totals") or {}).get(
            "total_recovery"
        )
    )

    if total_recovery <= 0:
        frappe.throw(
            _(
                "Total recommended recovery must be greater than zero."
            )
        )

    if raw_material.get("recommended"):
        _validate_raw_material_stock(
            processor_lot=processor_lot,
            facts=facts,
            raw_material=raw_material,
        )


def _validate_raw_material_stock(
    processor_lot: "ProcessorLot",
    facts: dict[str, Any],
    raw_material: dict[str, Any],
) -> None:
    """
    Ensure the recommended stock return can be made from the supplier warehouse.

    Version 1 supports one principal component. Therefore the recovery quantity
    must not exceed either its SCO-specific outstanding quantity or its current
    Supplier Warehouse balance.
    """
    component = _get_principal_component(facts)

    recovery_qty = flt(
        raw_material.get("quantity")
    )

    outstanding_qty = flt(
        component.get("outstanding_qty")
    )

    warehouse_balance = flt(
        component.get("warehouse_balance")
    )

    qty_tolerance = 0.000001

    if recovery_qty - outstanding_qty > qty_tolerance:
        frappe.throw(
            _(
                "Recommended raw-material recovery quantity {0} cannot "
                "exceed the SCO-specific outstanding quantity {1} for {2}."
            ).format(
                frappe.bold(recovery_qty),
                frappe.bold(outstanding_qty),
                frappe.bold(
                    component.get("component_item")
                ),
            )
        )

    if recovery_qty - warehouse_balance > qty_tolerance:
        frappe.throw(
            _(
                "Recommended raw-material recovery quantity {0} cannot "
                "exceed the current balance {1} of {2} in Supplier "
                "Warehouse {3}."
            ).format(
                frappe.bold(recovery_qty),
                frappe.bold(warehouse_balance),
                frappe.bold(
                    component.get("component_item")
                ),
                frappe.bold(
                    processor_lot.supplier_warehouse
                ),
            )
        )


def _set_debit_note_header(
    debit_note,
    processor_lot: "ProcessorLot",
) -> None:
    """Populate the standard Purchase Invoice Debit Note header."""
    debit_note.company = processor_lot.company
    debit_note.supplier = processor_lot.supplier
    debit_note.posting_date = processor_lot.settlement_date

    # A processor-shortage Debit Note is raised by Bhogals rather than
    # received against a supplier-issued invoice. Use the Processor Lot
    # number as the mandatory supplier invoice reference.
    # ERPNext replaces a backdated posting date with the current date during
    # insertion unless Set Posting Time is enabled. Preserve the settlement
    # date selected on the Processor Lot.
    debit_note.set_posting_time = 1

    debit_note.bill_no = processor_lot.name
    debit_note.bill_date = processor_lot.settlement_date

    debit_note.is_return = 1
    debit_note.update_stock = 1
    # debit_note.set_warehouse = (
    #     processor_lot.supplier_warehouse
    # )
    debit_note.cost_center = processor_lot.cost_center
    debit_note.branch = processor_lot.branch

    debit_note.custom_processor_lot_settlement = (
        processor_lot.name
    )


def _append_recovery_items(
    debit_note,
    processor_lot: "ProcessorLot",
    facts: dict[str, Any],
    recovery: dict[str, Any],
) -> None:
    """Append the recommended stock and service recovery rows."""
    raw_material = recovery.get("raw_material") or {}
    processing_charges = (
        recovery.get("processing_charges") or {}
    )

    if raw_material.get("recommended"):
        component = _get_principal_component(facts)

        debit_note.append(
            "items",
            {
                "item_code": component.get(
                    "component_item"
                ),
                "qty": -flt(
                    raw_material.get("quantity")
                ),
                "uom": (
                    raw_material.get("uom")
                    or component.get("stock_uom")
                ),
                "rate": flt(
                    raw_material.get("rate")
                ),
                "warehouse": (
                    processor_lot.supplier_warehouse
                ),
                "cost_center": processor_lot.cost_center,
                "branch": processor_lot.branch,
            },
        )

    if processing_charges.get("recommended"):
        purchase_order = (
            (facts.get("identity") or {}).get(
                "purchase_order"
            )
        )

        if not purchase_order:
            frappe.throw(
                _(
                    "Purchase Order is required to determine the "
                    "Production Process for the processing-service row."
                )
            )

        service_item = _get_processing_service_item(
            facts
        )

        production_process = (
            _get_processing_service_production_process(
                purchase_order=purchase_order,
                service_item=service_item,
            )
        )

        debit_note.append(
            "items",
            {
                "item_code": service_item,
                "qty": -flt(
                    processing_charges.get("quantity")
                ),
                "uom": processing_charges.get("uom"),
                "rate": flt(
                    processing_charges.get("rate")
                ),
                # A service recovery row must not issue stock from the
                # Supplier Warehouse.
                "warehouse": "",
                "cost_center": processor_lot.cost_center,
                "branch": processor_lot.branch,
                "custom_production_process": (
                    production_process
                ),
            },
        )

    if not debit_note.items:
        frappe.throw(
            _(
                "No Purchase Invoice recovery rows could be generated."
            )
        )


def _get_principal_component(
    facts: dict[str, Any],
) -> dict[str, Any]:
    """
    Return the one recoverable raw-material component supported by Version 1.
    """
    components = [
        row
        for row in (facts.get("components") or [])
        if flt(row.get("outstanding_qty")) > 0
    ]

    component_items = {
        row.get("component_item")
        for row in components
        if row.get("component_item")
    }

    if not components:
        frappe.throw(
            _(
                "No component with an outstanding quantity is available "
                "for raw-material recovery."
            )
        )

    if len(component_items) != 1:
        frappe.throw(
            _(
                "Version 1 of Processor Lot settlement supports one "
                "principal raw-material component. Found: {0}."
            ).format(
                ", ".join(
                    sorted(component_items)
                )
                or _("Unknown")
            )
        )

    return components[0]


def _get_processing_service_item(
    facts: dict[str, Any],
) -> str:
    """
    Resolve the processing-service Item linked to the subcontracted PO.

    Priority
    --------
    1. Purchase Invoice Item linked to the Purchase Order.
    2. Purchase Receipt Item linked to the Purchase Order.
    3. Purchase Order Item itself.

    Submitted commercial rows are preferred over draft rows.
    """
    purchase_order = (
        (facts.get("identity") or {}).get(
            "purchase_order"
        )
    )

    invoice_rows = (
        (facts.get("commercial") or {}).get(
            "purchase_invoices"
        )
        or []
    )

    service_item = _select_single_item(
        rows=invoice_rows,
        item_field="item_code",
        preferred_docstatus=1,
    )

    if service_item:
        return service_item

    receipt_rows = (
        (facts.get("purchase_receipts") or {}).get(
            "purchase_receipts"
        )
        or []
    )

    service_item = _select_single_item(
        rows=receipt_rows,
        item_field="item_code",
        preferred_docstatus=1,
    )

    if service_item:
        return service_item

    if purchase_order:
        purchase_order_items = frappe.get_all(
            "Purchase Order Item",
            filters={
                "parent": purchase_order,
                "parenttype": "Purchase Order",
                "parentfield": "items",
            },
            pluck="item_code",
        )

        unique_items = sorted(
            {
                item_code
                for item_code in purchase_order_items
                if item_code
            }
        )

        if len(unique_items) == 1:
            return unique_items[0]

        if len(unique_items) > 1:
            frappe.throw(
                _(
                    "Purchase Order {0} contains multiple Item Codes. "
                    "The processing-service Item cannot be determined "
                    "unambiguously."
                ).format(
                    frappe.bold(purchase_order)
                )
            )

    frappe.throw(
        _(
            "The processing-service Item could not be determined from "
            "the Purchase Invoice, Purchase Receipt or Purchase Order."
        )
    )

def _get_processing_service_production_process(
    purchase_order: str,
    service_item: str,
) -> str:
    """
    Return the Production Process assigned to the specified service Item
    in the Purchase Order.

    The Settlement Engine deliberately copies the Production Process from
    the same Purchase Order Item row that defines the service Item. This
    preserves row-level manufacturing attribution when a Purchase Order
    contains multiple service Items for different Production Processes.
    """

    rows = frappe.get_all(
        "Purchase Order Item",
        filters={
            "parent": purchase_order,
            "parenttype": "Purchase Order",
            "parentfield": "items",
            "item_code": service_item,
        },
        fields=[
            "custom_production_process",
        ],
        order_by="idx asc",
    )

    if not rows:
        frappe.throw(
            _(
                "Service Item {0} was not found in Purchase Order {1}."
            ).format(
                frappe.bold(service_item),
                frappe.bold(purchase_order),
            )
        )

    processes = sorted(
        {
            row.get("custom_production_process")
            for row in rows
            if row.get("custom_production_process")
        }
    )

    if not processes:
        frappe.throw(
            _(
                "Production Process is not defined for service Item {0} "
                "in Purchase Order {1}."
            ).format(
                frappe.bold(service_item),
                frappe.bold(purchase_order),
            )
        )

    if len(processes) > 1:
        frappe.throw(
            _(
                "Service Item {0} is associated with multiple Production "
                "Processes in Purchase Order {1}: {2}."
            ).format(
                frappe.bold(service_item),
                frappe.bold(purchase_order),
                ", ".join(processes),
            )
        )

    return processes[0]

def _select_single_item(
    rows: list[dict[str, Any]],
    item_field: str,
    preferred_docstatus: int,
) -> str | None:
    """Return one unambiguous Item Code from commercial evidence."""
    preferred_items = sorted(
        {
            str(row.get(item_field))
            for row in rows
            if row.get(item_field)
            and int(row.get("docstatus") or 0)
            == preferred_docstatus
        }
    )

    if len(preferred_items) == 1:
        return preferred_items[0]

    if len(preferred_items) > 1:
        frappe.throw(
            _(
                "Multiple processing-service Items were found in submitted "
                "commercial documents: {0}."
            ).format(
                ", ".join(preferred_items)
            )
        )

    all_items = sorted(
        {
            str(row.get(item_field))
            for row in rows
            if row.get(item_field)
        }
    )

    if len(all_items) == 1:
        return all_items[0]

    if len(all_items) > 1:
        frappe.throw(
            _(
                "Multiple possible processing-service Items were found: {0}."
            ).format(
                ", ".join(all_items)
            )
        )

    return None


def _load_purchase_taxes(debit_note) -> None:
    """
    Copy the selected Purchase Taxes and Charges Template into the document.

    Programmatically appended items do not trigger the client-side routine
    that normally copies the template rows.
    """
    if not debit_note.taxes_and_charges:
        return

    if debit_note.taxes:
        return

    tax_rows = get_taxes_and_charges(
        master_doctype=(
            "Purchase Taxes and Charges Template"
        ),
        master_name=debit_note.taxes_and_charges,
    )

    for tax_row in tax_rows or []:
        debit_note.append("taxes", tax_row)


def build_shortage_debit_note_remarks(
    processor_lot: "ProcessorLot",
    business_classification: str,
    recovery: dict[str, Any],
) -> str:
    """Build an auditable commercial explanation for the Debit Note."""
    raw_material = recovery.get("raw_material") or {}
    processing_charges = (
        recovery.get("processing_charges") or {}
    )
    totals = recovery.get("totals") or {}

    lines = [
        _("Processor Shortage Recovery"),
        "",
        _("Processor Lot: {0}").format(
            processor_lot.name
        ),
        _("Subcontracting Order: {0}").format(
            processor_lot.subcontracting_order
        ),
        _("Purchase Order: {0}").format(
            processor_lot.purchase_order or ""
        ),
        _("Supplier Warehouse: {0}").format(
            processor_lot.supplier_warehouse
        ),
        _("Business Classification: {0}").format(
            business_classification
        ),
        "",
        _("Recommended Recovery"),
    ]

    if raw_material.get("recommended"):
        lines.extend(
            [
                "",
                _("Raw Material Shortage"),
                raw_material.get("calculation") or "",
                _("Rate Source: {0}").format(
                    raw_material.get("rate_source")
                    or ""
                ),
                _("Source Documents: {0}").format(
                    _join_source_documents(
                        raw_material.get(
                            "source_documents"
                        )
                    )
                ),
            ]
        )

    if processing_charges.get("recommended"):
        lines.extend(
            [
                "",
                _("Processing Charges Recoverable"),
                processing_charges.get(
                    "calculation"
                )
                or "",
                _("Rate Source: {0}").format(
                    processing_charges.get(
                        "rate_source"
                    )
                    or ""
                ),
                _("Source Documents: {0}").format(
                    _join_source_documents(
                        processing_charges.get(
                            "source_documents"
                        )
                    )
                ),
            ]
        )

    lines.extend(
        [
            "",
            _("Total Recommended Recovery: {0}").format(
                _format_number(
                    totals.get("total_recovery")
                )
            ),
        ]
    )

    if processor_lot.reason:
        lines.extend(
            [
                "",
                _("Reason: {0}").format(
                    processor_lot.reason
                ),
            ]
        )

    if processor_lot.remarks:
        lines.extend(
            [
                "",
                _("Processor Lot Remarks:"),
                processor_lot.remarks,
            ]
        )

    lines.extend(
        [
            "",
            _(
                "Generated automatically from the Processor Lot "
                "Fact Engine, Recommendation Engine and Recovery Calculator."
            ),
        ]
    )

    return "\n".join(
        str(line)
        for line in lines
        if line is not None
    ).strip()


def _join_source_documents(
    source_documents: Any,
) -> str:
    """Return a readable comma-separated source-document list."""
    documents = [
        str(document)
        for document in (source_documents or [])
        if document
    ]

    return ", ".join(documents) or _("Not available")


def _format_number(value: Any) -> str:
    """Return a readable number without unnecessary trailing zeroes."""
    number = flt(value)

    return f"{number:,.3f}".rstrip("0").rstrip(".")
