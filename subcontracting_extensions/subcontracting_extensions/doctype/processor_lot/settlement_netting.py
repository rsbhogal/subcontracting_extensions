# Copyright (c) 2026, Bhogals Private Limited
# For license information, please see license.txt

"""Read-only Processor Material Account netting for Processor Lot settlement.

This version recognizes already-submitted Credit Applied entries.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import flt


def build_settlement_netting(
    subcontracting_order: str,
    facts: dict[str, Any],
    gross_recovery: dict[str, Any],
) -> dict[str, Any]:
    """Return a gross-to-net settlement plan without changing any document."""
    credits = get_compatible_open_credits(subcontracting_order)
    existing_applications = get_existing_credit_applications(
        subcontracting_order
    )
    return calculate_settlement_netting(
        facts=facts,
        gross_recovery=gross_recovery,
        credits=credits,
        existing_applications=existing_applications,
    )


def get_compatible_open_credits(
    subcontracting_order: str,
) -> list[dict[str, Any]]:
    """Return compatible submitted Advance Credits in FIFO order."""
    if not subcontracting_order:
        frappe.throw(_("Subcontracting Order is required."))

    sco = frappe.get_doc("Subcontracting Order", subcontracting_order)
    if sco.docstatus != 1:
        frappe.throw(
            _("Subcontracting Order {0} must be submitted.").format(
                frappe.bold(sco.name)
            )
        )

    processed_item, processed_uom = _get_single_processed_item(sco)
    component, account_uom = _get_single_component(sco)

    credit_rows = frappe.get_all(
        "Processor Material Account Entry",
        filters={
            "entry_type": "Advance Credit",
            "account_direction": "Credit",
            "docstatus": 1,
            "is_reversed": 0,
            "company": sco.company,
            "supplier": sco.supplier,
            "supplier_warehouse": sco.supplier_warehouse,
            "processed_item": processed_item,
            "processed_item_uom": processed_uom,
            "principal_component": component,
            "account_uom": account_uom,
        },
        fields=[
            "name",
            "posting_date",
            "creation",
            "source_event",
            "processor_lot_receipt",
            "receipt_item_key",
            "processor_lot",
            "subcontracting_order",
            "processed_qty",
            "account_qty",
        ],
        order_by="posting_date asc, creation asc, name asc",
    )
    if not credit_rows:
        return []

    source_names = [row.name for row in credit_rows]
    debit_rows = frappe.get_all(
        "Processor Material Account Entry",
        filters={
            "against_entry": ["in", source_names],
            "account_direction": "Debit",
            "docstatus": 1,
            "is_reversed": 0,
        },
        fields=[
            "against_entry",
            "processed_qty",
            "account_qty",
            "commercial_qty",
        ],
    )

    used_by_source = _summarize_used_credit_quantities(debit_rows)

    credits: list[dict[str, Any]] = []
    for row in credit_rows:
        used = used_by_source.get(
            row.name,
            {
                "processed_qty": 0.0,
                "account_qty": 0.0,
                "commercial_qty": 0.0,
            },
        )
        available_processed_qty = max(
            flt(row.processed_qty) - flt(used["processed_qty"]),
            0.0,
        )
        available_account_qty = max(
            flt(row.account_qty) - flt(used["account_qty"]),
            0.0,
        )
        available_qty = min(
            available_processed_qty,
            available_account_qty,
        )
        if available_qty <= 0:
            continue

        source_invoice_qty = _get_source_credit_invoice_qty(row)
        source_unbilled_qty = max(
            flt(row.processed_qty) - source_invoice_qty,
            0.0,
        )
        commercial_unbilled_qty = max(
            source_unbilled_qty - flt(used["commercial_qty"]),
            0.0,
        )

        credits.append(
            {
                "entry": row.name,
                "posting_date": row.posting_date,
                "source_event": row.source_event,
                "processor_lot_receipt": row.processor_lot_receipt,
                "receipt_item_key": row.receipt_item_key,
                "processor_lot": row.processor_lot,
                "subcontracting_order": row.subcontracting_order,
                "available_processed_qty": available_processed_qty,
                "available_account_qty": available_account_qty,
                "available_qty": available_qty,
                "commercial_unbilled_qty": min(
                    commercial_unbilled_qty,
                    available_qty,
                ),
            }
        )

    return credits


def _summarize_used_credit_quantities(
    debit_rows: list[Any],
) -> dict[str, dict[str, float]]:
    """Aggregate each source credit's physical, account and commercial use."""
    used_by_source: dict[str, dict[str, float]] = {}
    for row in debit_rows:
        used = used_by_source.setdefault(
            row.against_entry,
            {
                "processed_qty": 0.0,
                "account_qty": 0.0,
                "commercial_qty": 0.0,
            },
        )
        used["processed_qty"] += flt(row.processed_qty)
        used["account_qty"] += flt(row.account_qty)
        used["commercial_qty"] += flt(row.commercial_qty)

    return used_by_source


def calculate_settlement_netting(
    facts: dict[str, Any],
    gross_recovery: dict[str, Any],
    credits: list[dict[str, Any]],
    existing_applications: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Net compatible PMA credits against physical and commercial facts."""
    facts = facts or {}
    gross_recovery = gross_recovery or {}
    credits = credits or []
    existing_applications = existing_applications or []

    summary = facts.get("summary") or {}
    physical = summary.get("physical_inventory") or {}
    comparisons = summary.get("comparisons") or {}

    physical_shortage_qty = max(
        flt(physical.get("outstanding_qty")),
        flt((gross_recovery.get("quantity") or {}).get("shortage_qty")),
        0.0,
    )
    commercial_variance_qty = max(
        flt(comparisons.get("invoice_vs_scr_received")),
        0.0,
    )

    already_applied_qty = sum(
        flt(row.get("account_qty")) for row in existing_applications
    )
    already_commercially_matched_qty = sum(
        flt(row.get("commercial_qty")) for row in existing_applications
    )
    remaining_physical_qty = max(
        physical_shortage_qty - already_applied_qty,
        0.0,
    )
    remaining_commercial_qty = max(
        commercial_variance_qty - already_commercially_matched_qty,
        0.0,
    )
    applications: list[dict[str, Any]] = []

    for credit in credits:
        if remaining_physical_qty <= 0:
            break

        applied_qty = min(
            remaining_physical_qty,
            max(flt(credit.get("available_qty")), 0.0),
        )
        if applied_qty <= 0:
            continue

        commercially_matched_qty = min(
            applied_qty,
            remaining_commercial_qty,
            max(flt(credit.get("commercial_unbilled_qty")), 0.0),
        )
        applications.append(
            {
                "against_entry": credit.get("entry"),
                "posting_date": credit.get("posting_date"),
                "source_processor_lot_receipt": credit.get(
                    "processor_lot_receipt"
                ),
                "source_processor_lot": credit.get("processor_lot"),
                "account_qty": applied_qty,
                "processed_qty": applied_qty,
                "commercially_matched_qty": commercially_matched_qty,
            }
        )
        remaining_physical_qty -= applied_qty
        remaining_commercial_qty -= commercially_matched_qty

    applied_qty = sum(flt(row["account_qty"]) for row in applications)
    commercially_matched_qty = sum(
        flt(row["commercially_matched_qty"]) for row in applications
    )

    raw_material = gross_recovery.get("raw_material") or {}
    processing = gross_recovery.get("processing_charges") or {}
    raw_rate = flt(raw_material.get("rate"))
    processing_rate = flt(processing.get("rate"))

    net_raw_qty = (
        max(remaining_physical_qty, 0.0)
        if raw_material.get("recommended")
        else 0.0
    )
    net_processing_qty = (
        max(remaining_commercial_qty, 0.0)
        if processing.get("recommended")
        else 0.0
    )
    net_raw_amount = net_raw_qty * raw_rate
    net_processing_amount = net_processing_qty * processing_rate
    net_total = net_raw_amount + net_processing_amount

    if applications and net_total > 0:
        recommended_action = "Apply Processor Material Credit and Create Draft Debit Note"
    elif applications:
        recommended_action = "Apply Processor Material Credit"
    elif net_total > 0:
        recommended_action = "Create Draft Debit Note"
    else:
        recommended_action = "No Recovery"

    return {
        "plan_version": 1,
        "gross": {
            "physical_shortage_qty": physical_shortage_qty,
            "commercial_variance_qty": commercial_variance_qty,
            "raw_material_recovery": flt(
                (gross_recovery.get("totals") or {}).get(
                    "raw_material_recovery"
                )
            ),
            "processing_charge_recovery": flt(
                (gross_recovery.get("totals") or {}).get(
                    "processing_charge_recovery"
                )
            ),
            "total_recovery": flt(
                (gross_recovery.get("totals") or {}).get("total_recovery")
            ),
        },
        "material_credit": {
            "available_qty": sum(
                flt(row.get("available_qty")) for row in credits
            ),
            "proposed_applied_qty": applied_qty,
            "commercially_matched_qty": commercially_matched_qty,
            "already_applied_qty": already_applied_qty,
            "already_commercially_matched_qty": (
                already_commercially_matched_qty
            ),
            "existing_applications": existing_applications,
            "applications": applications,
        },
        "net": {
            "physical_shortage_qty": max(remaining_physical_qty, 0.0),
            "commercial_variance_qty": max(remaining_commercial_qty, 0.0),
            "raw_material_recovery_qty": net_raw_qty,
            "raw_material_recovery": net_raw_amount,
            "processing_charge_recovery_qty": net_processing_qty,
            "processing_charge_recovery": net_processing_amount,
            "total_recovery": net_total,
        },
        "recommended_action": (
            "Processor Material Credit Applied"
            if existing_applications and not applications and net_total <= 0
            else recommended_action
        ),
    }


def get_existing_credit_applications(
    subcontracting_order: str,
) -> list[dict[str, Any]]:
    """Return effective submitted Credit Applied entries for the target SCO."""
    rows = frappe.get_all(
        "Processor Material Account Entry",
        filters={
            "entry_type": "Credit Applied",
            "source_event": "Processor Lot Shortage",
            "subcontracting_order": subcontracting_order,
            "account_direction": "Debit",
            "docstatus": 1,
            "is_reversed": 0,
        },
        fields=[
            "name",
            "posting_date",
            "processor_lot",
            "against_entry",
            "processed_qty",
            "account_qty",
            "commercial_qty",
            "application_stock_entry",
            "application_journal_entry",
        ],
        order_by="posting_date asc, creation asc, name asc",
    )
    return [dict(row) for row in rows]


def _get_single_processed_item(sco) -> tuple[str, str]:
    values = {
        (
            row.item_code,
            row.stock_uom
            or frappe.db.get_value("Item", row.item_code, "stock_uom"),
        )
        for row in sco.items
        if row.item_code
    }
    if len(values) != 1:
        frappe.throw(
            _(
                "Processor Material Account netting currently requires "
                "exactly one processed item in Subcontracting Order {0}."
            ).format(frappe.bold(sco.name))
        )
    return next(iter(values))


def _get_single_component(sco) -> tuple[str, str]:
    values = {
        (
            row.rm_item_code,
            row.stock_uom
            or frappe.db.get_value("Item", row.rm_item_code, "stock_uom"),
        )
        for row in sco.supplied_items
        if row.rm_item_code
    }
    if len(values) != 1:
        frappe.throw(
            _(
                "Processor Material Account netting currently requires "
                "exactly one principal component in Subcontracting Order {0}."
            ).format(frappe.bold(sco.name))
        )
    return next(iter(values))


def _get_source_credit_invoice_qty(credit) -> float:
    if credit.source_event != "PLR Excess" or not credit.processor_lot_receipt:
        return 0.0
    receipt_item_key = getattr(credit, "receipt_item_key", None)
    if receipt_item_key:
        invoice_qty = frappe.db.get_value(
            "Processor Lot Receipt Item",
            {
                "parent": credit.processor_lot_receipt,
                "parenttype": "Processor Lot Receipt",
                "item_key": receipt_item_key,
            },
            "material_credit_invoice_qty",
        )
        if invoice_qty is None:
            frappe.throw(
                _(
                    "Receipt Item {0} is missing from "
                    "Processor Lot Receipt {1}."
                ).format(
                    frappe.bold(receipt_item_key),
                    frappe.bold(credit.processor_lot_receipt),
                )
            )
        return flt(invoice_qty, 6)

    return flt(
        frappe.db.get_value(
            "Processor Lot Receipt",
            credit.processor_lot_receipt,
            "material_credit_invoice_qty",
        )
    )
