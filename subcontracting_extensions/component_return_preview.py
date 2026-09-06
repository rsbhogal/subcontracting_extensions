"""J18A read-only component return preview and draft reservation discovery.

No Stock Entry is created, changed, submitted, cancelled, or deleted here.
Draft rows are discovered by exact ``sco_rm_detail`` identity so malformed
drafts cannot disappear behind a correct SCO header or an item-code fallback.
"""

from copy import deepcopy

from subcontracting_extensions.material_reconciliation import _qty


CONTRACT_VERSION = "J18A"


def assess_component_return_preview(
    report,
    draft_rows=(),
    *,
    can_prepare=False,
):
    """Add exact-row return reservations and read-only preparation guidance."""
    result = deepcopy(report)
    drafts = list(draft_rows or [])
    known_rows = {
        row.get("sco_supplied_item")
        for row in result.get("components") or []
        if row.get("sco_supplied_item")
    }
    orphan_drafts = [
        row for row in drafts if row.get("sco_rm_detail") not in known_rows
    ]

    for component in result.get("components") or []:
        row_name = component.get("sco_supplied_item")
        matching = [row for row in drafts if row.get("sco_rm_detail") == row_name]
        observed, valid, blockers = [], [], []
        expected = {
            "processor_lot": result.get("processor_lot"),
            "subcontracting_order": result.get("subcontracting_order"),
            "company": result.get("company"),
            "supplier": result.get("supplier"),
            "source_warehouse": result.get("supplier_warehouse"),
            "target_warehouse": component.get("reserve_warehouse"),
            "component_item": component.get("component_item"),
            "subcontracted_item": component.get("component_return_subcontracted_item"),
            "stock_uom": component.get("stock_uom"),
            "sco_supplied_item": row_name,
        }

        if not all(expected.values()):
            blockers.append("INCOMPLETE_COMPONENT_RETURN_IDENTITY")
        if component.get("reserve_warehouse_disabled"):
            blockers.append("RESERVE_WAREHOUSE_DISABLED")
        if (
            component.get("reserve_warehouse_company")
            and component.get("reserve_warehouse_company") != result.get("company")
        ):
            blockers.append("RESERVE_WAREHOUSE_COMPANY_MISMATCH")
        if expected["target_warehouse"] == expected["source_warehouse"]:
            blockers.append("RETURN_WAREHOUSES_MUST_DIFFER")
        if (
            result.get("processor_lot_docstatus") == 1
            and result.get("processor_lot_settlement_status") == "Completed"
        ):
            blockers.append("PROCESSOR_LOT_ALREADY_COMPLETED")
        elif result.get("processor_lot_settlement_status") not in (None, "", "Draft"):
            blockers.append("PROCESSOR_LOT_SETTLEMENT_ALREADY_STARTED")

        for draft in matching:
            draft_issues = []
            for field in ("subcontracting_order", "company", "supplier"):
                if draft.get(field) != expected[field]:
                    draft_issues.append("DRAFT_RETURN_HEADER_MISMATCH")
                    break
            if (
                draft.get("item_code") != expected["component_item"]
                or draft.get("subcontracted_item") != expected["subcontracted_item"]
                or draft.get("stock_uom") != expected["stock_uom"]
            ):
                draft_issues.append("DRAFT_RETURN_ITEM_UOM_MISMATCH")
            if (
                draft.get("s_warehouse") != expected["source_warehouse"]
                or draft.get("t_warehouse") != expected["target_warehouse"]
                or draft.get("is_return") != 1
            ):
                draft_issues.append("DRAFT_RETURN_ROUTE_MISMATCH")
            try:
                quantity = _qty(draft.get("stock_qty"))
            except ValueError:
                quantity = _qty(0)
                draft_issues.append("DRAFT_RETURN_INVALID_QUANTITY")
            if quantity <= 0:
                draft_issues.append("DRAFT_RETURN_INVALID_QUANTITY")

            preview = dict(draft, stock_qty=float(quantity), issues=list(dict.fromkeys(draft_issues)))
            observed.append(preview)
            if preview["issues"]:
                blockers.extend(preview["issues"])
            else:
                valid.append(preview)

        physical = _qty(component.get("physical_remaining_qty"))
        unaccounted = _qty(component.get("unaccounted_remaining_qty"))
        reserved = sum((_qty(row.get("stock_qty")) for row in valid), _qty(0))
        available = unaccounted - reserved
        if available < 0:
            blockers.append("DRAFT_RETURN_EXCEEDS_UNACCOUNTED")
        if len(valid) > 1:
            blockers.append("MULTIPLE_ACTIVE_DRAFT_RETURNS")
        if "component_return_source_stock_qty" in component:
            source_stock = _qty(component.get("component_return_source_stock_qty"))
            stock_required = reserved if valid else max(available, _qty(0))
            if source_stock < stock_required:
                blockers.append("INSUFFICIENT_COMPONENT_RETURN_SOURCE_STOCK")
        blockers = list(dict.fromkeys(blockers))

        if blockers or component.get("evidence_consistent") is not True:
            code = "REVIEW_RETURN_EVIDENCE"
            label = "Component return blocked"
            detail = (
                "Correct the highlighted component evidence, identity, or "
                "draft return before any component return is prepared."
            )
        elif unaccounted <= 0 or physical <= 0:
            code = "NO_COMPONENT_RETURN_REQUIRED"
            label = "No component return required"
            detail = "No positive unaccounted physical balance is available for return."
        elif valid:
            code = "OPEN_EXISTING_DRAFT_RETURN"
            label = "Draft component return already exists"
            detail = "Review the existing draft Stock Entry; its quantity is reserved but is not submitted return evidence."
        elif not can_prepare:
            code = "READ_ONLY_COMPONENT_RETURN"
            label = "Component return requires permission"
            detail = "The material balance is returnable, but this user cannot prepare a Stock Entry."
        else:
            code = "READY_TO_PREPARE_COMPONENT_RETURN"
            label = "Component return can be prepared"
            detail = "A later explicit action may prepare one draft Stock Entry for this exact component row."

        component.update(
            component_return_contract_version=CONTRACT_VERSION,
            component_return_identity=expected,
            component_return_source_warehouse=expected["source_warehouse"],
            component_return_target_warehouse=expected["target_warehouse"],
            draft_return_reserved_qty=float(reserved),
            return_qty_available_to_prepare=float(max(available, _qty(0))),
            draft_component_returns=observed,
            component_return_blockers=blockers,
            component_return_code=code,
            component_return_label=label,
            component_return_detail=detail,
            component_return_prepare_permitted=bool(
                code == "READY_TO_PREPARE_COMPONENT_RETURN"
            ),
            component_return_action_available=False,
        )

    result.update(
        component_return_contract_version=CONTRACT_VERSION,
        component_return_preview_enabled=True,
        component_return_creation_enabled=False,
        component_return_submission_enabled=False,
        component_return_can_prepare=bool(can_prepare),
        orphan_draft_component_returns=orphan_drafts,
        component_return_scope=(
            "Read-only draft reservation preview by exact SCO supplied row; "
            "source is SCO Supplier Warehouse and target is that row's Reserve Warehouse"
        ),
    )
    return result


def read_component_return_preview(api, report):
    """Discover draft Stock Entry rows and authoritative warehouse facts."""
    result = deepcopy(report)
    components = result.get("components") or []
    row_names = [row.get("sco_supplied_item") for row in components if row.get("sco_supplied_item")]
    lot = api.get_doc("Processor Lot", result.get("processor_lot"))
    lot.check_permission("read")
    sco = api.get_doc("Subcontracting Order", result.get("subcontracting_order"))
    sco.check_permission("read")

    supplied = {row.name: row for row in sco.get("supplied_items", [])}
    for component in components:
        native = supplied.get(component.get("sco_supplied_item"))
        if native:
            component["reserve_warehouse"] = native.get("reserve_warehouse")
            component["component_return_subcontracted_item"] = native.get("main_item_code")
            if native.get("reserve_warehouse"):
                warehouse = api.get_doc("Warehouse", native.get("reserve_warehouse"))
                warehouse.check_permission("read")
                component["reserve_warehouse_company"] = warehouse.get("company")
                component["reserve_warehouse_disabled"] = bool(warehouse.get("disabled"))
        component["component_return_source_stock_qty"] = float(_qty(
            api.db.get_value(
                "Bin",
                {
                    "item_code": component.get("component_item"),
                    "warehouse": sco.get("supplier_warehouse"),
                },
                "actual_qty",
            ) or 0
        ))

    parent_names = set()
    if row_names:
        parent_names = {
            row.parent
            for row in api.get_all(
                "Stock Entry Detail",
                filters={
                    "sco_rm_detail": ["in", row_names],
                    "docstatus": 0,
                    "parenttype": "Stock Entry",
                    "parentfield": "items",
                },
                fields=["parent"],
                limit_page_length=0,
            )
        }

    drafts = []
    for name in sorted(parent_names):
        doc = api.get_doc("Stock Entry", name)
        doc.check_permission("read")
        if doc.get("docstatus") != 0:
            continue
        for row in doc.get("items", []):
            if row.get("sco_rm_detail") not in row_names:
                continue
            drafts.append({
                "doctype": "Stock Entry",
                "name": doc.name,
                "row_name": row.name,
                "docstatus": doc.get("docstatus"),
                "subcontracting_order": doc.get("subcontracting_order"),
                "company": doc.get("company"),
                "supplier": doc.get("supplier"),
                "is_return": int(bool(doc.get("is_return"))),
                "item_code": row.get("item_code"),
                "subcontracted_item": row.get("subcontracted_item"),
                "stock_uom": row.get("stock_uom"),
                "stock_qty": row.get("transfer_qty"),
                "sco_rm_detail": row.get("sco_rm_detail"),
                "s_warehouse": row.get("s_warehouse"),
                "t_warehouse": row.get("t_warehouse"),
            })

    result.update(
        company=sco.get("company"),
        supplier=sco.get("supplier"),
        supplier_warehouse=sco.get("supplier_warehouse"),
        processor_lot_docstatus=lot.get("docstatus"),
        processor_lot_settlement_status=lot.get("settlement_status"),
    )
    can_prepare = bool(
        lot.has_permission("write")
        and api.has_permission("Stock Entry", "create")
    )
    return assess_component_return_preview(result, drafts, can_prepare=can_prepare)
