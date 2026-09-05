"""J13C console-only, read-only adapter for the J13B quantity engine.

No whitelist, hooks, UI, writes, commits, settlement or capacity decisions.
Call get_material_position(lot_name) explicitly from the isolated bench console.
All discovered evidence parents require read permission; failure aborts the read.
Native supplied_qty is gross; total_supplied_qty is only a cross-check.
"""

from subcontracting_extensions.material_reconciliation import _qty, reconcile_material


def get_material_position(processor_lot):
    import frappe

    return _read_material_position(frappe, processor_lot)


def _read_material_position(api, processor_lot):
    documents = {}
    issues = []

    def read(doctype, name):
        key = (doctype, name)
        if key not in documents:
            doc = api.get_doc(doctype, name)
            doc.check_permission("read")
            documents[key] = doc
        return documents[key]

    def names(doctype, filters, field="name"):
        # get_all avoids permission filtering silently omitting evidence.
        # Every discovered parent is checked before any report is returned.
        return {row[field] for row in api.get_all(
            doctype, filters=filters, fields=[field], limit_page_length=0
        )}

    def child_parents(doctype, parenttype, parentfield, filters):
        return names(doctype, dict(filters, parenttype=parenttype,
                                  parentfield=parentfield), "parent")

    def issue(code):
        if code not in issues:
            issues.append(code)

    lot = read("Processor Lot", processor_lot)
    sco = read("Subcontracting Order", lot.get("subcontracting_order"))
    if lot.get("docstatus") == 2 or sco.get("docstatus") != 1:
        raise ValueError("Material evidence requires an active lot and submitted SCO")
    for field in ("company", "supplier", "supplier_warehouse"):
        if not sco.get(field) or lot.get(field) != sco.get(field):
            raise ValueError("Processor Lot and SCO identity mismatch: " + field)

    normalized = {field: sco.get(field) for field in
                  ("name", "company", "supplier", "supplier_warehouse")}
    normalized["items"] = [row.as_dict() for row in sco.get("items", [])]
    normalized["supplied_items"] = [row.as_dict() for row in sco.get("supplied_items", [])]
    fg_names = [row["name"] for row in normalized["items"]]
    rm_names = [row["name"] for row in normalized["supplied_items"]]
    for row in normalized["supplied_items"]:
        if _qty(row.get("total_supplied_qty")) != (
                _qty(row.get("supplied_qty")) - _qty(row.get("returned_qty"))):
            issue("NATIVE_NET_SUPPLY_MISMATCH")

    # Header discovery includes returns regardless of purpose. Exact child-link
    # discovery also exposes movements whose SCO header is wrong or missing.
    transfers = names("Stock Entry", {"subcontracting_order": sco.name, "docstatus": 1})
    if rm_names:
        transfers |= child_parents("Stock Entry Detail", "Stock Entry", "items",
                                   {"sco_rm_detail": ["in", rm_names], "docstatus": 1})
    movements = []
    for name in sorted(transfers):
        doc = read("Stock Entry", name)
        if doc.get("docstatus") != 1:
            continue
        for row in doc.get("items", []):
            if doc.get("subcontracting_order") != sco.name and row.get("sco_rm_detail") not in rm_names:
                continue
            source, target = row.get("s_warehouse"), row.get("t_warehouse")
            returning = source == sco.supplier_warehouse and target and target != source
            sending = target == sco.supplier_warehouse and source and source != target
            if (returning and not doc.get("is_return")) or (sending and doc.get("is_return")):
                issue("MOVEMENT_RETURN_FLAG_MISMATCH")
            movements.append(dict(
                name=row.get("name"), parent=doc.name, docstatus=doc.docstatus,
                subcontracting_order=doc.get("subcontracting_order"),
                company=doc.get("company"), supplier=doc.get("supplier"),
                item_code=row.get("item_code"), stock_uom=row.get("stock_uom"),
                stock_qty=row.get("transfer_qty"), sco_rm_detail=row.get("sco_rm_detail"),
                s_warehouse=source, t_warehouse=target,
            ))

    scr_names = child_parents("Subcontracting Receipt Item", "Subcontracting Receipt", "items",
                              {"subcontracting_order": sco.name, "docstatus": 1})
    if fg_names:
        scr_names |= child_parents("Subcontracting Receipt Item", "Subcontracting Receipt", "items",
                                   {"subcontracting_order_item": ["in", fg_names], "docstatus": 1})
    # Include orphan consumption linked to this SCO, even without a valid FG row.
    scr_names |= child_parents("Subcontracting Receipt Supplied Item", "Subcontracting Receipt",
                               "supplied_items", {"subcontracting_order": sco.name, "docstatus": 1})
    receipts, consumptions = [], []
    for name in sorted(scr_names):
        doc = read("Subcontracting Receipt", name)
        if doc.get("docstatus") != 1:
            continue
        if any(doc.get(field) != sco.get(field) for field in ("company", "supplier", "supplier_warehouse")):
            issue("SCR_HEADER_MISMATCH")
            continue
        if doc.get("is_return"):
            # J13B deliberately does not net negative consumption. Even zero
            # consumption on an SCR return must not silently establish balance.
            issue("SCR_RETURN_REQUIRES_REVIEW")
        selected = [row for row in doc.get("items", []) if
                    row.get("subcontracting_order") == sco.name or
                    row.get("subcontracting_order_item") in fg_names]
        selected_names = {row.get("name") for row in selected}
        for row in selected:
            receipts.append(dict(name=row.get("name"), parent=doc.name, docstatus=doc.docstatus,
                subcontracting_order=row.get("subcontracting_order"),
                subcontracting_order_item=row.get("subcontracting_order_item"), item_code=row.get("item_code")))
        for row in doc.get("supplied_items", []):
            if row.get("subcontracting_order") == sco.name or row.get("reference_name") in selected_names:
                consumptions.append(dict(name=row.get("name"), parent=doc.name, docstatus=doc.docstatus,
                    subcontracting_order=row.get("subcontracting_order"), reference_name=row.get("reference_name"),
                    rm_item_code=row.get("rm_item_code"), stock_uom=row.get("stock_uom"),
                    consumed_qty=row.get("consumed_qty")))

    # Quantity reconciliation is SCO-scoped. Inspect all linked lots, including
    # historical ones, rather than silently treating shared-SCO evidence as local.
    lot_names = names("Processor Lot", {"subcontracting_order": sco.name}) | {lot.name}
    adjustments = []
    settlement_evidence = []

    def add_settlement_evidence(doctype, name, reason):
        if not any((row["doctype"], row["name"], row["reason"]) ==
                   (doctype, name, reason) for row in settlement_evidence):
            settlement_evidence.append(dict(doctype=doctype, name=name, reason=reason))
    for name in sorted(lot_names):
        related = read("Processor Lot", name)
        if related.get("debit_note"):
            debit = read("Purchase Invoice", related.get("debit_note"))
            if debit.docstatus != 2:
                add_settlement_evidence("Purchase Invoice", debit.name, "Debit Note")
        if related.get("settlement_status") not in (None, "", "Draft", "Reopened", "Cancelled"):
            add_settlement_evidence("Processor Lot", name, "Settlement state")

    plr_names = child_parents("Processor Lot Receipt Allocation", "Processor Lot Receipt", "lot_allocations",
                              {"processor_lot": ["in", sorted(lot_names)]})
    for name in sorted(plr_names):
        receipt = read("Processor Lot Receipt", name)
        if receipt.docstatus == 2:
            continue
        # Conservative at this checkpoint: credit on a related combined PLR
        # requires review, even when it may belong to a different receipt item.
        for credit in [receipt] + list(receipt.get("receipt_items", [])):
            if (credit.get("material_credit_stock_entry")
                    or _qty(credit.get("processor_material_credit_qty") or 0)
                    or credit.get("material_credit_status") not in (None, "", "Not Applicable")):
                adjustments.append(dict(doctype="Processor Lot Receipt", name=name,
                                        reason="Material credit evidence"))
                break
    account_names = names("Processor Material Account Entry", {"subcontracting_order": sco.name,
                                                               "docstatus": ["!=", 2]})
    account_names |= names("Processor Material Account Entry", {"processor_lot": ["in", sorted(lot_names)],
                                                                "docstatus": ["!=", 2]})
    if plr_names:
        account_names |= names("Processor Material Account Entry",
                               {"processor_lot_receipt": ["in", sorted(plr_names)], "docstatus": ["!=", 2]})
    for name in sorted(account_names):
        doc = read("Processor Material Account Entry", name)
        if doc.docstatus != 2:
            adjustments.append(dict(
                doctype="Processor Material Account Entry", name=name,
                docstatus=doc.get("docstatus"), entry_type=doc.get("entry_type"),
                account_direction=doc.get("account_direction"), account_qty=doc.get("account_qty"),
                subcontracting_order=doc.get("subcontracting_order"),
                sco_supplied_item=doc.get("sco_supplied_item"),
                principal_component=doc.get("principal_component"), account_uom=doc.get("account_uom"),
                processor_lot=doc.get("processor_lot"), is_reversed=doc.get("is_reversed"),
                against_entry=doc.get("against_entry"), reversal_of=doc.get("reversal_of"),
                application_stock_entry=doc.get("application_stock_entry"),
            ))
    for name in sorted(names("Purchase Invoice", {"custom_processor_lot_settlement": ["in", sorted(lot_names)],
                                                   "docstatus": ["!=", 2]})):
        doc = read("Purchase Invoice", name)
        if doc.docstatus != 2:
            add_settlement_evidence("Purchase Invoice", name, "Debit Note")

    application_entries = {row.get("application_stock_entry"): row["name"] for row in adjustments
                           if row.get("doctype") == "Processor Material Account Entry"
                           and row.get("docstatus") == 1 and row.get("entry_type") == "Credit Applied"
                           and row.get("application_stock_entry")}
    physical_movements = []
    for movement in movements:
        account_entry = application_entries.get(movement["parent"])
        if account_entry:
            movement["evidence_role"] = "Material credit application"
            movement["processor_material_account_entry"] = account_entry
        else:
            movement["evidence_role"] = "Physical transfer or return"
            physical_movements.append(movement)

    report = reconcile_material(normalized, physical_movements, receipts, consumptions, adjustments)
    if issues:
        report["issues"] = list(dict.fromkeys(report["issues"] + issues))
        report["evidence_consistent"] = report["material_balanced"] = report["material_accounted"] = False
        for row in report["components"]:
            row["issues"] = list(dict.fromkeys(row["issues"] + issues))
            row["evidence_consistent"] = row["material_balanced"] = row["material_accounted"] = False
    report.update(processor_lot=lot.name, subcontracting_order=sco.name,
                  evidence_scope="Entire SCO; quantity evidence only, not lot completion or settlement approval",
                  reader_issues=issues, movements=movements, receipts=receipts,
                  consumptions=consumptions, adjustments=adjustments,
                  settlement_evidence=settlement_evidence,
                  sources=[dict(doctype=dt, name=name, docstatus=doc.get("docstatus"))
                           for (dt, name), doc in sorted(documents.items())])
    return report
