# Copyright (c) 2026, Bhogals Private Limited
# For license information, please see license.txt

"""
Read-only Fact Engine for Processor Lot.

The Fact Engine gathers authoritative facts from ERPNext documents. It does
not classify variances, recommend settlements, change document statuses or
create Stock Ledger / General Ledger transactions.

Version 1
---------
- Reads one submitted Subcontracting Order.
- Returns identity and component accountability facts.
- Finds submitted Purchase Invoices linked to the originating Purchase Order.
- Reports warnings and blocking errors separately.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import frappe
from frappe import _
from frappe.utils import flt


def get_sco_facts(subcontracting_order: str) -> dict[str, Any]:
    """
    Return the current factual position of one Subcontracting Order.

    This is deliberately read-only. No database values are changed.

    Parameters
    ----------
    subcontracting_order:
        Name of the Subcontracting Order to inspect.
    """
    if not subcontracting_order:
        frappe.throw(_("Subcontracting Order is required."))

    if not frappe.db.exists(
        "Subcontracting Order",
        subcontracting_order,
    ):
        frappe.throw(
            _("Subcontracting Order {0} does not exist.").format(
                frappe.bold(subcontracting_order)
            )
        )

    sco = frappe.get_doc(
        "Subcontracting Order",
        subcontracting_order,
    )

    warnings: list[dict[str, Any]] = []
    blocking_errors: list[dict[str, Any]] = []

    _validate_sco_status(
        sco=sco,
        warnings=warnings,
        blocking_errors=blocking_errors,
    )

    purchase_order = _get_purchase_order(
        sco=sco,
        warnings=warnings,
        blocking_errors=blocking_errors,
    )

    settlement_policy = _get_settlement_policy(
        purchase_order=purchase_order,
    )

    component_facts = _get_component_facts(
        sco=sco,
        warnings=warnings,
        blocking_errors=blocking_errors,
    )

    material_transfer_facts = _get_material_transfer_facts(
        sco=sco,
        warnings=warnings,
    )

    receipt_facts = _get_receipt_facts(
        sco=sco,
        component_facts=component_facts,
        warnings=warnings,
    )

    processor_lot_receipt_facts = (
        _get_processor_lot_receipt_facts(
            sco=sco,
            warnings=warnings,
        )
    )

    advance_credit_facts = _get_submitted_advance_credit_facts(
        sco=sco,
        warnings=warnings,
    )

    purchase_receipt_facts = _get_purchase_receipt_facts(
        purchase_order=purchase_order,
        warnings=warnings,
    )

    commercial_facts = _get_commercial_facts(
        purchase_order=purchase_order,
        warnings=warnings,
    )

    totals = _calculate_totals(component_facts)

    fact_summary = _build_fact_summary(
        component_totals=totals,
        material_transfer_facts=material_transfer_facts,
        receipt_facts=receipt_facts,
        purchase_receipt_facts=purchase_receipt_facts,
        commercial_facts=commercial_facts,
        advance_credit_facts=advance_credit_facts,
    )

    return {
        "identity": {
            "subcontracting_order": sco.name,
            "purchase_order": sco.purchase_order,
            "company": sco.company,
            "supplier": sco.supplier,
            "supplier_warehouse": sco.supplier_warehouse,
            "sco_status": sco.status,
            "sco_docstatus": sco.docstatus,
        },
        "settlement_policy": settlement_policy,
        "summary": fact_summary,
        "components": component_facts,
        "component_totals": totals,
        "receipts": receipt_facts,
        "purchase_receipts": purchase_receipt_facts,
        "processor_lot_receipts": processor_lot_receipt_facts,
        "processor_material_credits": advance_credit_facts,
        "commercial": commercial_facts,
        "integrity": {
            "is_valid": not blocking_errors,
            "warnings": warnings,
            "blocking_errors": blocking_errors,
        },
        "material_transfers": material_transfer_facts,
    }


def _validate_sco_status(
    sco,
    warnings: list[dict[str, Any]],
    blocking_errors: list[dict[str, Any]],
) -> None:
    """Validate whether the SCO is suitable for factual analysis."""
    if sco.docstatus == 2:
        blocking_errors.append(
            {
                "code": "SCO_CANCELLED",
                "message": _(
                    "Subcontracting Order {0} is cancelled."
                ).format(sco.name),
                "doctype": "Subcontracting Order",
                "document": sco.name,
            }
        )
        return

    if sco.docstatus != 1:
        blocking_errors.append(
            {
                "code": "SCO_NOT_SUBMITTED",
                "message": _(
                    "Subcontracting Order {0} is not submitted."
                ).format(sco.name),
                "doctype": "Subcontracting Order",
                "document": sco.name,
            }
        )

    if not sco.supplier_warehouse:
        blocking_errors.append(
            {
                "code": "SUPPLIER_WAREHOUSE_MISSING",
                "message": _(
                    "Supplier Warehouse is not set on Subcontracting Order {0}."
                ).format(sco.name),
                "doctype": "Subcontracting Order",
                "document": sco.name,
            }
        )

    if sco.status == "Closed":
        warnings.append(
            {
                "code": "SCO_CLOSED",
                "message": _(
                    "Subcontracting Order {0} is closed."
                ).format(sco.name),
                "doctype": "Subcontracting Order",
                "document": sco.name,
            }
        )


def _get_purchase_order(
    sco,
    warnings: list[dict[str, Any]],
    blocking_errors: list[dict[str, Any]],
):
    """Return the SCO's Purchase Order when available and valid."""
    if not sco.purchase_order:
        blocking_errors.append(
            {
                "code": "PURCHASE_ORDER_MISSING",
                "message": _(
                    "Subcontracting Order {0} has no Purchase Order."
                ).format(sco.name),
                "doctype": "Subcontracting Order",
                "document": sco.name,
            }
        )
        return None

    if not frappe.db.exists("Purchase Order", sco.purchase_order):
        blocking_errors.append(
            {
                "code": "PURCHASE_ORDER_NOT_FOUND",
                "message": _(
                    "Purchase Order {0} linked to Subcontracting Order {1} "
                    "does not exist."
                ).format(sco.purchase_order, sco.name),
                "doctype": "Purchase Order",
                "document": sco.purchase_order,
            }
        )
        return None

    purchase_order = frappe.get_doc(
        "Purchase Order",
        sco.purchase_order,
    )

    if purchase_order.docstatus == 2:
        blocking_errors.append(
            {
                "code": "PURCHASE_ORDER_CANCELLED",
                "message": _(
                    "Purchase Order {0} is cancelled."
                ).format(purchase_order.name),
                "doctype": "Purchase Order",
                "document": purchase_order.name,
            }
        )
    elif purchase_order.docstatus != 1:
        warnings.append(
            {
                "code": "PURCHASE_ORDER_NOT_SUBMITTED",
                "message": _(
                    "Purchase Order {0} is not submitted."
                ).format(purchase_order.name),
                "doctype": "Purchase Order",
                "document": purchase_order.name,
            }
        )

    if purchase_order.supplier != sco.supplier:
        blocking_errors.append(
            {
                "code": "SUPPLIER_MISMATCH",
                "message": _(
                    "Purchase Order supplier {0} differs from "
                    "Subcontracting Order supplier {1}."
                ).format(
                    purchase_order.supplier,
                    sco.supplier,
                ),
                "doctype": "Purchase Order",
                "document": purchase_order.name,
            }
        )

    return purchase_order

def _get_settlement_policy(
    purchase_order,
) -> dict[str, Any]:
    """
    Return the contractual settlement policy defined on the Purchase Order.

    This is a read-only interpretation of the Purchase Order contract.
    No recommendations or calculations are performed here.
    """

    if not purchase_order:
        return {
            "policy_available": False,
            "policy_source": None,
            "purchase_order": None,
            "recover_raw_material_shortage": False,
            "recover_processing_charges_on_shortage": False,
            "settlement_basis": None,
            "settlement_remarks": None,
        }

    return {
        "policy_available": True,
        "policy_source": "Purchase Order",
        "purchase_order": purchase_order.name,

        "recover_raw_material_shortage":
            bool(
                purchase_order.custom_recover_raw_material_shortage
            ),

        "recover_processing_charges_on_shortage":
            bool(
                purchase_order.custom_recover_processing_charges_on_shortage
            ),

        "settlement_basis":
            purchase_order.custom_settlement_basis,

        "settlement_remarks":
            purchase_order.custom_settlement_remarks,
    }


def apply_processor_lot_settlement_policy(
    facts: dict[str, Any],
    processor_lot,
) -> dict[str, Any]:
    """Return facts with the Processor Lot's effective policy snapshot.

    The Fact Engine continues to report the originating Purchase Order
    contract by default. Settlement workflows that operate on a specific
    Processor Lot must use its controlled snapshot, including any audited
    lot-level override.
    """
    effective_facts = deepcopy(facts or {})
    purchase_order_policy = dict(
        effective_facts.get("settlement_policy") or {}
    )

    purchase_order_policy.update(
        {
            "policy_available": True,
            "policy_source": (
                processor_lot.settlement_policy_source
                or "Purchase Order"
            ),
            "purchase_order": processor_lot.purchase_order,
            "recover_raw_material_shortage": bool(
                processor_lot.recover_raw_material_shortage
            ),
            "recover_processing_charges_on_shortage": bool(
                processor_lot
                .recover_processing_charges_on_shortage
            ),
            "settlement_basis": processor_lot.settlement_basis,
            "settlement_remarks": processor_lot.settlement_remarks,
        }
    )

    effective_facts["settlement_policy"] = purchase_order_policy
    return effective_facts

def _get_material_transfer_facts(
    sco,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Return Stock Entries used to send components to the subcontractor.

    ERPNext stores the authoritative SCO relationship on the Stock Entry
    header through Stock Entry.subcontracting_order. The transferred material,
    quantity, source warehouse, target warehouse and value are held in the
    Stock Entry Detail rows.

    Cancelled Stock Entries remain visible in history but do not contribute
    to active transfer totals.
    """
    transfer_rows = frappe.db.sql(
        """
        SELECT
            se.name AS stock_entry,
            se.docstatus,
            se.stock_entry_type,
            se.purpose,
            se.posting_date,
            se.posting_time,
            se.company,
            se.supplier,
            se.subcontracting_order,
            sed.name AS stock_entry_detail,
            sed.item_code,
            sed.qty,
            sed.uom,
            sed.stock_uom,
            sed.s_warehouse,
            sed.t_warehouse,
            sed.basic_rate,
            sed.basic_amount
        FROM `tabStock Entry Detail` sed
        INNER JOIN `tabStock Entry` se
            ON se.name = sed.parent
        WHERE
            se.subcontracting_order = %(subcontracting_order)s
            AND (
                se.purpose = 'Send to Subcontractor'
                OR se.stock_entry_type = 'Send to Subcontractor'
            )
        ORDER BY
            se.posting_date,
            se.posting_time,
            se.creation,
            sed.idx
        """,
        {
            "subcontracting_order": sco.name,
        },
        as_dict=True,
    )

    submitted_rows = [
        row
        for row in transfer_rows
        if row.docstatus == 1
    ]

    draft_rows = [
        row
        for row in transfer_rows
        if row.docstatus == 0
    ]

    cancelled_rows = [
        row
        for row in transfer_rows
        if row.docstatus == 2
    ]

    submitted_names = sorted(
        {
            row.stock_entry
            for row in submitted_rows
        }
    )

    draft_names = sorted(
        {
            row.stock_entry
            for row in draft_rows
        }
    )

    cancelled_names = sorted(
        {
            row.stock_entry
            for row in cancelled_rows
        }
    )

    for stock_entry in draft_names:
        warnings.append(
            {
                "code": "DRAFT_MATERIAL_TRANSFER",
                "message": _(
                    "Draft material-transfer Stock Entry {0} exists against "
                    "Subcontracting Order {1}."
                ).format(
                    stock_entry,
                    sco.name,
                ),
                "doctype": "Stock Entry",
                "document": stock_entry,
            }
        )

    for stock_entry in cancelled_names:
        warnings.append(
            {
                "code": "CANCELLED_MATERIAL_TRANSFER",
                "message": _(
                    "Cancelled material-transfer Stock Entry {0} exists in "
                    "the history of Subcontracting Order {1}."
                ).format(
                    stock_entry,
                    sco.name,
                ),
                "doctype": "Stock Entry",
                "document": stock_entry,
            }
        )

    transfers = []

    for row in transfer_rows:
        transfers.append(
            {
                "stock_entry": row.stock_entry,
                "docstatus": row.docstatus,
                "stock_entry_type": row.stock_entry_type,
                "purpose": row.purpose,
                "posting_date": row.posting_date,
                "posting_time": row.posting_time,
                "company": row.company,
                "supplier": row.supplier,
                "subcontracting_order": (
                    row.subcontracting_order
                ),
                "stock_entry_detail": row.stock_entry_detail,
                "item_code": row.item_code,
                "qty": flt(row.qty),
                "uom": row.uom,
                "stock_uom": row.stock_uom,
                "source_warehouse": row.s_warehouse,
                "target_warehouse": row.t_warehouse,
                "basic_rate": flt(row.basic_rate),
                "basic_amount": flt(row.basic_amount),
            }
        )

    total_transferred_qty = flt(
        sum(
            flt(row.qty)
            for row in submitted_rows
        )
    )

    total_transfer_value = flt(
        sum(
            flt(row.basic_amount)
            for row in submitted_rows
        )
    )

    sco_supplied_qty = flt(
        sum(
            flt(row.supplied_qty)
            for row in sco.supplied_items
        )
    )

    if total_transferred_qty != sco_supplied_qty:
        warnings.append(
            {
                "code": "TRANSFER_QTY_MISMATCH",
                "message": _(
                    "Submitted material-transfer quantity {0} differs from "
                    "SCO supplied quantity {1}."
                ).format(
                    total_transferred_qty,
                    sco_supplied_qty,
                ),
                "doctype": "Subcontracting Order",
                "document": sco.name,
            }
        )

    target_warehouses = sorted(
        {
            row.t_warehouse
            for row in submitted_rows
            if row.t_warehouse
        }
    )

    if (
        sco.supplier_warehouse
        and target_warehouses
        and sco.supplier_warehouse not in target_warehouses
    ):
        warnings.append(
            {
                "code": "SUPPLIER_WAREHOUSE_MISMATCH",
                "message": _(
                    "Submitted material transfers do not target the Supplier "
                    "Warehouse {0} defined on Subcontracting Order {1}."
                ).format(
                    sco.supplier_warehouse,
                    sco.name,
                ),
                "doctype": "Subcontracting Order",
                "document": sco.name,
            }
        )

    return {
        "transfers": transfers,
        "submitted_stock_entries": submitted_names,
        "draft_stock_entries": draft_names,
        "cancelled_stock_entries": cancelled_names,
        "total_transferred_qty": total_transferred_qty,
        "sco_supplied_qty": sco_supplied_qty,
        "total_transfer_value": total_transfer_value,
        "target_warehouses": target_warehouses,
    }

def _get_component_facts(
    sco,
    warnings: list[dict[str, Any]],
    blocking_errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return SCO-wise component accountability facts."""
    facts: list[dict[str, Any]] = []

    if not sco.supplied_items:
        blocking_errors.append(
            {
                "code": "NO_SUPPLIED_ITEMS",
                "message": _(
                    "Subcontracting Order {0} has no supplied component rows."
                ).format(sco.name),
                "doctype": "Subcontracting Order",
                "document": sco.name,
            }
        )
        return facts

    submitted_credit_applications = frappe.get_all(
        "Processor Material Account Entry",
        filters={
            "entry_type": "Credit Applied",
            "source_event": "Processor Lot Shortage",
            "subcontracting_order": sco.name,
            "account_direction": "Debit",
            "docstatus": 1,
            "is_reversed": 0,
        },
        fields=[
            "principal_component",
            "account_uom",
            "account_qty",
        ],
    )

    for row in sco.supplied_items:
        required_qty = flt(row.required_qty)
        supplied_qty = flt(row.supplied_qty)
        consumed_qty = flt(row.consumed_qty)
        returned_qty = flt(
            getattr(row, "returned_qty", 0)
        )

        outstanding_qty = flt(
            supplied_qty
            - consumed_qty
            - returned_qty
        )

        credit_applied_qty = flt(
            sum(
                flt(application.account_qty)
                for application in submitted_credit_applications
                if application.principal_component == row.rm_item_code
                and application.account_uom == row.stock_uom
            )
        )
        unsettled_outstanding_qty = max(
            flt(outstanding_qty - credit_applied_qty),
            0.0,
        )

        warehouse_balance = _get_warehouse_balance(
            item_code=row.rm_item_code,
            warehouse=sco.supplier_warehouse,
        )

        if outstanding_qty < 0:
            blocking_errors.append(
                {
                    "code": "NEGATIVE_OUTSTANDING_QTY",
                    "message": _(
                        "Component {0} has negative outstanding quantity {1}."
                    ).format(
                        row.rm_item_code,
                        outstanding_qty,
                    ),
                    "doctype": "Subcontracting Order",
                    "document": sco.name,
                    "row_name": row.name,
                }
            )

        if (
            unsettled_outstanding_qty - warehouse_balance
            > 0.000001
        ):
            warnings.append(
                {
                    "code": "OUTSTANDING_EXCEEDS_WAREHOUSE_BALANCE",
                    "message": _(
                        "SCO-specific unsettled quantity {0} {1} for "
                        "{2} exceeds the current Supplier Warehouse balance "
                        "{3} {1}."
                    ).format(
                        unsettled_outstanding_qty,
                        row.stock_uom,
                        row.rm_item_code,
                        warehouse_balance,
                    ),
                    "doctype": "Subcontracting Order",
                    "document": sco.name,
                    "row_name": row.name,
                }
            )

        facts.append(
            {
                "sco_supplied_item": row.name,
                "sco_finished_item": row.reference_name,
                "component_item": row.rm_item_code,
                "finished_item": row.main_item_code,
                "stock_uom": row.stock_uom,
                "required_qty": required_qty,
                "supplied_qty": supplied_qty,
                "consumed_qty": consumed_qty,
                "returned_qty": returned_qty,
                "outstanding_qty": outstanding_qty,
                "credit_applied_qty": credit_applied_qty,
                "unsettled_outstanding_qty": (
                    unsettled_outstanding_qty
                ),
                "warehouse_balance": warehouse_balance,
                "component_rate": flt(row.rate),
                "source": {
                    "doctype": "Subcontracting Order",
                    "document": sco.name,
                    "row_name": row.name,
                    "docstatus": sco.docstatus,
                },
            }
        )

    return facts

def _get_processor_lot_receipt_facts(
    sco,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Return allocation-aware Processor Lot Receipt facts for one SCO.

    V1.1 Processor Lot Receipts may allocate one truck across multiple
    Processor Lots and Subcontracting Orders. For those receipts, quantities
    are attributed from Processor Lot Receipt Allocation rows.

    Older V1 receipts without allocation rows retain the direct header-SCO
    fallback for backward compatibility.
    """
    allocation_rows = frappe.get_all(
        "Processor Lot Receipt Allocation",
        filters={
            "subcontracting_order": sco.name,
            "parenttype": "Processor Lot Receipt",
            "parentfield": "lot_allocations",
        },
        fields=[
            "parent",
            "idx",
            "processor_lot",
            "processed_item",
            "stock_uom",
            "allocated_accepted_qty",
            "allocated_invoice_qty",
        ],
        order_by="parent asc, idx asc",
    )

    direct_rows = frappe.get_all(
        "Processor Lot Receipt",
        filters={
            "subcontracting_order": sco.name,
            "docstatus": ["!=", 2],
        },
        fields=["name"],
        order_by="creation asc",
    )

    receipt_names = list(
        dict.fromkeys(
            [
                row.parent
                for row in allocation_rows
                if row.parent
            ]
            + [
                row.name
                for row in direct_rows
                if row.name
            ]
        )
    )

    if not receipt_names:
        return {
            "receipts": [],
            "receipt_count": 0,
            "stock_uom": "",
            "total_supplier_invoice_qty": 0.0,
            "total_company_accepted_qty": 0.0,
            "total_supplier_vs_company_qty": 0.0,
        }

    receipt_rows = frappe.get_all(
        "Processor Lot Receipt",
        filters={
            "name": ["in", receipt_names],
            "docstatus": ["!=", 2],
        },
        fields=[
            "name",
            "processor_lot",
            "measurement_method",
            "physical_receipt_date",
            "creation",
            "processed_item",
            "stock_uom",
            "supplier_invoice_qty",
            "supplier_invoice_uom",
            "company_accepted_qty",
            "company_accepted_uom",
            "subcontracting_receipt",
            "purchase_receipt",
            "purchase_invoice",
        ],
    )

    receipts_by_name = {
        row.name: row
        for row in receipt_rows
    }

    active_receipt_names = set(receipts_by_name)

    allocation_rows = [
        row
        for row in allocation_rows
        if row.parent in active_receipt_names
    ]

    allocated_parent_names = set(
        frappe.get_all(
            "Processor Lot Receipt Allocation",
            filters={
                "parent": ["in", receipt_names],
                "parenttype": "Processor Lot Receipt",
                "parentfield": "lot_allocations",
            },
            pluck="parent",
        )
    )

    facts = []

    for allocation in allocation_rows:
        receipt = receipts_by_name.get(
            allocation.parent
        )

        if not receipt:
            continue

        accepted_qty = flt(
            allocation.allocated_accepted_qty
        )
        supplier_qty = flt(
            allocation.allocated_invoice_qty
        )

        facts.append(
            {
                "processor_lot_receipt":
                    receipt.name,
                "processor_lot":
                    allocation.processor_lot,
                "allocation_idx":
                    allocation.idx,
                "measurement_method":
                    receipt.measurement_method,
                "physical_receipt_date":
                    receipt.physical_receipt_date,
                "creation":
                    receipt.creation,
                "processed_item": (
                    allocation.processed_item
                    or receipt.processed_item
                ),
                "stock_uom": (
                    allocation.stock_uom
                    or receipt.stock_uom
                ),
                "supplier_invoice_qty":
                    supplier_qty,
                "supplier_invoice_uom": (
                    allocation.stock_uom
                    or receipt.supplier_invoice_uom
                ),
                "company_accepted_qty":
                    accepted_qty,
                "company_accepted_uom": (
                    allocation.stock_uom
                    or receipt.company_accepted_uom
                ),
                "supplier_invoice_vs_company_qty":
                    flt(
                        supplier_qty - accepted_qty,
                        3,
                    ),
                "subcontracting_receipt":
                    receipt.subcontracting_receipt,
                "purchase_receipt":
                    receipt.purchase_receipt,
                "purchase_invoice":
                    receipt.purchase_invoice,
                "attribution_source":
                    "Lot Allocation",
            }
        )

    for direct in direct_rows:
        if direct.name in allocated_parent_names:
            continue

        receipt = receipts_by_name.get(
            direct.name
        )

        if not receipt:
            continue

        supplier_qty = flt(
            receipt.supplier_invoice_qty
        )
        accepted_qty = flt(
            receipt.company_accepted_qty
        )

        facts.append(
            {
                "processor_lot_receipt":
                    receipt.name,
                "processor_lot":
                    receipt.processor_lot,
                "allocation_idx": 0,
                "measurement_method":
                    receipt.measurement_method,
                "physical_receipt_date":
                    receipt.physical_receipt_date,
                "creation":
                    receipt.creation,
                "processed_item":
                    receipt.processed_item,
                "stock_uom":
                    receipt.stock_uom,
                "supplier_invoice_qty":
                    supplier_qty,
                "supplier_invoice_uom":
                    receipt.supplier_invoice_uom,
                "company_accepted_qty":
                    accepted_qty,
                "company_accepted_uom":
                    receipt.company_accepted_uom,
                "supplier_invoice_vs_company_qty":
                    flt(
                        supplier_qty - accepted_qty,
                        3,
                    ),
                "subcontracting_receipt":
                    receipt.subcontracting_receipt,
                "purchase_receipt":
                    receipt.purchase_receipt,
                "purchase_invoice":
                    receipt.purchase_invoice,
                "attribution_source":
                    "Direct V1 Receipt",
            }
        )

    facts.sort(
        key=lambda row: (
            str(
                row.get("physical_receipt_date")
                or row.get("creation")
                or ""
            ),
            str(row.get("creation") or ""),
            row.get("processor_lot_receipt") or "",
            row.get("allocation_idx") or 0,
        )
    )

    for row in facts:
        row.pop("creation", None)

    stock_uoms = {
        row["stock_uom"]
        for row in facts
        if row.get("stock_uom")
    }

    if len(stock_uoms) > 1:
        warnings.append(
            {
                "code": "PLR_MULTIPLE_FINISHED_UOMS",
                "message": _(
                    "Processor Lot Receipt allocations against "
                    "Subcontracting Order {0} contain multiple "
                    "finished-item Stock UOMs: {1}."
                ).format(
                    sco.name,
                    ", ".join(
                        sorted(stock_uoms)
                    ),
                ),
                "doctype": "Subcontracting Order",
                "document": sco.name,
            }
        )

    total_supplier_invoice_qty = flt(
        sum(
            flt(row["supplier_invoice_qty"])
            for row in facts
        ),
        3,
    )

    total_company_accepted_qty = flt(
        sum(
            flt(row["company_accepted_qty"])
            for row in facts
        ),
        3,
    )

    total_supplier_vs_company_qty = flt(
        total_supplier_invoice_qty
        - total_company_accepted_qty,
        3,
    )

    stock_uom = (
        next(iter(stock_uoms))
        if len(stock_uoms) == 1
        else ""
    )

    receipt_count = len(
        {
            row["processor_lot_receipt"]
            for row in facts
            if row.get("processor_lot_receipt")
        }
    )

    return {
        "receipts": facts,
        "receipt_count": receipt_count,
        "stock_uom": stock_uom,
        "total_supplier_invoice_qty":
            total_supplier_invoice_qty,
        "total_company_accepted_qty":
            total_company_accepted_qty,
        "total_supplier_vs_company_qty":
            total_supplier_vs_company_qty,
    }


def _get_submitted_advance_credit_facts(
    sco,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return billed PLR-excess credits backed by submitted Stock Entries."""
    rows = frappe.get_all(
        "Processor Material Account Entry",
        filters={
            "entry_type": "Advance Credit",
            "source_event": "PLR Excess",
            "subcontracting_order": sco.name,
            "account_direction": "Credit",
            "docstatus": 1,
            "is_reversed": 0,
        },
        fields=[
            "name",
            "processor_lot_receipt",
            "receipt_item_key",
            "processor_lot",
            "processed_qty",
            "account_qty",
            "commercial_qty",
            "material_credit_stock_entry",
        ],
        order_by="posting_date asc, creation asc, name asc",
    )

    effective_rows = []
    for row in rows:
        stock_entry_docstatus = (
            frappe.db.get_value(
                "Stock Entry",
                row.material_credit_stock_entry,
                "docstatus",
            )
            if row.material_credit_stock_entry
            else None
        )
        if stock_entry_docstatus != 1:
            warnings.append(
                {
                    "code": "ADVANCE_CREDIT_STOCK_NOT_SUBMITTED",
                    "message": _(
                        "Processor Material Account Entry {0} is submitted "
                        "but its Material Credit Stock Entry is not submitted."
                    ).format(row.name),
                    "doctype": "Processor Material Account Entry",
                    "document": row.name,
                }
            )
            continue

        effective_rows.append(dict(row))

    return {
        "entries": effective_rows,
        "total_processed_qty": flt(
            sum(flt(row.get("processed_qty")) for row in effective_rows),
            6,
        ),
        "total_account_qty": flt(
            sum(flt(row.get("account_qty")) for row in effective_rows),
            6,
        ),
        "total_commercial_qty": flt(
            sum(flt(row.get("commercial_qty")) for row in effective_rows),
            6,
        ),
    }

def _get_receipt_facts(
    sco,
    component_facts: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Return submitted and draft Subcontracting Receipts linked to the SCO.

    ERPNext stores the authoritative SCO relationship on each
    Subcontracting Receipt Item row through:

        subcontracting_order
        subcontracting_order_item

    Cancelled SCRs are retained in document history but excluded from active
    receipt totals.
    """
    receipt_rows = frappe.db.sql(
        """
        SELECT
            scr.name AS subcontracting_receipt,
            scr.docstatus,
            scr.status,
            scr.posting_date,
            scr.posting_time,
            scr.supplier,
            scr.supplier_warehouse,
            scri.name AS subcontracting_receipt_item,
            scri.item_code,
            scri.stock_uom,
            scri.qty,
            scri.received_qty,
            scri.rejected_qty,
            scri.returned_qty,
            scri.rate,
            scri.amount,
            scri.rm_cost_per_qty,
            scri.service_cost_per_qty,
            scri.rm_supp_cost,
            scri.purchase_order,
            scri.purchase_order_item,
            scri.subcontracting_order,
            scri.subcontracting_order_item
        FROM `tabSubcontracting Receipt Item` scri
        INNER JOIN `tabSubcontracting Receipt` scr
            ON scr.name = scri.parent
        WHERE
            scri.subcontracting_order = %(subcontracting_order)s
        ORDER BY
            scr.posting_date,
            scr.posting_time,
            scr.creation,
            scri.idx
        """,
        {
            "subcontracting_order": sco.name,
        },
        as_dict=True,
    )

    supplied_rows = frappe.db.sql(
        """
        SELECT
            scr.name AS subcontracting_receipt,
            scr.docstatus,
            scrsi.name AS supplied_item_row,
            scrsi.rm_item_code,
            scrsi.main_item_code,
            scrsi.stock_uom,
            scrsi.required_qty,
            scrsi.consumed_qty,
            scrsi.rate,
            scrsi.amount,
            scrsi.current_stock,
            scrsi.subcontracting_order,
            scrsi.reference_name
        FROM `tabSubcontracting Receipt Supplied Item` scrsi
        INNER JOIN `tabSubcontracting Receipt` scr
            ON scr.name = scrsi.parent
        WHERE
            scrsi.subcontracting_order = %(subcontracting_order)s
        ORDER BY
            scr.posting_date,
            scr.posting_time,
            scr.creation,
            scrsi.idx
        """,
        {
            "subcontracting_order": sco.name,
        },
        as_dict=True,
    )

    active_receipt_rows = [
        row
        for row in receipt_rows
        if row.docstatus == 1
    ]

    draft_receipt_rows = [
        row
        for row in receipt_rows
        if row.docstatus == 0
    ]

    cancelled_receipt_rows = [
        row
        for row in receipt_rows
        if row.docstatus == 2
    ]

    active_supplied_rows = [
        row
        for row in supplied_rows
        if row.docstatus == 1
    ]

    for row in draft_receipt_rows:
        warnings.append(
            {
                "code": "DRAFT_SUBCONTRACTING_RECEIPT",
                "message": _(
                    "Draft Subcontracting Receipt {0} exists against "
                    "Subcontracting Order {1}."
                ).format(
                    row.subcontracting_receipt,
                    sco.name,
                ),
                "doctype": "Subcontracting Receipt",
                "document": row.subcontracting_receipt,
            }
        )

    for row in cancelled_receipt_rows:
        warnings.append(
            {
                "code": "CANCELLED_SUBCONTRACTING_RECEIPT",
                "message": _(
                    "Cancelled Subcontracting Receipt {0} exists in the "
                    "history of Subcontracting Order {1}."
                ).format(
                    row.subcontracting_receipt,
                    sco.name,
                ),
                "doctype": "Subcontracting Receipt",
                "document": row.subcontracting_receipt,
            }
        )

    receipt_items = []

    for row in receipt_rows:
        receipt_items.append(
            {
                "subcontracting_receipt": row.subcontracting_receipt,
                "docstatus": row.docstatus,
                "status": row.status,
                "posting_date": row.posting_date,
                "posting_time": row.posting_time,
                "supplier": row.supplier,
                "supplier_warehouse": row.supplier_warehouse,
                "subcontracting_receipt_item": (
                    row.subcontracting_receipt_item
                ),
                "item_code": row.item_code,
                "stock_uom": row.stock_uom,
                "qty": flt(row.qty),
                "received_qty": flt(row.received_qty),
                "rejected_qty": flt(row.rejected_qty),
                "returned_qty": flt(row.returned_qty),
                "rate": flt(row.rate),
                "amount": flt(row.amount),
                "raw_material_cost_per_qty": flt(
                    row.rm_cost_per_qty
                ),
                "service_cost_per_qty": flt(
                    row.service_cost_per_qty
                ),
                "raw_material_supplied_cost": flt(
                    row.rm_supp_cost
                ),
                "purchase_order": row.purchase_order,
                "purchase_order_item": row.purchase_order_item,
                "subcontracting_order": row.subcontracting_order,
                "subcontracting_order_item": (
                    row.subcontracting_order_item
                ),
            }
        )

    consumed_items = []

    for row in supplied_rows:
        consumed_items.append(
            {
                "subcontracting_receipt": row.subcontracting_receipt,
                "docstatus": row.docstatus,
                "supplied_item_row": row.supplied_item_row,
                "component_item": row.rm_item_code,
                "finished_item": row.main_item_code,
                "stock_uom": row.stock_uom,
                "required_qty": flt(row.required_qty),
                "consumed_qty": flt(row.consumed_qty),
                "rate": flt(row.rate),
                "amount": flt(row.amount),
                "current_stock_before_receipt": flt(
                    row.current_stock
                ),
                "subcontracting_order": row.subcontracting_order,
                "reference_name": row.reference_name,
            }
        )

    submitted_receipt_names = sorted(
        {
            row.subcontracting_receipt
            for row in active_receipt_rows
        }
    )

    draft_receipt_names = sorted(
        {
            row.subcontracting_receipt
            for row in draft_receipt_rows
        }
    )

    cancelled_receipt_names = sorted(
        {
            row.subcontracting_receipt
            for row in cancelled_receipt_rows
        }
    )

    total_received_qty = flt(
        sum(
            flt(row.received_qty)
            for row in active_receipt_rows
        )
    )

    total_rejected_qty = flt(
        sum(
            flt(row.rejected_qty)
            for row in active_receipt_rows
        )
    )

    total_consumed_qty = flt(
        sum(
            flt(row.consumed_qty)
            for row in active_supplied_rows
        )
    )

    sco_received_qty = flt(
        sum(
            flt(getattr(row, "received_qty", 0))
            for row in sco.items
        )
    )

    sco_consumed_qty = flt(
        sum(
            flt(row["consumed_qty"])
            for row in component_facts
        )
    )

    if total_received_qty != sco_received_qty:
        warnings.append(
            {
                "code": "SCR_RECEIVED_QTY_MISMATCH",
                "message": _(
                    "Submitted SCR received quantity {0} differs from "
                    "SCO received quantity {1}."
                ).format(
                    total_received_qty,
                    sco_received_qty,
                ),
                "doctype": "Subcontracting Order",
                "document": sco.name,
            }
        )

    if total_consumed_qty != sco_consumed_qty:
        warnings.append(
            {
                "code": "SCR_CONSUMED_QTY_MISMATCH",
                "message": _(
                    "Submitted SCR consumed quantity {0} differs from "
                    "SCO consumed quantity {1}."
                ).format(
                    total_consumed_qty,
                    sco_consumed_qty,
                ),
                "doctype": "Subcontracting Order",
                "document": sco.name,
            }
        )

    return {
        "receipt_items": receipt_items,
        "consumed_items": consumed_items,
        "submitted_subcontracting_receipts": (
            submitted_receipt_names
        ),
        "draft_subcontracting_receipts": draft_receipt_names,
        "cancelled_subcontracting_receipts": (
            cancelled_receipt_names
        ),
        "total_received_qty": total_received_qty,
        "total_rejected_qty": total_rejected_qty,
        "total_consumed_qty": total_consumed_qty,
        "sco_received_qty": sco_received_qty,
        "sco_consumed_qty": sco_consumed_qty,
    }


def _get_purchase_receipt_facts(
    purchase_order,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Return Purchase Receipts linked to the subcontracted Purchase Order.

    In the tested ERPNext flow, the Purchase Receipt represents commercial
    receipt of the processing service. It is linked to the Purchase Order and
    Purchase Order Item, but not necessarily to the Subcontracting Order or
    Subcontracting Receipt.

    Cancelled Purchase Receipts are retained in history but excluded from
    active totals.
    """
    if not purchase_order:
        return {
            "purchase_receipts": [],
            "submitted_purchase_receipts": [],
            "draft_purchase_receipts": [],
            "cancelled_purchase_receipts": [],
            "total_received_qty": 0.0,
            "total_net_amount": 0.0,
            "total_tax_amount": 0.0,
            "total_grand_total": 0.0,
        }

    receipt_rows = frappe.db.sql(
        """
        SELECT
            pr.name AS purchase_receipt,
            pr.docstatus,
            pr.status,
            pr.posting_date,
            pr.posting_time,
            pr.supplier,
            pr.net_total,
            pr.total_taxes_and_charges,
            pr.grand_total,
            pri.name AS purchase_receipt_item,
            pri.item_code,
            pri.qty,
            pri.received_qty,
            pri.rejected_qty,
            pri.returned_qty,
            pri.uom,
            pri.stock_uom,
            pri.rate,
            pri.net_rate,
            pri.net_amount,
            pri.purchase_order,
            pri.purchase_order_item,
            pri.subcontracting_receipt_item,
            pri.warehouse,
            pri.expense_account
        FROM `tabPurchase Receipt Item` pri
        INNER JOIN `tabPurchase Receipt` pr
            ON pr.name = pri.parent
        WHERE
            pri.purchase_order = %(purchase_order)s
        ORDER BY
            pr.posting_date,
            pr.posting_time,
            pr.creation,
            pri.idx
        """,
        {
            "purchase_order": purchase_order.name,
        },
        as_dict=True,
    )

    submitted_rows = [
        row
        for row in receipt_rows
        if row.docstatus == 1
    ]

    draft_rows = [
        row
        for row in receipt_rows
        if row.docstatus == 0
    ]

    cancelled_rows = [
        row
        for row in receipt_rows
        if row.docstatus == 2
    ]

    submitted_names = sorted(
        {
            row.purchase_receipt
            for row in submitted_rows
        }
    )

    draft_names = sorted(
        {
            row.purchase_receipt
            for row in draft_rows
        }
    )

    cancelled_names = sorted(
        {
            row.purchase_receipt
            for row in cancelled_rows
        }
    )

    for purchase_receipt in draft_names:
        warnings.append(
            {
                "code": "DRAFT_PURCHASE_RECEIPT",
                "message": _(
                    "Draft Purchase Receipt {0} exists against Purchase "
                    "Order {1}."
                ).format(
                    purchase_receipt,
                    purchase_order.name,
                ),
                "doctype": "Purchase Receipt",
                "document": purchase_receipt,
            }
        )

    for purchase_receipt in cancelled_names:
        warnings.append(
            {
                "code": "CANCELLED_PURCHASE_RECEIPT",
                "message": _(
                    "Cancelled Purchase Receipt {0} exists in the history "
                    "of Purchase Order {1}."
                ).format(
                    purchase_receipt,
                    purchase_order.name,
                ),
                "doctype": "Purchase Receipt",
                "document": purchase_receipt,
            }
        )

    purchase_receipts = []

    for row in receipt_rows:
        purchase_receipts.append(
            {
                "purchase_receipt": row.purchase_receipt,
                "docstatus": row.docstatus,
                "status": row.status,
                "posting_date": row.posting_date,
                "posting_time": row.posting_time,
                "supplier": row.supplier,
                "purchase_receipt_item": (
                    row.purchase_receipt_item
                ),
                "item_code": row.item_code,
                "qty": flt(row.qty),
                "received_qty": flt(row.received_qty),
                "rejected_qty": flt(row.rejected_qty),
                "returned_qty": flt(row.returned_qty),
                "uom": row.uom,
                "stock_uom": row.stock_uom,
                "rate": flt(row.rate),
                "net_rate": flt(row.net_rate),
                "net_amount": flt(row.net_amount),
                "purchase_order": row.purchase_order,
                "purchase_order_item": (
                    row.purchase_order_item
                ),
                "subcontracting_receipt_item": (
                    row.subcontracting_receipt_item
                ),
                "warehouse": row.warehouse,
                "expense_account": row.expense_account,
            }
        )

    submitted_headers = {}

    for row in submitted_rows:
        submitted_headers[row.purchase_receipt] = {
            "net_total": flt(row.net_total),
            "tax_amount": flt(
                row.total_taxes_and_charges
            ),
            "grand_total": flt(row.grand_total),
        }

    total_received_qty = flt(
        sum(
            flt(row.received_qty)
            for row in submitted_rows
        )
    )

    total_net_amount = flt(
        sum(
            header["net_total"]
            for header in submitted_headers.values()
        )
    )

    total_tax_amount = flt(
        sum(
            header["tax_amount"]
            for header in submitted_headers.values()
        )
    )

    total_grand_total = flt(
        sum(
            header["grand_total"]
            for header in submitted_headers.values()
        )
    )

    po_received_qty = flt(
        sum(
            flt(getattr(row, "received_qty", 0))
            for row in purchase_order.items
        )
    )

    if total_received_qty != po_received_qty:
        warnings.append(
            {
                "code": "PR_RECEIVED_QTY_MISMATCH",
                "message": _(
                    "Submitted Purchase Receipt quantity {0} differs from "
                    "Purchase Order received quantity {1}."
                ).format(
                    total_received_qty,
                    po_received_qty,
                ),
                "doctype": "Purchase Order",
                "document": purchase_order.name,
            }
        )

    return {
        "purchase_receipts": purchase_receipts,
        "submitted_purchase_receipts": submitted_names,
        "draft_purchase_receipts": draft_names,
        "cancelled_purchase_receipts": cancelled_names,
        "total_received_qty": total_received_qty,
        "purchase_order_received_qty": po_received_qty,
        "total_net_amount": total_net_amount,
        "total_tax_amount": total_tax_amount,
        "total_grand_total": total_grand_total,
    }

def _get_commercial_facts(
    purchase_order,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return submitted Purchase Invoice facts linked to the Purchase Order."""
    if not purchase_order:
        return {
            "purchase_invoices": [],
            "total_invoice_qty": 0.0,
            "total_net_amount": 0.0,
            "total_tax_amount": 0.0,
            "total_grand_total": 0.0,
        }

    invoice_rows = frappe.db.sql(
        """
        SELECT
            pi.name AS purchase_invoice,
            pi.docstatus,
            pi.posting_date,
            pi.bill_no,
            pi.bill_date,
            pi.net_total,
            pi.total_taxes_and_charges,
            pi.grand_total,
            pii.name AS purchase_invoice_item,
            pii.item_code,
            pii.qty,
            pii.uom,
            pii.rate,
            pii.net_rate,
            pii.net_amount,
            pii.purchase_order,
            pii.po_detail
        FROM `tabPurchase Invoice Item` pii
        INNER JOIN `tabPurchase Invoice` pi
            ON pi.name = pii.parent
        WHERE
            pii.purchase_order = %(purchase_order)s
            AND pi.docstatus != 2
        ORDER BY
            pi.posting_date,
            pi.creation,
            pii.idx
        """,
        {
            "purchase_order": purchase_order.name,
        },
        as_dict=True,
    )

    purchase_invoices: list[dict[str, Any]] = []

    submitted_rows = [
        row
        for row in invoice_rows
        if row.docstatus == 1
    ]

    draft_rows = [
        row
        for row in invoice_rows
        if row.docstatus == 0
    ]

    for row in draft_rows:
        warnings.append(
            {
                "code": "DRAFT_PURCHASE_INVOICE",
                "message": _(
                    "Draft Purchase Invoice {0} exists against Purchase "
                    "Order {1}."
                ).format(
                    row.purchase_invoice,
                    purchase_order.name,
                ),
                "doctype": "Purchase Invoice",
                "document": row.purchase_invoice,
            }
        )

    for row in invoice_rows:
        purchase_invoices.append(
            {
                "purchase_invoice": row.purchase_invoice,
                "docstatus": row.docstatus,
                "posting_date": row.posting_date,
                "bill_no": row.bill_no,
                "bill_date": row.bill_date,
                "purchase_invoice_item": row.purchase_invoice_item,
                "item_code": row.item_code,
                "qty": flt(row.qty),
                "uom": row.uom,
                "rate": flt(row.rate),
                "net_rate": flt(row.net_rate),
                "net_amount": flt(row.net_amount),
                "net_total": flt(row.net_total),
                "tax_amount": flt(
                    row.total_taxes_and_charges
                ),
                "grand_total": flt(row.grand_total),
                "purchase_order": row.purchase_order,
                "po_detail": row.po_detail,
            }
        )

    submitted_invoice_names = {
        row.purchase_invoice
        for row in submitted_rows
    }

    submitted_invoice_headers = {}

    for row in submitted_rows:
        submitted_invoice_headers[row.purchase_invoice] = {
            "net_total": flt(row.net_total),
            "tax_amount": flt(row.total_taxes_and_charges),
            "grand_total": flt(row.grand_total),
        }

    return {
        "purchase_invoices": purchase_invoices,
        "submitted_purchase_invoices": sorted(
            submitted_invoice_names
        ),
        "total_invoice_qty": flt(
            sum(flt(row.qty) for row in submitted_rows)
        ),
        "total_net_amount": flt(
            sum(
                header["net_total"]
                for header in submitted_invoice_headers.values()
            )
        ),
        "total_tax_amount": flt(
            sum(
                header["tax_amount"]
                for header in submitted_invoice_headers.values()
            )
        ),
        "total_grand_total": flt(
            sum(
                header["grand_total"]
                for header in submitted_invoice_headers.values()
            )
        ),
    }

def _build_fact_summary(
    component_totals: dict[str, float],
    material_transfer_facts: dict[str, Any],
    receipt_facts: dict[str, Any],
    purchase_receipt_facts: dict[str, Any],
    commercial_facts: dict[str, Any],
    advance_credit_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Return a compact read-only summary for the future Health Panel and
    Reconciliation Wizard.

    No variance is classified here. Differences remain factual comparisons.
    """
    supplied_qty = flt(
        component_totals.get("supplied_qty")
    )

    consumed_qty = flt(
        component_totals.get("consumed_qty")
    )

    returned_qty = flt(
        component_totals.get("returned_qty")
    )

    outstanding_qty = flt(
        component_totals.get("outstanding_qty")
    )

    outstanding_uom = (
        component_totals.get("stock_uom") or ""
    )

    transferred_qty = flt(
        material_transfer_facts.get(
            "total_transferred_qty"
        )
    )

    scr_received_qty = flt(
        receipt_facts.get("total_received_qty")
    )

    scr_consumed_qty = flt(
        receipt_facts.get("total_consumed_qty")
    )

    sco_received_qty = flt(
        receipt_facts.get("sco_received_qty")
    )

    sco_consumed_qty = flt(
        receipt_facts.get("sco_consumed_qty")
    )

    pr_received_qty = flt(
        purchase_receipt_facts.get(
            "total_received_qty"
        )
    )

    pi_invoice_qty = flt(
        commercial_facts.get("total_invoice_qty")
    )

    advance_credit_facts = advance_credit_facts or {}
    billed_advance_credit_qty = flt(
        advance_credit_facts.get("total_commercial_qty")
    )
    commercially_recognized_qty = flt(
        scr_received_qty + billed_advance_credit_qty
    )

    return {
        "entrustment": {
            "transferred_qty": transferred_qty,
            "sco_supplied_qty": supplied_qty,
            "transfer_value": flt(
                material_transfer_facts.get(
                    "total_transfer_value"
                )
            ),
        },
        "physical_inventory": {
            "scr_received_qty": scr_received_qty,
            "scr_consumed_qty": scr_consumed_qty,
            "components_returned_qty": returned_qty,
            "outstanding_qty": outstanding_qty,
            "outstanding_uom": outstanding_uom,
        },
        "commercial": {
            "purchase_receipt_qty": pr_received_qty,
            "purchase_invoice_qty": pi_invoice_qty,
            "billed_advance_credit_qty": billed_advance_credit_qty,
            "commercially_recognized_receipt_qty": (
                commercially_recognized_qty
            ),
            "service_invoice_net_amount": flt(
                commercial_facts.get(
                    "total_net_amount"
                )
            ),
            "service_invoice_tax_amount": flt(
                commercial_facts.get(
                    "total_tax_amount"
                )
            ),
            "service_invoice_grand_total": flt(
                commercial_facts.get(
                    "total_grand_total"
                )
            ),
        },
        "comparisons": {
            "transfer_vs_sco_supplied": flt(
                transferred_qty - supplied_qty
            ),
            "scr_received_vs_sco_received": flt(
                scr_received_qty - sco_received_qty
            ),
            "scr_consumed_vs_sco_consumed": flt(
                scr_consumed_qty - sco_consumed_qty
            ),
            "invoice_vs_scr_received": flt(
                pi_invoice_qty - commercially_recognized_qty
            ),
            "invoice_vs_stock_backed_scr_received": flt(
                pi_invoice_qty - scr_received_qty
            ),
            "purchase_receipt_vs_invoice": flt(
                pr_received_qty - pi_invoice_qty
            ),
        },
    }

def _calculate_totals(
    component_facts: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Return aggregate component facts for the current SCO.

    Besides quantity totals, this helper also carries the common Stock UOM
    forward so later Fact Engine summaries and Recommendation Reports can
    display quantities with their unit.

    If multiple Stock UOMs are encountered, the UOM is intentionally left
    blank. Mixed-UOM Processor Lots are expected to be handled explicitly in
    a future enhancement rather than silently reported incorrectly.
    """

    stock_uoms = {
        row.get("stock_uom")
        for row in component_facts
        if row.get("stock_uom")
    }

    stock_uom = (
        stock_uoms.pop()
        if len(stock_uoms) == 1
        else ""
    )

    return {
        "required_qty": flt(
            sum(
                flt(row["required_qty"])
                for row in component_facts
            )
        ),
        "supplied_qty": flt(
            sum(
                flt(row["supplied_qty"])
                for row in component_facts
            )
        ),
        "consumed_qty": flt(
            sum(
                flt(row["consumed_qty"])
                for row in component_facts
            )
        ),
        "returned_qty": flt(
            sum(
                flt(row["returned_qty"])
                for row in component_facts
            )
        ),
        "outstanding_qty": flt(
            sum(
                flt(row["outstanding_qty"])
                for row in component_facts
            )
        ),
        "stock_uom": stock_uom,
    }


def _get_warehouse_balance(
    item_code: str,
    warehouse: str,
) -> float:
    """Return current Bin actual quantity for one item and warehouse."""
    if not item_code or not warehouse:
        return 0.0

    return flt(
        frappe.db.get_value(
            "Bin",
            {
                "item_code": item_code,
                "warehouse": warehouse,
            },
            "actual_qty",
        )
        or 0
    )
