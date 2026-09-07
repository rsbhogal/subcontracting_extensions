"""J18B controlled creation of one exact-row draft component return.

This module never submits a Stock Entry.  Commercial settlement and lot/SCO
closure remain outside this contract.
"""

from copy import deepcopy
from datetime import timedelta

from subcontracting_extensions.material_reconciliation import _qty


CONTRACT_VERSION = "J18B"
ONE_DAY = timedelta(days=1)


def set_component_return_posting_datetime(target, settlement_date, source):
    """Use the later business date and exactly one second after source time."""
    target.set_posting_time = 1
    target.posting_date = max(settlement_date, source.posting_date)
    next_time = (source.posting_time or timedelta(0)) + timedelta(seconds=1)
    if next_time < ONE_DAY:
        target.posting_time = next_time
        return
    target.posting_date = target.posting_date + timedelta(days=1)
    target.posting_time = timedelta(0)


def get_component_return_sources(api, sco_supplied_item, identity,
                                 exclude_stock_entry=None):
    """Return every exact-row outgoing transfer eligible as ITC-04 source."""
    detail_rows = api.get_all(
        "Stock Entry Detail",
        filters={
            "sco_rm_detail": sco_supplied_item,
            "docstatus": 1,
            "parenttype": "Stock Entry",
            "parentfield": "items",
        },
        fields=["parent", "s_warehouse", "t_warehouse"],
        limit_page_length=0,
    )
    names = sorted({
        row.parent for row in detail_rows
        if row.parent
        and row.parent != exclude_stock_entry
        and row.s_warehouse
        and row.s_warehouse != identity["source_warehouse"]
        and row.t_warehouse == identity["source_warehouse"]
    })
    sources = api.get_all(
        "Stock Entry",
        filters={"name": ["in", names], "docstatus": 1},
        fields=["name", "posting_date", "posting_time", "is_return",
                "subcontracting_order", "company", "supplier"],
        order_by="posting_date asc, posting_time asc, creation asc, name asc",
        limit_page_length=0,
    ) if names else []
    valid = [source for source in sources if
             not source.is_return
             and source.subcontracting_order == identity["subcontracting_order"]
             and source.company == identity["company"]
             and source.supplier == identity["supplier"]]
    if {source.name for source in valid} != set(names):
        api.throw("Exact-row source Stock Entry identity is inconsistent")
    if not valid:
        api.throw("No submitted exact-row Stock Entry source was found")
    return valid


def enable_component_return_creation(report, *, enabled=False):
    """Expose an action only for rows already proven ready by J18A."""
    result = deepcopy(report)
    any_action = False
    for row in result.get("components") or []:
        available = _qty(row.get("return_qty_available_to_prepare"))
        permitted = bool(
            enabled
            and row.get("component_return_code") == "READY_TO_PREPARE_COMPONENT_RETURN"
            and row.get("component_return_prepare_permitted")
            and available > 0
        )
        row["component_return_action_available"] = permitted
        row["component_return_expected_qty"] = float(available) if permitted else None
        any_action = any_action or permitted

    result.update(
        component_return_execution_contract_version=CONTRACT_VERSION,
        component_return_creation_enabled=bool(enabled),
        component_return_submission_enabled=False,
        component_return_any_action_available=any_action,
        component_return_execution_scope=(
            "Create one unsubmitted Stock Entry for the full authoritative "
            "quantity of one exact SCO supplied row"
        ),
    )
    return result


def create_component_return_draft(api, report_reader, processor_lot,
                                  sco_supplied_item, expected_qty):
    """Lock, re-read, and insert or return one exact-row draft Stock Entry."""
    lot = api.get_doc("Processor Lot", processor_lot)
    lot.check_permission("write")
    if lot.get("docstatus") == 1 and lot.get("settlement_status") == "Completed":
        api.throw("Completed Processor Lots cannot prepare component returns")
    if lot.get("settlement_status") not in (None, "", "Draft"):
        api.throw("Processor Lot settlement has already started")
    if not api.has_permission("Stock Entry", "create"):
        api.throw("Not permitted to create Stock Entry", api.PermissionError)

    # The child-row lock serialises competing clicks for this exact identity.
    locked = api.db.sql(
        """select name from `tabSubcontracting Order Supplied Item`
           where name=%s for update""",
        (sco_supplied_item,),
    )
    if not locked:
        api.throw("The selected SCO component row no longer exists")

    report = report_reader(processor_lot)
    row = next((item for item in report.get("components") or []
                if item.get("sco_supplied_item") == sco_supplied_item), None)
    if not row:
        api.throw("The selected component does not belong to this Processor Lot")

    drafts = [item for item in row.get("draft_component_returns") or []
              if not item.get("issues")]
    if row.get("component_return_code") == "OPEN_EXISTING_DRAFT_RETURN" and len(drafts) == 1:
        return {"status": "existing", "doctype": "Stock Entry",
                "name": drafts[0]["name"], "created": False}

    if row.get("component_return_code") != "READY_TO_PREPARE_COMPONENT_RETURN":
        api.throw("Component return is no longer ready to prepare; refresh the material panel")

    authoritative_qty = _qty(row.get("return_qty_available_to_prepare"))
    try:
        requested_qty = _qty(expected_qty)
    except (TypeError, ValueError):
        api.throw("The expected component return quantity is invalid")
    if requested_qty <= 0 or requested_qty != authoritative_qty:
        api.throw("Component return quantity changed; refresh the material panel")

    identity = row.get("component_return_identity") or {}
    required = ("subcontracting_order", "company", "supplier", "source_warehouse",
                "target_warehouse", "component_item", "subcontracted_item", "stock_uom",
                "sco_supplied_item")
    if any(not identity.get(field) for field in required):
        api.throw("Component return identity is incomplete")

    item = api.get_cached_doc("Item", identity["component_item"])
    if item.get("has_serial_no") or item.get("has_batch_no"):
        api.throw("Serialized or batched component returns are not enabled at this checkpoint")

    stock_entry = api.new_doc("Stock Entry")
    stock_entry.update({
        "purpose": "Material Transfer",
        "is_return": 1,
        "company": identity["company"],
        "supplier": identity["supplier"],
        "subcontracting_order": identity["subcontracting_order"],
    })
    stock_entry.append("items", {
        "item_code": identity["component_item"],
        "qty": float(authoritative_qty),
        "uom": identity["stock_uom"],
        "stock_uom": identity["stock_uom"],
        "conversion_factor": 1,
        "s_warehouse": identity["source_warehouse"],
        "t_warehouse": identity["target_warehouse"],
        "sco_rm_detail": identity["sco_supplied_item"],
        "subcontracted_item": identity["subcontracted_item"],
    })
    stock_entry.set_posting_time = 1
    stock_entry.posting_date = lot.get("settlement_date")
    sources = get_component_return_sources(
        api, identity["sco_supplied_item"], identity
    )
    set_component_return_posting_datetime(
        stock_entry, lot.get("settlement_date"), sources[-1]
    )
    for source in sources:
        stock_entry.append("doc_references", {
            "link_doctype": "Stock Entry",
            "link_name": source.name,
        })
    stock_entry.set_stock_entry_type()
    stock_entry.insert()
    return {"status": "created", "doctype": "Stock Entry",
            "name": stock_entry.name, "created": True}
