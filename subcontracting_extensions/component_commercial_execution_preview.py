"""Pure J19B2A dispatch-cost and commercial-execution readiness preview.

This module never writes, authorises a document, infers a recovery quantity, or
authorises Processor Lot closure.  It only derives a historical dispatch-cost
suggestion from submitted Send to Subcontractor evidence at exact SCO supplied
row scope.
"""

from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


CONTRACT_VERSION = "J19B2A"
RETAINED_FACT_CONTRACT_VERSION = "J19B2C"
RETAINED_MATERIAL_EVIDENCE = "RAW_MATERIAL_RETAINED_BY_PROCESSOR"
RETAINED_QUANTITY_SOURCE = "PERSISTED_FULL_RESIDUAL_MATERIAL_DISPOSITION"
RATE_SOURCE = "HISTORICAL_SEND_TO_SUBCONTRACTOR"
PRECISION = Decimal("0.000001")
AMOUNT_TOLERANCE = Decimal("0.01")


def attach_commercial_execution_preview(commercial_preview, movements, identity):
    """Attach read-only rate evidence to each exact raw-material scope."""
    result = deepcopy(commercial_preview or {})
    movements = deepcopy(movements or [])
    identity = deepcopy(identity or {})
    for row in result.get("components") or []:
        _attach_component_rate(row, movements, identity)

    result.update(
        commercial_execution_preview_contract_version=CONTRACT_VERSION,
        retained_material_commercial_fact_contract_version=RETAINED_FACT_CONTRACT_VERSION,
        commercial_execution_scope=(
            "Historical material-content dispatch-cost suggestion at exact SCO "
            "supplied-row scope; "
            "no recovery quantity, tax treatment, document action, or lot closure"
        ),
        commercial_document_creation_enabled=False,
        commercial_document_authorized=False,
        lot_closure_authorized=False,
    )
    return result


def _attach_component_rate(row, movements, identity):
    exact = [
        movement for movement in movements
        if movement.get("sco_rm_detail") == row.get("sco_supplied_item")
    ]
    valid = []
    issues = []
    candidates = [
        movement for movement in exact
        if movement.get("movement_direction") == "Send to Subcontractor"
        and movement.get("evidence_role") == "Physical transfer or return"
    ]
    for movement in candidates:
        if not _valid_dispatch_identity(movement, row, identity):
            _issue(issues, "DISPATCH_COST_IDENTITY_MISMATCH")
            continue
        quantity = _number(movement.get("stock_qty"))
        if quantity is None or quantity <= 0:
            _issue(issues, "DISPATCH_COST_QUANTITY_INVALID")
            continue
        rate = _dispatch_rate(movement, quantity, issues)
        if rate is None:
            continue
        valid.append({
            "stock_entry": movement.get("parent"),
            "stock_entry_item": movement.get("name"),
            "posting_date": movement.get("posting_date"),
            "posting_time": movement.get("posting_time"),
            "source_warehouse": movement.get("s_warehouse"),
            "target_warehouse": movement.get("t_warehouse"),
            "stock_qty": float(quantity),
            "stock_uom": movement.get("stock_uom"),
            "dispatch_basic_rate": float(rate),
            "dispatch_basic_amount": float(quantity * rate),
            "rate_source": RATE_SOURCE,
        })

    total_quantity = sum((_number(evidence["stock_qty"]) for evidence in valid), Decimal(0))
    total_amount = sum((_number(evidence["dispatch_basic_amount"]) for evidence in valid), Decimal(0))
    suggested_rate = total_amount / total_quantity if total_quantity > 0 else None
    if not candidates:
        _issue(issues, "NO_EXACT_DISPATCH_COST_EVIDENCE")
    if candidates and not valid and not issues:
        _issue(issues, "DISPATCH_COST_EVIDENCE_INVALID")

    persisted = row.get("persisted_classification") or {}
    treatment = persisted.get("selected_treatment_method")
    classification = persisted.get("classification")
    unaccounted = _number(row.get("unaccounted_remaining_qty"))
    retained = bool(
        row.get("material_disposition_current")
        and (row.get("persisted_material_disposition") or {}).get("disposition")
            == "RETAINED_BY_PROCESSOR"
    )
    recovery_quantity = unaccounted if retained and unaccounted and unaccounted > 0 else None
    recovery_amount = (
        recovery_quantity * suggested_rate
        if recovery_quantity is not None and suggested_rate is not None and not issues
        else None
    )
    if issues or suggested_rate is None:
        readiness = "REVIEW_DISPATCH_COST_EVIDENCE"
    elif unaccounted is not None and unaccounted > 0 and not (
        row.get("material_disposition_current")
        and (row.get("persisted_material_disposition") or {}).get("disposition")
            == "RETAINED_BY_PROCESSOR"
    ):
        # A physical balance is not automatically a sale.  Return, material
        # credit/carry-forward, sale, or another controlled disposition must be
        # established before classification or commercial execution.
        readiness = "DEFINE_MATERIAL_DISPOSITION"
    elif retained and not classification:
        readiness = "PERSIST_COMMERCIAL_CLASSIFICATION"
    elif retained and classification in {
        "COMPANY_RESPONSIBLE", "NO_COMMERCIAL_ACTION_REQUIRED"
    }:
        readiness = "NO_COMMERCIAL_EXECUTION_REQUIRED"
    elif retained:
        readiness = "COMMERCIAL_TREATMENT_DEFERRED_J19B2C"
    elif not classification:
        readiness = "PERSIST_COMMERCIAL_CLASSIFICATION"
    elif classification == "NO_COMMERCIAL_ACTION_REQUIRED":
        readiness = "NO_COMMERCIAL_EXECUTION_REQUIRED"
    elif not treatment:
        readiness = "SELECT_COMMERCIAL_TREATMENT"
    elif treatment == "SALES_INVOICE":
        # J19B2A intentionally has no authoritative sale/disposition quantity.
        readiness = "DEFINE_COMMERCIAL_RECOVERY_QUANTITY"
    else:
        readiness = "SELECTED_TREATMENT_NOT_EXECUTABLE_IN_J19B2A"

    row.update(
        dispatch_cost_evidence=valid,
        dispatch_cost_issues=issues,
        dispatch_cost_quantity=float(total_quantity),
        dispatch_cost_amount=float(total_amount),
        suggested_recovery_rate=(float(suggested_rate) if suggested_rate is not None else None),
        suggested_recovery_rate_source=(RATE_SOURCE if suggested_rate is not None else None),
        suggested_recovery_rate_basis=(
            "Historical material-content cost carried by the exact Send to "
            "Subcontractor row(s); excludes ABC processing, consumable, labour, "
            "machine, overhead, and pending subcontracting costs"
            if suggested_rate is not None else None
        ),
        suggested_recovery_quantity=(
            float(recovery_quantity) if recovery_quantity is not None else None
        ),
        suggested_recovery_amount=(
            float(recovery_amount) if recovery_amount is not None else None
        ),
        recovery_quantity_source=(
            RETAINED_QUANTITY_SOURCE if recovery_quantity is not None else None
        ),
        retained_material_classification_ready=bool(
            retained and recovery_quantity is not None
            and recovery_amount is not None and not issues
        ),
        commercial_execution_readiness_code=readiness,
        commercial_execution_ready=False,
        commercial_document_creation_enabled=False,
        commercial_document_authorized=False,
        lot_closure_authorized=False,
    )
    if row["retained_material_classification_ready"]:
        row.update(
            commercial_decision_code=RETAINED_MATERIAL_EVIDENCE,
            commercial_review_permitted=True,
            raw_material_recovery_recommended=True,
        )


def _valid_dispatch_identity(movement, row, identity):
    return bool(
        movement.get("docstatus") == 1
        and not movement.get("is_return")
        and movement.get("purpose") == "Send to Subcontractor"
        and movement.get("movement_direction") == "Send to Subcontractor"
        and movement.get("evidence_role") == "Physical transfer or return"
        and movement.get("subcontracting_order") == identity.get("subcontracting_order")
        and movement.get("company") == identity.get("company")
        and movement.get("supplier") == identity.get("supplier")
        and movement.get("t_warehouse") == identity.get("supplier_warehouse")
        and movement.get("s_warehouse")
        and movement.get("s_warehouse") != identity.get("supplier_warehouse")
        and movement.get("item_code") == row.get("component_item")
        and movement.get("stock_uom") == row.get("stock_uom")
    )


def _dispatch_rate(movement, quantity, issues):
    rate = _number(movement.get("basic_rate"))
    amount = _number(movement.get("basic_amount"))
    if rate is None and amount is not None and amount >= 0:
        rate = amount / quantity
    if rate is None or rate < 0:
        _issue(issues, "DISPATCH_COST_RATE_INVALID")
        return None
    expected = quantity * rate
    if amount is not None and abs(amount - expected) > AMOUNT_TOLERANCE:
        _issue(issues, "DISPATCH_COST_AMOUNT_MISMATCH")
        return None
    return rate


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not number.is_finite():
        return None
    return number.quantize(PRECISION, rounding=ROUND_HALF_UP)


def _issue(issues, code):
    if code not in issues:
        issues.append(code)
