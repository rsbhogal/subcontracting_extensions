"""J18D controlled reversal of one exact submitted component return.

Discovery is read-only.  Execution locks and revalidates the selected Stock
Entry, then delegates cancellation to ERPNext.  Repost Item Valuation work is
reported but never executed, edited or deleted here.
"""

from copy import deepcopy

from subcontracting_extensions.component_return_creation import (
    get_component_return_sources,
)
from subcontracting_extensions.component_return_submission import (
    _require_exact_stock_entry,
)
from subcontracting_extensions.material_reconciliation import _qty


CONTRACT_VERSION = "J18D"
ACTIVE_REPOST_STATUSES = ("Queued", "In Progress", "Failed")


def _lock(api, query, values, message):
    if not api.db.sql(query, values):
        api.throw(message)


def _row(report, sco_supplied_item):
    return next((row for row in report.get("components") or []
                 if row.get("sco_supplied_item") == sco_supplied_item), None)


def _reposts(api, stock_entry):
    return [dict(row) for row in api.get_all(
        "Repost Item Valuation",
        filters={"voucher_type": "Stock Entry", "voucher_no": stock_entry},
        fields=["name", "docstatus", "status", "based_on", "recreate_stock_ledgers"],
        order_by="creation asc, name asc",
        limit_page_length=0,
    )]


def _candidate(api, doc, identity, quantity, settlement_date):
    issues = []
    if doc.get("docstatus") != 1:
        issues.append("COMPONENT_RETURN_NOT_SUBMITTED")
    if quantity <= 0:
        issues.append("SUBMITTED_COMPONENT_RETURN_INVALID_QUANTITY")
    try:
        sources = get_component_return_sources(
            api, identity["sco_supplied_item"], identity,
            exclude_stock_entry=doc.name,
        )
        _require_exact_stock_entry(
            api, doc, identity, quantity, settlement_date, sources
        )
    except Exception:
        issues.append("SUBMITTED_COMPONENT_RETURN_IDENTITY_MISMATCH")
    return {
        "doctype": "Stock Entry",
        "name": doc.name,
        "docstatus": doc.get("docstatus"),
        "stock_qty": float(quantity),
        "sco_rm_detail": identity["sco_supplied_item"],
        "issues": issues,
        "repost_item_valuations": _reposts(api, doc.name),
    }


def read_component_return_reversal(api, report):
    """Discover exact submitted returns without changing any document."""
    result = deepcopy(report)
    lot = api.get_doc("Processor Lot", result.get("processor_lot"))
    lot.check_permission("read")
    item_based = int(api.db.get_single_value(
        "Stock Reposting Settings", "item_based_reposting"
    ) or 0)
    can_cancel = bool(
        lot.has_permission("write")
        and api.has_permission("Stock Entry", "cancel")
    )

    for row in result.get("components") or []:
        identity = row.get("component_return_identity") or {}
        row_name = row.get("sco_supplied_item")
        candidates = []
        if row_name and all(identity.get(field) for field in (
            "subcontracting_order", "company", "supplier", "source_warehouse",
            "target_warehouse", "component_item", "subcontracted_item",
            "stock_uom", "sco_supplied_item",
        )):
            parents = sorted({item.parent for item in api.get_all(
                "Stock Entry Detail",
                filters={
                    "sco_rm_detail": row_name,
                    "docstatus": 1,
                    "parenttype": "Stock Entry",
                    "parentfield": "items",
                },
                fields=["parent"],
                limit_page_length=0,
            )})
            for name in parents:
                doc = api.get_doc("Stock Entry", name)
                doc.check_permission("read")
                if not doc.get("is_return"):
                    continue
                matching = [item for item in doc.get("items") or []
                            if item.get("sco_rm_detail") == row_name]
                try:
                    quantity = (_qty(matching[0].get("transfer_qty"))
                                if len(matching) == 1 else _qty(0))
                except (TypeError, ValueError):
                    quantity = _qty(0)
                candidates.append(_candidate(
                    api, doc, identity, quantity, lot.get("settlement_date")
                ))

        row["submitted_component_returns"] = candidates
        row["component_return_reversal_item_based_reposting"] = item_based
        row["component_return_reversal_can_cancel"] = can_cancel
        row["component_return_target_stock_qty"] = float(_qty(
            api.db.get_value("Bin", {
                "item_code": identity.get("component_item"),
                "warehouse": identity.get("target_warehouse"),
            }, "actual_qty") or 0
        )) if identity else 0.0

    result.update(
        component_return_reversal_contract_version=CONTRACT_VERSION,
        component_return_reversal_item_based_reposting=item_based,
        component_return_reversal_can_cancel=can_cancel,
        component_return_reversal_enabled=False,
    )
    return result


def enable_component_return_reversal(report, *, enabled=False):
    """Expose reversal only for one clean submitted exact-row return."""
    result = deepcopy(report)
    any_action = False
    commercial_dependency = bool(
        result.get("adjustments") or result.get("settlement_evidence")
    )
    lifecycle_blocked = bool(
        result.get("processor_lot_docstatus") == 2
        or result.get("processor_lot_settlement_status") not in (None, "", "Draft")
    )
    for row in result.get("components") or []:
        observed = row.get("submitted_component_returns") or []
        candidates = [item for item in observed
                      if not item.get("issues")]
        if not observed:
            candidate_blocker = "NO_SUBMITTED_COMPONENT_RETURN"
        elif len(observed) > 1:
            candidate_blocker = "MULTIPLE_SUBMITTED_COMPONENT_RETURNS"
        elif len(candidates) != 1:
            candidate_blocker = "SUBMITTED_COMPONENT_RETURN_REQUIRES_REVIEW"
        else:
            candidate_blocker = None
        repost_blocked = any(
            repost.get("status") in ("In Progress", "Failed")
            for item in candidates
            for repost in item.get("repost_item_valuations") or []
            if repost.get("docstatus") == 1
        )
        quantity = _qty(candidates[0].get("stock_qty")) if len(candidates) == 1 else _qty(0)
        permitted = bool(
            enabled
            and row.get("component_return_reversal_can_cancel")
            and not row.get("component_return_reversal_item_based_reposting")
            and len(observed) == 1
            and len(candidates) == 1
            and quantity > 0
            and _qty(row.get("component_return_target_stock_qty")) >= quantity
            and not lifecycle_blocked
            and not commercial_dependency
            and not repost_blocked
        )
        row["component_return_reversal_action_available"] = permitted
        row["component_return_reversal_stock_entry"] = candidates[0]["name"] if permitted else None
        row["component_return_reversal_expected_qty"] = float(quantity) if permitted else None
        row["component_return_reversal_blockers"] = list(filter(None, (
            "ITEM_BASED_REPOSTING_NOT_SUPPORTED" if row.get("component_return_reversal_item_based_reposting") else None,
            "PROCESSOR_LOT_LIFECYCLE_BLOCKS_REVERSAL" if lifecycle_blocked else None,
            "COMMERCIAL_OR_SETTLEMENT_DEPENDENCY_EXISTS" if commercial_dependency else None,
            "REPOST_ITEM_VALUATION_REQUIRES_REVIEW" if repost_blocked else None,
            "TARGET_STOCK_INSUFFICIENT_FOR_REVERSAL" if quantity > 0 and _qty(row.get("component_return_target_stock_qty")) < quantity else None,
            candidate_blocker,
            "CANCEL_PERMISSION_REQUIRED" if not row.get("component_return_reversal_can_cancel") else None,
        )))
        any_action = any_action or permitted
    result.update(
        component_return_reversal_contract_version=CONTRACT_VERSION,
        component_return_reversal_enabled=bool(enabled),
        component_return_any_reversal_action_available=any_action,
        component_return_reversal_scope=(
            "Cancel only one selected exact submitted component return through "
            "ERPNext; report but never execute or mutate valuation reposting"
        ),
    )
    return result


def cancel_component_return(api, report_reader, processor_lot,
                            sco_supplied_item, stock_entry, expected_qty):
    """Lock, revalidate and natively cancel exactly one selected return."""
    if not all((processor_lot, sco_supplied_item, stock_entry)):
        api.throw("Processor Lot, SCO supplied row and Stock Entry are required")
    _lock(api, "select name from `tabProcessor Lot` where name=%s for update",
          (processor_lot,), "The selected Processor Lot no longer exists")
    _lock(api, """select name from `tabSubcontracting Order Supplied Item`
                   where name=%s for update""", (sco_supplied_item,),
          "The selected SCO component row no longer exists")
    _lock(api, "select name from `tabStock Entry` where name=%s for update",
          (stock_entry,), "The selected Stock Entry no longer exists")

    lot = api.get_doc("Processor Lot", processor_lot)
    lot.check_permission("write")
    if lot.get("docstatus") == 2 or lot.get("settlement_status") not in (None, "", "Draft"):
        api.throw("Processor Lot lifecycle no longer permits component return reversal")
    if int(api.db.get_single_value(
        "Stock Reposting Settings", "item_based_reposting"
    ) or 0):
        api.throw("Item-based valuation reposting is not supported for controlled reversal")

    selected = api.get_doc("Stock Entry", stock_entry)
    selected.check_permission("cancel")
    if selected.get("docstatus") not in (1, 2):
        api.throw("Only a submitted component return can be cancelled")

    report = report_reader(processor_lot)
    row = _row(report, sco_supplied_item)
    if not row:
        api.throw("The selected component does not belong to this Processor Lot")
    identity = row.get("component_return_identity") or {}
    required = ("subcontracting_order", "company", "supplier", "source_warehouse",
                "target_warehouse", "component_item", "subcontracted_item",
                "stock_uom", "sco_supplied_item")
    if any(not identity.get(field) for field in required):
        api.throw("Component return identity is incomplete")
    if identity.get("sco_supplied_item") != sco_supplied_item:
        api.throw("Component return identity changed")
    try:
        quantity = _qty(expected_qty)
    except (TypeError, ValueError):
        api.throw("The expected component return quantity is invalid")
    if quantity <= 0:
        api.throw("The expected component return quantity is invalid")

    item_master = api.get_cached_doc("Item", identity["component_item"])
    if item_master.get("has_serial_no") or item_master.get("has_batch_no"):
        api.throw("Serialized or batched component returns are not enabled at this checkpoint")

    sources = get_component_return_sources(
        api, sco_supplied_item, identity, exclude_stock_entry=stock_entry
    )
    _require_exact_stock_entry(
        api, selected, identity, quantity, lot.get("settlement_date"), sources
    )
    existing_reposts = _reposts(api, stock_entry)
    if any(item.get("docstatus") == 1 and item.get("status") in ("In Progress", "Failed")
           for item in existing_reposts):
        api.throw("Repost Item Valuation must be resolved before controlled reversal")
    if selected.get("docstatus") == 2:
        return {"status": "already_cancelled", "doctype": "Stock Entry",
                "name": selected.name, "cancelled": False,
                "repost_item_valuations": existing_reposts}

    if report.get("adjustments") or report.get("settlement_evidence"):
        api.throw("Commercial or settlement dependency blocks component return reversal")
    observed = row.get("submitted_component_returns") or []
    candidates = [item for item in observed
                  if not item.get("issues")]
    if len(observed) != 1 or len(candidates) != 1 or candidates[0].get("name") != stock_entry \
            or _qty(candidates[0].get("stock_qty")) != quantity:
        api.throw("The selected submitted component return is no longer uniquely reversible")

    for warehouse, label in ((identity["source_warehouse"], "source"),
                             (identity["target_warehouse"], "target")):
        _lock(api, """select name from `tabBin`
                       where item_code=%s and warehouse=%s for update""",
              (identity["component_item"], warehouse),
              "No current %s-stock row exists for this component return" % label)
    report = report_reader(processor_lot)
    row = _row(report, sco_supplied_item)
    if not row or _qty(row.get("component_return_target_stock_qty")) < quantity:
        api.throw("Current target stock is insufficient for component return reversal")

    selected.cancel()
    reposts = _reposts(api, stock_entry)
    pending = any(item.get("docstatus") == 1 and item.get("status") in ("Queued", "In Progress")
                  for item in reposts)
    failed = any(item.get("docstatus") == 1 and item.get("status") == "Failed"
                 for item in reposts)
    status = "cancelled_repost_failed" if failed else (
        "cancelled_repost_pending" if pending else "cancelled"
    )
    return {"status": status, "doctype": "Stock Entry", "name": selected.name,
            "cancelled": True, "repost_item_valuations": reposts}
