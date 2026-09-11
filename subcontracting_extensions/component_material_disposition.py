"""Controlled J19B2B material-disposition evidence; never executes treatment."""

from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from subcontracting_extensions.commercial_classification_policy import (
    RAW_MATERIAL,
    canonical_scope,
    make_event_key,
    make_scope_key,
    validate_reason,
)


CONTRACT_VERSION = "J19B2B"
PENDING_INVESTIGATION = "PENDING_INVESTIGATION"
RETAINED_BY_PROCESSOR = "RETAINED_BY_PROCESSOR"
DISPOSITIONS = (
    {"value": PENDING_INVESTIGATION, "label": "Pending Investigation"},
    {"value": RETAINED_BY_PROCESSOR, "label": "Retained by Processor"},
)
QTY_PRECISION = Decimal("0.000001")


def attach_material_dispositions(api, lot, report):
    """Attach exact current projections and fail closed on broken evidence."""
    result = report
    issues = []
    rows = api.get_all(
        "Processor Lot Material Disposition",
        filters={"processor_lot": lot.name},
        fields=["name", "scope_key"],
        limit_page_length=0,
    )
    by_key = {}
    for reference in rows:
        doc = api.get_doc("Processor Lot Material Disposition", reference.get("name"))
        doc.check_permission("read")
        key = doc.get("scope_key")
        try:
            values = doc.as_dict() if callable(getattr(doc, "as_dict", None)) else dict(doc)
            expected = make_scope_key(dict(values, scope_type=RAW_MATERIAL))
        except (TypeError, ValueError):
            expected = None
        if not key or key != expected or key in by_key:
            _issue(issues, "INVALID_OR_DUPLICATE_MATERIAL_DISPOSITION_SCOPE")
            continue
        evidence, history_issues = _read_history(api, doc)
        for code in history_issues:
            _issue(issues, code)
        by_key[key] = evidence

    matched = set()
    for row in result.get("components") or []:
        try:
            key = make_scope_key(dict(
                row, scope_type=RAW_MATERIAL, processor_lot=lot.name
            ))
        except ValueError:
            row["persisted_material_disposition"] = None
            continue
        evidence = deepcopy(by_key.get(key))
        row["persisted_material_disposition"] = evidence
        if evidence:
            matched.add(key)
            current_qty = _qty(row.get("unaccounted_remaining_qty"))
            event_qty = _qty(evidence.get("disposition_qty"))
            if current_qty is None or event_qty is None or current_qty != event_qty:
                _issue(issues, "MATERIAL_DISPOSITION_QUANTITY_STALE")
                row["material_disposition_current"] = False
            else:
                row["material_disposition_current"] = True
        else:
            row["material_disposition_current"] = False
    if set(by_key) - matched:
        _issue(issues, "ORPHANED_MATERIAL_DISPOSITION_SCOPE")
    result["material_dispositions"] = [deepcopy(by_key[key]) for key in sorted(matched)]
    result["material_disposition_issues"] = issues
    result["material_disposition_contract_version"] = CONTRACT_VERSION
    result["commercial_document_authorized"] = False
    result["lot_closure_authorized"] = False
    return result


def attach_material_disposition_capabilities(api, report, *, enabled):
    """Attach server-owned options without authorising a stock or commercial write."""
    result = report
    if not enabled:
        for row in result.get("components") or []:
            row["material_disposition_capability"] = {
                "entry_available": False, "allowed_dispositions": [],
                "exact_disposition_qty": None,
                "commercial_document_authorized": False,
                "stock_document_authorized": False,
                "lot_closure_authorized": False,
            }
        result["material_disposition_entry_enabled"] = False
        return result
    lot = api.get_doc("Processor Lot", result.get("processor_lot"))
    can_write = bool(lot.has_permission("write"))
    active = lot.get("docstatus") != 2 and lot.get("settlement_status") not in (
        "Completed", "Cancelled"
    )
    for row in result.get("components") or []:
        quantity = _qty(row.get("unaccounted_remaining_qty"))
        evidence_ready = bool(
            row.get("evidence_consistent") is True
            and quantity is not None
            and quantity > 0
            and not result.get("material_disposition_issues")
        )
        available = bool(can_write and active and evidence_ready)
        row["material_disposition_capability"] = {
            "entry_available": available,
            "allowed_dispositions": deepcopy(DISPOSITIONS) if available else [],
            "exact_disposition_qty": float(quantity) if available else None,
            "commercial_document_authorized": False,
            "stock_document_authorized": False,
            "lot_closure_authorized": False,
        }
    result["material_disposition_entry_enabled"] = bool(can_write and active)
    return result


def record_material_disposition(
    api,
    read_preview,
    processor_lot,
    scope_identity,
    disposition,
    reason,
    expected_revision=0,
    expected_last_event=None,
):
    """Persist one full-residual disposition event in the caller transaction."""
    from frappe.utils import now_datetime

    if disposition not in {row["value"] for row in DISPOSITIONS}:
        raise ValueError("Unsupported component material disposition")
    reason = validate_reason(reason)
    lot = api.get_doc("Processor Lot", processor_lot)
    lot.check_permission("write")
    if lot.get("docstatus") == 2 or lot.get("settlement_status") in ("Completed", "Cancelled"):
        raise ValueError("Material disposition cannot change on a completed or cancelled Processor Lot")
    _lock_lot(api, processor_lot)

    report = read_preview(processor_lot)
    row, scope = _resolve_exact_component(report, processor_lot, scope_identity)
    if row.get("evidence_consistent") is not True:
        raise ValueError("Component material evidence requires review")
    quantity = _qty(row.get("unaccounted_remaining_qty"))
    if quantity is None or quantity <= 0:
        raise ValueError("A positive exact unaccounted material balance is required")
    if disposition == RETAINED_BY_PROCESSOR:
        if row.get("dispatch_cost_issues") or row.get("suggested_recovery_rate") is None:
            raise ValueError("Historical dispatch-cost evidence requires review")
        _reject_active_draft_return(api, report, row)

    sco = api.get_doc("Subcontracting Order", report.get("subcontracting_order"))
    po = api.get_doc("Purchase Order", sco.get("purchase_order"))
    scope.update(
        subcontracting_order=sco.name,
        purchase_order=po.name,
        company=lot.get("company"),
        supplier=lot.get("supplier"),
        finished_item=_finished_item(sco, scope.get("sco_finished_item")),
    )
    scope_key = make_scope_key(scope)
    projection = _get_or_create_projection(api, scope, scope_key)
    _lock_projection(api, projection.name)
    projection = api.get_doc("Processor Lot Material Disposition", projection.name)
    _validate_expected_revision(projection, expected_revision, expected_last_event)
    _reject_existing_commercial_classification(api, scope_key)

    sequence = int(projection.get("disposition_revision") or 0) + 1
    decided_at = now_datetime()
    event = api.get_doc({
        "doctype": "Processor Lot Material Disposition Event",
        "material_disposition": projection.name,
        "scope_key": scope_key,
        "event_sequence": sequence,
        "event_key": make_event_key(scope_key, sequence),
        "supersedes_event": projection.get("last_disposition_event") or None,
        "disposition": disposition,
        "disposition_qty": float(quantity),
        "stock_uom": row.get("stock_uom"),
        "reason": reason,
        "decision_by": api.session.user,
        "decision_at": decided_at,
        "evidence_code": row.get("commercial_decision_code") or report.get("commercial_decision_code"),
        "evidence_snapshot": _json(_evidence_snapshot(row)),
        "suggested_recovery_rate": row.get("suggested_recovery_rate"),
        "suggested_recovery_rate_source": row.get("suggested_recovery_rate_source"),
        "rate_evidence_snapshot": _json(row.get("dispatch_cost_evidence") or []),
        "commercial_document_authorized": 0,
        "stock_document_authorized": 0,
        "lot_closure_authorized": 0,
    })
    event.flags.controlled_material_disposition_insert = True
    event.insert(ignore_permissions=True)

    projection.flags.controlled_material_disposition_update = True
    projection.current_disposition = disposition
    projection.current_disposition_qty = float(quantity)
    projection.disposition_revision = sequence
    projection.last_disposition_event = event.name
    projection.last_decision_by = api.session.user
    projection.last_decision_at = decided_at
    projection.save(ignore_permissions=True)
    return {
        "material_disposition": projection.name,
        "disposition_event": event.name,
        "disposition": disposition,
        "disposition_qty": float(quantity),
        "stock_uom": row.get("stock_uom"),
        "disposition_revision": sequence,
        "commercial_document_authorized": False,
        "stock_document_authorized": False,
        "lot_closure_authorized": False,
    }


def _read_history(api, projection):
    issues = []
    references = api.get_all(
        "Processor Lot Material Disposition Event",
        filters={"material_disposition": projection.name},
        fields=["name", "event_sequence"],
        limit_page_length=0,
    )
    events = []
    for reference in references:
        event = api.get_doc("Processor Lot Material Disposition Event", reference.get("name"))
        event.check_permission("read")
        events.append(event)
    events.sort(key=lambda row: int(row.get("event_sequence") or 0))
    previous = None
    for index, event in enumerate(events, 1):
        if (
            int(event.get("event_sequence") or 0) != index
            or event.get("scope_key") != projection.get("scope_key")
            or event.get("event_key") != make_event_key(projection.get("scope_key"), index)
            or event.get("supersedes_event") != previous
        ):
            _issue(issues, "BROKEN_MATERIAL_DISPOSITION_HISTORY")
        if not all((event.get("reason"), event.get("decision_by"), event.get("decision_at"))):
            _issue(issues, "INCOMPLETE_MATERIAL_DISPOSITION_AUDIT")
        if event.get("commercial_document_authorized") or event.get("stock_document_authorized") or event.get("lot_closure_authorized"):
            _issue(issues, "UNSAFE_MATERIAL_DISPOSITION_AUTHORIZATION")
        previous = event.name
    latest = events[-1] if events else None
    if (
        int(projection.get("disposition_revision") or 0) != len(events)
        or projection.get("last_disposition_event") != (latest.name if latest else None)
        or (latest and (
            projection.get("current_disposition") != latest.get("disposition")
            or _qty(projection.get("current_disposition_qty")) != _qty(latest.get("disposition_qty"))
        ))
    ):
        _issue(issues, "MATERIAL_DISPOSITION_PROJECTION_MISMATCH")
    return {
        "name": projection.name,
        "scope_key": projection.get("scope_key"),
        "disposition": projection.get("current_disposition"),
        "disposition_qty": projection.get("current_disposition_qty"),
        "stock_uom": projection.get("stock_uom"),
        "disposition_revision": projection.get("disposition_revision") or 0,
        "last_disposition_event": projection.get("last_disposition_event"),
        "last_decision_by": projection.get("last_decision_by"),
        "last_decision_at": projection.get("last_decision_at"),
        "decision_events": [row.as_dict() if callable(getattr(row, "as_dict", None)) else dict(row) for row in events],
        "commercial_document_authorized": False,
        "stock_document_authorized": False,
        "lot_closure_authorized": False,
    }, issues


def _resolve_exact_component(report, processor_lot, identity):
    supplied = dict(identity or {})
    supplied.update(scope_type=RAW_MATERIAL, processor_lot=processor_lot)
    requested = canonical_scope(supplied)
    matches = []
    for row in report.get("components") or []:
        try:
            if make_scope_key(dict(row, scope_type=RAW_MATERIAL, processor_lot=processor_lot)) == make_scope_key(requested):
                matches.append(row)
        except ValueError:
            continue
    if len(matches) != 1:
        raise ValueError("Material disposition scope is missing or ambiguous")
    return matches[0], requested


def _get_or_create_projection(api, scope, scope_key):
    name = api.db.get_value("Processor Lot Material Disposition", {"scope_key": scope_key}, "name")
    if name:
        return api.get_doc("Processor Lot Material Disposition", name)
    values = {field: value for field, value in scope.items() if field != "scope_type"}
    doc = api.get_doc(dict(doctype="Processor Lot Material Disposition", scope_key=scope_key,
                           disposition_revision=0, **values))
    doc.flags.controlled_material_disposition_insert = True
    try:
        doc.insert(ignore_permissions=True)
        return doc
    except api.UniqueValidationError:
        name = api.db.get_value("Processor Lot Material Disposition", {"scope_key": scope_key}, "name")
        if not name:
            raise
        return api.get_doc("Processor Lot Material Disposition", name)


def _validate_expected_revision(projection, expected_revision, expected_last_event):
    try:
        expected = int(expected_revision or 0)
    except (TypeError, ValueError):
        raise ValueError("Expected material disposition revision must be an integer")
    if int(projection.get("disposition_revision") or 0) != expected or (
        projection.get("last_disposition_event") or None
    ) != (expected_last_event or None):
        raise ValueError("Material disposition changed since this screen was loaded; reload and review it")


def _reject_active_draft_return(api, report, row):
    references = api.get_all(
        "Stock Entry Detail",
        filters={"sco_rm_detail": row.get("sco_supplied_item"), "docstatus": 0},
        fields=["parent"],
        limit_page_length=0,
    )
    for reference in references:
        doc = api.get_doc("Stock Entry", reference.get("parent"))
        doc.check_permission("read")
        if doc.get("docstatus") == 0 and doc.get("is_return") and doc.get("subcontracting_order") == report.get("subcontracting_order"):
            raise ValueError("Resolve the active draft component return before recording retention")


def _reject_existing_commercial_classification(api, scope_key):
    if api.db.get_value("Processor Lot Commercial Classification", {"scope_key": scope_key}, "name"):
        raise ValueError("Resolve the existing commercial classification before revising material disposition")


def _lock_lot(api, name):
    api.db.sql("select name from `tabProcessor Lot` where name=%s for update", (name,))


def _lock_projection(api, name):
    api.db.sql("select name from `tabProcessor Lot Material Disposition` where name=%s for update", (name,))


def _finished_item(sco, name):
    matches = [row for row in sco.get("items") or [] if row.get("name") == name]
    if len(matches) != 1 or not matches[0].get("item_code"):
        raise ValueError("Material disposition has invalid finished-item lineage")
    return matches[0].get("item_code")


def _evidence_snapshot(row):
    return {field: row.get(field) for field in (
        "sco_supplied_item", "sco_finished_item", "component_item", "stock_uom",
        "physical_remaining_qty", "applied_credit_qty", "unaccounted_remaining_qty",
        "suggested_recovery_rate", "suggested_recovery_rate_source",
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


def _issue(issues, code):
    if code not in issues:
        issues.append(code)
