"""J19B2J controlled retained-material Sales Invoice draft creation."""

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


CONTRACT_VERSION = "J19B2J"


def create_sales_invoice_draft(
    api, read_preview, reservation_name, confirmation_name, reason,
    draft_creation_confirmed, expected_reservation_modified,
    expected_confirmation_modified, expected_invoice_number,
    expected_naming_rule_snapshot, expected_purchase_order_modified,
    expected_processor_lot_modified, expected_supplier_modified,
    expected_customer_modified, expected_posting_date, expected_recovery_quantity,
    expected_material_content_rate, expected_net_material_amount,
    expected_supplier_warehouse_qty, expected_supplier_warehouse_valuation_rate,
    expected_supplier_warehouse_stock_value, expected_disposition_revision,
    expected_last_disposition_event, expected_classification_revision,
    expected_treatment_revision, expected_last_decision_event,
    expected_policy_reconciliation_event, expected_taxes_and_charges,
    expected_item_tax_template, expected_tax_rows, expected_total_taxes,
    expected_grand_total,
):
    """Create exactly one draft using ERPNext naming and tax controllers."""
    from frappe.utils import cint, now_datetime

    reason = validate_reason(reason)
    _require_system_manager(api)
    if not cint(draft_creation_confirmed):
        raise ValueError("Explicit confirmation to create the Draft Sales Invoice is required")
    expected_rule = _json(expected_naming_rule_snapshot)
    expected_tax_rows = _json(expected_tax_rows) or []

    _lock(api, "Processor Lot Sales Invoice Number Reservation", reservation_name)
    reservation = api.get_doc("Processor Lot Sales Invoice Number Reservation", reservation_name)
    _same_modified(reservation, expected_reservation_modified, "Invoice-number reservation")
    _lock(api, "Processor Lot Sales Invoice Number Confirmation", confirmation_name)
    confirmation = api.get_doc("Processor Lot Sales Invoice Number Confirmation", confirmation_name)
    _same_modified(confirmation, expected_confirmation_modified, "Tally confirmation")
    if (confirmation.get("reservation") != reservation.name
            or confirmation.get("confirmation_status") != "CONFIRMED"
            or not confirmation.get("confirmation_attested")
            or confirmation.get("reserved_invoice_number") != expected_invoice_number
            or reservation.get("reserved_invoice_number") != expected_invoice_number):
        raise ValueError("Reservation and Tally confirmation do not match")
    if (_json(reservation.get("naming_rule_snapshot")) != expected_rule
            or _json(confirmation.get("naming_rule_snapshot")) != expected_rule):
        raise ValueError("Reserved naming-rule evidence changed")
    if api.db.exists("Sales Invoice", expected_invoice_number):
        raise ValueError("Reserved Sales Invoice number already exists")
    if api.get_all("Processor Lot Sales Invoice Draft Creation Event",
                   filters={"reservation": reservation.name}, fields=["name"],
                   limit_page_length=1):
        raise ValueError("Reservation already has draft-creation evidence")

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
    if not api.has_permission("Sales Invoice", "create"):
        raise PermissionError("Sales Invoice create permission is required")

    settings = api.get_single("Subcontracting Settlement Settings")
    if settings.get("sales_invoice_number_coordination_mode") != MODE:
        raise ValueError("Transitional Tally coordination is not enabled")
    if (settings.get("external_invoice_system_name") or "Tally") != "Tally":
        raise ValueError("External invoice system changed")
    method = get_method_contract(settings.get("allowed_settlement_methods") or [],
                                 "SALES_INVOICE", "Shortage")
    role = method.get("approval_role")
    if role and role not in api.get_roles():
        raise PermissionError(f"Draft creation requires role {role}")

    rules = _read_rules(api)
    for name in sorted(rule.get("name") for rule in rules):
        _lock(api, "Document Naming Rule", name)
    resolved = resolve_document_naming_rule(
        _read_rules(api), "Sales Invoice",
        {"company": reservation.get("company"), "is_return": 0,
         "custom_is_debitservice": 0},
    )
    if (naming_rule_snapshot(resolved) != expected_rule
            or resolved.get("name") != reservation.get("naming_rule")
            or forecast_from_naming_rule(resolved) != expected_invoice_number):
        raise ValueError("Document Naming Rule or reserved forecast changed")
    counter_before = int(resolved.get("counter") or 0)

    report = read_preview(lot.name)
    row = _exact_row(report, {"sco_supplied_item": reservation.get("sco_supplied_item"),
                              "sco_finished_item": reservation.get("sco_finished_item")})
    readiness = row.get("retained_material_sales_invoice_draft_readiness") or {}
    if (readiness.get("blocking_issues") or not readiness.get("applicable")
            or readiness.get("tally_confirmation_status") != "CONFIRMED"):
        raise ValueError("Sales Invoice draft readiness changed")
    disposition = row.get("persisted_material_disposition") or {}
    classification = row.get("persisted_classification") or {}
    _revision(disposition, "disposition_revision", expected_disposition_revision,
              "last_disposition_event", expected_last_disposition_event, "Disposition")
    _revision(classification, "classification_revision", expected_classification_revision,
              "last_decision_event", expected_last_decision_event, "Classification")
    if (int(classification.get("treatment_revision") or 0)
            != int(expected_treatment_revision or 0)
            or classification.get("selected_treatment_method") != "SALES_INVOICE"):
        raise ValueError("Selected treatment changed")
    draft = readiness.get("draft_values") or {}
    lineage = readiness.get("lineage") or {}
    if lineage.get("policy_reconciliation_event") != expected_policy_reconciliation_event:
        raise ValueError("Policy reconciliation event changed")
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

    invoice = api.new_doc("Sales Invoice")
    invoice.update({
        "company": draft.get("company"), "customer": draft.get("customer"),
        "customer_address": draft.get("customer_address"),
        "posting_date": expected_posting_date, "update_stock": 1,
        "custom_processor_lot_settlement": lot.name,
        "custom_invoice_number_reservation": reservation.name,
        "custom_tally_reservation_confirmation": confirmation.name,
    })
    if invoice.meta.has_field("branch"):
        invoice.branch = draft.get("branch")
    if invoice.meta.has_field("cost_center"):
        invoice.cost_center = draft.get("cost_center")
    item = invoice.append("items", {
        "item_code": draft.get("item_code"), "qty": draft.get("qty"),
        "uom": row.get("stock_uom"), "stock_uom": row.get("stock_uom"),
        "conversion_factor": 1, "rate": draft.get("rate"),
        "warehouse": draft.get("warehouse"), "income_account": draft.get("income_account"),
        "cost_center": draft.get("cost_center"),
        "custom_processor_lot_scope_key": row.get("commercial_scope_key"),
        "custom_material_disposition": disposition.get("name"),
        "custom_commercial_classification": classification.get("name"),
        "custom_policy_reconciliation_event": expected_policy_reconciliation_event,
        "custom_treatment_decision_event": expected_last_decision_event,
        "custom_disposition_revision": expected_disposition_revision,
        "custom_classification_revision": expected_classification_revision,
        "custom_treatment_revision": expected_treatment_revision,
    })
    invoice.set_missing_values()
    invoice.posting_date = expected_posting_date
    if invoice.meta.has_field("cost_center"):
        invoice.cost_center = draft.get("cost_center")
    item.cost_center = draft.get("cost_center")
    invoice.calculate_taxes_and_totals()
    _validate_resolved_invoice(invoice, item, expected_invoice_number,
                               expected_taxes_and_charges, expected_item_tax_template,
                               expected_tax_rows, expected_net_material_amount,
                               expected_total_taxes, expected_grand_total)
    invoice.flags.controlled_retained_material_draft_creation = True
    invoice.insert()
    if invoice.docstatus != 0 or invoice.name != expected_invoice_number:
        raise ValueError("ERPNext did not allocate the confirmed reserved Draft Sales Invoice number")
    counter_after = int(api.db.get_value("Document Naming Rule", resolved.get("name"), "counter") or 0)
    if counter_after != counter_before + 1:
        raise ValueError("Document Naming Rule counter did not advance exactly once")

    event = api.new_doc("Processor Lot Sales Invoice Draft Creation Event")
    event.update({
        "sales_invoice": invoice.name, "processor_lot": lot.name,
        "scope_key": reservation.get("scope_key"), "reservation": reservation.name,
        "tally_confirmation": confirmation.name, "naming_rule": resolved.get("name"),
        "naming_rule_counter_before": counter_before,
        "naming_rule_counter_after": counter_after, "reason": reason,
        "created_by": api.session.user, "created_at": now_datetime(), "draft_only": 1,
        "material_disposition": disposition.get("name"),
        "commercial_classification": classification.get("name"),
        "policy_reconciliation_event": expected_policy_reconciliation_event,
        "treatment_decision_event": expected_last_decision_event,
        "disposition_revision": expected_disposition_revision,
        "classification_revision": expected_classification_revision,
        "treatment_revision": expected_treatment_revision,
        "recovery_quantity": draft.get("qty"),
        "material_content_rate": draft.get("rate"),
        "supplier_warehouse": draft.get("warehouse"),
        "supplier_warehouse_qty_before": stock.get("actual_qty"),
        "supplier_warehouse_valuation_rate": stock.get("valuation_rate"),
        "supplier_warehouse_stock_value_before": stock.get("stock_value"),
        "taxes_and_charges": invoice.taxes_and_charges,
        "item_tax_template": item.item_tax_template,
        "tax_rows_snapshot": json.dumps(expected_tax_rows, sort_keys=True),
        "net_total": invoice.net_total, "total_taxes_and_charges": invoice.total_taxes_and_charges,
        "grand_total": invoice.grand_total,
    })
    event.flags.controlled_draft_creation_event_insert = True
    event.insert(ignore_permissions=True)
    return {
        "contract_version": CONTRACT_VERSION,
        "creation_code": "RETAINED_MATERIAL_SALES_INVOICE_DRAFT_CREATED",
        "sales_invoice": invoice.name, "docstatus": invoice.docstatus,
        "draft_creation_event": event.name, "reservation": reservation.name,
        "tally_confirmation": confirmation.name,
        "naming_rule_counter_before": counter_before,
        "naming_rule_counter_after": counter_after,
        "net_total": invoice.net_total,
        "total_taxes_and_charges": invoice.total_taxes_and_charges,
        "grand_total": invoice.grand_total, "submission_authorized": False,
        "stock_posting_authorized": False, "accounting_posting_authorized": False,
        "tax_posting_authorized": False, "lot_closure_authorized": False,
    }


def prevent_uncontrolled_submission(doc, method=None):
    """Keep every controlled retained-material invoice draft-only through J19B2J."""
    if (doc.get("custom_invoice_number_reservation")
            or doc.get("custom_tally_reservation_confirmation")):
        import frappe
        frappe.throw("Controlled retained-material Sales Invoice submission is not authorized")


def prevent_controlled_draft_deletion(doc, method=None):
    """Preserve a controlled draft and its immutable creation evidence."""
    if (doc.get("custom_invoice_number_reservation")
            or doc.get("custom_tally_reservation_confirmation")):
        import frappe
        frappe.throw("Controlled retained-material Sales Invoice cannot be deleted")


def protect_controlled_draft_integrity(doc):
    """After creation evidence exists, reject edits to controlled facts and lineage."""
    import frappe
    reservation_name = doc.get("custom_invoice_number_reservation")
    if not reservation_name:
        return
    events = frappe.get_all(
        "Processor Lot Sales Invoice Draft Creation Event",
        filters={"sales_invoice": doc.name},
        fields=["name", "processor_lot", "scope_key", "reservation",
                "tally_confirmation", "material_disposition",
                "commercial_classification", "policy_reconciliation_event",
                "treatment_decision_event", "disposition_revision",
                "classification_revision", "treatment_revision", "recovery_quantity",
                "material_content_rate", "supplier_warehouse", "net_total",
                "total_taxes_and_charges", "grand_total"],
        limit_page_length=2,
    )
    if not events:
        if not doc.flags.get("controlled_retained_material_draft_creation"):
            frappe.throw("Controlled Sales Invoice has no draft-creation evidence")
        return
    if len(events) != 1:
        frappe.throw("Controlled Sales Invoice draft-creation evidence is ambiguous")
    event = events[0]
    if (doc.docstatus != 0
            or reservation_name != event.get("reservation")
            or doc.get("custom_tally_reservation_confirmation") != event.get("tally_confirmation")
            or doc.get("custom_processor_lot_settlement") != event.get("processor_lot")):
        frappe.throw("Controlled Sales Invoice header lineage is immutable")
    items = doc.get("items") or []
    if len(items) != 1:
        frappe.throw("Controlled Sales Invoice must retain exactly one item")
    item = items[0]
    pairs = (
        (item.get("custom_processor_lot_scope_key"), event.get("scope_key")),
        (item.get("custom_material_disposition"), event.get("material_disposition")),
        (item.get("custom_commercial_classification"), event.get("commercial_classification")),
        (item.get("custom_policy_reconciliation_event"), event.get("policy_reconciliation_event")),
        (item.get("custom_treatment_decision_event"), event.get("treatment_decision_event")),
        (int(item.get("custom_disposition_revision") or 0), int(event.get("disposition_revision") or 0)),
        (int(item.get("custom_classification_revision") or 0), int(event.get("classification_revision") or 0)),
        (int(item.get("custom_treatment_revision") or 0), int(event.get("treatment_revision") or 0)),
        (str(item.get("warehouse") or ""), str(event.get("supplier_warehouse") or "")),
    )
    if any(actual != expected for actual, expected in pairs):
        frappe.throw("Controlled Sales Invoice item lineage is immutable")
    for actual, expected in (
        (item.get("qty"), event.get("recovery_quantity")),
        (item.get("rate"), event.get("material_content_rate")),
        (doc.get("net_total"), event.get("net_total")),
        (doc.get("total_taxes_and_charges"), event.get("total_taxes_and_charges")),
        (doc.get("grand_total"), event.get("grand_total")),
    ):
        if Decimal(str(actual)) != Decimal(str(expected)):
            frappe.throw("Controlled Sales Invoice commercial values are immutable")


def _validate_resolved_invoice(invoice, item, expected_name, expected_template,
                               expected_item_template, expected_rows, expected_net,
                               expected_tax, expected_grand):
    if invoice.name not in (None, "", expected_name):
        raise ValueError("Sales Invoice acquired a name before controlled insertion")
    if invoice.get("taxes_and_charges") != expected_template:
        raise ValueError("Resolved Sales Taxes and Charges Template changed")
    if item.get("item_tax_template") != expected_item_template:
        raise ValueError("Resolved Item Tax Template changed")
    actual = []
    for row in invoice.get("taxes") or []:
        actual.append({"charge_type": row.get("charge_type"),
                       "account_head": row.get("account_head"),
                       "rate": float(row.get("rate") or 0),
                       "tax_amount": float(row.get("tax_amount") or 0)})
    if actual != expected_rows:
        raise ValueError("Resolved Sales Invoice tax rows changed")
    _number(invoice.net_total, expected_net, "Resolved net total")
    _number(invoice.total_taxes_and_charges, expected_tax, "Resolved tax total")
    _number(invoice.grand_total, expected_grand, "Resolved grand total")


def _revision(doc, revision_field, revision, event_field, event, label):
    if (int(doc.get(revision_field) or 0) != int(revision or 0)
            or doc.get(event_field) != event):
        raise ValueError(label + " changed; reload and review it")


def _number(actual, expected, label):
    if Decimal(str(actual)) != Decimal(str(expected)):
        raise ValueError(label + " changed; reload and review it")


def _json(value):
    return json.loads(value) if isinstance(value, str) else value
