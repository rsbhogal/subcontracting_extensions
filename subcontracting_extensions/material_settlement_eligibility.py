"""Pure J16 material-quantity eligibility and guidance; never performs writes."""

from copy import deepcopy

from subcontracting_extensions.material_reconciliation import _qty


def assess_material_settlement(report):
    """Add component and SCO material-only eligibility to a reconciliation report."""
    result = deepcopy(report)
    components = result.get("components") or []
    for row in components:
        physical = _qty(row.get("physical_remaining_qty"))
        credit = _qty(row.get("applied_credit_qty"))
        unaccounted = _qty(row.get("unaccounted_remaining_qty"))
        if row.get("evidence_consistent") is not True or physical < 0 or unaccounted < 0:
            row.update(material_settlement_eligible=False,
                material_next_action="REVIEW_COMPONENT_EVIDENCE",
                material_next_action_label="Review component evidence",
                material_next_action_detail="Correct inconsistent transfer, consumption, return, or credit evidence before settlement review.")
        elif unaccounted > 0:
            row.update(material_settlement_eligible=False,
                material_next_action="ACCOUNT_REMAINING_MATERIAL",
                material_next_action_label="Account for remaining material",
                material_next_action_detail="Record a verified physical return, apply submitted material credit, or prepare the residual recovery separately.")
        elif physical > 0 and credit == physical:
            row.update(material_settlement_eligible=True,
                material_next_action="NONE_CREDIT_ACCOUNTED",
                material_next_action_label="No further material action",
                material_next_action_detail="Submitted material credit fully accounts for the physical balance; it does not record consumption or return.")
        elif physical == 0 and credit == 0:
            row.update(material_settlement_eligible=True,
                material_next_action="NONE_PHYSICALLY_RECONCILED",
                material_next_action_label="No further material action",
                material_next_action_detail="The component is physically reconciled through submitted consumption and return evidence.")
        else:
            row.update(material_settlement_eligible=False,
                material_next_action="REVIEW_COMPONENT_EVIDENCE",
                material_next_action_label="Review component evidence",
                material_next_action_detail="The component quantities do not form a supported settlement state.")

    if not components or result.get("evidence_consistent") is not True or any(
            row["material_next_action"] == "REVIEW_COMPONENT_EVIDENCE" for row in components):
        summary = dict(material_settlement_eligible=False,
            material_next_action="REVIEW_MATERIAL_EVIDENCE",
            material_next_action_label="Review material evidence",
            material_next_action_detail="Resolve the highlighted component evidence issues before taking any settlement action.")
    elif any(not row["material_settlement_eligible"] for row in components):
        summary = dict(material_settlement_eligible=False,
            material_next_action="ACCOUNT_REMAINING_MATERIAL",
            material_next_action_label="Account for remaining material",
            material_next_action_detail="Complete the indicated component actions. Keep quantities separate by component and UOM.")
    else:
        summary = dict(material_settlement_eligible=True,
            material_next_action="REVIEW_RECEIPT_AND_INVOICING",
            material_next_action_label="Review receipt and invoicing journey",
            material_next_action_detail="Material quantities are accounted for. Confirm receipt, invoicing, and commercial evidence before lot closure.")
    result.update(summary, settlement_enabled=False,
        settlement_eligibility_scope="Material quantities only; no settlement write, receipt completion, commercial approval, or lot closure")
    return result
