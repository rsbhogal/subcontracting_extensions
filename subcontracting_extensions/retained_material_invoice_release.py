"""Purchase handoff for one retained-material Sales Invoice scope."""

import hashlib
import json

from subcontracting_extensions.commercial_classification_policy import validate_reason
from subcontracting_extensions.settlement_action_authority import require_settlement_action
from subcontracting_extensions.retained_material_policy_reconciliation import _lock


ACTION = "RELEASE_RETAINED_MATERIAL_INVOICE_PREPARATION"
DOCTYPE = "Processor Lot Sales Invoice Preparation Release"


def _identity(row):
    classification = row.get("persisted_classification") or {}
    disposition = row.get("persisted_material_disposition") or {}
    lineage = (row.get("retained_material_sales_invoice_draft_readiness") or {}).get("lineage") or {}
    if (classification.get("selected_treatment_method") != "SALES_INVOICE"
            or not classification.get("last_decision_event")
            or not row.get("commercial_scope_key")
            or not disposition.get("name")
            or not lineage.get("policy_reconciliation_event")):
        raise ValueError("Retained-material Sales Invoice treatment evidence is incomplete")
    return {
        "processor_lot": row.get("processor_lot"),
        "scope_key": row.get("commercial_scope_key"),
        "sco_supplied_item": row.get("sco_supplied_item"),
        "sco_finished_item": row.get("sco_finished_item"),
        "material_disposition": disposition.get("name"),
        "disposition_revision": int(disposition.get("disposition_revision") or 0),
        "commercial_classification": classification.get("name"),
        "classification_revision": int(classification.get("classification_revision") or 0),
        "treatment_revision": int(classification.get("treatment_revision") or 0),
        "treatment_decision_event": classification.get("last_decision_event"),
        "policy_reconciliation_event": lineage.get("policy_reconciliation_event"),
    }


def current_release(api, row, *, processor_lot):
    """Return the one release matching the live scope; fail on ambiguous history."""
    expected = _identity(row)
    expected["processor_lot"] = processor_lot
    names = api.get_all(DOCTYPE, filters={"processor_lot": processor_lot,
                                         "scope_key": expected["scope_key"]},
                        fields=["name"], limit_page_length=0)
    matched = []
    for reference in names:
        doc = api.get_doc(DOCTYPE, reference.get("name"))
        doc.check_permission("read")
        if all(doc.get(key) == value for key, value in expected.items()):
            matched.append(doc)
    if len(matched) > 1:
        raise ValueError("Ambiguous current invoice preparation releases")
    return matched[0] if matched else None


def require_current_release(api, row, *, processor_lot):
    release = current_release(api, row, processor_lot=processor_lot)
    if not release:
        raise ValueError("Purchase has not released this current scope for invoice preparation")
    return release


def release_invoice_preparation(api, read_preview, processor_lot, scope_identity,
                                reason, expected_processor_lot_modified,
                                expected_scope_key, expected_treatment_event,
                                expected_treatment_revision):
    """Record a Purchase decision; no invoice or number is created."""
    from frappe.utils import now_datetime

    require_settlement_action(ACTION, api=api)
    reason = validate_reason(reason)
    _lock(api, "Processor Lot", processor_lot)
    lot = api.get_doc("Processor Lot", processor_lot)
    lot.check_permission("write")
    if str(lot.modified) != str(expected_processor_lot_modified):
        raise ValueError("Processor Lot changed; reload before releasing")
    if (lot.docstatus != 0 or lot.get("settlement_status") not in (None, "", "Draft")
            or lot.get("generated_document") or lot.get("debit_note")):
        raise ValueError("Processor Lot is no longer available for invoice preparation")
    report = read_preview(processor_lot)
    matches = [row for row in report.get("components") or []
               if row.get("commercial_scope_key") == expected_scope_key
               and row.get("sco_supplied_item") == scope_identity.get("sco_supplied_item")
               and row.get("sco_finished_item") == scope_identity.get("sco_finished_item")]
    if len(matches) != 1:
        raise ValueError("Retained-material scope is missing or ambiguous")
    row = matches[0]
    identity = _identity(row)
    if (identity["treatment_decision_event"] != expected_treatment_event
            or identity["treatment_revision"] != int(expected_treatment_revision)):
        raise ValueError("Selected treatment changed; reload before releasing")
    if current_release(api, row, processor_lot=processor_lot):
        raise ValueError("Current invoice preparation release already exists")
    event = api.new_doc(DOCTYPE)
    event_identity = dict(identity, processor_lot=processor_lot)
    release_key = hashlib.sha256(json.dumps(event_identity, sort_keys=True).encode()).hexdigest()
    event.update(dict(event_identity, release_key=release_key, reason=reason,
                      released_by=api.session.user, released_at=now_datetime()))
    event.flags.controlled_invoice_preparation_release_insert = True
    event.insert(ignore_permissions=True)
    return {"release": event.name, "processor_lot": processor_lot,
            "scope_key": expected_scope_key, "document_created": False,
            "submission_authorized": False, "lot_closure_authorized": False}
