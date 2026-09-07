"""J18C controlled submission of one selected component-return draft.

The caller names the existing Stock Entry.  This module never creates or
selects a replacement document and never performs commercial settlement or
lot/SCO closure.
"""

from copy import deepcopy

from subcontracting_extensions.component_return_creation import (
    get_component_return_sources,
    set_component_return_posting_datetime,
)
from subcontracting_extensions.material_reconciliation import _qty


CONTRACT_VERSION = "J18C"


def enable_component_return_submission(report, *, enabled=False):
    """Expose submission only for one clean, exact-row J18B draft."""
    result = deepcopy(report)
    any_action = False
    for row in result.get("components") or []:
        drafts = [draft for draft in row.get("draft_component_returns") or []
                  if not draft.get("issues")]
        reserved = _qty(row.get("draft_return_reserved_qty"))
        permitted = bool(
            enabled
            and row.get("component_return_code") == "OPEN_EXISTING_DRAFT_RETURN"
            and not row.get("component_return_blockers")
            and len(drafts) == 1
            and reserved > 0
        )
        row["component_return_submit_action_available"] = permitted
        row["component_return_submit_stock_entry"] = drafts[0]["name"] if permitted else None
        row["component_return_submit_expected_qty"] = float(reserved) if permitted else None
        any_action = any_action or permitted
    result.update(
        component_return_submission_contract_version=CONTRACT_VERSION,
        component_return_submission_enabled=bool(enabled),
        component_return_any_submit_action_available=any_action,
        component_return_submission_scope=(
            "Submit only the selected existing exact-row component-return draft; "
            "no commercial settlement or lifecycle closure"
        ),
    )
    return result


def _lock(api, query, values, message):
    if not api.db.sql(query, values):
        api.throw(message)


def _require_exact_stock_entry(api, stock_entry, identity, quantity,
                               settlement_date, sources):
    if len(stock_entry.get("items") or []) != 1:
        api.throw("The selected component return must contain exactly one item row")
    expected_header = {
        "purpose": "Material Transfer",
        "stock_entry_type": "Material Transfer",
        "is_return": 1,
        "company": identity["company"],
        "supplier": identity["supplier"],
        "subcontracting_order": identity["subcontracting_order"],
    }
    if any(stock_entry.get(field) != value for field, value in expected_header.items()):
        api.throw("The selected component return header no longer matches J18B")
    item = stock_entry.get("items")[0]
    expected_item = {
        "sco_rm_detail": identity["sco_supplied_item"],
        "item_code": identity["component_item"],
        "subcontracted_item": identity["subcontracted_item"],
        "uom": identity["stock_uom"],
        "stock_uom": identity["stock_uom"],
        "s_warehouse": identity["source_warehouse"],
        "t_warehouse": identity["target_warehouse"],
    }
    if any(item.get(field) != value for field, value in expected_item.items()):
        api.throw("The selected component return item no longer matches J18B")
    if _qty(item.get("conversion_factor")) != _qty(1):
        api.throw("The selected component return conversion factor changed")
    if _qty(item.get("transfer_qty")) != quantity or _qty(item.get("qty")) != quantity:
        api.throw("The selected component return quantity changed")

    references = [
        (row.get("link_doctype"), row.get("link_name"))
        for row in stock_entry.get("doc_references") or []
    ]
    expected_references = [("Stock Entry", source.name) for source in sources]
    if references != expected_references or len(references) != len(set(references)):
        api.throw("The selected component return original document references changed")

    expected_datetime = type("ExpectedPosting", (), {})()
    set_component_return_posting_datetime(
        expected_datetime, settlement_date, sources[-1]
    )
    if (
        stock_entry.get("posting_date") != expected_datetime.posting_date
        or stock_entry.get("posting_time") != expected_datetime.posting_time
        or int(bool(stock_entry.get("set_posting_time"))) != 1
    ):
        api.throw("The selected component return posting chronology changed")


def submit_component_return_draft(api, report_reader, processor_lot,
                                  sco_supplied_item, stock_entry, expected_qty):
    """Lock, revalidate and submit exactly one named existing draft."""
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
    if lot.get("docstatus") == 1 and lot.get("settlement_status") == "Completed":
        api.throw("Completed Processor Lots cannot submit component returns")
    if lot.get("settlement_status") not in (None, "", "Draft"):
        api.throw("Processor Lot settlement has already started")

    selected = api.get_doc("Stock Entry", stock_entry)
    selected.check_permission("submit")
    if selected.get("docstatus") == 2:
        api.throw("Cancelled component returns cannot be submitted")
    if selected.get("docstatus") not in (0, 1):
        api.throw("The selected Stock Entry has an invalid document status")

    report = report_reader(processor_lot)
    row = next((item for item in report.get("components") or []
                if item.get("sco_supplied_item") == sco_supplied_item), None)
    if not row:
        api.throw("The selected component does not belong to this Processor Lot")
    identity = row.get("component_return_identity") or {}
    required = ("subcontracting_order", "company", "supplier", "source_warehouse",
                "target_warehouse", "component_item", "subcontracted_item",
                "stock_uom", "sco_supplied_item")
    if any(not identity.get(field) for field in required):
        api.throw("Component return identity is incomplete")
    if identity["sco_supplied_item"] != sco_supplied_item:
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

    _lock(api, """select name from `tabBin`
                   where item_code=%s and warehouse=%s for update""",
          (identity["component_item"], identity["source_warehouse"]),
          "No current source-stock row exists for this component return")
    report = report_reader(processor_lot)
    row = next((item for item in report.get("components") or []
                if item.get("sco_supplied_item") == sco_supplied_item), None)
    if not row or (row.get("component_return_identity") or {}) != identity:
        api.throw("Component return identity changed while submission was being prepared")

    sources = get_component_return_sources(
        api, sco_supplied_item, identity, exclude_stock_entry=stock_entry
    )
    _require_exact_stock_entry(
        api, selected, identity, quantity, lot.get("settlement_date"), sources
    )

    if selected.get("docstatus") == 1:
        return {"status": "already_submitted", "doctype": "Stock Entry",
                "name": selected.name, "submitted": False}

    drafts = [draft for draft in row.get("draft_component_returns") or []
              if not draft.get("issues")]
    if (
        row.get("component_return_code") != "OPEN_EXISTING_DRAFT_RETURN"
        or row.get("component_return_blockers")
        or len(drafts) != 1
        or drafts[0].get("name") != stock_entry
        or _qty(row.get("draft_return_reserved_qty")) != quantity
    ):
        api.throw("The selected component return is no longer the one valid exact-row draft")
    if _qty(row.get("component_return_source_stock_qty")) < quantity:
        api.throw("Current source stock is insufficient for this component return")

    selected.submit()
    return {"status": "submitted", "doctype": "Stock Entry",
            "name": selected.name, "submitted": True}
