"""Controlled J19B2F retained-material treatment selection; never executes it."""

from __future__ import annotations

import json

from subcontracting_extensions.component_commercial_classification import (
    record_commercial_decision,
)
from subcontracting_extensions.commercial_classification_policy import validate_reason
from subcontracting_extensions.retained_material_policy_reconciliation import (
    _expected_revision,
    _lock,
    _lot_policy,
    _po_policy,
    _reject_settlement_evidence,
    _require_system_manager,
    _resolve_exact_scope,
    _same_modified,
    _validate_report,
)
from subcontracting_extensions.settlement_method_policy import get_method_contract


CONTRACT_VERSION = "J19B2F"
TARGET_METHOD = "SALES_INVOICE"
RESPONSIBLE_CLASSIFICATION = "PROCESSOR_RESPONSIBLE"


def select_retained_material_treatment(
    api,
    read_preview,
    processor_lot,
    scope_identity,
    selected_treatment_method,
    reason,
    expected_purchase_order_modified,
    expected_processor_lot_modified,
    expected_supplier_modified,
    expected_customer_modified,
    expected_policy_reconciliation_event,
    expected_recovery_quantity,
    expected_disposition_revision,
    expected_last_disposition_event,
    expected_classification_revision,
    expected_treatment_revision,
    expected_last_decision_event,
):
    """Persist one exact retained-material treatment decision in caller transaction."""
    reason = validate_reason(reason)
    _require_system_manager(api)
    if selected_treatment_method != TARGET_METHOD:
        raise ValueError("Retained-material treatment selection requires SALES_INVOICE")

    lot = api.get_doc("Processor Lot", processor_lot)
    lot.check_permission("write")
    _lock(api, "Processor Lot", processor_lot)
    lot = api.get_doc("Processor Lot", processor_lot)
    _same_modified(lot, expected_processor_lot_modified, "Processor Lot")
    if lot.get("docstatus") != 0 or lot.get("settlement_status") not in (None, "", "Draft"):
        raise ValueError("Processor Lot is not in the permitted pre-settlement state")
    if lot.get("override_settlement_policy") or lot.get("settlement_policy_source") != "Purchase Order":
        raise ValueError("Processor Lot no longer has an inherited Purchase Order policy")
    if any(lot.get(field) for field in ("generated_document", "generated_document_type", "debit_note")):
        raise ValueError("Processor Lot already references a commercial document")

    sco = api.get_doc("Subcontracting Order", lot.get("subcontracting_order"))
    if sco.get("docstatus") != 1 or sco.get("supplier") != lot.get("supplier"):
        raise ValueError("Processor Lot subcontracting lineage changed; reload and review it")
    po_name = sco.get("purchase_order")
    if not po_name or lot.get("purchase_order") not in (None, "", po_name):
        raise ValueError("Processor Lot Purchase Order lineage changed; reload and review it")
    _lock(api, "Purchase Order", po_name)
    po = api.get_doc("Purchase Order", po_name)
    _same_modified(po, expected_purchase_order_modified, "Purchase Order")
    if po.get("docstatus") != 1 or not po.get("is_subcontracted") or po.get("supplier") != sco.get("supplier"):
        raise ValueError("Contractual Purchase Order lineage or state changed")

    po_policy = _po_policy(po)
    lot_policy = _lot_policy(lot)
    if po_policy != lot_policy:
        raise ValueError("Processor Lot policy differs from its Purchase Order")
    if (po_policy.get("shortage_settlement_method") != TARGET_METHOD
            or po_policy.get("recovery_customer") in (None, "")):
        raise ValueError("Retained-material SALES_INVOICE policy is not ready")

    supplier_name = sco.get("supplier")
    _lock(api, "Supplier", supplier_name)
    supplier = api.get_doc("Supplier", supplier_name)
    _same_modified(supplier, expected_supplier_modified, "Supplier")
    if supplier.get("disabled"):
        raise ValueError("Supplier is disabled")
    customer_name = supplier.get("custom_recovery_customer")
    if not customer_name or customer_name != po_policy.get("recovery_customer"):
        raise ValueError("Recovery Customer no longer matches the live Supplier binding")
    _lock(api, "Customer", customer_name)
    customer = api.get_doc("Customer", customer_name)
    _same_modified(customer, expected_customer_modified, "Recovery Customer")
    if customer.get("disabled"):
        raise ValueError("Recovery Customer is disabled")

    rows = api.get_single("Subcontracting Settlement Settings").get(
        "allowed_settlement_methods"
    ) or []
    method = get_method_contract(rows, TARGET_METHOD, "Shortage")
    if not method.get("requires_customer"):
        raise ValueError("SALES_INVOICE treatment no longer requires a Customer")
    approval_role = method.get("approval_role")
    if approval_role and approval_role not in api.get_roles():
        raise PermissionError(f"Treatment selection requires role {approval_role}")

    report = read_preview(processor_lot)
    row, scope = _resolve_exact_scope(report, processor_lot, scope_identity)
    _validate_report(report, row, expected_recovery_quantity)
    disposition = row.get("persisted_material_disposition") or {}
    classification = row.get("persisted_classification") or {}
    _lock(api, "Processor Lot Material Disposition", disposition.get("name"))
    _lock(api, "Processor Lot Commercial Classification", classification.get("name"))
    scope_key = row.get("commercial_scope_key")
    if not scope_key:
        from subcontracting_extensions.commercial_classification_policy import make_scope_key
        scope_key = make_scope_key(scope)

    reconciliation_names = api.get_all(
        "Processor Lot Policy Reconciliation Event",
        filters={"processor_lot": processor_lot, "scope_key": scope_key},
        fields=["name"], limit_page_length=0,
    )
    if len(reconciliation_names) != 1 or reconciliation_names[0].get("name") != expected_policy_reconciliation_event:
        raise ValueError("Exact J19B2E policy reconciliation evidence is missing or ambiguous")
    _lock(api, "Processor Lot Policy Reconciliation Event", expected_policy_reconciliation_event)

    # Re-read after all mutable projections and reconciliation evidence are locked.
    report = read_preview(processor_lot)
    row, scope = _resolve_exact_scope(report, processor_lot, scope_identity)
    _validate_report(report, row, expected_recovery_quantity)
    readiness = row.get("retained_material_treatment_readiness") or {}
    if (readiness.get("readiness_code")
            != "RETAINED_MATERIAL_TREATMENT_READY_FOR_FUTURE_EXECUTION_DESIGN"
            or readiness.get("blocking_issues")
            or readiness.get("recommended_treatment") != TARGET_METHOD
            or readiness.get("recovery_customer_ready") is not True
            or readiness.get("future_update_stock") != 1
            or readiness.get("tax_calculation_status")
            != "DEFERRED_TO_STANDARD_ERPNEXT_SALES_INVOICE_TAX_RESOLUTION"):
        raise ValueError("J19B2D retained-material treatment readiness changed")

    disposition = row.get("persisted_material_disposition") or {}
    classification = row.get("persisted_classification") or {}
    _expected_revision(
        disposition, "disposition_revision", expected_disposition_revision,
        "last_disposition_event", expected_last_disposition_event,
        "Material disposition",
    )
    _expected_revision(
        classification, "classification_revision", expected_classification_revision,
        "last_decision_event", expected_last_decision_event,
        "Commercial classification",
    )
    try:
        treatment_current = int(classification.get("treatment_revision") or 0)
        treatment_expected = int(expected_treatment_revision or 0)
    except (TypeError, ValueError):
        raise ValueError("Expected treatment revision must be an integer")
    if treatment_current != treatment_expected:
        raise ValueError("Commercial treatment changed since this screen was loaded; reload and review it")
    if (classification.get("classification") != RESPONSIBLE_CLASSIFICATION
            or classification.get("selected_treatment_method")
            or treatment_current != 0):
        raise ValueError("Retained-material classification is not ready for initial treatment selection")

    reconciliation = api.get_doc(
        "Processor Lot Policy Reconciliation Event", expected_policy_reconciliation_event
    )
    _validate_reconciliation(
        reconciliation, processor_lot, po_name, scope_key, customer_name,
        expected_recovery_quantity, expected_disposition_revision,
        expected_last_disposition_event, expected_classification_revision,
        expected_treatment_revision, expected_last_decision_event,
        po_policy, lot_policy,
    )
    _reject_settlement_evidence(api, lot, report, row)

    selected = record_commercial_decision(
        api, read_preview, processor_lot, "Raw Material", scope_identity,
        "Treatment", "Shortage", TARGET_METHOD, reason,
        expected_classification_revision, expected_treatment_revision,
        expected_last_decision_event,
        allow_retained_material_treatment=True,
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "selection_code": "RETAINED_MATERIAL_TREATMENT_SELECTED",
        "processor_lot": processor_lot,
        "commercial_classification": selected.get("commercial_classification"),
        "decision_event": selected.get("decision_event"),
        "scope_key": scope_key,
        "classification": selected.get("classification"),
        "selected_treatment_method": selected.get("selected_treatment_method"),
        "classification_revision": int(expected_classification_revision or 0),
        "treatment_revision": treatment_current + 1,
        "commercial_execution_ready": False,
        "commercial_document_creation_enabled": False,
        "commercial_document_authorized": False,
        "stock_document_authorized": False,
        "lot_closure_authorized": False,
    }


def _validate_reconciliation(
    event, processor_lot, purchase_order, scope_key, customer, quantity,
    disposition_revision, disposition_event, classification_revision,
    treatment_revision, decision_event, po_policy, lot_policy,
):
    if (event.get("processor_lot") != processor_lot
            or event.get("purchase_order") != purchase_order
            or event.get("scope_key") != scope_key
            or event.get("supplier_bound_customer") != customer
            or event.get("target_shortage_settlement_method") != TARGET_METHOD
            or float(event.get("recovery_quantity") or 0) != float(quantity)
            or int(event.get("disposition_revision") or 0) != int(disposition_revision or 0)
            or event.get("last_disposition_event") != disposition_event
            or int(event.get("classification_revision") or 0) != int(classification_revision or 0)
            or int(event.get("treatment_revision") or 0) != int(treatment_revision or 0)
            or event.get("last_decision_event") != decision_event):
        raise ValueError("J19B2E policy reconciliation evidence is stale or mismatched")
    if any(event.get(field) for field in (
        "commercial_document_creation_enabled", "commercial_document_authorized",
        "stock_document_authorized", "lot_closure_authorized",
    )):
        raise ValueError("J19B2E policy reconciliation contains unsafe authorization")
    try:
        after_po = json.loads(event.get("purchase_order_policy_after") or "{}")
        after_lot = json.loads(event.get("processor_lot_policy_after") or "{}")
    except (TypeError, ValueError):
        raise ValueError("J19B2E policy reconciliation snapshots are invalid")
    if after_po != po_policy or after_lot != lot_policy:
        raise ValueError("J19B2E policy reconciliation snapshot no longer matches live policy")
