"""Controlled J19B1C classification persistence; never executes treatment."""

from __future__ import annotations

import json

from subcontracting_extensions.commercial_classification_policy import (
    EXCESS,
    FINISHED_ITEM,
    RAW_MATERIAL,
    canonical_scope,
    make_scope_key,
    validate_classification_for_evidence,
    validate_reason,
    validate_treatment,
)
from subcontracting_extensions.settlement_method_policy import get_method_contract


def record_commercial_decision(
    api,
    read_preview,
    processor_lot,
    scope_type,
    scope_identity,
    event_type,
    variance_direction,
    decision_value,
    reason,
):
    """Append exactly one classification or treatment event in the caller transaction."""
    from frappe.utils import now_datetime

    reason = validate_reason(reason)
    report = read_preview(processor_lot)
    if report.get("processor_lot") != processor_lot:
        raise ValueError("Commercial preview returned the wrong Processor Lot")
    if report.get("commercial_review_permitted") is not True:
        raise ValueError("Commercial evidence is not ready for classification")
    if report.get("policy_issues"):
        raise ValueError("Settlement policy evidence requires review")

    row, scope = _resolve_exact_scope(report, processor_lot, scope_type, scope_identity)
    if row.get("commercial_review_permitted") is not True:
        raise ValueError("The exact commercial scope is not ready for classification")

    lot = api.get_doc("Processor Lot", processor_lot)
    lot.check_permission("write")
    sco = api.get_doc("Subcontracting Order", report.get("subcontracting_order"))
    po = api.get_doc("Purchase Order", sco.get("purchase_order"))
    scope.update(
        subcontracting_order=sco.name,
        purchase_order=po.name,
        company=lot.get("company"),
        supplier=lot.get("supplier"),
        finished_item=_finished_item_for_scope(sco, scope),
    )
    scope_key = make_scope_key(scope)
    classification_doc = _get_or_create_scope(api, scope, scope_key)
    _lock_scope(api, classification_doc.name)
    classification_doc = api.get_doc(
        "Processor Lot Commercial Classification", classification_doc.name
    )

    if event_type == "Classification":
        classification = validate_classification_for_evidence(
            row.get("commercial_decision_code"),
            variance_direction,
            decision_value,
        )
        method_code = None
        supersedes = _latest_event(api, classification_doc.name, event_type)
        revision = int(classification_doc.get("classification_revision") or 0) + 1
        classification_changed = bool(
            classification_doc.get("current_classification")
            and (
                classification_doc.get("current_classification") != classification
                or classification_doc.get("current_variance_direction") != variance_direction
            )
        )
    elif event_type == "Treatment":
        classification = classification_doc.get("current_classification")
        if not classification or classification_doc.get("current_variance_direction") != variance_direction:
            raise ValueError("Persist a classification for this variance direction first")
        method_code = decision_value
        rows = api.get_single("Subcontracting Settlement Settings").get(
            "allowed_settlement_methods"
        ) or []
        method = get_method_contract(rows, method_code, variance_direction)
        validate_treatment(variance_direction, classification, method_code)
        _validate_method_counterparty(api, lot, method)
        approval_role = method.get("approval_role")
        if approval_role and approval_role not in api.get_roles():
            raise PermissionError(f"Treatment selection requires role {approval_role}")
        supersedes = _latest_event(api, classification_doc.name, event_type)
        revision = int(classification_doc.get("treatment_revision") or 0) + 1
    else:
        raise ValueError("Event Type must be Classification or Treatment")

    sequence = _latest_sequence(api, classification_doc.name) + 1
    policy = report.get("settlement_policy") or {}
    default_method = policy.get(
        "excess_settlement_method" if variance_direction == EXCESS
        else "shortage_settlement_method"
    )
    decided_at = now_datetime()
    event = api.get_doc({
        "doctype": "Processor Lot Commercial Decision Event",
        "commercial_classification": classification_doc.name,
        "scope_key": scope_key,
        "event_sequence": sequence,
        "event_type": event_type,
        "supersedes_event": supersedes,
        "variance_direction": variance_direction,
        "classification": classification if event_type == "Classification" else None,
        "selected_treatment_method": method_code,
        "reason": reason,
        "decision_by": api.session.user,
        "decision_at": decided_at,
        "evidence_code": row.get("commercial_decision_code") or report.get("commercial_decision_code"),
        "evidence_snapshot": _json(_evidence_snapshot(scope_type, row)),
        "policy_source": policy.get("policy_source"),
        "policy_default_method": default_method,
        "recovery_customer": policy.get("recovery_customer"),
        "policy_snapshot": _json(policy),
        "commercial_document_authorized": 0,
        "lot_closure_authorized": 0,
    })
    event.flags.controlled_commercial_decision_insert = True
    event.insert(ignore_permissions=True)

    classification_doc.flags.controlled_commercial_decision_update = True
    classification_doc.current_variance_direction = variance_direction
    if event_type == "Classification":
        classification_doc.current_classification = classification
        classification_doc.classification_revision = revision
        # A changed classification invalidates the prior treatment projection.
        if classification_changed and classification_doc.get("current_treatment_method"):
            classification_doc.current_treatment_method = None
    else:
        classification_doc.current_treatment_method = method_code
        classification_doc.treatment_revision = revision
    classification_doc.last_decision_event = event.name
    classification_doc.last_decision_by = api.session.user
    classification_doc.last_decision_at = decided_at
    classification_doc.save(ignore_permissions=True)

    return {
        "commercial_classification": classification_doc.name,
        "decision_event": event.name,
        "event_sequence": sequence,
        "event_type": event_type,
        "classification": classification_doc.current_classification,
        "selected_treatment_method": classification_doc.current_treatment_method,
        "policy_default_method": default_method,
        "commercial_document_creation_enabled": False,
        "commercial_document_authorized": False,
        "lot_closure_authorized": False,
    }


def _resolve_exact_scope(report, processor_lot, scope_type, identity):
    supplied = dict(identity or {})
    supplied.update(scope_type=scope_type, processor_lot=processor_lot)
    requested = canonical_scope(supplied)
    rows = report.get("components" if scope_type == RAW_MATERIAL else "finished_items") or []
    matches = []
    for row in rows:
        candidate = dict(row, scope_type=scope_type, processor_lot=processor_lot)
        try:
            if make_scope_key(candidate) == make_scope_key(requested):
                matches.append(row)
        except ValueError:
            continue
    if len(matches) != 1:
        raise ValueError("Commercial scope identity is missing or ambiguous")
    return matches[0], requested


def _get_or_create_scope(api, scope, scope_key):
    name = api.db.get_value(
        "Processor Lot Commercial Classification", {"scope_key": scope_key}, "name"
    )
    if name:
        return api.get_doc("Processor Lot Commercial Classification", name)
    doc = api.get_doc(dict(
        doctype="Processor Lot Commercial Classification",
        scope_key=scope_key,
        classification_revision=0,
        treatment_revision=0,
        **scope,
    ))
    doc.flags.controlled_commercial_classification_insert = True
    try:
        doc.insert(ignore_permissions=True)
        return doc
    except api.UniqueValidationError:
        name = api.db.get_value(
            "Processor Lot Commercial Classification", {"scope_key": scope_key}, "name"
        )
        if not name:
            raise
        return api.get_doc("Processor Lot Commercial Classification", name)


def _lock_scope(api, name):
    api.db.sql(
        "select name from `tabProcessor Lot Commercial Classification` where name=%s for update",
        (name,),
    )


def _validate_method_counterparty(api, lot, method):
    if not method.get("requires_customer"):
        return
    recovery_customer = lot.get("recovery_customer")
    bound_customer = api.db.get_value(
        "Supplier", lot.get("supplier"), "custom_recovery_customer"
    )
    if not recovery_customer or recovery_customer != bound_customer:
        raise ValueError("Selected treatment requires the exact Customer bound to the Supplier")
    disabled = api.db.get_value("Customer", recovery_customer, "disabled")
    if disabled is None or bool(disabled):
        raise ValueError("Selected treatment requires an enabled Recovery Customer")


def _finished_item_for_scope(sco, scope):
    matches = [
        row for row in (sco.get("items") or [])
        if row.get("name") == scope.get("sco_finished_item")
    ]
    if len(matches) != 1 or not matches[0].get("item_code"):
        raise ValueError("Commercial scope has invalid SCO finished-item lineage")
    return matches[0].get("item_code")


def _latest_sequence(api, parent):
    value = api.db.get_value(
        "Processor Lot Commercial Decision Event",
        {"commercial_classification": parent},
        "max(event_sequence)",
    )
    return int(value or 0)


def _latest_event(api, parent, event_type):
    rows = api.get_all(
        "Processor Lot Commercial Decision Event",
        filters={"commercial_classification": parent, "event_type": event_type},
        fields=["name"], order_by="event_sequence desc", limit_page_length=1,
    )
    return rows[0].get("name") if rows else None


def _evidence_snapshot(scope_type, row):
    fields = (
        ("sco_supplied_item", "sco_finished_item", "component_item", "stock_uom",
         "physical_remaining_qty", "applied_credit_qty", "unaccounted_remaining_qty")
        if scope_type == RAW_MATERIAL else
        ("sco_finished_item", "purchase_order_item", "finished_item", "stock_uom",
         "company_accepted_qty", "supplier_invoice_qty", "commercial_variance_qty",
         "processing_recovery_rate", "processing_recovery_amount")
    )
    return {field: row.get(field) for field in fields}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
