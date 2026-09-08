"""Pure J19A1 component-scoped commercial preview; never performs writes.

The contract keeps raw-material decisions at exact SCO supplied-row scope and
processing-charge decisions at exact SCO finished-row scope.  Quantities in
different UOMs are never aggregated.  Existing documents are evidence only;
this checkpoint never authorizes a document or Processor Lot closure.
"""

from copy import deepcopy
from math import isfinite


CONTRACT_VERSION = "J19A1"
QTY_TOLERANCE = 0.000001


def build_component_commercial_preview(
    material_report,
    finished_item_rows,
    commercial_context=None,
):
    """Return a database-independent commercial decision preview."""
    material_report = deepcopy(material_report or {})
    finished_item_rows = deepcopy(finished_item_rows or [])
    context = deepcopy(commercial_context or {})
    invoice_rows = context.get("invoice_rows") or []
    policy = context.get("settlement_policy") or {}
    existing_documents = context.get("existing_documents") or []
    legacy_evidence = context.get("legacy_evidence") or []
    policy_issues = context.get("policy_issues") or []

    components = [
        _assess_component(row, policy=policy)
        for row in (material_report.get("components") or [])
    ]
    finished = [
        _assess_finished_item(row, invoice_rows=invoice_rows, policy=policy)
        for row in finished_item_rows
    ]
    duplicate_components = _duplicate_keys(
        components,
        fields=("sco_supplied_item", "sco_finished_item", "component_item", "stock_uom"),
    )
    duplicate_finished = _duplicate_keys(
        finished,
        fields=("sco_finished_item", "purchase_order_item"),
    )
    for row in components:
        key = _identity_key(
            row,
            fields=("sco_supplied_item", "sco_finished_item", "component_item", "stock_uom"),
        )
        if key in duplicate_components:
            row.update(
                commercial_review_permitted=False,
                commercial_decision_code="DUPLICATE_COMPONENT_IDENTITY",
            )
    for row in finished:
        key = _identity_key(row, fields=("sco_finished_item", "purchase_order_item"))
        if key in duplicate_finished:
            row.update(
                commercial_review_permitted=False,
                commercial_decision_code="DUPLICATE_FINISHED_ITEM_IDENTITY",
                processing_recovery_recommended=False,
            )

    exact_documents = [
        row for row in existing_documents
        if row.get("docstatus") in (0, 1)
    ]
    if exact_documents:
        code = "COMMERCIAL_TREATMENT_ALREADY_RECORDED"
        permitted = False
        detail = "An active exact commercial document already records this scope."
    elif legacy_evidence:
        code = "REVIEW_LEGACY_COMMERCIAL_EVIDENCE"
        permitted = False
        detail = "Legacy commercial evidence lacks exact component-row identity and requires review."
    elif not components or not finished or duplicate_components or duplicate_finished:
        code = "REVIEW_COMMERCIAL_IDENTITY"
        permitted = False
        detail = "At least one exact component row and finished-item row are required."
    elif any(not row["commercial_review_permitted"] for row in components):
        code = "ACCOUNT_REMAINING_MATERIAL"
        permitted = False
        detail = "Complete or correct component material accounting before commercial review."
    elif any(not row["commercial_review_permitted"] for row in finished):
        code = "REVIEW_FINISHED_ITEM_COMMERCIAL_EVIDENCE"
        permitted = False
        detail = "Correct finished-item receipt or invoice evidence before commercial treatment."
    elif policy_issues:
        code = "REVIEW_SETTLEMENT_POLICY"
        permitted = False
        detail = "Correct settlement-policy evidence before commercial treatment."
    elif any(row["processing_recovery_recommended"] for row in finished):
        code = "COMMERCIAL_RECOVERY_RECOMMENDED"
        permitted = True
        detail = "A processing-charge variance is available for commercial classification."
    else:
        code = "COMMERCIAL_REVIEW_COMPLETE_NO_RECOVERY"
        permitted = True
        detail = "Exact component and finished-item evidence shows no recovery requirement."

    return {
        "commercial_preview_contract_version": CONTRACT_VERSION,
        "processor_lot": material_report.get("processor_lot"),
        "subcontracting_order": material_report.get("subcontracting_order"),
        "commercial_review_permitted": permitted,
        "commercial_decision_code": code,
        "commercial_decision_detail": detail,
        "components": components,
        "finished_items": finished,
        "existing_documents": exact_documents,
        "legacy_evidence": legacy_evidence,
        "policy_issues": policy_issues,
        "commercial_document_creation_enabled": False,
        "commercial_document_authorized": False,
        "lot_closure_authorized": False,
        "quantity_aggregation_scope": "Never across finished rows, component rows, or UOMs",
    }


def _assess_component(row, *, policy):
    result = deepcopy(row)
    identity_fields = (
        "sco_supplied_item", "sco_finished_item", "component_item", "stock_uom"
    )
    identity_complete = all(result.get(field) for field in identity_fields)
    physical, physical_valid = _number(result.get("physical_remaining_qty"))
    credit, credit_valid = _number(result.get("applied_credit_qty"))

    result.update(
        commercial_identity_complete=identity_complete,
        raw_material_recovery_policy_enabled=bool(
            policy.get("recover_raw_material_shortage")
        ),
        raw_material_recovery_recommended=False,
        commercial_document_authorized=False,
        lot_closure_authorized=False,
    )
    if not identity_complete:
        result.update(
            commercial_review_permitted=False,
            commercial_decision_code="REVIEW_COMPONENT_IDENTITY",
        )
    elif (
        result.get("evidence_consistent") is not True
        or not physical_valid
        or not credit_valid
        or physical < 0
        or credit < 0
    ):
        result.update(
            commercial_review_permitted=False,
            commercial_decision_code="REVIEW_COMPONENT_EVIDENCE",
        )
    elif result.get("material_settlement_eligible") is not True:
        result.update(
            commercial_review_permitted=False,
            commercial_decision_code="ACCOUNT_REMAINING_MATERIAL",
        )
    elif _equal(physical, 0) and _equal(credit, 0):
        result.update(
            commercial_review_permitted=True,
            commercial_decision_code="NO_RAW_MATERIAL_RECOVERY",
        )
    elif physical > 0 and _equal(credit, physical):
        result.update(
            commercial_review_permitted=True,
            commercial_decision_code="RAW_MATERIAL_CREDIT_ACCOUNTED",
        )
    else:
        result.update(
            commercial_review_permitted=False,
            commercial_decision_code="REVIEW_COMPONENT_EVIDENCE",
        )
    return result


def _assess_finished_item(row, *, invoice_rows, policy):
    result = deepcopy(row)
    finished_row = result.get("sco_finished_item")
    po_detail = result.get("purchase_order_item")
    stock_uom = result.get("stock_uom")
    accepted, accepted_valid = _number(result.get("company_accepted_qty"))
    matched = [
        deepcopy(invoice)
        for invoice in invoice_rows
        if invoice.get("docstatus") == 1
        and not invoice.get("is_return")
        and invoice.get("po_detail") == po_detail
    ]
    result.update(
        commercial_identity_complete=bool(finished_row and po_detail and stock_uom),
        matched_invoice_rows=matched,
        processing_recovery_policy_enabled=bool(
            policy.get("recover_processing_charges_on_shortage")
        ),
        processing_recovery_recommended=False,
        commercial_document_authorized=False,
        lot_closure_authorized=False,
    )
    if not result["commercial_identity_complete"]:
        result.update(
            commercial_review_permitted=False,
            commercial_decision_code="REVIEW_FINISHED_ITEM_IDENTITY",
        )
        return result
    if result.get("evidence_consistent") is not True:
        result.update(
            commercial_review_permitted=False,
            commercial_decision_code="REVIEW_FINISHED_ITEM_COMMERCIAL_EVIDENCE",
        )
        return result
    if not accepted_valid or accepted < 0:
        result.update(
            commercial_review_permitted=False,
            commercial_decision_code="REVIEW_FINISHED_ITEM_QUANTITY_EVIDENCE",
        )
        return result
    if not matched:
        result.update(
            commercial_review_permitted=False,
            commercial_decision_code="REVIEW_FINISHED_ITEM_INVOICING",
        )
        return result
    if any(invoice.get("uom") != stock_uom for invoice in matched):
        result.update(
            commercial_review_permitted=False,
            commercial_decision_code="REVIEW_FINISHED_ITEM_UOM",
        )
        return result

    invoice_quantities = [_number(invoice.get("qty")) for invoice in matched]
    if any(not valid or quantity < 0 for quantity, valid in invoice_quantities):
        result.update(
            commercial_review_permitted=False,
            commercial_decision_code="REVIEW_FINISHED_ITEM_QUANTITY_EVIDENCE",
        )
        return result

    invoiced = sum(quantity for quantity, _valid in invoice_quantities)
    variance = invoiced - accepted
    rate_results = [
        _number(
            invoice.get("net_rate")
            if invoice.get("net_rate") is not None
            else invoice.get("rate")
        )
        for invoice in matched
        if _number(invoice.get("qty"))[0] > 0
    ]
    rates = {rate for rate, valid in rate_results if valid and rate > 0}
    result.update(
        supplier_invoice_qty=invoiced,
        commercial_variance_qty=max(variance, 0.0),
        matched_invoice_net_amount=sum(_number(row.get("net_amount"))[0] for row in matched),
    )
    if variance < -QTY_TOLERANCE:
        result.update(
            commercial_review_permitted=False,
            commercial_decision_code="REVIEW_FINISHED_ITEM_INVOICING",
        )
    elif variance > QTY_TOLERANCE and not policy.get(
        "recover_processing_charges_on_shortage"
    ):
        result.update(
            commercial_review_permitted=True,
            commercial_decision_code="PROCESSING_RECOVERY_POLICY_DISABLED",
        )
    elif variance > QTY_TOLERANCE and (
        len(rates) != 1 or any(not valid or rate <= 0 for rate, valid in rate_results)
    ):
        result.update(
            commercial_review_permitted=False,
            commercial_decision_code="REVIEW_PROCESSING_RATE_EVIDENCE",
        )
    elif variance > QTY_TOLERANCE:
        rate = next(iter(rates))
        result.update(
            commercial_review_permitted=True,
            commercial_decision_code="PROCESSING_RECOVERY_RECOMMENDED",
            processing_recovery_recommended=True,
            processing_recovery_rate=rate,
            processing_recovery_amount=variance * rate,
        )
    else:
        result.update(
            commercial_review_permitted=True,
            commercial_decision_code="NO_PROCESSING_RECOVERY",
        )
    return result


def _number(value):
    if value is None or isinstance(value, bool):
        return 0.0, False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0, False
    return (number, True) if isfinite(number) else (0.0, False)


def _identity_key(row, *, fields):
    values = tuple(row.get(field) for field in fields)
    return values if all(values) else None


def _duplicate_keys(rows, *, fields):
    seen = set()
    duplicates = set()
    for row in rows:
        key = _identity_key(row, fields=fields)
        if key in seen:
            duplicates.add(key)
        elif key:
            seen.add(key)
    return duplicates


def _equal(left, right):
    return abs(left - right) <= QTY_TOLERANCE
