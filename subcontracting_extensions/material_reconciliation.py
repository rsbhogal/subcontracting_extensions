"""J13A pure quantity reconciliation; no Frappe calls, posting or settlement.

Inputs are normalized dictionaries. Movements require explicit stock_qty in
stock_uom (never transaction qty), document status and warehouse direction.
The future database adapter must supply complete evidence, including returns.
Missing/ambiguous evidence must not be apportioned by quantity or row order.
This module does not establish physical loss, recovery value or lot readiness.
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def _qty(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError("Non-finite quantity")
        return number.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError) as error:
        raise ValueError("Missing or invalid quantity") from error


def reconcile_material(sco, movements, receipts, consumptions, adjustments=()):
    """Reconcile by SCO supplied-row identity, within its own component UOM.

    sco: name, company, supplier, supplier_warehouse, items, supplied_items.
    movements: name, parent, docstatus, subcontracting_order, company, supplier,
      item_code, stock_uom, stock_qty, s_warehouse, t_warehouse; optional
      sco_rm_detail identifies the exact SCO supplied row. An invalid explicit
      link is never replaced by an item/UOM fallback.
    receipts: name (SCR item), parent, docstatus, subcontracting_order,
      subcontracting_order_item, item_code.
    consumptions: name, parent, docstatus, subcontracting_order, reference_name
      (SCR item), rm_item_code, stock_uom, consumed_qty.
    adjustments: any existing credit/settlement evidence blocks completion;
      attribution/netting is intentionally not implemented at J13A.
    """
    finished = {}
    for row in sco["items"]:
        if not row.get("name") or row["name"] in finished:
            raise ValueError("Missing or duplicate SCO finished-row identity")
        finished[row["name"]] = row
    components = {}
    identities = set()
    for row in sco["supplied_items"]:
        key = row.get("name")
        fg = row.get("reference_name")
        identity = (fg, row.get("rm_item_code"), row.get("stock_uom"))
        if not key or key in components or identity in identities:
            raise ValueError("Missing or duplicate component identity")
        if fg not in finished or not all(identity) or row.get("main_item_code") != finished[fg]["item_code"]:
            raise ValueError("Invalid component finished-row lineage")
        identities.add(identity)
        values = {field: _qty(row.get(field)) for field in ("supplied_qty", "consumed_qty", "returned_qty")}
        if any(value < 0 for value in values.values()):
            raise ValueError("Negative SCO component quantity")
        components[key] = dict(sco_supplied_item=key, sco_finished_item=fg,
            component_item=row["rm_item_code"], stock_uom=row["stock_uom"],
            **values, transferred_qty=Decimal(0), evidenced_returned_qty=Decimal(0),
            evidenced_consumed_qty=Decimal(0), issues=[])
    issues = []

    def problem(code, rows):
        if code not in issues:
            issues.append(code)
        for row in rows:
            if code not in row["issues"]:
                row["issues"].append(code)

    if not components:
        problem("NO_COMPONENTS", [])
    seen = set()
    for movement in movements:
        if movement.get("docstatus") != 1:
            continue
        identity = (movement.get("parent"), movement.get("name"))
        if not all(identity) or identity in seen:
            raise ValueError("Missing or duplicate movement identity")
        seen.add(identity)
        matches = [row for row in components.values() if
                   (row["component_item"], row["stock_uom"]) ==
                   (movement.get("item_code"), movement.get("stock_uom"))]
        if (movement.get("subcontracting_order"), movement.get("company"), movement.get("supplier")) != (
                sco["name"], sco["company"], sco["supplier"]):
            problem("MOVEMENT_HEADER_MISMATCH", matches)
            continue
        explicit = movement.get("sco_rm_detail")
        if explicit:
            linked = components.get(explicit)
            if linked is None:
                problem("INVALID_EXPLICIT_COMPONENT_LINK", matches)
                continue
            if (linked["component_item"], linked["stock_uom"]) != (
                    movement.get("item_code"), movement.get("stock_uom")):
                affected = matches + ([linked] if linked not in matches else [])
                problem("EXPLICIT_COMPONENT_ITEM_UOM_MISMATCH", affected)
                continue
            matches = [linked]
        if len(matches) != 1:
            problem("AMBIGUOUS_TRANSFER_ATTRIBUTION" if matches else "UNMATCHED_MOVEMENT", matches)
            continue
        row = matches[0]
        amount = _qty(movement.get("stock_qty"))
        if amount <= 0:
            problem("NONPOSITIVE_MOVEMENT_QUANTITY", [row])
            continue
        source, target = movement.get("s_warehouse"), movement.get("t_warehouse")
        warehouse = sco["supplier_warehouse"]
        if source and source != warehouse and target == warehouse:
            row["transferred_qty"] += amount
        elif source == warehouse and target and target != warehouse:
            row["evidenced_returned_qty"] += amount
        else:
            problem("UNSUPPORTED_MOVEMENT_DIRECTION", [row])

    receipt_rows = {}
    for receipt in receipts:
        if receipt.get("docstatus") != 1:
            continue
        key = (receipt.get("parent"), receipt.get("name"))
        if not all(key) or key in receipt_rows:
            raise ValueError("Missing or duplicate SCR finished-row identity")
        receipt_rows[key] = receipt
    seen = set()
    for consumption in consumptions:
        if consumption.get("docstatus") != 1:
            continue
        identity = (consumption.get("parent"), consumption.get("name"))
        if not all(identity) or identity in seen:
            raise ValueError("Missing or duplicate consumption identity")
        seen.add(identity)
        receipt = receipt_rows.get((consumption.get("parent"), consumption.get("reference_name")))
        fg = receipt.get("subcontracting_order_item") if receipt else None
        if (not receipt or consumption.get("subcontracting_order") != sco["name"]
                or receipt.get("subcontracting_order") != sco["name"] or fg not in finished
                or receipt.get("item_code") != finished[fg]["item_code"]):
            problem("CONSUMPTION_LINEAGE_MISMATCH", [])
            continue
        matches = [row for row in components.values() if
                   (row["sco_finished_item"], row["component_item"], row["stock_uom"]) ==
                   (fg, consumption.get("rm_item_code"), consumption.get("stock_uom"))]
        if len(matches) != 1:
            problem("UNMATCHED_CONSUMPTION", matches)
            continue
        amount = _qty(consumption.get("consumed_qty"))
        if amount < 0:
            problem("NEGATIVE_CONSUMPTION_REQUIRES_REVIEW", matches)
            continue
        matches[0]["evidenced_consumed_qty"] += amount

    if adjustments:
        problem("ADJUSTMENT_ATTRIBUTION_REQUIRES_REVIEW", list(components.values()))
    for row in components.values():
        for native, evidence, code in (
            ("supplied_qty", "transferred_qty", "SUPPLY_EVIDENCE_MISMATCH"),
            ("returned_qty", "evidenced_returned_qty", "RETURN_EVIDENCE_MISMATCH"),
            ("consumed_qty", "evidenced_consumed_qty", "CONSUMPTION_EVIDENCE_MISMATCH"),
        ):
            if row[native] != row[evidence]:
                problem(code, [row])
        row["remaining_qty"] = row["supplied_qty"] - row["consumed_qty"] - row["returned_qty"]
        row["evidence_remaining_qty"] = row["transferred_qty"] - row["evidenced_consumed_qty"] - row["evidenced_returned_qty"]
        if row["remaining_qty"] < 0 or row["evidence_remaining_qty"] < 0:
            problem("NEGATIVE_MATERIAL_BALANCE", [row])
    consistent = not issues
    for row in components.values():
        row["evidence_consistent"] = not row["issues"]
        if row["remaining_qty"] > 0:
            problem("MATERIAL_BALANCE_REMAINS", [row])
        row["material_balanced"] = not row["issues"]
        for key, value in row.items():
            if isinstance(value, Decimal):
                row[key] = float(value)
    return dict(components=list(components.values()), issues=issues,
        evidence_consistent=consistent, material_balanced=bool(components) and not issues,
        settlement_enabled=False,
        scope="Quantity evidence only; remaining material is not automatically a recoverable shortage")
