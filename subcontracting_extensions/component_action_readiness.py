"""J17 component-scoped action-readiness contract; never performs writes.

This layer deliberately stops before commercial approval or document creation.
It gives the UI a stable, exact component identity and explains whether the
material evidence is fit for a later policy decision.  Debit Note versus Sales
Invoice, commercial posting, and lot closure remain explicitly deferred.
"""

from copy import deepcopy


CONTRACT_VERSION = "J17"
COMMERCIAL_POLICY_STATUS = "DEFERRED"


def assess_component_action_readiness(report, *, can_write=False):
    """Return an instrument-neutral, read-only readiness view.

    ``can_write`` records whether the current user may eventually take a
    controlled action on the Processor Lot.  It never enables an action at this
    checkpoint.  Exact SCO supplied-row identity and the row's own stock UOM
    are preserved independently for every component.
    """
    result = deepcopy(report)
    processor_lot = result.get("processor_lot")
    subcontracting_order = result.get("subcontracting_order")

    for row in result.get("components") or []:
        identity = {
            "processor_lot": processor_lot,
            "subcontracting_order": subcontracting_order,
            "sco_supplied_item": row.get("sco_supplied_item"),
            "sco_finished_item": row.get("sco_finished_item"),
            "component_item": row.get("component_item"),
            "stock_uom": row.get("stock_uom"),
        }
        identity_complete = all(
            identity.get(field)
            for field in (
                "processor_lot",
                "subcontracting_order",
                "sco_supplied_item",
                "sco_finished_item",
                "component_item",
                "stock_uom",
            )
        )

        row.update(
            component_action_contract_version=CONTRACT_VERSION,
            component_action_identity=identity,
            component_action_identity_complete=identity_complete,
            commercial_policy_status=COMMERCIAL_POLICY_STATUS,
            commercial_treatment_status="NOT_DETERMINED",
            commercial_treatment_label="Commercial treatment not determined",
            commercial_document_authorized=False,
            component_action_available=False,
            lot_closure_authorized=False,
        )

        if not identity_complete:
            row.update(
                commercial_review_permitted=False,
                component_action_code="REVIEW_COMPONENT_IDENTITY",
                component_action_label="Review component identity",
                component_action_detail=(
                    "Exact Processor Lot, SCO supplied-row, finished-row, "
                    "component, and stock-UOM identity is required."
                ),
            )
        elif row.get("evidence_consistent") is not True:
            row.update(
                commercial_review_permitted=False,
                component_action_code="REVIEW_COMPONENT_EVIDENCE",
                component_action_label="Review component evidence",
                component_action_detail=(
                    "Correct inconsistent component evidence before any "
                    "commercial treatment is considered."
                ),
            )
        elif row.get("material_settlement_eligible") is not True:
            row.update(
                commercial_review_permitted=False,
                component_action_code="ACCOUNT_REMAINING_MATERIAL",
                component_action_label="Account for remaining material",
                component_action_detail=(
                    "Complete the component's material accounting before any "
                    "commercial treatment is considered."
                ),
            )
        elif not can_write:
            row.update(
                commercial_review_permitted=False,
                component_action_code="READ_ONLY_ACCESS",
                component_action_label="Material position reconciled",
                component_action_detail=(
                    "Commercial treatment is not determined. This user has "
                    "read-only access to the Processor Lot."
                ),
            )
        else:
            row.update(
                commercial_review_permitted=True,
                component_action_code="AWAIT_COMMERCIAL_POLICY",
                component_action_label="Material position reconciled",
                component_action_detail=(
                    "Commercial treatment is not determined and no commercial "
                    "document is authorised at this checkpoint."
                ),
            )

    result.update(
        component_action_contract_version=CONTRACT_VERSION,
        commercial_policy_status=COMMERCIAL_POLICY_STATUS,
        commercial_review_permitted=bool(can_write),
        commercial_document_creation_enabled=False,
        commercial_document_authorized=False,
        lot_closure_authorized=False,
        component_action_scope=(
            "Read-only readiness by exact SCO supplied-component row and its "
            "own stock UOM; no commercial approval, accounting document, "
            "cross-UOM aggregation, or lot closure"
        ),
    )
    return result
