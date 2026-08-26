# Copyright (c) 2026, Bhogals Private Limited
# For license information, please see license.txt

"""
Recovery Calculator for Processor Lot.

Purpose
-------
Calculates the monetary recovery recommended for a Processor Lot.

The calculator consumes:

- read-only Fact Engine output, and
- a Settlement Recommendation Report.

It does not:

- query the database,
- classify the business reality,
- decide whether recovery is contractually permitted,
- modify ERPNext documents,
- create Debit Notes, or
- post Stock Ledger / General Ledger entries.

Version 1
---------
Version 1 supports one principal component and one processing-service rate
per Processor Lot.

The report separately calculates:

- raw material recovery,
- processing-charge recovery, and
- total recommended recovery.

Every selected rate carries its factual source for auditability.
"""

from __future__ import annotations

from typing import Any

from frappe import _


def calculate_recovery(
    facts: dict[str, Any],
    recommendation: dict[str, Any],
) -> dict[str, Any]:
    """
    Return a read-only Processor Lot Recovery Report.

    Parameters
    ----------
    facts:
        Complete output returned by the Processor Lot Fact Engine.

    recommendation:
        Complete output returned by the Settlement Recommendation Engine.

    Returns
    -------
    dict
        Structured Recovery Report containing quantity, rates, amounts,
        sources, reasoning and integrity.
    """
    facts = facts or {}
    recommendation = recommendation or {}

    warnings: list[dict[str, Any]] = []
    blocking_errors: list[dict[str, Any]] = []

    _collect_input_integrity(
        facts=facts,
        recommendation=recommendation,
        warnings=warnings,
        blocking_errors=blocking_errors,
    )

    recovery_recommendation = (
        recommendation.get("recovery") or {}
    )

    raw_material_recommendation = (
        recovery_recommendation.get("raw_material") or {}
    )

    processing_recommendation = (
        recovery_recommendation.get("processing_charges") or {}
    )

    recover_raw_material = bool(
        raw_material_recommendation.get("recommended")
    )

    recover_processing_charges = bool(
        processing_recommendation.get("recommended")
    )

    shortage_qty = _get_shortage_qty(
        facts=facts,
        recommendation=recommendation,
    )

    shortage_uom = _get_shortage_uom(
        facts=facts,
        recommendation=recommendation,
    )

    processing_shortage_qty = (
        _get_processing_shortage_qty(facts)
    )

    processing_shortage_uom = (
        _get_processing_shortage_uom(facts)
    )

    if (
        recover_raw_material
        and shortage_qty <= 0
    ):
        blocking_errors.append(
            {
                "code": "RAW_MATERIAL_RECOVERY_QTY_NOT_POSITIVE",
                "message": _(
                    "Raw-material recovery is recommended, but the "
                    "factual outstanding component quantity is not "
                    "greater than zero."
                ),
            }
        )

    if (
        recover_processing_charges
        and processing_shortage_qty <= 0
    ):
        blocking_errors.append(
            {
                "code": "PROCESSING_RECOVERY_QTY_NOT_POSITIVE",
                "message": _(
                    "Processing-charge recovery is recommended, but no "
                    "positive Supplier Invoice versus Company Accepted "
                    "quantity variance is available from Processor Lot "
                    "Receipts."
                ),
            }
        )

    raw_material_rate_result = _get_raw_material_rate(
        facts=facts,
    )

    processing_rate_result = _get_processing_charge_rate(
        facts=facts,
    )

    if (
        recover_raw_material
        and raw_material_rate_result["rate"] <= 0
    ):
        blocking_errors.append(
            {
                "code": "RAW_MATERIAL_RATE_UNAVAILABLE",
                "message": _(
                    "Raw material recovery is recommended, but no reliable "
                    "raw material recovery rate is available."
                ),
            }
        )

    if (
        recover_processing_charges
        and processing_rate_result["rate"] <= 0
    ):
        blocking_errors.append(
            {
                "code": "PROCESSING_RATE_UNAVAILABLE",
                "message": _(
                    "Processing-charge recovery is recommended, but no "
                    "reliable processing-charge rate is available."
                ),
            }
        )

    raw_material_amount = (
        shortage_qty * raw_material_rate_result["rate"]
        if recover_raw_material
        else 0.0
    )

    processing_charge_amount = (
        processing_shortage_qty
        * processing_rate_result["rate"]
        if recover_processing_charges
        else 0.0
    )

    total_recovery_amount = (
        raw_material_amount
        + processing_charge_amount
    )

    raw_material = {
        "recommended": recover_raw_material,
        "quantity": shortage_qty if recover_raw_material else 0.0,
        "uom": shortage_uom,
        "rate": raw_material_rate_result["rate"],
        "amount": raw_material_amount,
        "rate_source": raw_material_rate_result["source"],
        "source_document_type": raw_material_rate_result[
            "source_document_type"
        ],
        "source_documents": raw_material_rate_result[
            "source_documents"
        ],
        "calculation": (
            _build_calculation_text(
                qty=shortage_qty,
                uom=shortage_uom,
                rate=raw_material_rate_result["rate"],
                amount=raw_material_amount,
            )
            if recover_raw_material
            else _("Raw material recovery is not recommended.")
        ),
    }

    processing_charges = {
        "recommended": recover_processing_charges,
        "quantity": (
            processing_shortage_qty
            if recover_processing_charges
            else 0.0
        ),
        "uom": processing_shortage_uom,
        "rate": processing_rate_result["rate"],
        "amount": processing_charge_amount,
        "rate_source": processing_rate_result["source"],
        "source_document_type": processing_rate_result[
            "source_document_type"
        ],
        "source_documents": processing_rate_result[
            "source_documents"
        ],
        "calculation": (
            _build_calculation_text(
                qty=processing_shortage_qty,
                uom=processing_shortage_uom,
                rate=processing_rate_result["rate"],
                amount=processing_charge_amount,
            )
            if recover_processing_charges
            else _("Processing-charge recovery is not recommended.")
        ),
    }

    reasoning = _build_reasoning(
        shortage_qty=shortage_qty,
        shortage_uom=shortage_uom,
        raw_material=raw_material,
        processing_charges=processing_charges,
        total_recovery_amount=total_recovery_amount,
        blocking_errors=blocking_errors,
    )

    status = _get_report_status(
        recover_raw_material=recover_raw_material,
        recover_processing_charges=recover_processing_charges,
        blocking_errors=blocking_errors,
    )

    next_step = _get_next_step(
        recover_raw_material=recover_raw_material,
        recover_processing_charges=recover_processing_charges,
        blocking_errors=blocking_errors,
    )

    return {
        "report_version": 1,
        "status": status,
        "next_step": next_step,
        "currency": _get_currency(facts),
        "quantity": {
            "shortage_qty": shortage_qty,
            "shortage_uom": shortage_uom,
        },
        "raw_material": raw_material,
        "processing_charges": processing_charges,
        "totals": {
            "raw_material_recovery": raw_material_amount,
            "processing_charge_recovery": (
                processing_charge_amount
            ),
            "total_recovery": total_recovery_amount,
        },
        "reasoning": reasoning,
        "integrity": {
            "is_valid": not blocking_errors,
            "warnings": warnings,
            "blocking_errors": blocking_errors,
        },
    }


def _collect_input_integrity(
    facts: dict[str, Any],
    recommendation: dict[str, Any],
    warnings: list[dict[str, Any]],
    blocking_errors: list[dict[str, Any]],
) -> None:
    """Carry forward relevant integrity findings from input reports."""
    fact_integrity = facts.get("integrity") or {}
    recommendation_integrity = (
        recommendation.get("integrity") or {}
    )

    for error in fact_integrity.get("blocking_errors") or []:
        blocking_errors.append(
            {
                "code": "FACT_ENGINE_BLOCKING_ERROR",
                "message": error.get(
                    "message",
                    _("The Fact Engine reported a blocking error."),
                ),
                "source_code": error.get("code"),
                "doctype": error.get("doctype"),
                "document": error.get("document"),
            }
        )

    for error in (
        recommendation_integrity.get("blocking_errors") or []
    ):
        blocking_errors.append(
            {
                "code": "RECOMMENDATION_BLOCKING_ERROR",
                "message": error.get(
                    "message",
                    _(
                        "The Recommendation Engine reported a "
                        "blocking error."
                    ),
                ),
                "source_code": error.get("code"),
            }
        )

    for warning in fact_integrity.get("warnings") or []:
        warnings.append(
            {
                "code": "FACT_ENGINE_WARNING",
                "message": warning.get(
                    "message",
                    _("The Fact Engine reported a warning."),
                ),
                "source_code": warning.get("code"),
                "doctype": warning.get("doctype"),
                "document": warning.get("document"),
            }
        )

    for warning in (
        recommendation_integrity.get("warnings") or []
    ):
        warnings.append(
            {
                "code": "RECOMMENDATION_WARNING",
                "message": warning.get(
                    "message",
                    _(
                        "The Recommendation Engine reported a warning."
                    ),
                ),
                "source_code": warning.get("code"),
            }
        )


def _get_shortage_qty(
    facts: dict[str, Any],
    recommendation: dict[str, Any],
) -> float:
    """Return the authoritative outstanding quantity."""
    summary = facts.get("summary") or {}
    physical = summary.get("physical_inventory") or {}

    candidates = [
        physical.get("outstanding_qty"),
        recommendation.get("outstanding_qty"),
        (
            (recommendation.get("evidence") or {})
            .get("facts", {})
            .get("outstanding_qty")
        ),
    ]

    for value in candidates:
        number = _as_number(value)

        if number:
            return number

    return 0.0


def _get_shortage_uom(
    facts: dict[str, Any],
    recommendation: dict[str, Any],
) -> str:
    """Return the best available outstanding-quantity UOM."""
    summary = facts.get("summary") or {}
    physical = summary.get("physical_inventory") or {}
    components = facts.get("components") or []

    candidates = [
        physical.get("outstanding_uom"),
        recommendation.get("outstanding_uom"),
        (
            (recommendation.get("evidence") or {})
            .get("facts", {})
            .get("outstanding_uom")
        ),
        components[0].get("stock_uom")
        if components
        else None,
    ]

    for value in candidates:
        if value:
            return str(value)

    return ""


def _get_raw_material_rate(
    facts: dict[str, Any],
) -> dict[str, Any]:
    """
    Return the preferred raw material recovery rate.

    Priority
    --------
    1. Weighted actual valuation from submitted material-transfer rows.
    2. Aggregate submitted transfer value / transferred quantity.
    3. SCO component rate.
    """
    material_transfers = facts.get("material_transfers") or {}
    transfers = material_transfers.get("transfers") or []

    submitted_rows = [
        row
        for row in transfers
        if _as_integer(row.get("docstatus")) == 1
        and _as_number(row.get("qty")) > 0
    ]

    total_qty = sum(
        _as_number(row.get("qty"))
        for row in submitted_rows
    )

    total_value = sum(
        _as_number(row.get("basic_amount"))
        for row in submitted_rows
    )

    if total_qty > 0 and total_value > 0:
        return {
            "rate": total_value / total_qty,
            "source": _(
                "Weighted valuation of submitted material transfers"
            ),
            "source_document_type": "Stock Entry",
            "source_documents": sorted(
                {
                    str(row.get("stock_entry"))
                    for row in submitted_rows
                    if row.get("stock_entry")
                }
            ),
        }

    aggregate_qty = _as_number(
        material_transfers.get("total_transferred_qty")
    )

    aggregate_value = _as_number(
        material_transfers.get("total_transfer_value")
    )

    if aggregate_qty > 0 and aggregate_value > 0:
        return {
            "rate": aggregate_value / aggregate_qty,
            "source": _(
                "Aggregate submitted material-transfer valuation"
            ),
            "source_document_type": "Stock Entry",
            "source_documents": (
                material_transfers.get(
                    "submitted_stock_entries"
                )
                or []
            ),
        }

    components = facts.get("components") or []

    component_rates = [
        _as_number(row.get("component_rate"))
        for row in components
        if _as_number(row.get("component_rate")) > 0
    ]

    if component_rates:
        return {
            "rate": component_rates[0],
            "source": _("Subcontracting Order component rate"),
            "source_document_type": "Subcontracting Order",
            "source_documents": [
                (
                    (facts.get("identity") or {}).get(
                        "subcontracting_order"
                    )
                    or ""
                )
            ],
        }

    return _empty_rate_result()


def _get_processing_charge_rate(
    facts: dict[str, Any],
) -> dict[str, Any]:
    """
    Return the preferred processing-charge recovery rate.

    Priority
    --------
    1. Weighted net rate from submitted Purchase Invoice rows.
    2. Weighted net rate from submitted Purchase Receipt rows.
    3. Weighted service cost per quantity from submitted SCR rows.
    4. Draft Purchase Invoice rate as a clearly identified fallback.

    Net rates are used so GST is not treated as part of the base processing
    charge. ERPNext will calculate tax on the eventual accounting document.
    """
    commercial = facts.get("commercial") or {}
    purchase_invoices = (
        commercial.get("purchase_invoices") or []
    )

    submitted_invoice_rows = [
        row
        for row in purchase_invoices
        if _as_integer(row.get("docstatus")) == 1
        and _as_number(row.get("qty")) > 0
    ]

    result = _get_weighted_rate(
        rows=submitted_invoice_rows,
        qty_field="qty",
        preferred_rate_fields=("net_rate", "rate"),
        document_field="purchase_invoice",
        source=_(
            "Weighted net rate from submitted Purchase Invoices"
        ),
        source_document_type="Purchase Invoice",
    )

    if result["rate"] > 0:
        return result

    purchase_receipt_facts = (
        facts.get("purchase_receipts") or {}
    )

    purchase_receipt_rows = (
        purchase_receipt_facts.get("purchase_receipts") or []
    )

    submitted_receipt_rows = [
        row
        for row in purchase_receipt_rows
        if _as_integer(row.get("docstatus")) == 1
        and _as_number(
            row.get("received_qty") or row.get("qty")
        ) > 0
    ]

    result = _get_weighted_rate(
        rows=submitted_receipt_rows,
        qty_field="received_qty",
        fallback_qty_field="qty",
        preferred_rate_fields=("net_rate", "rate"),
        document_field="purchase_receipt",
        source=_(
            "Weighted net rate from submitted Purchase Receipts"
        ),
        source_document_type="Purchase Receipt",
    )

    if result["rate"] > 0:
        return result

    receipt_facts = facts.get("receipts") or {}
    scr_rows = receipt_facts.get("receipt_items") or []

    submitted_scr_rows = [
        row
        for row in scr_rows
        if _as_integer(row.get("docstatus")) == 1
        and _as_number(
            row.get("received_qty") or row.get("qty")
        ) > 0
        and _as_number(
            row.get("service_cost_per_qty")
        ) > 0
    ]

    result = _get_weighted_rate(
        rows=submitted_scr_rows,
        qty_field="received_qty",
        fallback_qty_field="qty",
        preferred_rate_fields=(
            "service_cost_per_qty",
        ),
        document_field="subcontracting_receipt",
        source=_(
            "Weighted service cost from submitted "
            "Subcontracting Receipts"
        ),
        source_document_type="Subcontracting Receipt",
    )

    if result["rate"] > 0:
        return result

    draft_invoice_rows = [
        row
        for row in purchase_invoices
        if _as_integer(row.get("docstatus")) == 0
        and _as_number(row.get("qty")) > 0
    ]

    result = _get_weighted_rate(
        rows=draft_invoice_rows,
        qty_field="qty",
        preferred_rate_fields=("net_rate", "rate"),
        document_field="purchase_invoice",
        source=_(
            "Weighted net rate from draft Purchase Invoices"
        ),
        source_document_type="Purchase Invoice",
    )

    return result


def _get_weighted_rate(
    rows: list[dict[str, Any]],
    qty_field: str,
    preferred_rate_fields: tuple[str, ...],
    document_field: str,
    source: str,
    source_document_type: str,
    fallback_qty_field: str | None = None,
) -> dict[str, Any]:
    """Return a quantity-weighted rate and its document evidence."""
    weighted_value = 0.0
    total_qty = 0.0
    source_documents: set[str] = set()

    for row in rows:
        qty = _as_number(row.get(qty_field))

        if qty <= 0 and fallback_qty_field:
            qty = _as_number(
                row.get(fallback_qty_field)
            )

        rate = 0.0

        for fieldname in preferred_rate_fields:
            candidate = _as_number(row.get(fieldname))

            if candidate > 0:
                rate = candidate
                break

        if qty <= 0 or rate <= 0:
            continue

        total_qty += qty
        weighted_value += qty * rate

        if row.get(document_field):
            source_documents.add(
                str(row.get(document_field))
            )

    if total_qty <= 0 or weighted_value <= 0:
        return _empty_rate_result()

    return {
        "rate": weighted_value / total_qty,
        "source": source,
        "source_document_type": source_document_type,
        "source_documents": sorted(source_documents),
    }

def _get_processing_shortage_qty(
    facts: dict[str, Any],
) -> float:
    """
    Return the finished-goods quantity variance relevant to
    processing-charge recovery.

    Positive quantity means the supplier claimed/invoiced more processed
    quantity than the company accepted.
    """
    plr_facts = (
        facts.get("processor_lot_receipts") or {}
    )

    variance_qty = _as_number(
        plr_facts.get(
            "total_supplier_vs_company_qty"
        )
    )

    return max(variance_qty, 0.0)


def _get_processing_shortage_uom(
    facts: dict[str, Any],
) -> str:
    """Return the finished-item UOM for processing-charge recovery."""
    plr_facts = (
        facts.get("processor_lot_receipts") or {}
    )

    return str(
        plr_facts.get("stock_uom") or ""
    )

def _build_reasoning(
    shortage_qty: float,
    shortage_uom: str,
    raw_material: dict[str, Any],
    processing_charges: dict[str, Any],
    total_recovery_amount: float,
    blocking_errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build a presentation-independent calculation trail."""
    reasoning: list[dict[str, Any]] = [
        {
            "sequence": 10,
            "type": "fact",
            "code": "RECOVERY_QUANTITY",
            "message": _(
                "Recovery quantity: {0}{1}."
            ).format(
                _format_number(shortage_qty),
                f" {shortage_uom}" if shortage_uom else "",
            ),
        }
    ]

    if raw_material.get("recommended"):
        reasoning.append(
            {
                "sequence": 20,
                "type": "calculation",
                "code": "RAW_MATERIAL_RECOVERY",
                "message": _(
                    "Raw material recovery: {0} × {1} = {2}."
                ).format(
                    _format_number(shortage_qty),
                    _format_number(raw_material.get("rate")),
                    _format_number(raw_material.get("amount")),
                ),
            }
        )

        reasoning.append(
            {
                "sequence": 25,
                "type": "source",
                "code": "RAW_MATERIAL_RATE_SOURCE",
                "message": _("Raw material rate source: {0}.").format(
                    raw_material.get("rate_source") or ""
                ),
            }
        )

    if processing_charges.get("recommended"):
        processing_qty = _as_number(
            processing_charges.get("quantity")
        )

        processing_uom = (
            processing_charges.get("uom") or ""
        )

        reasoning.append(
            {
                "sequence": 30,
                "type": "calculation",
                "code": "PROCESSING_CHARGE_RECOVERY",
                "message": _(
                    "Processing-charge recovery: {0}{1} × {2} = {3}."
                ).format(
                    _format_number(processing_qty),
                    (
                        f" {processing_uom}"
                        if processing_uom
                        else ""
                    ),
                    _format_number(
                        processing_charges.get("rate")
                    ),
                    _format_number(
                        processing_charges.get("amount")
                    ),
                ),
            }
        )

        reasoning.append(
            {
                "sequence": 35,
                "type": "source",
                "code": "PROCESSING_RATE_SOURCE",
                "message": _(
                    "Processing-charge rate source: {0}."
                ).format(
                    processing_charges.get("rate_source") or ""
                ),
            }
        )

    if blocking_errors:
        reasoning.append(
            {
                "sequence": 90,
                "type": "integrity",
                "code": "RECOVERY_CALCULATION_BLOCKED",
                "message": _(
                    "The recovery calculation cannot be approved until "
                    "the blocking errors are resolved."
                ),
            }
        )
    else:
        reasoning.append(
            {
                "sequence": 90,
                "type": "total",
                "code": "TOTAL_RECOVERY",
                "message": _(
                    "Total recommended recovery: {0}."
                ).format(
                    _format_number(total_recovery_amount)
                ),
            }
        )

    return reasoning


def _get_report_status(
    recover_raw_material: bool,
    recover_processing_charges: bool,
    blocking_errors: list[dict[str, Any]],
) -> str:
    """Return the Recovery Report status."""
    if blocking_errors:
        return "Blocked"

    if recover_raw_material or recover_processing_charges:
        return "Recovery Calculated"

    return "No Recovery Required"


def _get_next_step(
    recover_raw_material: bool,
    recover_processing_charges: bool,
    blocking_errors: list[dict[str, Any]],
) -> str:
    """Return the next operational step."""
    if blocking_errors:
        return "Resolve calculation errors"

    if recover_raw_material or recover_processing_charges:
        return "Review recovery and create draft settlement document"

    return "Return to lot reconciliation"


def _get_currency(facts: dict[str, Any]) -> str:
    """
    Return the transaction currency when exposed by the Fact Engine.

    The current Fact Engine may not yet expose currency. In that case the
    UI can use the company/default currency while the report remains valid.
    """
    candidates = [
        (facts.get("identity") or {}).get("currency"),
        (facts.get("commercial") or {}).get("currency"),
        (facts.get("summary") or {}).get("currency"),
    ]

    for value in candidates:
        if value:
            return str(value)

    return ""


def _build_calculation_text(
    qty: float,
    uom: str,
    rate: float,
    amount: float,
) -> str:
    """Return a compact human-readable calculation."""
    quantity_text = _format_number(qty)

    if uom:
        quantity_text = f"{quantity_text} {uom}"

    return _("{0} × {1} = {2}").format(
        quantity_text,
        _format_number(rate),
        _format_number(amount),
    )


def _empty_rate_result() -> dict[str, Any]:
    """Return an empty rate result with a stable schema."""
    return {
        "rate": 0.0,
        "source": "",
        "source_document_type": "",
        "source_documents": [],
    }


def _format_number(value: Any) -> str:
    """Return a readable number without unnecessary trailing zeroes."""
    number = _as_number(value)

    return f"{number:,.3f}".rstrip("0").rstrip(".")


def _as_number(value: Any) -> float:
    """Return a safe floating-point number."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_integer(value: Any) -> int:
    """Return a safe integer value."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0