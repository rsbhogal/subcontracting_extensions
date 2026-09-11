"""Controlled J19B2E retained-material policy reconciliation; never settles."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from subcontracting_extensions.commercial_classification_policy import (
    RAW_MATERIAL,
    canonical_scope,
    make_event_key,
    make_scope_key,
    validate_reason,
)
from subcontracting_extensions.settlement_method_policy import get_method_contract


CONTRACT_VERSION = "J19B2E"
TARGET_METHOD = "SALES_INVOICE"
RETAINED_DISPOSITION = "RETAINED_BY_PROCESSOR"
RESPONSIBLE_CLASSIFICATION = "PROCESSOR_RESPONSIBLE"
QTY_PRECISION = Decimal("0.000001")
POLICY_FIELDS = (
    "recover_raw_material_shortage",
    "recover_processing_charges_on_shortage",
    "settlement_basis",
    "settlement_remarks",
    "shortage_settlement_method",
    "excess_settlement_method",
    "recovery_customer",
)
PO_FIELDS = {
    "recover_raw_material_shortage": "custom_recover_raw_material_shortage",
    "recover_processing_charges_on_shortage": "custom_recover_processing_charges_on_shortage",
    "settlement_basis": "custom_settlement_basis",
    "settlement_remarks": "custom_settlement_remarks",
    "shortage_settlement_method": "custom_shortage_settlement_method",
    "excess_settlement_method": "custom_excess_settlement_method",
    "recovery_customer": "custom_recovery_customer",
}


def reconcile_retained_material_policy(
    api,
    read_preview,
    processor_lot,
    scope_identity,
    target_shortage_settlement_method,
    target_recovery_customer,
    reason,
    expected_purchase_order_modified,
    expected_processor_lot_modified,
    expected_supplier_modified,
    expected_customer_modified,
    expected_purchase_order_policy,
    expected_processor_lot_policy,
    expected_recovery_quantity,
    expected_disposition_revision,
    expected_last_disposition_event,
    expected_classification_revision,
    expected_treatment_revision,
    expected_last_decision_event,
):
    """Reconcile PO-owned policy and its lot snapshot in the caller transaction."""
    from frappe.utils import now_datetime

    reason = validate_reason(reason)
    _require_system_manager(api)
    if target_shortage_settlement_method != TARGET_METHOD:
        raise ValueError("Retained-material reconciliation requires SALES_INVOICE")

    lot = api.get_doc("Processor Lot", processor_lot)
    lot.check_permission("write")
    _lock(api, "Processor Lot", processor_lot)
    lot = api.get_doc("Processor Lot", processor_lot)
    _same_modified(lot, expected_processor_lot_modified, "Processor Lot")
    _validate_lot_state(lot)

    sco = api.get_doc("Subcontracting Order", lot.get("subcontracting_order"))
    if sco.get("docstatus") != 1 or sco.get("supplier") != lot.get("supplier"):
        raise ValueError("Processor Lot subcontracting lineage changed; reload and review it")
    po_name = sco.get("purchase_order")
    if not po_name or lot.get("purchase_order") not in (None, "", po_name):
        raise ValueError("Processor Lot Purchase Order lineage changed; reload and review it")

    _lock(api, "Purchase Order", po_name)
    po = api.get_doc("Purchase Order", po_name)
    po.check_permission("write")
    _same_modified(po, expected_purchase_order_modified, "Purchase Order")
    if po.get("docstatus") != 1 or not po.get("is_subcontracted"):
        raise ValueError("Contractual Purchase Order must be submitted and subcontracted")
    if po.get("supplier") != sco.get("supplier"):
        raise ValueError("Purchase Order Supplier does not match the retained-material lineage")

    supplier_name = sco.get("supplier")
    _lock(api, "Supplier", supplier_name)
    supplier = api.get_doc("Supplier", supplier_name)
    _same_modified(supplier, expected_supplier_modified, "Supplier")
    if supplier.get("disabled"):
        raise ValueError("Supplier is disabled")
    bound_customer = supplier.get("custom_recovery_customer")
    if not bound_customer or bound_customer != target_recovery_customer:
        raise ValueError("Recovery Customer must match the live Customer bound to the Supplier")

    _lock(api, "Customer", bound_customer)
    customer = api.get_doc("Customer", bound_customer)
    _same_modified(customer, expected_customer_modified, "Recovery Customer")
    if customer.get("disabled"):
        raise ValueError("Recovery Customer is disabled")

    method_rows = api.get_single("Subcontracting Settlement Settings").get(
        "allowed_settlement_methods"
    ) or []
    method = get_method_contract(method_rows, TARGET_METHOD, "Shortage")
    if not method.get("requires_customer"):
        raise ValueError("SALES_INVOICE settlement contract no longer requires a Customer")
    approval_role = method.get("approval_role")
    if approval_role and approval_role not in api.get_roles():
        raise PermissionError(f"Policy reconciliation requires role {approval_role}")

    before_po = _po_policy(po)
    before_lot = _lot_policy(lot)
    _same_policy(before_po, expected_purchase_order_policy, "Purchase Order")
    _same_policy(before_lot, expected_processor_lot_policy, "Processor Lot")
    if before_po["shortage_settlement_method"] != "PENDING_INVESTIGATION":
        raise ValueError("Purchase Order shortage method changed; reload and review it")
    if before_po["recovery_customer"] not in (None, ""):
        raise ValueError("Purchase Order Recovery Customer is no longer blank")

    report = read_preview(processor_lot)
    row, scope = _resolve_exact_scope(report, processor_lot, scope_identity)
    _validate_report(report, row, expected_recovery_quantity)
    disposition = row.get("persisted_material_disposition") or {}
    classification = row.get("persisted_classification") or {}
    _lock(api, "Processor Lot Material Disposition", disposition.get("name"))
    _lock(api, "Processor Lot Commercial Classification", classification.get("name"))
    # Re-read after projection locks so concurrent decisions cannot slip through.
    report = read_preview(processor_lot)
    row, scope = _resolve_exact_scope(report, processor_lot, scope_identity)
    _validate_report(report, row, expected_recovery_quantity)
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
    if int(classification.get("treatment_revision") or 0) != int(expected_treatment_revision or 0):
        raise ValueError("Commercial treatment changed since this screen was loaded; reload and review it")
    if classification.get("selected_treatment_method"):
        raise ValueError("A commercial treatment has already been selected")
    _reject_settlement_evidence(api, lot, report, row)

    scope_key = make_scope_key(scope)
    if api.db.get_value(
        "Processor Lot Policy Reconciliation Event", {"scope_key": scope_key}, "name"
    ):
        raise ValueError("Retained-material policy has already been reconciled for this scope")

    after_po = dict(before_po)
    after_po.update(
        shortage_settlement_method=TARGET_METHOD,
        recovery_customer=bound_customer,
    )
    after_lot = dict(after_po)
    decided_at = now_datetime()

    api.db.set_value("Purchase Order", po.name, {
        PO_FIELDS[key]: value for key, value in after_po.items()
    }, update_modified=True)
    lot_updates = dict(after_lot)
    lot_updates.update(
        override_settlement_policy=0,
        settlement_policy_source="Purchase Order",
        settlement_policy_override_reason=None,
        overridden_by=None,
        settlement_policy_overridden_on=None,
    )
    api.db.set_value("Processor Lot", lot.name, lot_updates, update_modified=True)

    event = api.get_doc({
        "doctype": "Processor Lot Policy Reconciliation Event",
        "processor_lot": lot.name,
        "subcontracting_order": sco.name,
        "purchase_order": po.name,
        "company": lot.get("company"),
        "supplier": supplier_name,
        "scope_key": scope_key,
        "sco_supplied_item": scope.get("sco_supplied_item"),
        "sco_finished_item": scope.get("sco_finished_item"),
        "component_item": scope.get("component_item"),
        "stock_uom": scope.get("stock_uom"),
        "event_sequence": 1,
        "event_key": make_event_key(scope_key, 1),
        "reason": reason,
        "reconciled_by": api.session.user,
        "reconciled_at": decided_at,
        "supplier_bound_customer": bound_customer,
        "target_shortage_settlement_method": TARGET_METHOD,
        "purchase_order_modified_before": str(po.get("modified") or ""),
        "processor_lot_modified_before": str(lot.get("modified") or ""),
        "supplier_modified": str(supplier.get("modified") or ""),
        "customer_modified": str(customer.get("modified") or ""),
        "disposition_revision": disposition.get("disposition_revision"),
        "last_disposition_event": disposition.get("last_disposition_event"),
        "classification_revision": classification.get("classification_revision"),
        "treatment_revision": classification.get("treatment_revision"),
        "last_decision_event": classification.get("last_decision_event"),
        "recovery_quantity": row.get("suggested_recovery_quantity"),
        "material_content_rate": row.get("suggested_recovery_rate"),
        "net_material_amount": row.get("suggested_recovery_amount"),
        "purchase_order_policy_before": _json(before_po),
        "processor_lot_policy_before": _json(before_lot),
        "purchase_order_policy_after": _json(after_po),
        "processor_lot_policy_after": _json(after_lot),
        "retained_material_evidence": _json(_evidence(row)),
        "settlement_evidence": _json(report.get("settlement_evidence") or []),
        "commercial_document_creation_enabled": 0,
        "commercial_document_authorized": 0,
        "stock_document_authorized": 0,
        "lot_closure_authorized": 0,
    })
    event.flags.controlled_policy_reconciliation_insert = True
    event.insert(ignore_permissions=True)

    return {
        "contract_version": CONTRACT_VERSION,
        "reconciliation_code": "RETAINED_MATERIAL_POLICY_RECONCILED",
        "processor_lot": lot.name,
        "purchase_order": po.name,
        "scope_key": scope_key,
        "policy_source": "Purchase Order",
        "shortage_settlement_method": TARGET_METHOD,
        "recovery_customer": bound_customer,
        "policy_reconciliation_event": event.name,
        "commercial_execution_ready": False,
        "commercial_document_creation_enabled": False,
        "commercial_document_authorized": False,
        "stock_document_authorized": False,
        "lot_closure_authorized": False,
    }


def _require_system_manager(api):
    roles = api.get_roles()
    if api.session.user != "Administrator" and "System Manager" not in roles:
        raise PermissionError("Only a System Manager may reconcile retained-material policy")


def _lock(api, doctype, name):
    if not name:
        raise ValueError(f"Required {doctype} evidence is missing")
    api.db.sql(f"select name from `tab{doctype}` where name=%s for update", (name,))


def _same_modified(doc, expected, label):
    if not expected or str(doc.get("modified") or "") != str(expected):
        raise ValueError(f"{label} changed since this screen was loaded; reload and review it")


def _validate_lot_state(lot):
    if lot.get("docstatus") != 0 or lot.get("settlement_status") not in (None, "", "Draft"):
        raise ValueError("Processor Lot is not in the permitted pre-settlement state")
    if lot.get("override_settlement_policy"):
        raise ValueError("Processor Lot policy override must not be active")
    if any(lot.get(field) for field in ("generated_document", "generated_document_type", "debit_note")):
        raise ValueError("Processor Lot already references a commercial document")


def _po_policy(po):
    return {key: po.get(field) for key, field in PO_FIELDS.items()}


def _lot_policy(lot):
    return {key: lot.get(key) for key in POLICY_FIELDS}


def _normalized_policy(value):
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict) or set(value) != set(POLICY_FIELDS):
        raise ValueError("Expected policy must contain the complete seven-field snapshot")
    return {key: value.get(key) for key in POLICY_FIELDS}


def _same_policy(current, expected, label):
    if current != _normalized_policy(expected):
        raise ValueError(f"{label} policy changed since this screen was loaded; reload and review it")


def _resolve_exact_scope(report, processor_lot, identity):
    supplied = dict(identity or {})
    supplied.update(scope_type=RAW_MATERIAL, processor_lot=processor_lot)
    requested = canonical_scope(supplied)
    key = make_scope_key(requested)
    matches = []
    for row in report.get("components") or []:
        try:
            candidate = dict(row, scope_type=RAW_MATERIAL, processor_lot=processor_lot)
            if make_scope_key(candidate) == key:
                matches.append(row)
        except ValueError:
            continue
    if len(matches) != 1:
        raise ValueError("Retained-material scope is missing or ambiguous")
    return matches[0], requested


def _validate_report(report, row, expected_quantity):
    if report.get("policy_issues") not in (None, [], ["PROCESSOR_LOT_POLICY_DIFFERS_FROM_PURCHASE_ORDER"]):
        raise ValueError("Settlement policy has unsupported issues")
    if report.get("classification_issues") or report.get("material_disposition_issues"):
        raise ValueError("Retained-material decision history requires review")
    disposition = row.get("persisted_material_disposition") or {}
    classification = row.get("persisted_classification") or {}
    if (
        row.get("commercial_decision_code") != "RAW_MATERIAL_RETAINED_BY_PROCESSOR"
        or row.get("material_disposition_current") is not True
        or disposition.get("disposition") != RETAINED_DISPOSITION
        or classification.get("classification") != RESPONSIBLE_CLASSIFICATION
    ):
        raise ValueError("Exact retained-material disposition or classification changed")
    quantity = _qty(row.get("suggested_recovery_quantity"))
    if quantity is None or quantity <= 0 or quantity != _qty(expected_quantity):
        raise ValueError("Retained quantity changed since this screen was loaded; reload and review it")
    readiness = row.get("retained_material_treatment_readiness") or {}
    permitted = {
        "PROCESSOR_LOT_POLICY_DIFFERS_FROM_PURCHASE_ORDER",
        "SHORTAGE_SETTLEMENT_METHOD_PENDING_INVESTIGATION",
        "RECOVERY_CUSTOMER_NOT_SNAPSHOTTED_ON_PURCHASE_ORDER",
        "RECOVERY_CUSTOMER_NOT_SNAPSHOTTED_ON_PROCESSOR_LOT",
    }
    if set(readiness.get("blocking_issues") or []) - permitted:
        raise ValueError("Retained-material readiness evidence changed; reload and review it")
    if not all(readiness.get(key) is True for key in (
        "quantity_ready", "rate_ready", "amount_ready", "stock_consequence_ready"
    )):
        raise ValueError("Retained-material quantity, rate, amount, or stock evidence is not ready")


def _expected_revision(doc, revision_field, expected_revision, event_field, expected_event, label):
    try:
        same_revision = int(doc.get(revision_field) or 0) == int(expected_revision or 0)
    except (TypeError, ValueError):
        same_revision = False
    if not same_revision or (doc.get(event_field) or None) != (expected_event or None):
        raise ValueError(f"{label} changed since this screen was loaded; reload and review it")


def _reject_settlement_evidence(api, lot, report, row):
    if report.get("settlement_evidence"):
        raise ValueError("Settlement or commercial evidence already exists")
    if api.get_all("Purchase Invoice", filters={
        "custom_processor_lot_settlement": lot.name, "docstatus": ["!=", 2]
    }, fields=["name"], limit_page_length=1):
        raise ValueError("A linked Purchase Invoice already exists")
    if api.get_all("Processor Material Account Entry", filters={
        "processor_lot": lot.name, "docstatus": ["!=", 2]
    }, fields=["name"], limit_page_length=1):
        raise ValueError("A Processor Material Account Entry already exists")
    refs = api.get_all("Stock Entry Detail", filters={
        "sco_rm_detail": row.get("sco_supplied_item"), "docstatus": ["!=", 2]
    }, fields=["parent"], limit_page_length=0)
    for ref in refs:
        stock_entry = api.get_doc("Stock Entry", ref.get("parent"))
        if stock_entry.get("is_return") and stock_entry.get("subcontracting_order") == lot.get("subcontracting_order"):
            raise ValueError("An active exact-scope component return already exists")


def _evidence(row):
    return {key: row.get(key) for key in (
        "commercial_decision_code", "sco_supplied_item", "sco_finished_item",
        "component_item", "stock_uom", "suggested_recovery_quantity",
        "suggested_recovery_rate", "suggested_recovery_amount",
        "recovery_quantity_source", "suggested_recovery_rate_source",
    )}


def _qty(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number.quantize(QTY_PRECISION, rounding=ROUND_HALF_UP) if number.is_finite() else None


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
