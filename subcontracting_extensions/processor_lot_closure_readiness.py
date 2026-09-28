"""J19B2N read-only whole-Processor-Lot closure readiness.

This module reconciles operational receipt completion with component-scoped
commercial settlement.  It never submits a Processor Lot, creates an
obligation, approves a waiver, or writes stock/accounting evidence.
"""

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json


CONTRACT_VERSION = "J19B2N"


def attach_processor_lot_closure_readiness(report, context=None):
    """Attach a fail-closed whole-lot assessment to a commercial report."""
    result = deepcopy(report or {})
    context = deepcopy(context or {})
    blockers = []
    completed_scopes = []
    remaining_obligations = []

    lot = context.get("processor_lot") or {}
    completion = result.get("receipt_completion") or {}

    if lot.get("docstatus") != 0:
        _issue(blockers, "PROCESSOR_LOT_IS_NOT_OPEN_DRAFT")
    if lot.get("settlement_status") in ("Completed", "Cancelled", "Reversal In Progress"):
        _issue(blockers, "PROCESSOR_LOT_LIFECYCLE_DOES_NOT_PERMIT_CLOSURE_REVIEW")

    completion_items = []
    for row in completion.get("items") or []:
        item = {
            "subcontracting_order_item": row.get("subcontracting_order_item"),
            "processed_item": row.get("processed_item"),
            "stock_uom": row.get("stock_uom"),
            "ordered_qty": row.get("ordered_qty"),
            "submitted_scr_qty": row.get("submitted_scr_qty"),
            "submitted_pr_qty": row.get("submitted_pr_qty"),
            "submitted_pi_qty": row.get("submitted_pi_qty"),
            "journey_complete": bool(row.get("journey_complete")),
            "issues": list(row.get("issues") or []),
        }
        completion_items.append(item)
        if "ORDER_QUANTITY_NOT_FULLY_RECEIVED" in item["issues"]:
            _issue(blockers, "FINISHED_ITEM_ORDER_QUANTITY_NOT_FULLY_RECEIVED")
        if (not item["journey_complete"]
                or "JOURNEY_NOT_FULLY_VERIFIED" in item["issues"]):
            _issue(blockers, "FINISHED_ITEM_RECEIPT_JOURNEY_NOT_FULLY_VERIFIED")
    if not completion_items:
        _issue(blockers, "FINISHED_ITEM_RECEIPT_EVIDENCE_MISSING")

    finished_scopes = []
    for row in result.get("finished_items") or []:
        scope = {
            "sco_finished_item": row.get("sco_finished_item"),
            "finished_item": row.get("finished_item"),
            "stock_uom": row.get("stock_uom"),
            "commercial_decision_code": row.get("commercial_decision_code"),
            "commercial_variance_qty": row.get("commercial_variance_qty"),
            "classification": (row.get("persisted_classification") or {}).get(
                "classification"
            ),
            "resolved": False,
        }
        receipt_item = _one(completion_items, "subcontracting_order_item",
                            scope["sco_finished_item"])
        receipt_complete = bool(receipt_item and receipt_item.get("journey_complete"))
        decision = scope["commercial_decision_code"]
        commercial_ready = decision in (
            "COMMERCIAL_REVIEW_COMPLETE_NO_RECOVERY",
            "COMMERCIAL_REVIEW_COMPLETE",
        )
        scope["resolved"] = bool(receipt_complete and commercial_ready)
        if not commercial_ready:
            _issue(blockers, "FINISHED_ITEM_COMMERCIAL_EVIDENCE_NOT_READY")
        if scope["resolved"]:
            completed_scopes.append({
                "scope_type": "FINISHED_ITEM",
                "scope_key": scope["sco_finished_item"],
                "resolution_code": "FINISHED_ITEM_RECEIPT_AND_COMMERCIAL_REVIEW_COMPLETE",
            })
        finished_scopes.append(scope)

    posting_by_scope = context.get("sales_invoice_posting_evidence") or {}
    component_scopes = []
    for row in result.get("components") or []:
        scope_key = row.get("commercial_scope_key")
        submission = (
            row.get("retained_material_sales_invoice_submission_readiness") or {}
        )
        scope = {
            "scope_key": scope_key,
            "sco_supplied_item": row.get("sco_supplied_item"),
            "component_item": row.get("component_item"),
            "stock_uom": row.get("stock_uom"),
            "physical_remaining_qty": row.get("physical_remaining_qty"),
            "disposition": (row.get("persisted_material_disposition") or {}).get(
                "disposition"
            ),
            "classification": (row.get("persisted_classification") or {}).get(
                "classification"
            ),
            "selected_treatment_method": (
                row.get("persisted_classification") or {}
            ).get("selected_treatment_method"),
            "resolution_code": None,
            "resolved": False,
        }
        physical_remaining = _number(row.get("physical_remaining_qty"))
        if row.get("evidence_consistent") is False:
            _issue(blockers, "COMPONENT_PHYSICAL_EVIDENCE_INCONSISTENT")
        if physical_remaining <= 0 and row.get("evidence_consistent") is not False:
            scope.update(
                resolved=True,
                resolution_code="NO_COMPONENT_PHYSICAL_BALANCE_REMAINS",
            )
        elif _submitted_sales_invoice_scope_is_valid(
            row, submission, posting_by_scope.get(scope_key) or {}
        ):
            scope.update(
                resolved=True,
                resolution_code="RETAINED_MATERIAL_RECOVERY_COMPLETE",
                sales_invoice=submission.get("sales_invoice"),
                submission_event=(submission.get("submission_event") or {}).get("name"),
            )
        else:
            remaining_obligations.append({
                "scope_type": "COMPONENT",
                "scope_key": scope_key,
                "item": row.get("component_item"),
                "quantity": row.get("physical_remaining_qty"),
                "stock_uom": row.get("stock_uom"),
                "position": "UNRESOLVED_POSITIVE_RESIDUAL",
            })
            _issue(blockers, "COMPONENT_POSITIVE_RESIDUAL_NOT_CONTROLLED")
        if scope["resolved"]:
            completed_scopes.append({
                "scope_type": "COMPONENT",
                "scope_key": scope_key,
                "resolution_code": scope["resolution_code"],
                "sales_invoice": scope.get("sales_invoice"),
            })
        component_scopes.append(scope)

    if not component_scopes:
        _issue(blockers, "COMPONENT_RECONCILIATION_EVIDENCE_MISSING")

    for code in result.get("policy_issues") or []:
        _issue(blockers, "SETTLEMENT_POLICY_EVIDENCE_NOT_READY")
    for code in result.get("classification_issues") or []:
        _issue(blockers, "COMMERCIAL_CLASSIFICATION_EVIDENCE_NOT_READY")
    for code in result.get("material_disposition_issues") or []:
        _issue(blockers, "MATERIAL_DISPOSITION_EVIDENCE_NOT_READY")
    if result.get("legacy_evidence"):
        _issue(blockers, "LEGACY_SETTLEMENT_EVIDENCE_REQUIRES_REVIEW")

    # V2 has not implemented its obligation ledger yet.  Never infer an
    # obligation from policy text or from a positive residual.
    active_obligations = list(context.get("active_carry_forward_obligations") or [])
    obligation_model_available = bool(context.get("obligation_model_available"))
    if active_obligations and not obligation_model_available:
        _issue(blockers, "CARRY_FORWARD_OBLIGATION_MODEL_NOT_AVAILABLE")

    ready = not blockers
    can_submit = bool(context.get("user_can_submit_processor_lot"))
    return result | {
        "processor_lot_closure_readiness_contract_version": CONTRACT_VERSION,
        "processor_lot_closure_readiness_enabled": bool(context.get("enabled")),
        "processor_lot_closure_readiness": {
            "contract_version": CONTRACT_VERSION,
            "applicable": True,
            "readiness_code": (
                "PROCESSOR_LOT_READY_FOR_FUTURE_CONTROLLED_CLOSURE"
                if ready else "PROCESSOR_LOT_CLOSURE_NOT_READY"
            ),
            "blocking_issues": blockers,
            "completed_scopes": completed_scopes,
            "remaining_obligations": remaining_obligations,
            "active_carry_forward_obligations": active_obligations,
            "receipt_cycle_complete": bool(completion.get("journey_complete")),
            "finished_item_scopes": finished_scopes,
            "component_scopes": component_scopes,
            "collection_status": context.get("collection_status") or "NOT_APPLICABLE",
            "collection_blocks_lot_closure": False,
            "user_can_submit_processor_lot": can_submit,
            # J19B2N is evidence only. A later controlled-closure checkpoint
            # may consume readiness and permission, but this contract exposes
            # no action even when its diagnostic flag is enabled.
            "closure_action_visible": False,
            "closure_authorized": False,
            "lot_submission_authorized": False,
            "document_creation_enabled": False,
        },
        "lot_closure_authorized": False,
    }


def _submitted_sales_invoice_scope_is_valid(row, readiness, posting):
    event = readiness.get("submission_event") or {}
    snapshots = (
        readiness.get("posted_stock_ledger_entries") or [],
        readiness.get("posted_gl_entries") or [],
    )
    return bool(
        (row.get("persisted_material_disposition") or {}).get("disposition")
        == "RETAINED_BY_PROCESSOR"
        and (row.get("persisted_classification") or {}).get("classification")
        == "PROCESSOR_RESPONSIBLE"
        and (row.get("persisted_classification") or {}).get("selected_treatment_method")
        == "SALES_INVOICE"
        and readiness.get("readiness_code")
        == "SALES_INVOICE_SUBMITTED_LOT_CLOSURE_DEFERRED"
        and not readiness.get("blocking_issues")
        and event.get("name")
        and not event.get("lot_closure_authorized")
        and len(snapshots[0]) == 1
        and len(snapshots[1]) == 6
        and posting.get("sales_invoice_docstatus") == 1
        and posting.get("lineage_matches") is True
        and posting.get("commercial_values_match") is True
        and posting.get("stock_ledger_entry_count") == 1
        and posting.get("gl_entry_count") == 6
        and _canonical_rows(posting.get("live_stock_ledger_entries"))
        == _canonical_rows(snapshots[0])
        and _canonical_rows(posting.get("live_gl_entries"))
        == _canonical_rows(snapshots[1])
        and _same_number(posting.get("supplier_warehouse_qty"), 0)
        and _same_number(posting.get("supplier_warehouse_stock_value"), 0)
    )


def _one(rows, fieldname, value):
    matches = [row for row in rows if row.get(fieldname) == value]
    return matches[0] if len(matches) == 1 else None


def _issue(issues, code):
    if code not in issues:
        issues.append(code)


def _number(value):
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _same_number(actual, expected):
    try:
        return Decimal(str(actual)) == Decimal(str(expected))
    except (InvalidOperation, TypeError, ValueError):
        return False


def _canonical_rows(rows):
    try:
        normalized = [dict(row) for row in (rows or [])]
        return json.dumps(
            sorted(normalized, key=lambda row: str(row.get("name") or "")),
            sort_keys=True,
            default=str,
        )
    except (TypeError, ValueError):
        return None
