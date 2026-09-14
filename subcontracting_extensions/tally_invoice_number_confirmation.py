"""Controlled J19B2I Tally confirmation; never creates or authorizes an invoice."""

import json
from decimal import Decimal

from subcontracting_extensions.commercial_classification_policy import validate_reason
from subcontracting_extensions.document_naming_rule_resolver import (
    forecast_from_naming_rule, naming_rule_snapshot, resolve_document_naming_rule,
)
from subcontracting_extensions.retained_material_policy_reconciliation import (
    _lock, _require_system_manager, _same_modified,
)
from subcontracting_extensions.sales_invoice_number_reservation import (
    MODE, _exact_row, _read_rules,
)
from subcontracting_extensions.settlement_method_policy import get_method_contract


CONTRACT_VERSION = "J19B2I"


def confirm_tally_reservation(
    api, read_preview, reservation_name, reason, confirmation_attested,
    expected_reservation_modified, expected_invoice_number,
    expected_naming_rule_snapshot, expected_purchase_order_modified,
    expected_processor_lot_modified, expected_supplier_modified,
    expected_customer_modified, expected_recovery_quantity,
    expected_material_content_rate, expected_net_material_amount,
    expected_supplier_warehouse_qty, expected_supplier_warehouse_valuation_rate,
    expected_supplier_warehouse_stock_value, expected_disposition_revision,
    expected_last_disposition_event, expected_classification_revision,
    expected_treatment_revision, expected_last_decision_event,
    expected_policy_reconciliation_event,
):
    """Persist one immutable external-system confirmation in the caller transaction."""
    from frappe.utils import cint, now_datetime

    reason = validate_reason(reason)
    _require_system_manager(api)
    if not cint(confirmation_attested):
        raise ValueError("Explicit confirmation that the number is reserved in Tally is required")

    _lock(api, "Processor Lot Sales Invoice Number Reservation", reservation_name)
    reservation = api.get_doc("Processor Lot Sales Invoice Number Reservation", reservation_name)
    reservation.check_permission("read")
    _same_modified(reservation, expected_reservation_modified, "Invoice-number reservation")
    if reservation.get("reserved_invoice_number") != expected_invoice_number:
        raise ValueError("Reserved invoice number changed; reload and review it")
    expected_rule = _json(expected_naming_rule_snapshot)
    if _json(reservation.get("naming_rule_snapshot")) != expected_rule:
        raise ValueError("Reservation naming-rule evidence changed")
    if reservation.get("tally_confirmation_status") != "PENDING":
        raise ValueError("Reservation is not awaiting Tally confirmation")
    if api.get_all("Processor Lot Sales Invoice Number Confirmation",
                   filters={"reservation": reservation.name}, fields=["name"],
                   limit_page_length=1):
        raise ValueError("Tally reservation is already confirmed")
    if api.db.exists("Sales Invoice", expected_invoice_number):
        raise ValueError("Reserved invoice number already exists as a Sales Invoice")

    lot = api.get_doc("Processor Lot", reservation.get("processor_lot"))
    lot.check_permission("write")
    _lock(api, "Processor Lot", lot.name)
    lot = api.get_doc("Processor Lot", lot.name)
    _same_modified(lot, expected_processor_lot_modified, "Processor Lot")
    if lot.get("docstatus") != 0 or lot.get("settlement_status") not in (None, "", "Draft"):
        raise ValueError("Processor Lot is not in the permitted pre-settlement state")
    if any(lot.get(field) for field in ("generated_document", "generated_document_type", "debit_note")):
        raise ValueError("Processor Lot already references a commercial document")

    po = api.get_doc("Purchase Order", reservation.get("purchase_order"))
    _lock(api, "Purchase Order", po.name)
    po = api.get_doc("Purchase Order", po.name)
    _same_modified(po, expected_purchase_order_modified, "Purchase Order")
    if po.get("docstatus") != 1 or po.get("custom_shortage_settlement_method") != "SALES_INVOICE":
        raise ValueError("Purchase Order Sales Invoice policy is no longer ready")

    supplier = api.get_doc("Supplier", reservation.get("supplier"))
    _lock(api, "Supplier", supplier.name)
    supplier = api.get_doc("Supplier", supplier.name)
    _same_modified(supplier, expected_supplier_modified, "Supplier")
    if supplier.get("custom_recovery_customer") != reservation.get("recovery_customer"):
        raise ValueError("Supplier-bound Recovery Customer changed")
    customer = api.get_doc("Customer", reservation.get("recovery_customer"))
    _lock(api, "Customer", customer.name)
    customer = api.get_doc("Customer", customer.name)
    _same_modified(customer, expected_customer_modified, "Recovery Customer")
    if supplier.get("disabled") or customer.get("disabled"):
        raise ValueError("Supplier or Recovery Customer is disabled")

    settings = api.get_single("Subcontracting Settlement Settings")
    if settings.get("sales_invoice_number_coordination_mode") != MODE:
        raise ValueError("Transitional Tally coordination is not enabled")
    if (settings.get("external_invoice_system_name") or "Tally") != "Tally":
        raise ValueError("External invoice system changed; reload and review it")
    method = get_method_contract(settings.get("allowed_settlement_methods") or [],
                                 "SALES_INVOICE", "Shortage")
    role = method.get("approval_role")
    if role and role not in api.get_roles():
        raise PermissionError(f"Tally confirmation requires role {role}")

    rules = _read_rules(api)
    for name in sorted(rule.get("name") for rule in rules):
        _lock(api, "Document Naming Rule", name)
    resolved = resolve_document_naming_rule(
        _read_rules(api), "Sales Invoice",
        {"company": reservation.get("company"), "is_return": 0,
         "custom_is_debitservice": 0},
    )
    live_snapshot = naming_rule_snapshot(resolved)
    if live_snapshot != expected_rule or resolved.get("name") != reservation.get("naming_rule"):
        raise ValueError("Document Naming Rule changed; reload and review it")
    if forecast_from_naming_rule(resolved) != expected_invoice_number:
        raise ValueError("Naming-rule counter no longer forecasts the reserved number")

    report = read_preview(lot.name)
    identity = {"sco_supplied_item": reservation.get("sco_supplied_item"),
                "sco_finished_item": reservation.get("sco_finished_item")}
    row = _exact_row(report, identity)
    readiness = row.get("retained_material_sales_invoice_draft_readiness") or {}
    if readiness.get("blocking_issues") or not readiness.get("applicable"):
        raise ValueError("Sales Invoice readiness changed; reload and review it")
    disposition = row.get("persisted_material_disposition") or {}
    classification = row.get("persisted_classification") or {}
    _same(disposition, "disposition_revision", expected_disposition_revision,
          "last_disposition_event", expected_last_disposition_event, "Disposition")
    _same(classification, "classification_revision", expected_classification_revision,
          "last_decision_event", expected_last_decision_event, "Classification")
    if int(classification.get("treatment_revision") or 0) != int(expected_treatment_revision or 0):
        raise ValueError("Treatment revision changed; reload and review it")
    if classification.get("selected_treatment_method") != "SALES_INVOICE":
        raise ValueError("Sales Invoice treatment is no longer selected")
    lineage = readiness.get("lineage") or {}
    if lineage.get("policy_reconciliation_event") != expected_policy_reconciliation_event:
        raise ValueError("Policy reconciliation event changed; reload and review it")
    draft = readiness.get("draft_values") or {}
    _number(draft.get("qty"), expected_recovery_quantity, "Recovery quantity")
    _number(draft.get("rate"), expected_material_content_rate, "Material-content rate")
    _number(draft.get("net_amount_excluding_tax"), expected_net_material_amount,
            "Net material amount")
    bins = api.get_all("Bin", filters={"item_code": draft.get("item_code"),
                       "warehouse": draft.get("warehouse")},
                       fields=["actual_qty", "valuation_rate", "stock_value"],
                       limit_page_length=2)
    if len(bins) != 1:
        raise ValueError("Supplier warehouse stock evidence is ambiguous")
    stock = bins[0]
    _number(stock.get("actual_qty"), expected_supplier_warehouse_qty, "Warehouse quantity")
    _number(stock.get("valuation_rate"), expected_supplier_warehouse_valuation_rate,
            "Warehouse valuation rate")
    _number(stock.get("stock_value"), expected_supplier_warehouse_stock_value,
            "Warehouse stock value")

    event = api.new_doc("Processor Lot Sales Invoice Number Confirmation")
    event.update({
        "reservation": reservation.name, "processor_lot": lot.name,
        "scope_key": reservation.get("scope_key"),
        "reserved_invoice_number": expected_invoice_number,
        "external_invoice_system": "Tally", "confirmation_status": "CONFIRMED",
        "confirmation_attested": 1, "reason": reason,
        "confirmed_by": api.session.user, "confirmed_at": now_datetime(),
        "naming_rule": resolved.get("name"),
        "naming_rule_counter_at_confirmation": resolved.get("counter"),
        "naming_rule_snapshot": json.dumps(live_snapshot, sort_keys=True),
        "material_disposition": disposition.get("name"),
        "commercial_classification": classification.get("name"),
        "policy_reconciliation_event": expected_policy_reconciliation_event,
        "treatment_decision_event": expected_last_decision_event,
        "disposition_revision": expected_disposition_revision,
        "classification_revision": expected_classification_revision,
        "treatment_revision": expected_treatment_revision,
        "recovery_quantity": draft.get("qty"), "material_content_rate": draft.get("rate"),
        "net_material_amount": draft.get("net_amount_excluding_tax"),
        "supplier_warehouse_qty": stock.get("actual_qty"),
        "supplier_warehouse_valuation_rate": stock.get("valuation_rate"),
        "supplier_warehouse_stock_value": stock.get("stock_value"),
    })
    event.flags.controlled_tally_confirmation_insert = True
    event.insert(ignore_permissions=True)
    return {
        "contract_version": CONTRACT_VERSION,
        "confirmation_code": "TALLY_INVOICE_NUMBER_RESERVATION_CONFIRMED",
        "confirmation_event": event.name, "reservation": reservation.name,
        "reserved_invoice_number": expected_invoice_number,
        "tally_confirmation_status": "CONFIRMED",
        "naming_rule_counter_unchanged": True,
        "commercial_execution_ready": False,
        "commercial_document_creation_enabled": False,
        "commercial_document_authorized": False, "stock_document_authorized": False,
        "accounting_posting_authorized": False, "tax_posting_authorized": False,
        "lot_closure_authorized": False,
    }


def _same(doc, revision_field, revision, event_field, event, label):
    if (int(doc.get(revision_field) or 0) != int(revision or 0)
            or doc.get(event_field) != event):
        raise ValueError(label + " changed; reload and review it")


def _number(actual, expected, label):
    if Decimal(str(actual)) != Decimal(str(expected)):
        raise ValueError(label + " changed; reload and review it")


def _json(value):
    return json.loads(value) if isinstance(value, str) else value
