"""Controlled J19B2H reservation; never creates or authorizes a Sales Invoice."""

import json
from decimal import Decimal

from subcontracting_extensions.commercial_classification_policy import validate_reason
from subcontracting_extensions.document_naming_rule_resolver import (
    forecast_from_naming_rule, naming_rule_snapshot, resolve_document_naming_rule,
)
from subcontracting_extensions.retained_material_policy_reconciliation import (
    _lock, _require_system_manager, _same_modified,
)
from subcontracting_extensions.settlement_method_policy import get_method_contract


CONTRACT_VERSION = "J19B2H"
MODE = "ERPNEXT_FORECAST_WITH_TALLY_COORDINATION"


def reserve_sales_invoice_number(
    api, read_preview, processor_lot, scope_identity, reason,
    expected_invoice_number, expected_naming_rule_snapshot,
    expected_purchase_order_modified, expected_processor_lot_modified,
    expected_supplier_modified, expected_customer_modified,
    expected_recovery_quantity, expected_material_content_rate,
    expected_net_material_amount, expected_supplier_warehouse_qty,
    expected_supplier_warehouse_valuation_rate, expected_supplier_warehouse_stock_value,
    expected_disposition_revision, expected_last_disposition_event,
    expected_classification_revision, expected_treatment_revision,
    expected_last_decision_event, expected_policy_reconciliation_event,
):
    """Reserve one dynamically resolved name in the caller transaction."""
    from frappe.utils import now_datetime

    reason = validate_reason(reason)
    _require_system_manager(api)
    expected_rule = _json(expected_naming_rule_snapshot)
    identity = _json(scope_identity)

    lot = api.get_doc("Processor Lot", processor_lot)
    lot.check_permission("write")
    _lock(api, "Processor Lot", lot.name)
    lot = api.get_doc("Processor Lot", lot.name)
    _same_modified(lot, expected_processor_lot_modified, "Processor Lot")
    if lot.get("docstatus") != 0 or lot.get("settlement_status") not in (None, "", "Draft"):
        raise ValueError("Processor Lot is not in the permitted pre-settlement state")
    if any(lot.get(field) for field in ("generated_document", "generated_document_type", "debit_note")):
        raise ValueError("Processor Lot already references a commercial document")

    sco = api.get_doc("Subcontracting Order", lot.get("subcontracting_order"))
    po = api.get_doc("Purchase Order", sco.get("purchase_order"))
    _lock(api, "Purchase Order", po.name)
    po = api.get_doc("Purchase Order", po.name)
    _same_modified(po, expected_purchase_order_modified, "Purchase Order")
    if po.get("docstatus") != 1 or po.get("custom_shortage_settlement_method") != "SALES_INVOICE":
        raise ValueError("Purchase Order Sales Invoice policy is no longer ready")

    supplier = api.get_doc("Supplier", sco.get("supplier"))
    _lock(api, "Supplier", supplier.name)
    supplier = api.get_doc("Supplier", supplier.name)
    _same_modified(supplier, expected_supplier_modified, "Supplier")
    customer_name = supplier.get("custom_recovery_customer")
    if not customer_name or customer_name != po.get("custom_recovery_customer"):
        raise ValueError("Supplier-bound Recovery Customer changed")
    customer = api.get_doc("Customer", customer_name)
    _lock(api, "Customer", customer.name)
    customer = api.get_doc("Customer", customer.name)
    _same_modified(customer, expected_customer_modified, "Recovery Customer")
    if supplier.get("disabled") or customer.get("disabled"):
        raise ValueError("Supplier or Recovery Customer is disabled")

    settings = api.get_single("Subcontracting Settlement Settings")
    if settings.get("sales_invoice_number_coordination_mode") != MODE:
        raise ValueError("Transitional invoice-number coordination is not enabled")
    method = get_method_contract(settings.get("allowed_settlement_methods") or [],
                                 "SALES_INVOICE", "Shortage")
    role = method.get("approval_role")
    if role and role not in api.get_roles():
        raise PermissionError(f"Invoice-number reservation requires role {role}")

    facts = {"company": sco.get("company"), "is_return": 0,
             "custom_is_debitservice": 0}
    rules = _read_rules(api)
    for name in sorted(rule.get("name") for rule in rules):
        _lock(api, "Document Naming Rule", name)
    resolved = resolve_document_naming_rule(
        _read_rules(api), "Sales Invoice", facts
    )
    live_snapshot = naming_rule_snapshot(resolved)
    if live_snapshot != expected_rule:
        raise ValueError("Document Naming Rule changed; reload and review the forecast")
    invoice_number = forecast_from_naming_rule(resolved)
    if invoice_number != expected_invoice_number:
        raise ValueError("Forecast invoice number changed; reload and review it")
    configured = settings.get("outward_sales_invoice_series")
    expected_pattern = resolved.get("prefix") + "#" * int(resolved.get("prefix_digits") or 0)
    if configured != expected_pattern:
        raise ValueError("Configured outward series differs from the applicable naming rule")

    report = read_preview(processor_lot)
    row = _exact_row(report, identity)
    readiness = row.get("retained_material_sales_invoice_draft_readiness") or {}
    if readiness.get("blocking_issues") or not readiness.get("applicable"):
        raise ValueError("Sales Invoice draft readiness changed; reload and review it")
    disposition = row.get("persisted_material_disposition") or {}
    classification = row.get("persisted_classification") or {}
    _expected(disposition, "disposition_revision", expected_disposition_revision,
              "last_disposition_event", expected_last_disposition_event, "Disposition")
    _expected(classification, "classification_revision", expected_classification_revision,
              "last_decision_event", expected_last_decision_event, "Classification")
    if int(classification.get("treatment_revision") or 0) != int(expected_treatment_revision or 0):
        raise ValueError("Treatment revision changed; reload and review it")
    if classification.get("selected_treatment_method") != "SALES_INVOICE":
        raise ValueError("Sales Invoice treatment is no longer selected")
    lineage = readiness.get("lineage") or {}
    if lineage.get("policy_reconciliation_event") != expected_policy_reconciliation_event:
        raise ValueError("Policy reconciliation event changed; reload and review it")

    draft = readiness.get("draft_values") or {}
    _same_number(draft.get("qty"), expected_recovery_quantity, "Recovery quantity")
    _same_number(draft.get("rate"), expected_material_content_rate, "Material-content rate")
    _same_number(draft.get("net_amount_excluding_tax"), expected_net_material_amount,
                 "Net material amount")
    bins = api.get_all("Bin", filters={"item_code": draft.get("item_code"),
                       "warehouse": draft.get("warehouse")},
                       fields=["actual_qty", "valuation_rate", "stock_value"],
                       limit_page_length=2)
    if len(bins) != 1:
        raise ValueError("Supplier warehouse stock evidence is ambiguous")
    stock = bins[0]
    _same_number(stock.get("actual_qty"), expected_supplier_warehouse_qty, "Warehouse quantity")
    _same_number(stock.get("valuation_rate"), expected_supplier_warehouse_valuation_rate,
                 "Warehouse valuation rate")
    _same_number(stock.get("stock_value"), expected_supplier_warehouse_stock_value,
                 "Warehouse stock value")

    if api.db.exists("Sales Invoice", invoice_number):
        raise ValueError("Forecast invoice number already exists")
    if api.get_all("Processor Lot Sales Invoice Number Reservation",
                   filters={"reserved_invoice_number": invoice_number}, fields=["name"],
                   limit_page_length=1):
        raise ValueError("Forecast invoice number is already reserved")
    if api.get_all("Processor Lot Sales Invoice Number Reservation",
                   filters={"scope_key": row.get("commercial_scope_key")}, fields=["name"],
                   limit_page_length=1):
        raise ValueError("Exact commercial scope already has a reservation")

    reservation = api.new_doc("Processor Lot Sales Invoice Number Reservation")
    reservation.update({
        "processor_lot": lot.name, "subcontracting_order": sco.name,
        "purchase_order": po.name, "company": sco.get("company"),
        "supplier": supplier.name, "recovery_customer": customer.name,
        "scope_key": row.get("commercial_scope_key"),
        "sco_supplied_item": row.get("sco_supplied_item"),
        "sco_finished_item": row.get("sco_finished_item"),
        "component_item": row.get("component_item"), "stock_uom": row.get("stock_uom"),
        "reserved_invoice_number": invoice_number, "configured_series": configured,
        "naming_rule": resolved.get("name"), "naming_rule_modified": str(resolved.get("modified")),
        "naming_rule_priority": resolved.get("priority"), "naming_rule_prefix": resolved.get("prefix"),
        "naming_rule_prefix_digits": resolved.get("prefix_digits"),
        "naming_rule_counter_before": resolved.get("counter"),
        "naming_rule_snapshot": json.dumps(live_snapshot, sort_keys=True),
        "reason": reason, "reserved_by": api.session.user, "reserved_at": now_datetime(),
        "tally_confirmation_required": 1, "tally_confirmation_status": "PENDING",
        "material_disposition": disposition.get("name"),
        "commercial_classification": classification.get("name"),
        "policy_reconciliation_event": expected_policy_reconciliation_event,
        "treatment_decision_event": expected_last_decision_event,
        "disposition_revision": expected_disposition_revision,
        "classification_revision": expected_classification_revision,
        "treatment_revision": expected_treatment_revision,
        "recovery_quantity": draft.get("qty"), "material_content_rate": draft.get("rate"),
        "net_material_amount": draft.get("net_amount_excluding_tax"),
        "supplier_warehouse": draft.get("warehouse"),
        "supplier_warehouse_qty": stock.get("actual_qty"),
        "supplier_warehouse_valuation_rate": stock.get("valuation_rate"),
        "supplier_warehouse_stock_value": stock.get("stock_value"),
    })
    reservation.flags.controlled_number_reservation_insert = True
    reservation.insert(ignore_permissions=True)
    return {
        "contract_version": CONTRACT_VERSION, "reservation_code": "SALES_INVOICE_NUMBER_RESERVED",
        "reservation": reservation.name, "reserved_invoice_number": invoice_number,
        "naming_rule": resolved.get("name"), "naming_rule_counter_unchanged": True,
        "tally_confirmation_status": "PENDING", "commercial_document_creation_enabled": False,
        "commercial_document_authorized": False, "stock_document_authorized": False,
        "accounting_posting_authorized": False, "tax_posting_authorized": False,
        "lot_closure_authorized": False,
    }


def protect_reserved_sales_invoice_number(doc, method=None):
    """Block a reserved name unless the future invoice carries exact lineage."""
    import frappe
    if not doc.name or doc.name.startswith("new-"):
        return
    rows = frappe.get_all("Processor Lot Sales Invoice Number Reservation",
                          filters={"reserved_invoice_number": doc.name},
                          fields=["name", "processor_lot", "scope_key", "treatment_decision_event"],
                          limit_page_length=2)
    if not rows:
        return
    if len(rows) != 1:
        frappe.throw("Reserved Sales Invoice number has ambiguous reservation evidence")
    reservation = rows[0]
    matching = [item for item in (doc.get("items") or [])
                if item.get("custom_processor_lot_scope_key") == reservation.get("scope_key")
                and item.get("custom_treatment_decision_event")
                == reservation.get("treatment_decision_event")]
    if (doc.get("custom_processor_lot_settlement") != reservation.get("processor_lot")
            or len(matching) != 1):
        frappe.throw("This Sales Invoice number is reserved for a controlled Processor Lot scope")
    from subcontracting_extensions.retained_material_sales_invoice_draft_creation import (
        protect_controlled_draft_integrity,
    )
    protect_controlled_draft_integrity(doc)


def _read_rules(api):
    result = []
    for reference in api.get_all("Document Naming Rule", fields=["name"], limit_page_length=0):
        doc = api.get_doc("Document Naming Rule", reference.get("name"))
        if doc.get("document_type") == "Sales Invoice":
            result.append(doc.as_dict())
    return result


def _exact_row(report, identity):
    rows = [row for row in (report.get("components") or [])
            if row.get("sco_supplied_item") == identity.get("sco_supplied_item")
            and row.get("sco_finished_item") == identity.get("sco_finished_item")]
    if len(rows) != 1:
        raise ValueError("Expected exactly one retained-material commercial scope")
    return rows[0]


def _expected(doc, revision_field, revision, event_field, event, label):
    if (int(doc.get(revision_field) or 0) != int(revision or 0)
            or doc.get(event_field) != event):
        raise ValueError(label + " changed; reload and review it")


def _same_number(actual, expected, label):
    if Decimal(str(actual)) != Decimal(str(expected)):
        raise ValueError(label + " changed; reload and review it")


def _json(value):
    return json.loads(value) if isinstance(value, str) else value
