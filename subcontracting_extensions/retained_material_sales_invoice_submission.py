"""J19B2M atomic controlled retained-material Sales Invoice submission."""

import json
from decimal import Decimal

from subcontracting_extensions.commercial_classification_policy import validate_reason
from subcontracting_extensions.retained_material_policy_reconciliation import (
    _lock, _require_system_manager, _same_modified,
)


CONTRACT_VERSION = "J19B2M"


def submit_sales_invoice(
    api, read_preview, sales_invoice, statutory_confirmation, reason,
    submission_confirmed, expected_invoice_modified,
    expected_statutory_confirmation_modified, expected_scope_key,
    expected_disposition_revision, expected_classification_revision,
    expected_treatment_revision, expected_supplier_warehouse_qty,
    expected_supplier_warehouse_valuation_rate,
    expected_supplier_warehouse_stock_value,
):
    """Submit once and verify every posting before the caller can commit."""
    from frappe.utils import cint, now_datetime

    reason = validate_reason(reason)
    _require_system_manager(api)
    if not cint(submission_confirmed):
        raise ValueError("Explicit Sales Invoice submission confirmation is required")

    _lock(api, "Sales Invoice", sales_invoice)
    invoice = api.get_doc("Sales Invoice", sales_invoice)
    invoice.check_permission("submit")
    _same_modified(invoice, expected_invoice_modified, "Sales Invoice")
    if invoice.get("docstatus") != 0:
        raise ValueError("Controlled Sales Invoice is no longer Draft")

    _lock(api, "Processor Lot Sales Invoice Statutory Evidence Confirmation",
          statutory_confirmation)
    confirmation = api.get_doc(
        "Processor Lot Sales Invoice Statutory Evidence Confirmation",
        statutory_confirmation,
    )
    _same_modified(confirmation, expected_statutory_confirmation_modified,
                   "Statutory-evidence confirmation")
    if (confirmation.get("sales_invoice") != invoice.name
            or confirmation.get("scope_key") != expected_scope_key
            or confirmation.get("evidence_outcome")
            != "NOT_APPLICABLE_NO_PHYSICAL_MOVEMENT"
            or not confirmation.get("confirmation_attested")):
        raise ValueError("No-physical-movement confirmation does not match the invoice")

    lot = api.get_doc("Processor Lot", confirmation.get("processor_lot"))
    lot.check_permission("write")
    _lock(api, "Processor Lot", lot.name)
    lot = api.get_doc("Processor Lot", lot.name)
    if (lot.get("docstatus") != 0 or lot.get("settlement_status") != "Draft"
            or lot.get("generated_document") or lot.get("generated_document_type")
            or lot.get("debit_note")):
        raise ValueError("Processor Lot settlement already began")

    if api.get_all("Processor Lot Sales Invoice Submission Event",
                   filters={"sales_invoice": invoice.name}, fields=["name"],
                   limit_page_length=1):
        raise ValueError("Sales Invoice already has controlled submission evidence")
    if api.db.count("Stock Ledger Entry", {"voucher_type": "Sales Invoice",
                                           "voucher_no": invoice.name}):
        raise ValueError("Sales Invoice already has stock posting")
    if api.db.count("GL Entry", {"voucher_type": "Sales Invoice",
                                 "voucher_no": invoice.name}):
        raise ValueError("Sales Invoice already has accounting posting")

    settings = api.get_single("Subcontracting Settlement Settings")
    if (settings.get("sales_invoice_number_coordination_mode")
            != "ERPNEXT_FORECAST_WITH_TALLY_COORDINATION"
            or (settings.get("external_invoice_system_name") or "Tally") != "Tally"):
        raise ValueError("Tally statutory-lead configuration changed")

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
    if (readiness.get("blocking_issues")
            or readiness.get("readiness_code")
            != "SALES_INVOICE_DRAFT_READY_FOR_FUTURE_CONTROLLED_SUBMISSION"):
        raise ValueError("Sales Invoice submission readiness changed")
    if ((readiness.get("statutory_evidence_confirmation") or {}).get("name")
            != confirmation.name):
        raise ValueError("Statutory-evidence confirmation changed")
    disposition = row.get("persisted_material_disposition") or {}
    classification = row.get("persisted_classification") or {}
    if (int(disposition.get("disposition_revision") or 0)
            != int(expected_disposition_revision or 0)
            or int(classification.get("classification_revision") or 0)
            != int(expected_classification_revision or 0)
            or int(classification.get("treatment_revision") or 0)
            != int(expected_treatment_revision or 0)):
        raise ValueError("Controlled commercial revisions changed")

    items = invoice.get("items") or []
    if len(items) != 1 or items[0].get("custom_processor_lot_scope_key") != expected_scope_key:
        raise ValueError("Controlled Sales Invoice item scope changed")
    item = items[0]
    bins = api.get_all("Bin", filters={"item_code": item.get("item_code"),
                       "warehouse": item.get("warehouse")},
                       fields=["name", "actual_qty", "valuation_rate", "stock_value"],
                       limit_page_length=2)
    if len(bins) != 1:
        raise ValueError("Supplier warehouse stock evidence is ambiguous")
    stock = bins[0]
    _lock(api, "Bin", stock.get("name"))
    _number(stock.get("actual_qty"), expected_supplier_warehouse_qty,
            "Supplier warehouse quantity")
    _number(stock.get("valuation_rate"), expected_supplier_warehouse_valuation_rate,
            "Supplier warehouse valuation rate")
    _number(stock.get("stock_value"), expected_supplier_warehouse_stock_value,
            "Supplier warehouse stock value")

    before = {
        "actual_qty": stock.get("actual_qty"),
        "valuation_rate": stock.get("valuation_rate"),
        "stock_value": stock.get("stock_value"),
    }
    invoice.custom_allow_blank_ewaybill_transport_details = 1
    # Preserve the reviewed Draft posting date/time. ERPNext otherwise replaces
    # an older Draft date with today's date during submission validation, which
    # also makes the reviewed due date stale.
    invoice.set_posting_time = 1
    invoice._submitted_from_ui = 1
    invoice.flags.controlled_retained_material_submission = True
    invoice.submit()
    if invoice.get("docstatus") != 1:
        raise ValueError("ERPNext did not submit the controlled Sales Invoice")

    sle = api.get_all(
        "Stock Ledger Entry",
        filters={"voucher_type": "Sales Invoice", "voucher_no": invoice.name,
                 "is_cancelled": 0},
        fields=["name", "item_code", "warehouse", "actual_qty",
                "qty_after_transaction", "valuation_rate", "stock_value_difference"],
        order_by="creation asc", limit_page_length=0,
    )
    gl = api.get_all(
        "GL Entry",
        filters={"voucher_type": "Sales Invoice", "voucher_no": invoice.name,
                 "is_cancelled": 0},
        fields=["name", "account", "party_type", "party", "debit", "credit",
                "cost_center"], order_by="creation asc", limit_page_length=0,
    )
    _validate_postings(api, invoice, item, sle, gl,
                       expected_supplier_warehouse_stock_value)
    after_rows = api.get_all("Bin", filters={"name": stock.get("name")},
                             fields=["actual_qty", "valuation_rate", "stock_value"],
                             limit_page_length=2)
    if len(after_rows) != 1:
        raise ValueError("Submitted supplier warehouse balance is ambiguous")
    after = after_rows[0]
    _number(after.get("actual_qty"), Decimal(str(before["actual_qty"]))
            - Decimal(str(item.get("stock_qty") or item.get("qty"))),
            "Submitted warehouse quantity")
    _number(after.get("stock_value"), 0, "Submitted warehouse stock value")

    event = api.new_doc("Processor Lot Sales Invoice Submission Event")
    event.update({
        "sales_invoice": invoice.name, "processor_lot": lot.name,
        "scope_key": expected_scope_key,
        "draft_creation_event": confirmation.get("draft_creation_event"),
        "reservation": confirmation.get("reservation"),
        "tally_confirmation": confirmation.get("tally_confirmation"),
        "statutory_evidence_confirmation": confirmation.name,
        "reason": reason, "submission_confirmed": 1,
        "submitted_by": api.session.user, "submitted_at": now_datetime(),
        "statutory_lead_system": "Tally",
        "erpnext_statutory_generation_suppressed": 1,
        "blank_transport_override_applied": 1,
        "material_disposition": disposition.get("name"),
        "commercial_classification": classification.get("name"),
        "disposition_revision": expected_disposition_revision,
        "classification_revision": expected_classification_revision,
        "treatment_revision": expected_treatment_revision,
        "supplier_warehouse": item.get("warehouse"),
        "warehouse_qty_before": before.get("actual_qty"),
        "warehouse_qty_after": after.get("actual_qty"),
        "warehouse_stock_value_before": before.get("stock_value"),
        "warehouse_stock_value_after": after.get("stock_value"),
        "stock_ledger_snapshot": json.dumps(sle, sort_keys=True, default=str),
        "gl_entry_snapshot": json.dumps(gl, sort_keys=True, default=str),
        "net_total": invoice.get("net_total"),
        "total_taxes_and_charges": invoice.get("total_taxes_and_charges"),
        "grand_total": invoice.get("grand_total"),
    })
    event.flags.controlled_sales_invoice_submission_event_insert = True
    event.insert(ignore_permissions=True)

    lot.generated_document_type = "Sales Invoice"
    lot.generated_document = invoice.name
    lot.settlement_status = "Sales Invoice Created"
    lot.flags.ignore_validate_update_after_submit = True
    lot.save(ignore_permissions=True)
    return {
        "contract_version": CONTRACT_VERSION,
        "submission_code": "RETAINED_MATERIAL_SALES_INVOICE_SUBMITTED",
        "sales_invoice": invoice.name, "docstatus": invoice.docstatus,
        "submission_event": event.name,
        "stock_ledger_entry_count": len(sle), "gl_entry_count": len(gl),
        "warehouse_qty_after": after.get("actual_qty"),
        "warehouse_stock_value_after": after.get("stock_value"),
        "statutory_generation_suppressed": True,
        "lot_closure_authorized": False,
    }


def _validate_postings(api, invoice, item, sle, gl, stock_value):
    if len(sle) != 1:
        raise ValueError("Expected exactly one controlled Stock Ledger Entry")
    entry = sle[0]
    _number(entry.get("actual_qty"), -Decimal(str(item.get("stock_qty") or item.get("qty"))),
            "Stock Ledger quantity")
    _number(entry.get("qty_after_transaction"), 0, "Stock Ledger balance")
    _number(entry.get("stock_value_difference"), -Decimal(str(stock_value)),
            "Stock Ledger value")
    expected = {}
    _add(expected, invoice.get("debit_to"), invoice.get("grand_total"), 0)
    for tax in invoice.get("taxes") or []:
        _add(expected, tax.get("account_head"), 0, tax.get("tax_amount"))
    _add(expected, item.get("income_account"), 0, invoice.get("net_total"))
    _add(expected, item.get("expense_account"), stock_value, 0)
    _add(expected, _warehouse_account(api, item.get("warehouse")), 0, stock_value)
    actual = {}
    for row in gl:
        account = row.get("account")
        debit, credit = actual.get(account, (Decimal("0"), Decimal("0")))
        actual[account] = (debit + Decimal(str(row.get("debit") or 0)),
                           credit + Decimal(str(row.get("credit") or 0)))
    if actual != expected:
        raise ValueError("Submitted Sales Invoice GL entries do not match controlled projection")


def _warehouse_account(api, warehouse_name):
    visited = set()
    while warehouse_name and warehouse_name not in visited:
        visited.add(warehouse_name)
        warehouse = api.get_doc("Warehouse", warehouse_name)
        if warehouse.get("account"):
            return warehouse.get("account")
        warehouse_name = warehouse.get("parent_warehouse")
    raise ValueError("Supplier warehouse stock account is not configured")


def _add(rows, account, debit, credit):
    if not account:
        raise ValueError("Projected GL account is missing")
    current_debit, current_credit = rows.get(
        account, (Decimal("0"), Decimal("0"))
    )
    rows[account] = (
        current_debit + Decimal(str(debit or 0)),
        current_credit + Decimal(str(credit or 0)),
    )


def _number(actual, expected, label):
    if Decimal(str(actual)) != Decimal(str(expected)):
        raise ValueError(label + " changed; rollback required")
