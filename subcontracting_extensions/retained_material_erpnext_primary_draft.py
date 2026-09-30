"""Controlled ERPNext-primary retained-material Sales Invoice Draft creation."""

import json
from decimal import Decimal

from subcontracting_extensions.commercial_classification_policy import validate_reason
from subcontracting_extensions.document_naming_rule_resolver import (
    forecast_from_naming_rule, naming_rule_snapshot, resolve_document_naming_rule,
)
from subcontracting_extensions.retained_material_invoice_mode import (
    ERPNEXT_PRIMARY, creation_mode, verify_draft_allocation,
)
from subcontracting_extensions.retained_material_invoice_release import require_current_release
from subcontracting_extensions.retained_material_policy_reconciliation import _lock, _same_modified
from subcontracting_extensions.retained_material_sales_invoice_draft_creation import (
    _number, _revision, _validate_resolved_invoice,
)
from subcontracting_extensions.sales_invoice_number_reservation import _exact_row, _read_rules
from subcontracting_extensions.settlement_action_authority import require_settlement_action
from subcontracting_extensions.settlement_method_policy import get_method_contract


def create_draft(api, read_preview, processor_lot, scope_identity, reason,
                 draft_creation_confirmed, expected_lot_modified,
                 expected_purchase_order_modified, expected_supplier_modified,
                 expected_customer_modified, expected_posting_date,
                 expected_draft_values, expected_lineage, expected_naming_rule_snapshot,
                 expected_stock, expected_taxes_and_charges, expected_item_tax_template,
                 expected_tax_rows, expected_total_taxes, expected_grand_total):
    """Insert one Draft; only ERPNext's naming rule assigns its actual name."""
    from frappe.utils import cint, now_datetime

    reason = validate_reason(reason)
    require_settlement_action("CREATE_DRAFT_RETAINED_MATERIAL_SALES_INVOICE", api=api)
    if not cint(draft_creation_confirmed):
        raise ValueError("Explicit Draft creation confirmation is required")
    expected_draft_values = _json(expected_draft_values)
    expected_lineage = _json(expected_lineage)
    expected_stock = _json(expected_stock)
    expected_naming_rule_snapshot = _json(expected_naming_rule_snapshot)
    expected_tax_rows = _json(expected_tax_rows)
    if not all(isinstance(value, dict) for value in
               (expected_draft_values, expected_lineage, expected_stock,
                expected_naming_rule_snapshot)) or not isinstance(expected_tax_rows, list):
        raise ValueError("Reviewed Draft evidence is incomplete")
    if creation_mode(api.get_single("Subcontracting Settlement Settings")) != ERPNEXT_PRIMARY:
        raise ValueError("ERPNext-primary invoice mode is not selected")

    lot = api.get_doc("Processor Lot", processor_lot)
    lot.check_permission("read")
    _lock(api, "Processor Lot", processor_lot)
    lot = api.get_doc("Processor Lot", processor_lot)
    _same_modified(lot, expected_lot_modified, "Processor Lot")
    if (lot.get("docstatus") != 0 or lot.get("settlement_status") not in (None, "", "Draft")
            or lot.get("generated_document") or lot.get("generated_document_type")
            or lot.get("debit_note")):
        raise ValueError("Processor Lot settlement already began")
    report = read_preview(lot.name)
    row = _exact_row(report, scope_identity)
    if (row.get("commercial_scope_key") != expected_lineage.get("scope_key")
            or expected_lineage.get("processor_lot") != lot.name):
        raise ValueError("Reviewed commercial scope changed")
    require_current_release(api, row, processor_lot=lot.name)
    readiness = row.get("retained_material_sales_invoice_draft_readiness") or {}
    if (readiness.get("blocking_issues") or not readiness.get("applicable")
            or readiness.get("invoice_number_coordination_mode") != ERPNEXT_PRIMARY
            or readiness.get("invoice_number_reservation")
            or readiness.get("tally_reservation_confirmation")
            or readiness.get("sales_invoice_draft_creation_event")):
        raise ValueError("ERPNext-primary Draft readiness changed")
    draft = readiness.get("draft_values") or {}
    lineage = readiness.get("lineage") or {}
    if _canonical(draft) != _canonical(expected_draft_values) or _canonical(lineage) != _canonical(expected_lineage):
        raise ValueError("Reviewed Draft values or lineage changed")
    if api.get_all("Processor Lot Sales Invoice Draft Creation Event",
                   filters={"scope_key": lineage["scope_key"]}, fields=["name"], limit_page_length=1):
        raise ValueError("This scope already has Draft creation evidence")
    if api.get_all("Sales Invoice Item", filters={"custom_processor_lot_scope_key": lineage["scope_key"]},
                   fields=["name"], limit_page_length=1):
        raise ValueError("This scope already has an invoice history")
    disposition = row.get("persisted_material_disposition") or {}
    classification = row.get("persisted_classification") or {}
    _revision(disposition, "disposition_revision", lineage.get("disposition_revision"),
              "last_disposition_event", row.get("persisted_material_disposition", {}).get("last_disposition_event"), "Disposition")
    _revision(classification, "classification_revision", lineage.get("classification_revision"),
              "last_decision_event", lineage.get("treatment_decision_event"), "Classification")
    if (classification.get("selected_treatment_method") != "SALES_INVOICE"
            or int(classification.get("treatment_revision") or 0)
            != int(lineage.get("treatment_revision") or 0)):
        raise ValueError("Treatment selection changed")
    sco = api.get_doc("Subcontracting Order", lot.get("subcontracting_order"))
    sco.check_permission("read")
    if (sco.get("docstatus") != 1 or sco.get("purchase_order") != lot.get("purchase_order")
            or sco.get("supplier") != lot.get("supplier")
            or sco.get("company") != draft.get("company")):
        raise ValueError("Subcontracting Order identity changed")
    po = api.get_doc("Purchase Order", sco.get("purchase_order"))
    _lock(api, "Purchase Order", po.name)
    po = api.get_doc("Purchase Order", po.name)
    _same_modified(po, expected_purchase_order_modified, "Purchase Order")
    if (po.get("docstatus") != 1 or po.get("supplier") != sco.get("supplier")
            or po.get("company") != sco.get("company")
            or po.get("custom_shortage_settlement_method") != "SALES_INVOICE"):
        raise ValueError("Purchase Order Sales Invoice policy changed")
    supplier = api.get_doc("Supplier", po.get("supplier"))
    _lock(api, "Supplier", supplier.name)
    supplier = api.get_doc("Supplier", supplier.name)
    _same_modified(supplier, expected_supplier_modified, "Supplier")
    customer = api.get_doc("Customer", draft.get("customer"))
    _lock(api, "Customer", customer.name)
    customer = api.get_doc("Customer", customer.name)
    _same_modified(customer, expected_customer_modified, "Customer")
    if (supplier.get("disabled") or customer.get("disabled")
            or supplier.get("custom_recovery_customer") != customer.name
            or po.get("custom_recovery_customer") != customer.name):
        raise ValueError("Recovery Customer binding changed")
    settings = api.get_single("Subcontracting Settlement Settings")
    if creation_mode(settings) != ERPNEXT_PRIMARY:
        raise ValueError("ERPNext-primary invoice mode changed")
    get_method_contract(settings.get("allowed_settlement_methods") or [], "SALES_INVOICE", "Shortage")
    if not api.has_permission("Sales Invoice", "create"):
        raise PermissionError("Sales Invoice create permission is required")
    bins = api.get_all("Bin", filters={"item_code": draft.get("item_code"),
                       "warehouse": draft.get("warehouse")},
                       fields=["actual_qty", "valuation_rate", "stock_value"], limit_page_length=2)
    if len(bins) != 1:
        raise ValueError("Supplier warehouse stock evidence is ambiguous")
    stock = bins[0]
    for key in ("actual_qty", "valuation_rate", "stock_value"):
        _number(stock.get(key), expected_stock.get(key), "Supplier warehouse " + key)

    rules = _read_rules(api)
    for name in sorted(rule.get("name") for rule in rules):
        _lock(api, "Document Naming Rule", name)
    resolved = resolve_document_naming_rule(_read_rules(api), "Sales Invoice",
                {"company": draft.get("company"), "is_return": 0, "custom_is_debitservice": 0})
    if naming_rule_snapshot(resolved) != expected_naming_rule_snapshot:
        raise ValueError("Document Naming Rule changed; reload before creating the Draft")
    counter_before = int(resolved.get("counter") or 0)
    forecast = forecast_from_naming_rule(resolved)
    if api.db.exists("Sales Invoice", forecast):
        raise ValueError("Next Document Naming Rule number already exists")

    invoice = api.new_doc("Sales Invoice")
    invoice.update({
        "company": draft.get("company"), "customer": draft.get("customer"),
        "customer_address": draft.get("customer_address"),
        "posting_date": expected_posting_date, "update_stock": 1,
        "custom_processor_lot_settlement": lot.name,
        "custom_retained_material_invoice_mode": ERPNEXT_PRIMARY,
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
        "custom_processor_lot_scope_key": lineage["scope_key"],
        "custom_material_disposition": disposition.get("name"),
        "custom_commercial_classification": classification.get("name"),
        "custom_policy_reconciliation_event": lineage.get("policy_reconciliation_event"),
        "custom_treatment_decision_event": lineage.get("treatment_decision_event"),
        "custom_disposition_revision": lineage.get("disposition_revision"),
        "custom_classification_revision": lineage.get("classification_revision"),
        "custom_treatment_revision": lineage.get("treatment_revision"),
    })
    invoice.set_missing_values()
    invoice.posting_date = expected_posting_date
    if invoice.meta.has_field("cost_center"):
        invoice.cost_center = draft.get("cost_center")
    item.cost_center = draft.get("cost_center")
    invoice.calculate_taxes_and_totals()
    _validate_resolved_invoice(invoice, item, forecast,
                               expected_taxes_and_charges, expected_item_tax_template,
                               expected_tax_rows, draft.get("net_amount_excluding_tax"),
                               expected_total_taxes, expected_grand_total)
    invoice.flags.controlled_retained_material_draft_creation = True
    invoice.insert()
    counter_after = int(api.db.get_value("Document Naming Rule", resolved["name"], "counter") or 0)
    verify_draft_allocation(forecast, invoice.name, counter_before, counter_after,
                            invoice.docstatus)
    _validate_resolved_invoice(invoice, item, forecast,
                               expected_taxes_and_charges, expected_item_tax_template,
                               expected_tax_rows, draft.get("net_amount_excluding_tax"),
                               expected_total_taxes, expected_grand_total)

    event = api.new_doc("Processor Lot Sales Invoice Draft Creation Event")
    event.update({
        "sales_invoice": invoice.name, "processor_lot": lot.name,
        "scope_key": lineage["scope_key"], "coordination_mode": ERPNEXT_PRIMARY,
        "naming_rule": resolved["name"], "naming_rule_counter_before": counter_before,
        "naming_rule_counter_after": counter_after, "reason": reason,
        "created_by": api.session.user, "created_at": now_datetime(), "draft_only": 1,
        "material_disposition": disposition.get("name"),
        "commercial_classification": classification.get("name"),
        "policy_reconciliation_event": lineage.get("policy_reconciliation_event"),
        "treatment_decision_event": lineage.get("treatment_decision_event"),
        "disposition_revision": lineage.get("disposition_revision"),
        "classification_revision": lineage.get("classification_revision"),
        "treatment_revision": lineage.get("treatment_revision"),
        "recovery_quantity": draft.get("qty"), "material_content_rate": draft.get("rate"),
        "supplier_warehouse": draft.get("warehouse"),
        "supplier_warehouse_qty_before": stock.get("actual_qty"),
        "supplier_warehouse_valuation_rate": stock.get("valuation_rate"),
        "supplier_warehouse_stock_value_before": stock.get("stock_value"),
        "taxes_and_charges": invoice.taxes_and_charges,
        "item_tax_template": item.item_tax_template,
        "tax_rows_snapshot": json.dumps(expected_tax_rows, sort_keys=True),
        "net_total": invoice.net_total,
        "total_taxes_and_charges": invoice.total_taxes_and_charges,
        "grand_total": invoice.grand_total,
    })
    event.flags.controlled_draft_creation_event_insert = True
    event.insert(ignore_permissions=True)
    return {"sales_invoice": invoice.name, "docstatus": invoice.docstatus,
            "draft_creation_event": event.name, "coordination_mode": ERPNEXT_PRIMARY,
            "naming_rule_counter_before": counter_before,
            "naming_rule_counter_after": counter_after,
            "submission_authorized": False, "lot_closure_authorized": False}


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _canonical(value):
    return json.loads(json.dumps(value, sort_keys=True, default=str))
