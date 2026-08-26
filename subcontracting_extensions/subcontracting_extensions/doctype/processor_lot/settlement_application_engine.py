# Copyright (c) 2026, Bhogals Private Limited
# For license information, please see license.txt

"""Controlled document creation for Processor Material Credit application.

Complete version with explicit Material Issue purpose, header source warehouse,
and controlled posting date/time.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import time
from typing import Any

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowtime

from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.fact_engine import (
    get_sco_facts,
)
from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.recovery_calculator import (
    calculate_recovery,
)
from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.settlement_netting import (
    build_settlement_netting,
)
from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.settlement_recommendation import (
    recommend_settlement,
)


def processor_lot_accepts_credit_application(
    docstatus: int,
    settlement_status: str | None,
) -> bool:
    """Return whether settlement is open for a new credit application."""
    return bool(
        docstatus == 0
        or (
            docstatus == 1
            and settlement_status == "Reopened"
        )
    )


def credit_application_sco_temporary_status(
    lot_docstatus: int,
    settlement_status: str | None,
    sco_status: str | None,
) -> str | None:
    """Return the narrow SCO status window required for SE submission."""
    if (
        lot_docstatus == 1
        and settlement_status == "Reopened"
        and sco_status == "Closed"
    ):
        return "Completed"
    return None


def classify_credit_application_bundle(
    pma_docstatus: int | None,
    stock_entry_docstatus: int | None,
    journal_entry_docstatus: int | None,
    has_stock_entry: bool = True,
    has_journal_entry: bool = True,
) -> dict[str, Any]:
    """Return the controlled completion state and next submission step."""
    if not has_stock_entry or not has_journal_entry:
        return {
            "state": "Broken",
            "next_doctype": None,
        }

    if 2 in (
        pma_docstatus,
        stock_entry_docstatus,
        journal_entry_docstatus,
    ):
        return {
            "state": "Broken",
            "next_doctype": None,
        }

    if stock_entry_docstatus != 1:
        return {
            "state": "Pending",
            "next_doctype": "Stock Entry",
        }

    if journal_entry_docstatus != 1:
        return {
            "state": "Pending",
            "next_doctype": "Journal Entry",
        }

    if pma_docstatus != 1:
        return {
            "state": "Pending",
            "next_doctype": "Processor Material Account Entry",
        }

    return {
        "state": "Complete",
        "next_doctype": None,
    }


@frappe.whitelist()
def get_credit_application_completion_status(
    processor_lot: str,
) -> dict[str, Any]:
    """Return every active credit-application bundle and its live status."""
    if not processor_lot:
        frappe.throw(_("Processor Lot is required."))

    lot = frappe.get_doc("Processor Lot", processor_lot)
    lot.check_permission("read")

    rows = frappe.get_all(
        "Processor Material Account Entry",
        filters={
            "entry_type": "Credit Applied",
            "source_event": "Processor Lot Shortage",
            "processor_lot": lot.name,
            "account_direction": "Debit",
            "docstatus": ["!=", 2],
            "is_reversed": 0,
        },
        fields=[
            "name",
            "docstatus",
            "posting_date",
            "against_entry",
            "account_qty",
            "commercial_qty",
            "account_uom",
            "application_stock_entry",
            "application_journal_entry",
        ],
        order_by="posting_date asc, creation asc, name asc",
    )

    bundles: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        stock_entry = row.application_stock_entry
        journal_entry = row.application_journal_entry
        stock_entry_docstatus = (
            frappe.db.get_value(
                "Stock Entry",
                stock_entry,
                "docstatus",
            )
            if stock_entry
            else None
        )
        journal_entry_docstatus = (
            frappe.db.get_value(
                "Journal Entry",
                journal_entry,
                "docstatus",
            )
            if journal_entry
            else None
        )
        classification = classify_credit_application_bundle(
            pma_docstatus=row.docstatus,
            stock_entry_docstatus=stock_entry_docstatus,
            journal_entry_docstatus=journal_entry_docstatus,
            has_stock_entry=bool(stock_entry),
            has_journal_entry=bool(journal_entry),
        )

        bundles.append(
            {
                "sequence": index,
                "processor_material_account_entry": row.name,
                "pma_docstatus": row.docstatus,
                "stock_entry": stock_entry,
                "stock_entry_docstatus": stock_entry_docstatus,
                "journal_entry": journal_entry,
                "journal_entry_docstatus": journal_entry_docstatus,
                "against_entry": row.against_entry,
                "account_qty": flt(row.account_qty, 3),
                "commercial_qty": flt(row.commercial_qty, 3),
                "account_uom": row.account_uom,
                "state": classification["state"],
                "next_doctype": classification["next_doctype"],
            }
        )

    pending_count = sum(
        bundle["state"] == "Pending"
        for bundle in bundles
    )
    broken_count = sum(
        bundle["state"] == "Broken"
        for bundle in bundles
    )

    return {
        "processor_lot": lot.name,
        "has_applications": bool(bundles),
        "is_complete": bool(bundles)
        and not pending_count
        and not broken_count,
        "pending_count": pending_count,
        "broken_count": broken_count,
        "bundles": bundles,
    }


def validate_credit_application_documents_complete(
    processor_lot: str,
) -> None:
    """Block residual settlement while an application bundle is unfinished."""
    status = get_credit_application_completion_status(processor_lot)
    if not status["has_applications"] or status["is_complete"]:
        return

    pending = []
    for bundle in status["bundles"]:
        if bundle["state"] == "Complete":
            continue
        if bundle["state"] == "Broken":
            pending.append(
                _("Bundle {0} is incomplete or contains a cancelled document.").format(
                    bundle["sequence"]
                )
            )
            continue
        pending.append(
            _("Bundle {0}: submit {1} next.").format(
                bundle["sequence"],
                frappe.bold(bundle["next_doctype"]),
            )
        )

    frappe.throw(
        "<br>".join(pending),
        title=_("Credit Application Documents Pending"),
    )


@frappe.whitelist()
def create_credit_application_documents(
    processor_lot: str,
    business_classification: str,
) -> dict[str, Any]:
    """Create linked Draft PMA, Material Issue and Journal Entry documents."""
    if not processor_lot:
        frappe.throw(_("Processor Lot is required."))
    if not business_classification:
        frappe.throw(_("Business Classification is required."))

    lot = frappe.get_doc("Processor Lot", processor_lot)
    if not processor_lot_accepts_credit_application(
        lot.docstatus,
        lot.settlement_status,
    ):
        frappe.throw(
            _(
                "Processor Lot {0} must be Draft or have a Reopened "
                "settlement."
            ).format(frappe.bold(lot.name))
        )

    existing_draft = frappe.get_all(
        "Processor Material Account Entry",
        filters={
            "entry_type": "Credit Applied",
            "processor_lot": lot.name,
            "docstatus": 0,
        },
        pluck="name",
    )
    if existing_draft:
        frappe.throw(
            _("Processor Lot {0} already has Draft credit application {1}.").format(
                frappe.bold(lot.name),
                frappe.utils.get_link_to_form(
                    "Processor Material Account Entry",
                    existing_draft[0],
                ),
            ),
            title=_("Draft Credit Application Already Exists"),
        )

    facts = get_sco_facts(lot.subcontracting_order)
    recommendation = recommend_settlement(facts, business_classification)
    recovery = calculate_recovery(facts, recommendation)
    netting = build_settlement_netting(
        subcontracting_order=lot.subcontracting_order,
        facts=facts,
        gross_recovery=recovery,
    )
    material_credit = netting.get("material_credit") or {}
    applications = material_credit.get("applications") or []
    if not applications:
        frappe.throw(
            _("No compatible open Processor Material Credit is available for this lot."),
            title=_("No Credit Application Proposed"),
        )

    created = []
    commercial_match_offset = flt(
        material_credit.get("already_commercially_matched_qty"),
        3,
    )
    for proposed in applications:
        created.append(
            _create_one_application(
                lot,
                facts,
                proposed,
                commercial_match_offset=commercial_match_offset,
            )
        )
        commercial_match_offset = flt(
            commercial_match_offset
            + flt(proposed.get("commercially_matched_qty"), 3),
            3,
        )

    return {
        "action": "Created",
        "processor_lot": lot.name,
        "settlement_netting": netting,
        "applications": created,
    }


def _create_one_application(
    lot,
    facts,
    proposed: dict[str, Any],
    commercial_match_offset: float = 0.0,
) -> dict[str, Any]:
    source = frappe.get_doc(
        "Processor Material Account Entry",
        proposed.get("against_entry"),
    )
    posting_date = max(
        getdate(lot.settlement_date),
        getdate(source.posting_date),
    )

    application = frappe.new_doc("Processor Material Account Entry")
    application.posting_date = posting_date
    application.entry_type = "Credit Applied"
    application.source_event = "Processor Lot Shortage"
    application.processor_lot = lot.name
    application.against_entry = source.name
    application.processed_qty = flt(proposed.get("processed_qty"), 3)
    application.account_qty = flt(proposed.get("account_qty"), 3)
    application.commercial_qty = flt(
        proposed.get("commercially_matched_qty"),
        3,
    )
    application.created_by_system = 1
    application.remarks = _(
        "FIFO Processor Material Credit application from {0} to Processor Lot {1}."
    ).format(source.name, lot.name)
    application.insert()

    source_valuation = _get_source_credit_valuation(source)
    stock_entry = _create_application_stock_entry(
        lot=lot,
        application=application,
        source=source,
    )
    commercial_matches = _get_commercial_matches(
        facts=facts,
        required_qty=flt(application.commercial_qty, 3),
        skip_qty=flt(commercial_match_offset, 3),
    )
    journal_entry = _create_application_journal_entry(
        lot=lot,
        application=application,
        source=source,
        source_valuation=source_valuation,
        commercial_matches=commercial_matches,
    )

    frappe.db.set_value(
        application.doctype,
        application.name,
        {
            "application_stock_entry": stock_entry.name,
            "application_journal_entry": journal_entry.name,
        },
        update_modified=False,
    )

    return {
        "processor_material_account_entry": application.name,
        "stock_entry": stock_entry.name,
        "journal_entry": journal_entry.name,
        "against_entry": source.name,
        "account_qty": flt(application.account_qty, 3),
        "commercial_qty": flt(application.commercial_qty, 3),
        "account_uom": application.account_uom,
    }


def _create_application_stock_entry(lot, application, source):
    stock_adjustment = _get_company_account(
        lot.company,
        "stock_adjustment_account",
        "Stock Adjustment Account",
    )
    stock_entry = frappe.new_doc("Stock Entry")
    stock_entry.company = lot.company
    stock_entry.stock_entry_type = "Material Issue"
    stock_entry.purpose = "Material Issue"
    stock_entry.from_warehouse = source.supplier_warehouse
    stock_entry.set_posting_time = 1
    stock_entry.posting_date = application.posting_date
    stock_entry.posting_time = nowtime() or time()
    stock_entry.subcontracting_order = lot.subcontracting_order
    if stock_entry.meta.has_field("branch"):
        stock_entry.branch = lot.branch
    stock_entry.remarks = _(
        "Processor Material Credit application {0}; issue {1} {2} of {3} "
        "from supplier warehouse for Processor Lot {4}."
    ).format(
        application.name,
        application.account_qty,
        application.account_uom,
        source.principal_component,
        lot.name,
    )
    values = {
        "item_code": source.principal_component,
        "s_warehouse": source.supplier_warehouse,
        "qty": application.account_qty,
        "expense_account": stock_adjustment,
        "cost_center": lot.cost_center,
    }
    if frappe.get_meta("Stock Entry Detail").has_field("branch"):
        values["branch"] = lot.branch
    stock_entry.append("items", values)
    stock_entry.insert()
    return stock_entry


def _create_application_journal_entry(
    lot,
    application,
    source,
    source_valuation: dict[str, Any],
    commercial_matches: list[dict[str, Any]],
):
    stock_adjustment = _get_company_account(
        lot.company,
        "stock_adjustment_account",
        "Stock Adjustment Account",
    )
    raw_liability_value = flt(
        flt(application.account_qty) * source_valuation["component_rate"],
        2,
    )
    source_processing_value = flt(
        flt(application.commercial_qty) * source_valuation["processing_rate"],
        2,
    )
    target_processing_value = flt(
        sum(flt(row["amount"]) for row in commercial_matches),
        2,
    )

    journal = frappe.new_doc("Journal Entry")
    journal.company = lot.company
    journal.voucher_type = "Journal Entry"
    journal.posting_date = application.posting_date
    if journal.meta.has_field("branch"):
        journal.branch = lot.branch
    journal.user_remark = _(
        "Processor Material Credit application {0} against {1} for Processor "
        "Lot {2}. Raw liability {3}; source accrued processing {4}; target "
        "invoice expense {5}."
    ).format(
        application.name,
        source.name,
        lot.name,
        raw_liability_value,
        source_processing_value,
        target_processing_value,
    )

    _append_je_row(
        journal,
        account=source_valuation["processor_liability"],
        debit=raw_liability_value,
        cost_center=lot.cost_center,
        branch=lot.branch,
    )
    _append_je_row(
        journal,
        account=stock_adjustment,
        credit=raw_liability_value,
        cost_center=lot.cost_center,
        branch=lot.branch,
    )

    if source_processing_value > 0:
        _append_je_row(
            journal,
            account=source_valuation["accrued_processing"],
            debit=source_processing_value,
            cost_center=lot.cost_center,
            branch=lot.branch,
        )

    grouped_matches: dict[tuple[str, str, str], float] = defaultdict(float)
    for row in commercial_matches:
        key = (
            row["expense_account"],
            row.get("cost_center") or lot.cost_center,
            row.get("branch") or lot.branch,
        )
        grouped_matches[key] += flt(row["amount"])
    for (account, cost_center, branch), amount in grouped_matches.items():
        _append_je_row(
            journal,
            account=account,
            credit=flt(amount, 2),
            cost_center=cost_center,
            branch=branch,
        )

    processing_difference = flt(
        target_processing_value - source_processing_value,
        2,
    )
    if processing_difference > 0:
        _append_je_row(
            journal,
            account=stock_adjustment,
            debit=processing_difference,
            cost_center=lot.cost_center,
            branch=lot.branch,
        )
    elif processing_difference < 0:
        _append_je_row(
            journal,
            account=stock_adjustment,
            credit=abs(processing_difference),
            cost_center=lot.cost_center,
            branch=lot.branch,
        )

    journal.insert()
    return journal


def _append_je_row(
    journal,
    account: str,
    debit: float = 0.0,
    credit: float = 0.0,
    cost_center: str | None = None,
    branch: str | None = None,
) -> None:
    values = {
        "account": account,
        "debit_in_account_currency": flt(debit, 2),
        "credit_in_account_currency": flt(credit, 2),
        "cost_center": cost_center,
    }
    if frappe.get_meta("Journal Entry Account").has_field("branch"):
        values["branch"] = branch
    journal.append("accounts", values)


def _get_source_credit_valuation(source) -> dict[str, Any]:
    if not source.material_credit_stock_entry:
        frappe.throw(_("Source credit {0} has no Material Credit Stock Entry.").format(source.name))
    stock_entry = frappe.get_doc("Stock Entry", source.material_credit_stock_entry)
    if stock_entry.docstatus != 1:
        frappe.throw(_("Material Credit Stock Entry {0} must be submitted.").format(stock_entry.name))

    items = [
        row
        for row in stock_entry.items
        if row.item_code == source.processed_item and flt(row.qty) > 0
    ]
    if len(items) != 1:
        frappe.throw(_("Source credit Stock Entry must contain one processed-item row."))
    item = items[0]
    processing_rows = [row for row in stock_entry.additional_costs if flt(row.amount) > 0]
    if len(processing_rows) > 1:
        frappe.throw(_("Source credit Stock Entry has ambiguous processing-cost rows."))

    processing_rate = flt(flt(item.additional_cost) / flt(item.qty), 9)
    accrued_processing = (
        processing_rows[0].expense_account if processing_rows else None
    )
    if processing_rate > 0 and not accrued_processing:
        frappe.throw(
            _("Source credit Stock Entry has processing value but no accrued-processing account.")
        )
    return {
        "component_rate": flt(item.basic_rate, 9),
        "processing_rate": processing_rate,
        "processor_liability": item.expense_account,
        "accrued_processing": accrued_processing,
    }


def _get_commercial_matches(
    facts: dict[str, Any],
    required_qty: float,
    skip_qty: float = 0.0,
) -> list[dict[str, Any]]:
    receipt_rows = (facts.get("processor_lot_receipts") or {}).get("receipts") or []
    allocations, remaining = _allocate_commercial_match_quantities(
        receipt_rows=receipt_rows,
        required_qty=required_qty,
        skip_qty=skip_qty,
    )
    if flt(required_qty, 3) <= 0:
        return []
    matches = []
    purchase_order = (facts.get("identity") or {}).get("purchase_order")

    for row, qty in allocations:
        purchase_invoice = row.get("purchase_invoice")
        if not purchase_invoice:
            frappe.throw(_("PLR {0} has commercial variance but no Purchase Invoice.").format(row.get("processor_lot_receipt")))
        invoice = frappe.get_doc("Purchase Invoice", purchase_invoice)
        if invoice.docstatus != 1:
            frappe.throw(_("Purchase Invoice {0} must be submitted.").format(invoice.name))
        invoice_items = [
            item
            for item in invoice.items
            if item.purchase_order == purchase_order and flt(item.qty) > 0
        ]
        if len(invoice_items) != 1:
            frappe.throw(
                _("Purchase Invoice {0} must contain exactly one applicable service row.").format(invoice.name)
            )
        item = invoice_items[0]
        matches.append(
            {
                "processor_lot_receipt": row.get("processor_lot_receipt"),
                "purchase_invoice": invoice.name,
                "purchase_invoice_item": item.name,
                "qty": qty,
                "base_net_rate": flt(item.base_net_rate or item.net_rate, 9),
                "amount": flt(qty * flt(item.base_net_rate or item.net_rate), 2),
                "expense_account": item.expense_account,
                "cost_center": item.cost_center,
                "branch": getattr(item, "branch", None) or getattr(invoice, "branch", None),
            }
        )

    if remaining > 0:
        frappe.throw(
            _("Could not attribute {0} of commercial matching quantity to submitted Purchase Invoices.").format(remaining)
        )
    return matches


def _allocate_commercial_match_quantities(
    receipt_rows: list[dict[str, Any]],
    required_qty: float,
    skip_qty: float = 0.0,
) -> tuple[list[tuple[dict[str, Any], float]], float]:
    """Allocate after earlier applications' commercial quantity in FIFO order."""
    remaining = max(flt(required_qty, 3), 0.0)
    to_skip = max(flt(skip_qty, 3), 0.0)
    allocations: list[tuple[dict[str, Any], float]] = []

    for row in receipt_rows:
        variance_qty = max(
            flt(row.get("supplier_invoice_vs_company_qty"), 3),
            0.0,
        )
        if variance_qty <= 0:
            continue

        skipped = min(to_skip, variance_qty)
        to_skip = flt(to_skip - skipped, 3)
        available_qty = flt(variance_qty - skipped, 3)
        if available_qty <= 0 or remaining <= 0:
            continue

        allocated_qty = min(remaining, available_qty)
        allocations.append((row, allocated_qty))
        remaining = flt(remaining - allocated_qty, 3)

    return allocations, remaining


def _get_company_account(company: str, fieldname: str, label: str) -> str:
    account = frappe.get_cached_value("Company", company, fieldname)
    if not account:
        frappe.throw(_("{0} is not configured for Company {1}.").format(label, company))
    return account


def validate_credit_application_documents(application) -> None:
    """Require exact submitted stock and accounting documents."""
    if not application.application_stock_entry or not application.application_journal_entry:
        frappe.throw(_("Credit Applied requires linked Stock Entry and Journal Entry."))
    stock_entry = frappe.get_doc("Stock Entry", application.application_stock_entry)
    journal_entry = frappe.get_doc("Journal Entry", application.application_journal_entry)
    if stock_entry.docstatus != 1:
        frappe.throw(_("Credit Application Stock Entry {0} must be submitted.").format(stock_entry.name))
    if journal_entry.docstatus != 1:
        frappe.throw(_("Credit Application Journal Entry {0} must be submitted.").format(journal_entry.name))
    validate_credit_application_stock_entry(stock_entry)
    validate_credit_application_journal_entry(journal_entry)


def validate_credit_application_stock_entry(doc, method=None) -> None:
    application_name = frappe.db.get_value(
        "Processor Material Account Entry",
        {"application_stock_entry": doc.name, "docstatus": ["!=", 2]},
        "name",
    )
    if not application_name:
        return
    application = frappe.get_doc("Processor Material Account Entry", application_name)
    lot = frappe.get_doc("Processor Lot", application.processor_lot)
    stock_adjustment = _get_company_account(
        application.company,
        "stock_adjustment_account",
        "Stock Adjustment Account",
    )
    valid = (
        doc.company == application.company
        and doc.purpose == "Material Issue"
        and doc.stock_entry_type == "Material Issue"
        and doc.subcontracting_order == application.subcontracting_order
        and getdate(doc.posting_date) == getdate(application.posting_date)
        and len(doc.items) == 1
    )
    if valid:
        item = doc.items[0]
        valid = (
            item.item_code == application.principal_component
            and item.s_warehouse == application.supplier_warehouse
            and flt(item.qty, 3) == flt(application.account_qty, 3)
            and item.expense_account == stock_adjustment
            and item.cost_center == lot.cost_center
        )
    if not valid:
        frappe.throw(_("Credit Application Stock Entry {0} no longer matches PMA {1}.").format(doc.name, application.name))


def prepare_credit_application_stock_entry_submit(
    doc,
    method=None,
) -> None:
    """Open a rollback-safe SCO status window for a reopened lot's SE."""
    application_name = frappe.db.get_value(
        "Processor Material Account Entry",
        {
            "application_stock_entry": doc.name,
            "docstatus": ["!=", 2],
        },
        "name",
    )
    if not application_name or not doc.subcontracting_order:
        return

    application = frappe.get_doc(
        "Processor Material Account Entry",
        application_name,
    )
    lot_state = frappe.db.get_value(
        "Processor Lot",
        application.processor_lot,
        ["docstatus", "settlement_status"],
        as_dict=True,
    )
    if not lot_state:
        return

    original_status = frappe.db.get_value(
        "Subcontracting Order",
        doc.subcontracting_order,
        "status",
    )
    temporary_status = credit_application_sco_temporary_status(
        lot_state.docstatus,
        lot_state.settlement_status,
        original_status,
    )
    if not temporary_status:
        return

    doc.flags.credit_application_sco = doc.subcontracting_order
    doc.flags.credit_application_sco_original_status = original_status
    frappe.db.set_value(
        "Subcontracting Order",
        doc.subcontracting_order,
        "status",
        temporary_status,
        update_modified=False,
    )


def restore_credit_application_stock_entry_sco_status(
    doc,
    method=None,
) -> None:
    """Restore the operational SCO status after standard SE submission."""
    subcontracting_order = doc.flags.get(
        "credit_application_sco"
    )
    original_status = doc.flags.get(
        "credit_application_sco_original_status"
    )
    if not subcontracting_order or not original_status:
        return

    frappe.db.set_value(
        "Subcontracting Order",
        subcontracting_order,
        "status",
        original_status,
        update_modified=False,
    )


def is_credit_application_journal_submission_allowed(
    stock_entry_docstatus,
) -> bool:
    return stock_entry_docstatus == 1


def validate_credit_application_journal_entry(doc, method=None) -> None:
    application_name = frappe.db.get_value(
        "Processor Material Account Entry",
        {"application_journal_entry": doc.name, "docstatus": ["!=", 2]},
        "name",
    )
    if not application_name:
        return
    application = frappe.get_doc("Processor Material Account Entry", application_name)

    if doc.docstatus == 1:
        stock_entry_status = frappe.db.get_value(
            "Stock Entry",
            application.application_stock_entry,
            "docstatus",
        )
        if not is_credit_application_journal_submission_allowed(
            stock_entry_status
        ):
            frappe.throw(
                _(
                    "Submit Credit Application Stock Entry {0} before "
                    "submitting Journal Entry {1}."
                ).format(
                    application.application_stock_entry or _("Not linked"),
                    doc.name,
                ),
                title=_("Stock Entry Must Be Submitted First"),
            )

    lot = frappe.get_doc("Processor Lot", application.processor_lot)
    if (
        doc.company != application.company
        or getdate(doc.posting_date) != getdate(application.posting_date)
        or (doc.meta.has_field("branch") and doc.branch != lot.branch)
    ):
        frappe.throw(_("Credit Application Journal Entry {0} header no longer matches PMA {1}.").format(doc.name, application.name))
    if flt(doc.total_debit, 2) != flt(doc.total_credit, 2) or flt(doc.total_debit, 2) <= 0:
        frappe.throw(_("Credit Application Journal Entry {0} must remain balanced.").format(doc.name))

    expected = _get_expected_journal_totals(application, lot)
    actual: dict[tuple[str, str, str], dict[str, float]] = defaultdict(
        lambda: {"debit": 0.0, "credit": 0.0}
    )
    for row in doc.accounts:
        key = (
            row.account,
            row.cost_center or "",
            getattr(row, "branch", None) or "",
        )
        actual[key]["debit"] += flt(row.debit_in_account_currency, 2)
        actual[key]["credit"] += flt(row.credit_in_account_currency, 2)
    actual = {
        key: {
            "debit": flt(value["debit"], 2),
            "credit": flt(value["credit"], 2),
        }
        for key, value in actual.items()
        if flt(value["debit"], 2) or flt(value["credit"], 2)
    }
    if actual != expected:
        frappe.throw(
            _("Credit Application Journal Entry {0} accounts no longer match PMA {1}.").format(
                doc.name,
                application.name,
            )
        )


def _get_expected_journal_totals(application, lot):
    source = frappe.get_doc(
        "Processor Material Account Entry",
        application.against_entry,
    )
    source_valuation = _get_source_credit_valuation(source)
    facts = get_sco_facts(application.subcontracting_order)
    commercial_matches = _get_commercial_matches(
        facts,
        flt(application.commercial_qty, 3),
        skip_qty=_get_prior_commercial_match_qty(application),
    )
    stock_adjustment = _get_company_account(
        application.company,
        "stock_adjustment_account",
        "Stock Adjustment Account",
    )
    raw_value = flt(
        flt(application.account_qty) * source_valuation["component_rate"],
        2,
    )
    source_processing = flt(
        flt(application.commercial_qty) * source_valuation["processing_rate"],
        2,
    )
    target_processing = flt(
        sum(flt(row["amount"]) for row in commercial_matches),
        2,
    )
    expected: dict[tuple[str, str, str], dict[str, float]] = defaultdict(
        lambda: {"debit": 0.0, "credit": 0.0}
    )
    account_has_branch = frappe.get_meta("Journal Entry Account").has_field(
        "branch"
    )

    def add(account, debit=0.0, credit=0.0, cost_center=None, branch=None):
        key = (
            account,
            cost_center or "",
            (branch or "") if account_has_branch else "",
        )
        expected[key]["debit"] += flt(debit, 2)
        expected[key]["credit"] += flt(credit, 2)

    add(
        source_valuation["processor_liability"],
        debit=raw_value,
        cost_center=lot.cost_center,
        branch=lot.branch,
    )
    add(
        stock_adjustment,
        credit=raw_value,
        cost_center=lot.cost_center,
        branch=lot.branch,
    )
    if source_processing > 0:
        add(
            source_valuation["accrued_processing"],
            debit=source_processing,
            cost_center=lot.cost_center,
            branch=lot.branch,
        )
    for row in commercial_matches:
        add(
            row["expense_account"],
            credit=row["amount"],
            cost_center=row.get("cost_center") or lot.cost_center,
            branch=row.get("branch") or lot.branch,
        )
    difference = flt(target_processing - source_processing, 2)
    if difference > 0:
        add(
            stock_adjustment,
            debit=difference,
            cost_center=lot.cost_center,
            branch=lot.branch,
        )
    elif difference < 0:
        add(
            stock_adjustment,
            credit=abs(difference),
            cost_center=lot.cost_center,
            branch=lot.branch,
        )
    return {
        key: {
            "debit": flt(value["debit"], 2),
            "credit": flt(value["credit"], 2),
        }
        for key, value in expected.items()
        if flt(value["debit"], 2) or flt(value["credit"], 2)
    }


def _get_prior_commercial_match_qty(application) -> float:
    """Return commercial quantity reserved by earlier active target PMAs."""
    rows = frappe.get_all(
        "Processor Material Account Entry",
        filters={
            "entry_type": "Credit Applied",
            "processor_lot": application.processor_lot,
            "docstatus": ["!=", 2],
        },
        fields=["name", "commercial_qty"],
        order_by="creation asc, name asc",
    )
    prior_qty = 0.0
    for row in rows:
        if row.name == application.name:
            break
        prior_qty += flt(row.commercial_qty)
    return flt(prior_qty, 3)


def prevent_credit_application_document_cancel(doc, method=None) -> None:
    filters = {
        "docstatus": 1,
        "entry_type": "Credit Applied",
    }
    if doc.doctype == "Stock Entry":
        filters["application_stock_entry"] = doc.name
    elif doc.doctype == "Journal Entry":
        filters["application_journal_entry"] = doc.name
    else:
        return
    application = frappe.db.get_value(
        "Processor Material Account Entry",
        filters,
        "name",
    )
    if application:
        frappe.throw(
            _("Cancel Processor Material Account Entry {0} before cancelling {1} {2}.").format(
                application,
                doc.doctype,
                doc.name,
            )
        )


def unlink_credit_application_document(doc, method=None) -> None:
    fieldname = {
        "Stock Entry": "application_stock_entry",
        "Journal Entry": "application_journal_entry",
    }.get(doc.doctype)
    if not fieldname:
        return
    for name in frappe.get_all(
        "Processor Material Account Entry",
        filters={fieldname: doc.name, "docstatus": ["!=", 1]},
        pluck="name",
    ):
        frappe.db.set_value(
            "Processor Material Account Entry",
            name,
            fieldname,
            None,
            update_modified=False,
        )
