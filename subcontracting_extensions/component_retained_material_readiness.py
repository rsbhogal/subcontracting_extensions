"""Pure J19B2D retained-material treatment readiness; never executes treatment."""

from copy import deepcopy
from decimal import Decimal, InvalidOperation


CONTRACT_VERSION = "J19B2D"
RETAINED_EVIDENCE = "RAW_MATERIAL_RETAINED_BY_PROCESSOR"
RETAINED_DISPOSITION = "RETAINED_BY_PROCESSOR"
RESPONSIBLE_CLASSIFICATION = "PROCESSOR_RESPONSIBLE"
ELIGIBLE_TREATMENT = "SALES_INVOICE"
AMOUNT_TOLERANCE = Decimal("0.01")


def attach_retained_material_treatment_readiness(report, context):
    """Attach a read-only future Sales Invoice-with-stock consequence preview."""
    result = deepcopy(report or {})
    context = deepcopy(context or {})
    retained_rows = 0
    for row in result.get("components") or []:
        readiness = _assess_row(row, result, context)
        row["retained_material_treatment_readiness"] = readiness
        if readiness.get("applicable"):
            retained_rows += 1

    result.update(
        retained_material_treatment_readiness_contract_version=CONTRACT_VERSION,
        retained_material_treatment_readiness_scope=(
            "Read-only exact-scope readiness for a future Sales Invoice with Update Stock; "
            "no treatment selection, document, stock posting, accounting posting, or lot closure"
        ),
        retained_material_treatment_readiness_rows=retained_rows,
        treatment_selection_available=False,
        commercial_execution_ready=False,
        commercial_document_creation_enabled=False,
        commercial_document_authorized=False,
        stock_document_authorized=False,
        lot_closure_authorized=False,
    )
    return result


def _assess_row(row, report, context):
    base = {
        "contract_version": CONTRACT_VERSION,
        "applicable": False,
        "readiness_code": "NOT_RETAINED_PROCESSOR_MATERIAL",
        "eligible_treatments": [],
        "treatment_selection_available": False,
        "commercial_execution_ready": False,
        "commercial_document_creation_enabled": False,
        "commercial_document_authorized": False,
        "stock_document_authorized": False,
        "lot_closure_authorized": False,
    }
    disposition = row.get("persisted_material_disposition") or {}
    classification = row.get("persisted_classification") or {}
    applicable = bool(
        row.get("commercial_decision_code") == RETAINED_EVIDENCE
        and row.get("material_disposition_current") is True
        and disposition.get("disposition") == RETAINED_DISPOSITION
    )
    if not applicable:
        return base

    issues = []
    quantity = _number(row.get("suggested_recovery_quantity"))
    rate = _number(row.get("suggested_recovery_rate"))
    amount = _number(row.get("suggested_recovery_amount"))
    bin_qty = _number(context.get("supplier_warehouse_actual_qty"))
    bin_rate = _number(context.get("supplier_warehouse_valuation_rate"))
    bin_value = _number(context.get("supplier_warehouse_stock_value"))

    quantity_ready = bool(quantity is not None and quantity > 0)
    rate_ready = bool(rate is not None and rate >= 0 and not row.get("dispatch_cost_issues"))
    amount_ready = bool(
        amount is not None and quantity_ready and rate_ready
        and abs(amount - quantity * rate) <= AMOUNT_TOLERANCE
    )
    stock_scope_unambiguous = context.get("retained_scope_count") == 1
    stock_ready = bool(
        stock_scope_unambiguous
        and quantity_ready and bin_qty is not None and bin_qty >= quantity
        and bin_rate is not None and bin_value is not None
    )
    if not quantity_ready:
        _issue(issues, "RETAINED_MATERIAL_RECOVERY_QUANTITY_NOT_READY")
    if not rate_ready:
        _issue(issues, "RETAINED_MATERIAL_RECOVERY_RATE_NOT_READY")
    if not amount_ready:
        _issue(issues, "RETAINED_MATERIAL_RECOVERY_AMOUNT_NOT_READY")
    if not stock_scope_unambiguous:
        _issue(issues, "RETAINED_MATERIAL_STOCK_SCOPE_AMBIGUOUS")
    elif not stock_ready:
        _issue(issues, "RETAINED_MATERIAL_SUPPLIER_WAREHOUSE_STOCK_NOT_READY")

    po_policy = context.get("purchase_order_policy") or {}
    lot_policy = context.get("processor_lot_policy") or {}
    policy_reconciled = bool(
        not lot_policy.get("override_settlement_policy")
        and bool(po_policy.get("recover_raw_material_shortage"))
        and _same_policy(po_policy, lot_policy)
    )
    if not policy_reconciled:
        _issue(issues, "PROCESSOR_LOT_POLICY_DIFFERS_FROM_PURCHASE_ORDER")
    shortage_method = po_policy.get("shortage_settlement_method")
    method_ready = shortage_method == ELIGIBLE_TREATMENT
    if shortage_method == "PENDING_INVESTIGATION":
        _issue(issues, "SHORTAGE_SETTLEMENT_METHOD_PENDING_INVESTIGATION")
    elif not method_ready:
        _issue(issues, "RETAINED_MATERIAL_SALES_INVOICE_METHOD_NOT_READY")

    bound_customer = context.get("supplier_bound_customer")
    po_customer = po_policy.get("recovery_customer")
    lot_customer = lot_policy.get("recovery_customer")
    if not po_customer:
        _issue(issues, "RECOVERY_CUSTOMER_NOT_SNAPSHOTTED_ON_PURCHASE_ORDER")
    if not lot_customer:
        _issue(issues, "RECOVERY_CUSTOMER_NOT_SNAPSHOTTED_ON_PROCESSOR_LOT")
    customer_ready = bool(
        bound_customer and po_customer == bound_customer and lot_customer == bound_customer
        and context.get("recovery_customer_enabled") is True
    )
    if po_customer and lot_customer and not customer_ready:
        _issue(issues, "RECOVERY_CUSTOMER_COUNTERPARTY_MISMATCH")

    classification_ready = classification.get("classification") == RESPONSIBLE_CLASSIFICATION
    if not classification_ready:
        _issue(issues, "RETAINED_MATERIAL_PROCESSOR_RESPONSIBILITY_NOT_READY")

    eligible = [{
        "value": ELIGIBLE_TREATMENT,
        "label": "Sales Invoice with Update Stock",
        "update_stock": 1,
        "warehouse": context.get("supplier_warehouse"),
    }]
    projected_after = float(bin_qty - quantity) if stock_ready else None
    stock_value_reduction = float(quantity * bin_rate) if stock_ready else None
    return {
        **base,
        "applicable": True,
        "readiness_code": (
            "RETAINED_MATERIAL_TREATMENT_READY_FOR_FUTURE_EXECUTION_DESIGN"
            if not issues else "RETAINED_MATERIAL_TREATMENT_POLICY_NOT_READY"
        ),
        "blocking_issues": issues,
        "contractual_policy_source": "PURCHASE_ORDER",
        "policy_reconciliation_status": (
            "READY" if policy_reconciled else "BLOCKED_STALE_PROCESSOR_LOT_SNAPSHOT"
        ),
        "policy_reconciliation_ready": policy_reconciled,
        "processing_charge_recovery_applicable": False,
        "eligible_treatments": eligible,
        "recommended_treatment": ELIGIBLE_TREATMENT,
        "recovery_customer_candidate": bound_customer,
        "recovery_customer_ready": customer_ready,
        "quantity_ready": quantity_ready,
        "recovery_quantity": float(quantity) if quantity_ready else None,
        "recovery_quantity_source": row.get("recovery_quantity_source"),
        "rate_ready": rate_ready,
        "material_content_rate": float(rate) if rate_ready else None,
        "rate_source": row.get("suggested_recovery_rate_source"),
        "amount_ready": amount_ready,
        "net_material_amount_excluding_tax": float(amount) if amount_ready else None,
        "tax_amount": None,
        "gross_amount": None,
        "tax_calculation_status": "DEFERRED_TO_STANDARD_ERPNEXT_SALES_INVOICE_TAX_RESOLUTION",
        "tax_context_ready": bool(customer_ready and context.get("recovery_customer_address_ready")),
        "stock_consequence_ready": stock_ready,
        "future_update_stock": 1,
        "future_source_warehouse": context.get("supplier_warehouse"),
        "supplier_warehouse_qty_before": float(bin_qty) if bin_qty is not None else None,
        "projected_stock_reduction": float(quantity) if quantity_ready else None,
        "projected_supplier_warehouse_qty_after": projected_after,
        "supplier_warehouse_valuation_rate": float(bin_rate) if bin_rate is not None else None,
        "projected_stock_value_reduction": stock_value_reduction,
    }


def _same_policy(po, lot):
    return all(po.get(key) == lot.get(key) for key in (
        "recover_raw_material_shortage", "recover_processing_charges_on_shortage",
        "settlement_basis", "shortage_settlement_method", "excess_settlement_method",
        "recovery_customer",
    ))


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _issue(issues, code):
    if code not in issues:
        issues.append(code)
