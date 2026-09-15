"""J19B2K read-only controlled Sales Invoice submission readiness.

This module never submits a document, writes ledger rows, generates statutory
documents, posts tax, changes stock, or closes a Processor Lot.
"""

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json


CONTRACT_VERSION = "J19B2K"
TALLY_MODE = "ERPNEXT_FORECAST_WITH_TALLY_COORDINATION"


def attach_sales_invoice_submission_readiness(report, context):
    """Attach exact-scope, fail-closed submission evidence to commercial rows."""
    result = deepcopy(report or {})
    context = deepcopy(context or {})
    count = 0
    for row in result.get("components") or []:
        readiness = _assess(row, context)
        row["retained_material_sales_invoice_submission_readiness"] = readiness
        if readiness["applicable"]:
            count += 1
    result.update(
        retained_material_sales_invoice_submission_readiness_contract_version=(
            CONTRACT_VERSION
        ),
        retained_material_sales_invoice_submission_readiness_rows=count,
        sales_invoice_submission_enabled=False,
        submission_authorized=False,
        stock_posting_authorized=False,
        accounting_posting_authorized=False,
        statutory_generation_authorized=False,
        tax_posting_authorized=False,
        lot_closure_authorized=False,
    )
    return result


def _assess(row, context):
    base = {
        "contract_version": CONTRACT_VERSION,
        "applicable": False,
        "readiness_code": "NO_CONTROLLED_RETAINED_MATERIAL_SALES_INVOICE_DRAFT",
        "blocking_issues": [],
        "sales_invoice_submission_enabled": False,
        "submission_authorized": False,
        "stock_posting_authorized": False,
        "accounting_posting_authorized": False,
        "statutory_generation_authorized": False,
        "tax_posting_authorized": False,
        "lot_closure_authorized": False,
    }
    draft_readiness = row.get("retained_material_sales_invoice_draft_readiness") or {}
    if not draft_readiness.get("sales_invoice_draft_creation_event"):
        return base

    issues = []
    invoice = context.get("sales_invoice") or {}
    event = context.get("draft_creation_event") or {}
    disposition = row.get("persisted_material_disposition") or {}
    classification = row.get("persisted_classification") or {}
    lineage = draft_readiness.get("lineage") or {}

    if context.get("invoice_count") != 1:
        _issue(issues, "CONTROLLED_SALES_INVOICE_DRAFT_MISSING_OR_AMBIGUOUS")
    if context.get("draft_creation_event_count") != 1:
        _issue(issues, "SALES_INVOICE_DRAFT_CREATION_EVIDENCE_MISSING_OR_AMBIGUOUS")
    if invoice.get("docstatus") != 0:
        _issue(issues, "CONTROLLED_SALES_INVOICE_IS_NOT_DRAFT")
    if invoice.get("name") != event.get("sales_invoice"):
        _issue(issues, "CONTROLLED_SALES_INVOICE_EVENT_MISMATCH")
    if invoice.get("custom_processor_lot_settlement") != lineage.get("processor_lot"):
        _issue(issues, "CONTROLLED_SALES_INVOICE_PROCESSOR_LOT_CHANGED")
    if (invoice.get("custom_invoice_number_reservation") != event.get("reservation")
            or invoice.get("custom_tally_reservation_confirmation")
            != event.get("tally_confirmation")):
        _issue(issues, "CONTROLLED_SALES_INVOICE_COORDINATION_LINEAGE_CHANGED")

    items = context.get("sales_invoice_items") or []
    if len(items) != 1:
        _issue(issues, "CONTROLLED_SALES_INVOICE_ITEM_COUNT_CHANGED")
    else:
        item = items[0]
        pairs = (
            (item.get("custom_processor_lot_scope_key"), lineage.get("scope_key")),
            (item.get("custom_material_disposition"), disposition.get("name")),
            (item.get("custom_commercial_classification"), classification.get("name")),
            (item.get("custom_policy_reconciliation_event"),
             lineage.get("policy_reconciliation_event")),
            (item.get("custom_treatment_decision_event"),
             lineage.get("treatment_decision_event")),
            (_integer(item.get("custom_disposition_revision")),
             _integer(disposition.get("disposition_revision"))),
            (_integer(item.get("custom_classification_revision")),
             _integer(classification.get("classification_revision"))),
            (_integer(item.get("custom_treatment_revision")),
             _integer(classification.get("treatment_revision"))),
            (item.get("warehouse"), event.get("supplier_warehouse")),
        )
        if any(actual != expected for actual, expected in pairs):
            _issue(issues, "CONTROLLED_SALES_INVOICE_ITEM_LINEAGE_CHANGED")
        for actual, expected in (
            (item.get("qty"), event.get("recovery_quantity")),
            (item.get("rate"), event.get("material_content_rate")),
            (invoice.get("net_total"), event.get("net_total")),
            (invoice.get("total_taxes_and_charges"),
             event.get("total_taxes_and_charges")),
            (invoice.get("grand_total"), event.get("grand_total")),
        ):
            if not _same_number(actual, expected):
                _issue(issues, "CONTROLLED_SALES_INVOICE_COMMERCIAL_VALUES_CHANGED")

    for code in context.get("live_state_issues") or []:
        _issue(issues, code)
    if context.get("linked_stock_ledger_entries"):
        _issue(issues, "CONTROLLED_SALES_INVOICE_ALREADY_HAS_STOCK_POSTING")
    if context.get("linked_gl_entries"):
        _issue(issues, "CONTROLLED_SALES_INVOICE_ALREADY_HAS_ACCOUNTING_POSTING")
    if context.get("settlement_started"):
        _issue(issues, "PROCESSOR_LOT_SETTLEMENT_ALREADY_BEGUN")

    tally_lead = context.get("coordination_mode") == TALLY_MODE
    statutory = context.get("statutory_evidence") or {}
    confirmation_count = context.get("statutory_evidence_confirmation_count") or 0
    confirmation = context.get("statutory_evidence_confirmation") or {}
    controlled_no_movement = False
    if confirmation_count > 1:
        _issue(issues, "AMBIGUOUS_TALLY_STATUTORY_EVIDENCE_CONFIRMATION")
    elif confirmation_count == 1:
        snapshot = confirmation.get("statutory_evidence_snapshot")
        try:
            snapshot = json.loads(snapshot) if isinstance(snapshot, str) else snapshot
        except (TypeError, ValueError):
            snapshot = None
        controlled_no_movement = bool(
            confirmation.get("sales_invoice") == invoice.get("name")
            and confirmation.get("scope_key") == lineage.get("scope_key")
            and confirmation.get("draft_creation_event") == event.get("name")
            and confirmation.get("evidence_outcome")
            == "NOT_APPLICABLE_NO_PHYSICAL_MOVEMENT"
            and confirmation.get("confirmation_attested")
            and _canonical(snapshot) == _canonical(statutory)
        )
        if not controlled_no_movement:
            _issue(issues, "TALLY_STATUTORY_EVIDENCE_CONFIRMATION_STALE")
    if tally_lead:
        if (not controlled_no_movement and not statutory.get("ewaybill")
                and statutory.get("ewaybill_applicable")):
            _issue(issues, "TALLY_STATUTORY_REFERENCE_NOT_RECORDED")
        if statutory.get("transport_details_required") and not controlled_no_movement:
            if not statutory.get("vehicle_no"):
                _issue(issues, "TALLY_VEHICLE_NUMBER_NOT_RECORDED")
            if not statutory.get("lr_date"):
                _issue(issues, "TALLY_TRANSPORT_RECEIPT_DATE_NOT_RECORDED")
        if (statutory.get("allow_blank_transport_details")
                and not statutory.get("controlled_non_applicability_evidence")):
            _issue(issues, "BLANK_TRANSPORT_OVERRIDE_HAS_NO_CONTROLLED_JUSTIFICATION")
        if statutory.get("einvoice_applicable") and not statutory.get("irn"):
            _issue(issues, "TALLY_EINVOICE_REFERENCE_NOT_RECORDED")

    stock = context.get("stock_projection") or {}
    gl_rows = context.get("projected_gl_entries") or []
    if stock.get("quantity_after") is None or stock.get("stock_value_reduction") is None:
        _issue(issues, "PROJECTED_STOCK_CONSEQUENCE_NOT_READY")
    if not gl_rows:
        _issue(issues, "PROJECTED_ACCOUNTING_CONSEQUENCE_NOT_READY")

    return {
        **base,
        "applicable": True,
        "readiness_code": (
            "SALES_INVOICE_DRAFT_READY_FOR_FUTURE_CONTROLLED_SUBMISSION"
            if not issues else "SALES_INVOICE_DRAFT_NOT_READY_FOR_CONTROLLED_SUBMISSION"
        ),
        "blocking_issues": issues,
        "sales_invoice": invoice.get("name"),
        "sales_invoice_modified": invoice.get("modified"),
        "draft_creation_event": event.get("name"),
        "operational_lead_system": "Tally" if tally_lead else "ERPNext",
        "statutory_lead_system": "Tally" if tally_lead else "ERPNext",
        "statutory_evidence": statutory,
        "statutory_evidence_confirmation": confirmation or None,
        "statutory_evidence_confirmation_available": bool(
            context.get("statutory_evidence_confirmation_enabled")
            and tally_lead and not confirmation_count
            and set(issues) == {
                "TALLY_STATUTORY_REFERENCE_NOT_RECORDED",
                "TALLY_VEHICLE_NUMBER_NOT_RECORDED",
            }
        ),
        "stock_projection": stock,
        "projected_gl_entries": gl_rows,
        "tax_calculation_status": "CALCULATED_ON_DRAFT_NOT_POSTED",
    }


def _same_number(actual, expected):
    try:
        return Decimal(str(actual)) == Decimal(str(expected))
    except (InvalidOperation, TypeError, ValueError):
        return False


def _integer(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return None


def _issue(issues, code):
    if code not in issues:
        issues.append(code)


def _canonical(value):
    return json.loads(json.dumps(value, sort_keys=True, default=str))
