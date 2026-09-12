"""J19B2G read-only retained-material Sales Invoice draft readiness.

No function in this module creates a document, allocates a name, changes stock,
posts accounting or tax, or closes a Processor Lot.
"""

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import re


CONTRACT_VERSION = "J19B2G"
TREATMENT = "SALES_INVOICE"
COORDINATION_DISABLED = "DISABLED"
COORDINATION_TALLY = "ERPNEXT_FORECAST_WITH_TALLY_COORDINATION"


def attach_sales_invoice_draft_readiness(report, context):
    """Attach fail-closed, exact-scope draft facts to retained-material rows."""
    result = deepcopy(report or {})
    context = deepcopy(context or {})
    count = 0
    for row in result.get("components") or []:
        readiness = _assess(row, context)
        row["retained_material_sales_invoice_draft_readiness"] = readiness
        if readiness["applicable"]:
            count += 1
    result.update(
        retained_material_sales_invoice_draft_readiness_contract_version=CONTRACT_VERSION,
        retained_material_sales_invoice_draft_readiness_rows=count,
        commercial_execution_ready=False,
        commercial_document_creation_enabled=False,
        commercial_document_authorized=False,
        stock_document_authorized=False,
        accounting_posting_authorized=False,
        tax_posting_authorized=False,
        lot_closure_authorized=False,
    )
    return result


def forecast_invoice_number(pattern, series_current, existing_names):
    """Return the next number without updating Series; raise on ambiguity."""
    match = re.fullmatch(r"(.*?)(#+)", str(pattern or ""))
    if not match:
        raise ValueError("OUTWARD_SALES_INVOICE_SERIES_INVALID")
    prefix, marks = match.groups()
    width = len(marks)
    expression = re.compile(r"^" + re.escape(prefix) + r"(\d{" + str(width) + r",})$")
    numbers = []
    for name in existing_names or []:
        found = expression.fullmatch(str(name or ""))
        if not found:
            raise ValueError("OUTWARD_SALES_INVOICE_EXISTING_NAME_INVALID")
        numbers.append(int(found.group(1)))
    if len(numbers) != len(set(numbers)):
        raise ValueError("OUTWARD_SALES_INVOICE_NUMERIC_SEQUENCE_AMBIGUOUS")
    current = _integer(series_current)
    observed = max(numbers) if numbers else 0
    # A document beyond the counter means the chosen series is not authoritative.
    if current is not None and observed > current:
        raise ValueError("OUTWARD_SALES_INVOICE_SERIES_COUNTER_BEHIND_DOCUMENTS")
    source = (
        "SERIES_COUNTER_CROSS_CHECKED_WITH_EXISTING_DOCUMENTS"
        if current is not None else "HIGHEST_EXISTING_SALES_INVOICE_NAME"
    )
    basis = current if current is not None else observed
    return {
        "configured_series": pattern,
        "series_prefix": prefix,
        "series_current": current,
        "highest_existing_number": observed,
        "forecast_number": prefix + str(basis + 1).zfill(width),
        "forecast_source": source,
        "forecast_status": "FORECAST_ONLY_NOT_RESERVED",
    }


def _assess(row, context):
    base = {
        "contract_version": CONTRACT_VERSION,
        "applicable": False,
        "readiness_code": "NOT_SELECTED_RETAINED_MATERIAL_SALES_INVOICE",
        "blocking_issues": [],
        "commercial_execution_ready": False,
        "commercial_document_creation_enabled": False,
        "commercial_document_authorized": False,
        "stock_document_authorized": False,
        "accounting_posting_authorized": False,
        "tax_posting_authorized": False,
        "lot_closure_authorized": False,
    }
    disposition = row.get("persisted_material_disposition") or {}
    classification = row.get("persisted_classification") or {}
    applicable = bool(
        disposition.get("disposition") == "RETAINED_BY_PROCESSOR"
        and classification.get("classification") == "PROCESSOR_RESPONSIBLE"
        and classification.get("selected_treatment_method") == TREATMENT
    )
    if not applicable:
        return base

    issues = []
    quantity = _number(row.get("suggested_recovery_quantity"))
    rate = _number(row.get("suggested_recovery_rate"))
    amount = _number(row.get("suggested_recovery_amount"))
    if quantity is None or quantity <= 0:
        _issue(issues, "SALES_INVOICE_QUANTITY_NOT_READY")
    if rate is None or rate < 0:
        _issue(issues, "SALES_INVOICE_RATE_NOT_READY")
    if amount is None or quantity is None or rate is None or abs(amount - quantity * rate) > Decimal("0.01"):
        _issue(issues, "SALES_INVOICE_AMOUNT_NOT_READY")

    required = {
        "company": "SALES_INVOICE_COMPANY_NOT_READY",
        "customer": "SALES_INVOICE_CUSTOMER_NOT_READY",
        "customer_address": "SALES_INVOICE_CUSTOMER_ADDRESS_NOT_READY",
        "item_code": "SALES_INVOICE_ITEM_NOT_READY",
        "warehouse": "SALES_INVOICE_WAREHOUSE_NOT_READY",
        "income_account": "SALES_INVOICE_INCOME_ACCOUNT_NOT_READY",
        "cost_center": "SALES_INVOICE_COST_CENTER_NOT_READY",
        "decision_event": "TREATMENT_DECISION_EVENT_NOT_READY",
        "policy_reconciliation_event": "POLICY_RECONCILIATION_EVENT_NOT_READY",
    }
    for key, code in required.items():
        if not context.get(key):
            _issue(issues, code)
    if context.get("scope_key") != row.get("commercial_scope_key"):
        _issue(issues, "COMMERCIAL_SCOPE_CHANGED")
    if context.get("duplicate_documents"):
        _issue(issues, "EXISTING_RETAINED_MATERIAL_SALES_INVOICE")
    for issue in context.get("stale_state_issues") or []:
        _issue(issues, issue)

    mode = context.get("coordination_mode") or COORDINATION_DISABLED
    forecast = context.get("invoice_number_forecast")
    if mode == COORDINATION_TALLY and not forecast:
        _issue(issues, "TALLY_INVOICE_NUMBER_FORECAST_NOT_READY")
    elif mode not in (COORDINATION_DISABLED, COORDINATION_TALLY):
        _issue(issues, "INVOICE_NUMBER_COORDINATION_MODE_INVALID")

    facts = {
        "company": context.get("company"),
        "customer": context.get("customer"),
        "customer_address": context.get("customer_address"),
        "item_code": context.get("item_code"),
        "qty": float(quantity) if quantity is not None else None,
        "uom": row.get("stock_uom"),
        "rate": float(rate) if rate is not None else None,
        "net_amount_excluding_tax": float(amount) if amount is not None else None,
        "warehouse": context.get("warehouse"),
        "update_stock": 1,
        "income_account": context.get("income_account"),
        "cost_center": context.get("cost_center"),
        "branch": context.get("branch"),
    }
    return {
        **base,
        "applicable": True,
        "readiness_code": (
            "SALES_INVOICE_DRAFT_FACTS_READY_FUTURE_CREATION_DEFERRED"
            if not issues else "SALES_INVOICE_DRAFT_FACTS_NOT_READY"
        ),
        "blocking_issues": issues,
        "draft_values": facts,
        "lineage": {
            "processor_lot": context.get("processor_lot"),
            "scope_key": context.get("scope_key"),
            "material_disposition": disposition.get("name"),
            "commercial_classification": classification.get("name"),
            "policy_reconciliation_event": context.get("policy_reconciliation_event"),
            "treatment_decision_event": context.get("decision_event"),
            "disposition_revision": disposition.get("disposition_revision"),
            "classification_revision": classification.get("classification_revision"),
            "treatment_revision": classification.get("treatment_revision"),
        },
        "tax_calculation_status": "DEFERRED_TO_STANDARD_ERPNEXT_SALES_INVOICE_TAX_RESOLUTION",
        "invoice_number_lead_system": "ERPNEXT",
        "invoice_number_coordination_mode": mode,
        "external_invoice_system": context.get("external_system_name") if mode == COORDINATION_TALLY else None,
        "invoice_number_forecast": forecast,
        "tally_reservation_confirmation_required_before_draft_creation": mode == COORDINATION_TALLY,
        "critical_coordination_warning": (
            "Forecast only: reserve the confirmed ERPNext outward Sales Invoice number in Tally before future draft creation."
            if mode == COORDINATION_TALLY else None
        ),
    }


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return value if value.is_finite() else None


def _integer(value):
    if value in (None, ""):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _issue(issues, code):
    if code not in issues:
        issues.append(code)
