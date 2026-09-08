"""J19A2 authoritative read-only component commercial preview reader."""

from copy import deepcopy

from subcontracting_extensions.component_commercial_preview import (
    build_component_commercial_preview,
)
from subcontracting_extensions.material_reconciliation_reader import (
    get_material_position,
)


READER_VERSION = "J19A2"


def get_component_commercial_preview(processor_lot):
    """Return exact component/finished-row commercial evidence without writes."""
    import frappe

    return _read_component_commercial_preview(frappe, processor_lot)


def _read_component_commercial_preview(
    api,
    processor_lot,
    *,
    material_position=None,
    completion_position=None,
):
    """Internal injectable reader used by tests and the console entry point."""
    lot = api.get_doc("Processor Lot", processor_lot)
    lot.check_permission("read")
    sco = api.get_doc("Subcontracting Order", lot.get("subcontracting_order"))
    sco.check_permission("read")
    if lot.get("docstatus") == 2 or sco.get("docstatus") != 1:
        raise ValueError("Commercial evidence requires an active lot and submitted SCO")
    for field in ("company", "supplier", "supplier_warehouse", "purchase_order"):
        if not sco.get(field) or lot.get(field) != sco.get(field):
            raise ValueError("Processor Lot and SCO identity mismatch: " + field)

    po = api.get_doc("Purchase Order", sco.get("purchase_order"))
    po.check_permission("read")
    if (
        po.get("docstatus") != 1
        or po.get("company") != sco.get("company")
        or po.get("supplier") != sco.get("supplier")
    ):
        raise ValueError("Submitted Purchase Order identity does not match the SCO")

    material = material_position or get_material_position(lot.name)
    if completion_position is None:
        # Keep the reader module importable by its injected, database-free
        # tests.  The Frappe-backed completion reader is needed only for a
        # live authoritative read.
        from subcontracting_extensions.receipt_completion import (
            read_completion_evidence,
        )

        completion = read_completion_evidence(lot, sco)
    else:
        completion = completion_position
    finished_rows, invoice_rows = _normalize_finished_rows(api, sco, po, completion)
    policy, policy_issues = _read_policy(lot, po)
    legacy_evidence = _read_legacy_evidence(api, lot, sco)
    _merge_material_settlement_evidence(
        legacy_evidence,
        material.get("settlement_evidence") or [],
    )

    result = build_component_commercial_preview(
        material,
        finished_rows,
        {
            "invoice_rows": invoice_rows,
            "settlement_policy": policy,
            "policy_issues": policy_issues,
            "existing_documents": [],
            "legacy_evidence": legacy_evidence,
        },
    )
    result.update(
        commercial_reader_version=READER_VERSION,
        evidence_scope=(
            "Exact SCO supplied rows and verified allocation-linked SCR/PR/PI "
            "journeys; no classification, write, accounting action, or closure"
        ),
        receipt_completion=deepcopy(completion),
        settlement_policy=policy,
        policy_issues=policy_issues,
        commercial_document_creation_enabled=False,
        commercial_document_authorized=False,
        lot_closure_authorized=False,
    )
    return result


def _normalize_finished_rows(api, sco, po, completion):
    sco_rows = {row.name: row for row in (sco.get("items") or [])}
    po_rows = {row.name: row for row in (po.get("items") or [])}
    finished_rows = []
    invoice_rows = []
    seen_invoice_rows = set()

    for item in completion.get("items") or []:
        key = item.get("subcontracting_order_item")
        source = sco_rows.get(key)
        if not source:
            raise ValueError("Completion evidence contains an unknown SCO finished row")
        po_detail = source.get("purchase_order_item")
        po_row = po_rows.get(po_detail)
        if (
            not po_row
            or po_row.get("stock_uom") != source.get("stock_uom")
        ):
            raise ValueError("SCO finished row does not match its Purchase Order row")
        finished_rows.append({
            "sco_finished_item": key,
            "finished_item": source.get("item_code"),
            "purchase_order_item": po_detail,
            "stock_uom": source.get("stock_uom"),
            "company_accepted_qty": item.get("submitted_scr_qty"),
            "evidence_consistent": bool(item.get("journey_complete")),
            "issues": list(item.get("issues") or []),
        })

    for journey in completion.get("journeys") or []:
        if not journey.get("pi_verified"):
            continue
        pi_name = journey.get("purchase_invoice")
        if not pi_name:
            raise ValueError("Verified journey is missing its Purchase Invoice document")
        pi = api.get_doc("Purchase Invoice", pi_name)
        pi.check_permission("read")
        matches = [row for row in pi.get("items", [])
                   if row.name == journey.get("pi_detail")]
        if len(matches) != 1 or matches[0].name in seen_invoice_rows:
            raise ValueError("Verified Purchase Invoice row identity is missing or duplicated")
        row = matches[0]
        seen_invoice_rows.add(row.name)
        invoice_rows.append({
            "purchase_invoice": pi.name,
            "purchase_invoice_item": row.name,
            "docstatus": pi.get("docstatus"),
            "is_return": pi.get("is_return"),
            "po_detail": row.get("po_detail"),
            "qty": row.get("stock_qty"),
            "uom": row.get("stock_uom"),
            "rate": row.get("rate"),
            "net_rate": row.get("net_rate"),
            "net_amount": row.get("net_amount"),
        })
    return finished_rows, invoice_rows


def _read_policy(lot, po):
    override = bool(lot.get("override_settlement_policy"))
    if override:
        if not all((lot.get("overridden_by"), lot.get("settlement_policy_overridden_on"),
                    lot.get("settlement_policy_override_reason"))):
            issues = ["SETTLEMENT_POLICY_OVERRIDE_EVIDENCE_INCOMPLETE"]
        else:
            issues = []
        return {
            "policy_source": "Processor Lot Override",
            "recover_raw_material_shortage": bool(lot.get("recover_raw_material_shortage")),
            "recover_processing_charges_on_shortage": bool(
                lot.get("recover_processing_charges_on_shortage")
            ),
            "settlement_basis": lot.get("settlement_basis"),
        }, issues
    expected = {
        "recover_raw_material_shortage": bool(po.get("custom_recover_raw_material_shortage")),
        "recover_processing_charges_on_shortage": bool(
            po.get("custom_recover_processing_charges_on_shortage")
        ),
        "settlement_basis": po.get("custom_settlement_basis"),
    }
    issues = []
    for field in ("recover_raw_material_shortage",
                  "recover_processing_charges_on_shortage"):
        lot_value = lot.get(field)
        if lot_value is not None and _flag(lot_value) != expected[field]:
            issues.append("PROCESSOR_LOT_POLICY_DIFFERS_FROM_PURCHASE_ORDER")
            break
    lot_basis = lot.get("settlement_basis")
    if lot_basis not in (None, "", expected["settlement_basis"]):
        issues.append("PROCESSOR_LOT_SETTLEMENT_BASIS_DIFFERS_FROM_PURCHASE_ORDER")
    return dict(policy_source="Purchase Order", **expected), issues


def _read_legacy_evidence(api, lot, sco):
    evidence = []
    for row in api.get_all(
        "Purchase Invoice",
        filters={"custom_processor_lot_settlement": lot.name, "docstatus": ["!=", 2]},
        fields=["name", "docstatus"],
        limit_page_length=0,
    ):
        doc = api.get_doc("Purchase Invoice", row.name)
        doc.check_permission("read")
        evidence.append({"doctype": "Purchase Invoice", "name": doc.name,
                         "docstatus": doc.get("docstatus"), "reason": "Legacy Debit Note"})
    for row in api.get_all(
        "Processor Material Account Entry",
        filters={"processor_lot": lot.name, "docstatus": ["!=", 2]},
        fields=["name"],
        limit_page_length=0,
    ):
        doc = api.get_doc("Processor Material Account Entry", row.name)
        doc.check_permission("read")
        if not doc.get("sco_supplied_item"):
            evidence.append({"doctype": "Processor Material Account Entry", "name": doc.name,
                             "docstatus": doc.get("docstatus"), "reason": "Missing exact SCO supplied row"})
    if lot.get("settlement_status") not in (None, "", "Draft", "Reopened", "Cancelled"):
        evidence.append({"doctype": "Processor Lot", "name": lot.name,
                         "docstatus": lot.get("docstatus"), "reason": "Legacy settlement state"})
    return evidence


def _merge_material_settlement_evidence(target, material_evidence):
    """Preserve J16's SCO-wide settlement discovery without duplicates."""
    seen = {(row.get("doctype"), row.get("name")) for row in target}
    for source in material_evidence:
        identity = (source.get("doctype"), source.get("name"))
        if identity in seen:
            continue
        target.append({
            "doctype": source.get("doctype"),
            "name": source.get("name"),
            "reason": source.get("reason"),
        })
        seen.add(identity)


def _flag(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)
