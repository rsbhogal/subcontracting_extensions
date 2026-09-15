"""J19B2L controlled Tally statutory-evidence confirmation."""

import json

from subcontracting_extensions.commercial_classification_policy import validate_reason
from subcontracting_extensions.retained_material_policy_reconciliation import (
    _lock, _require_system_manager, _same_modified,
)


CONTRACT_VERSION = "J19B2L"
OUTCOME = "NOT_APPLICABLE_NO_PHYSICAL_MOVEMENT"
EXPECTED_BLOCKERS = {
    "TALLY_STATUTORY_REFERENCE_NOT_RECORDED",
    "TALLY_VEHICLE_NUMBER_NOT_RECORDED",
}


def confirm_no_physical_movement(
    api, read_preview, sales_invoice, reason, confirmation_attested,
    expected_invoice_modified, expected_scope_key, expected_draft_creation_event,
    expected_reservation, expected_tally_confirmation,
    expected_disposition_revision, expected_classification_revision,
    expected_treatment_revision, expected_statutory_evidence,
):
    """Persist one immutable operational determination; authorize nothing."""
    from frappe.utils import cint, now_datetime

    reason = validate_reason(reason)
    _require_system_manager(api)
    if not cint(confirmation_attested):
        raise ValueError("Explicit no-physical-movement attestation is required")

    _lock(api, "Sales Invoice", sales_invoice)
    invoice = api.get_doc("Sales Invoice", sales_invoice)
    invoice.check_permission("write")
    _same_modified(invoice, expected_invoice_modified, "Sales Invoice")
    if invoice.get("docstatus") != 0:
        raise ValueError("Controlled Sales Invoice is no longer Draft")
    if (invoice.get("custom_invoice_number_reservation") != expected_reservation
            or invoice.get("custom_tally_reservation_confirmation")
            != expected_tally_confirmation):
        raise ValueError("Sales Invoice coordination lineage changed")
    if api.get_all("Processor Lot Sales Invoice Statutory Evidence Confirmation",
                   filters={"sales_invoice": invoice.name}, fields=["name"],
                   limit_page_length=1):
        raise ValueError("Statutory evidence is already confirmed")

    settings = api.get_single("Subcontracting Settlement Settings")
    if settings.get("sales_invoice_number_coordination_mode") != (
            "ERPNEXT_FORECAST_WITH_TALLY_COORDINATION"):
        raise ValueError("Transitional Tally coordination is not enabled")
    if (settings.get("external_invoice_system_name") or "Tally") != "Tally":
        raise ValueError("External statutory lead system changed")

    lot = api.get_doc("Processor Lot", invoice.get("custom_processor_lot_settlement"))
    lot.check_permission("write")
    _lock(api, "Processor Lot", lot.name)
    if (lot.get("docstatus") != 0
            or lot.get("settlement_status") not in (None, "", "Draft")
            or lot.get("generated_document") or lot.get("debit_note")):
        raise ValueError("Processor Lot is no longer in the pre-settlement state")

    report = read_preview(lot.name)
    matches = []
    for row in report.get("components") or []:
        readiness = row.get("retained_material_sales_invoice_submission_readiness") or {}
        if (row.get("commercial_scope_key") == expected_scope_key
                and readiness.get("sales_invoice") == invoice.name):
            matches.append((row, readiness))
    if len(matches) != 1:
        raise ValueError("Controlled Sales Invoice scope is missing or ambiguous")
    row, readiness = matches[0]
    if set(readiness.get("blocking_issues") or []) != EXPECTED_BLOCKERS:
        raise ValueError("Submission-readiness blockers changed; reload and review")
    disposition = row.get("persisted_material_disposition") or {}
    classification = row.get("persisted_classification") or {}
    if (int(disposition.get("disposition_revision") or 0)
            != int(expected_disposition_revision or 0)
            or int(classification.get("classification_revision") or 0)
            != int(expected_classification_revision or 0)
            or int(classification.get("treatment_revision") or 0)
            != int(expected_treatment_revision or 0)):
        raise ValueError("Controlled commercial revisions changed")
    event = readiness.get("draft_creation_event")
    if event != expected_draft_creation_event:
        raise ValueError("Draft-creation evidence changed")
    live_statutory = readiness.get("statutory_evidence") or {}
    expected_statutory = _json(expected_statutory_evidence) or {}
    if _canonical(live_statutory) != _canonical(expected_statutory):
        raise ValueError("Sales Invoice statutory fields changed; reload and review")
    if live_statutory.get("ewaybill") or live_statutory.get("irn"):
        raise ValueError("No-physical-movement outcome conflicts with statutory references")
    if live_statutory.get("vehicle_no"):
        raise ValueError("No-physical-movement outcome conflicts with vehicle evidence")

    creation = api.get_doc("Processor Lot Sales Invoice Draft Creation Event", event)
    _lock(api, "Processor Lot Sales Invoice Draft Creation Event", creation.name)
    if (creation.get("reservation") != expected_reservation
            or creation.get("tally_confirmation") != expected_tally_confirmation
            or creation.get("scope_key") != expected_scope_key):
        raise ValueError("Draft-creation lineage changed")
    if api.db.count("Stock Ledger Entry", {"voucher_type": "Sales Invoice",
                                           "voucher_no": invoice.name}):
        raise ValueError("Sales Invoice already has stock posting")
    if api.db.count("GL Entry", {"voucher_type": "Sales Invoice",
                                 "voucher_no": invoice.name}):
        raise ValueError("Sales Invoice already has accounting posting")

    evidence = api.new_doc("Processor Lot Sales Invoice Statutory Evidence Confirmation")
    evidence.update({
        "sales_invoice": invoice.name, "processor_lot": lot.name,
        "scope_key": expected_scope_key, "draft_creation_event": creation.name,
        "reservation": expected_reservation,
        "tally_confirmation": expected_tally_confirmation,
        "external_statutory_system": "Tally", "evidence_outcome": OUTCOME,
        "confirmation_attested": 1, "reason": reason,
        "confirmed_by": api.session.user, "confirmed_at": now_datetime(),
        "invoice_modified_at_confirmation": invoice.modified,
        "statutory_evidence_snapshot": json.dumps(_canonical(live_statutory),
                                                   sort_keys=True),
        "material_disposition": disposition.get("name"),
        "commercial_classification": classification.get("name"),
        "policy_reconciliation_event": creation.get("policy_reconciliation_event"),
        "treatment_decision_event": creation.get("treatment_decision_event"),
        "disposition_revision": expected_disposition_revision,
        "classification_revision": expected_classification_revision,
        "treatment_revision": expected_treatment_revision,
        "recovery_quantity": creation.get("recovery_quantity"),
        "material_content_rate": creation.get("material_content_rate"),
        "net_total": invoice.get("net_total"),
        "total_taxes_and_charges": invoice.get("total_taxes_and_charges"),
        "grand_total": invoice.get("grand_total"),
    })
    evidence.flags.controlled_statutory_confirmation_insert = True
    evidence.insert(ignore_permissions=True)
    return {
        "contract_version": CONTRACT_VERSION,
        "confirmation_code": "TALLY_STATUTORY_EVIDENCE_CONFIRMED_NO_PHYSICAL_MOVEMENT",
        "statutory_evidence_confirmation": evidence.name,
        "sales_invoice": invoice.name, "evidence_outcome": OUTCOME,
        "submission_authorized": False, "stock_posting_authorized": False,
        "accounting_posting_authorized": False,
        "statutory_generation_authorized": False,
        "tax_posting_authorized": False, "lot_closure_authorized": False,
    }


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _canonical(value):
    return json.loads(json.dumps(value, sort_keys=True, default=str))
