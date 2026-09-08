"""J19A2 authoritative read-only component commercial preview reader."""

from copy import deepcopy

from subcontracting_extensions.component_commercial_preview import (
    build_component_commercial_preview,
)
from subcontracting_extensions.material_reconciliation_reader import (
    get_material_position,
)
from subcontracting_extensions.settlement_method_policy import (
    SettlementMethodPolicyError,
    get_default_method,
    get_method_contract,
    initial_method_rows,
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
    policy, policy_issues = _read_policy(api, lot, po, sco)
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


def _read_policy(api, lot, po, sco):
    rows = (
        api.get_single("Subcontracting Settlement Settings").get(
            "allowed_settlement_methods"
        )
        if hasattr(api, "get_single") else initial_method_rows()
    ) or []
    override = bool(lot.get("override_settlement_policy"))
    if override:
        if not all((lot.get("overridden_by"), lot.get("settlement_policy_overridden_on"),
                    lot.get("settlement_policy_override_reason"))):
            issues = ["SETTLEMENT_POLICY_OVERRIDE_EVIDENCE_INCOMPLETE"]
        else:
            issues = []
        policy = {
            "policy_source": "Processor Lot Override",
            "recover_raw_material_shortage": bool(lot.get("recover_raw_material_shortage")),
            "recover_processing_charges_on_shortage": bool(
                lot.get("recover_processing_charges_on_shortage")
            ),
            "settlement_basis": lot.get("settlement_basis"),
        }
        policy.update(_read_method_readiness(api, rows, lot, sco, issues))
        return policy, issues
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
    for lot_field, po_field in (
        ("shortage_settlement_method", "custom_shortage_settlement_method"),
        ("excess_settlement_method", "custom_excess_settlement_method"),
        ("recovery_customer", "custom_recovery_customer"),
    ):
        lot_value = lot.get(lot_field)
        po_value = po.get(po_field)
        if lot_value not in (None, "") and po_value not in (None, "") and lot_value != po_value:
            issues.append("PROCESSOR_LOT_COMMERCIAL_POLICY_DIFFERS_FROM_PURCHASE_ORDER")
            break
    policy = dict(policy_source="Purchase Order", **expected)
    policy.update(_read_method_readiness(api, rows, po, sco, issues, po_source=True))
    return policy, issues


def _read_method_readiness(api, rows, source, sco, issues, po_source=False):
    prefix = "custom_" if po_source else ""
    shortage_code = source.get(prefix + "shortage_settlement_method")
    excess_code = source.get(prefix + "excess_settlement_method")
    recovery_customer = source.get(prefix + "recovery_customer")
    try:
        shortage_code = shortage_code or get_default_method(
            rows, "Shortage"
        )["method_code"]
        shortage = get_method_contract(rows, shortage_code, "Shortage")
    except SettlementMethodPolicyError:
        shortage = None
        issues.append("SHORTAGE_SETTLEMENT_METHOD_NOT_READY")
    try:
        excess_code = excess_code or get_default_method(
            rows, "Excess"
        )["method_code"]
        excess = get_method_contract(rows, excess_code, "Excess")
    except SettlementMethodPolicyError:
        excess = None
        issues.append("EXCESS_SETTLEMENT_METHOD_NOT_READY")

    customer_required = bool(shortage and shortage.get("requires_customer"))
    customer_ready = not customer_required
    bound_customer = None
    try:
        supplier = api.get_doc("Supplier", sco.get("supplier"))
        bound_customer = supplier.get("custom_recovery_customer")
    except (KeyError, TypeError):
        pass
    if recovery_customer:
        customer_ready = recovery_customer == bound_customer
        try:
            customer = api.get_doc("Customer", recovery_customer)
            customer_ready = customer_ready and not bool(customer.get("disabled"))
        except (KeyError, TypeError):
            customer_ready = False
    if customer_required and not customer_ready:
        issues.append("RECOVERY_CUSTOMER_NOT_READY")
    elif recovery_customer and not customer_ready:
        issues.append("RECOVERY_CUSTOMER_COUNTERPARTY_MISMATCH")

    return {
        "shortage_settlement_method": shortage_code,
        "shortage_settlement_method_label": shortage and shortage.get("method_label"),
        "shortage_method_enabled": bool(shortage),
        "excess_settlement_method": excess_code,
        "excess_settlement_method_label": excess and excess.get("method_label"),
        "excess_method_enabled": bool(excess),
        "recovery_customer": recovery_customer,
        "recovery_customer_required": customer_required,
        "recovery_customer_ready": customer_ready,
        "policy_ready": not issues,
    }


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
