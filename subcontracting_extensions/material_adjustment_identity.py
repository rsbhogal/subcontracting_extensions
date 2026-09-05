"""Pure J15A resolver for exact SCO component-row attribution.

The persistent row name is document-local evidence. It is not part of the
portable Processor Material Account identity and must not be compared across
different SCOs.
"""


def resolve_sco_component(
    items,
    supplied_items,
    *,
    explicit=None,
    finished_row=None,
    processed_item=None,
    processed_uom=None,
    component_item=None,
    account_uom=None,
):
    """Return one exact supplied row or raise a fail-closed ValueError."""
    finished = {row.get("name"): row for row in items if row.get("name")}
    if len(finished) != len(items):
        raise ValueError("Missing or duplicate SCO finished-row identity")

    rows = [row for row in supplied_items if row.get("name")]
    if len(rows) != len(supplied_items) or len({row.get("name") for row in rows}) != len(rows):
        raise ValueError("Missing or duplicate SCO component-row identity")

    if explicit:
        matches = [row for row in rows if row.get("name") == explicit]
        if len(matches) != 1:
            raise ValueError("Explicit SCO component row does not exist")
        candidates = matches
    else:
        candidates = rows

    if finished_row:
        candidates = [row for row in candidates if row.get("reference_name") == finished_row]
    if processed_item or processed_uom:
        candidates = [row for row in candidates if (
            row.get("main_item_code"),
            finished.get(row.get("reference_name"), {}).get("stock_uom"),
        ) == (processed_item, processed_uom)]
    if component_item or account_uom:
        candidates = [row for row in candidates if (
            row.get("rm_item_code"), row.get("stock_uom")
        ) == (component_item, account_uom)]

    if len(candidates) != 1:
        raise ValueError(
            "SCO component attribution is ambiguous"
            if candidates else "SCO component attribution has no match"
        )
    row = candidates[0]
    fg = finished.get(row.get("reference_name"))
    if not fg or row.get("main_item_code") != fg.get("item_code"):
        raise ValueError("SCO component finished-row lineage is invalid")
    return row
