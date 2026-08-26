# Copyright (c) 2026, Bhogals Private Limited
# For license information, please see license.txt

"""
Settlement Recommendation Engine for Processor Lot.

The engine interprets:

- Processor Lot Fact Engine output,
- Purchase Order settlement policy, and
- the selected business classification.

It does not:

- read the database,
- modify ERPNext documents,
- calculate monetary recovery,
- create accounting documents, or
- close a Processor Lot.

Recommendation Report v2
------------------------
The engine returns a structured, explainable report containing:

- verdict,
- recovery recommendations,
- reasoning,
- confidence,
- evidence, and
- integrity.

Legacy top-level result fields are retained temporarily as compatibility
aliases while the Processor Lot UI is developed around the v2 report.
"""

from __future__ import annotations

from typing import Any

from frappe import _


SUPPORTED_CLASSIFICATIONS = {
    "accepted_process_loss",
    "recoverable_shortage",
    "components_returned",
    "pending_investigation",
}


def recommend_settlement(
    facts: dict[str, Any],
    business_classification: str,
) -> dict[str, Any]:
    """
    Return a read-only and database-independent settlement recommendation.

    Parameters
    ----------
    facts:
        Complete output returned by the Processor Lot Fact Engine.

    business_classification:
        Business interpretation selected during reconciliation.

        Supported values:

        - accepted_process_loss
        - recoverable_shortage
        - components_returned
        - pending_investigation

    Returns
    -------
    dict
        Structured Recommendation Report containing verdict, recovery,
        reasoning, confidence, evidence and integrity.
    """
    facts = facts or {}
    business_classification = (
        business_classification or ""
    ).strip()

    warnings: list[dict[str, Any]] = []
    blocking_errors: list[dict[str, Any]] = []

    fact_integrity = facts.get("integrity") or {}
    settlement_policy = facts.get("settlement_policy") or {}
    summary = facts.get("summary") or {}

    physical_inventory = (
        summary.get("physical_inventory") or {}
    )

    outstanding_qty = _as_number(
        physical_inventory.get("outstanding_qty")
    )

    outstanding_uom = _get_outstanding_uom(
        facts=facts,
        physical_inventory=physical_inventory,
    )

    _collect_fact_integrity_errors(
        integrity=fact_integrity,
        blocking_errors=blocking_errors,
    )

    _validate_classification(
        business_classification=business_classification,
        blocking_errors=blocking_errors,
    )

    _validate_settlement_policy(
        settlement_policy=settlement_policy,
        blocking_errors=blocking_errors,
    )

    if (
        business_classification == "recoverable_shortage"
        and outstanding_qty <= 0
    ):
        warnings.append(
            {
                "code": "NO_OUTSTANDING_QTY",
                "message": _(
                    "Recoverable Shortage was selected, but the factual "
                    "outstanding quantity is not greater than zero."
                ),
            }
        )

    recommendation = _build_recommendation(
        business_classification=business_classification,
        settlement_policy=settlement_policy,
        outstanding_qty=outstanding_qty,
        outstanding_uom=outstanding_uom,
        blocking_errors=blocking_errors,
    )

    classification_label = _get_classification_label(
        business_classification
    )

    reasoning = _build_reasoning(
        business_classification=business_classification,
        classification_label=classification_label,
        settlement_policy=settlement_policy,
        outstanding_qty=outstanding_qty,
        outstanding_uom=outstanding_uom,
        recommendation=recommendation,
        blocking_errors=blocking_errors,
    )

    confidence = _build_confidence(
        warnings=warnings,
        blocking_errors=blocking_errors,
    )

    verdict = {
        "status": recommendation["recommendation_status"],
        "next_step": recommendation["recommended_next_step"],
        "classification": business_classification,
        "classification_label": classification_label,
        "settlement_basis": recommendation[
            "recommended_settlement_basis"
        ],
        "explanation": recommendation["explanation"],
    }

    recovery = {
        "raw_material": {
            "recommended": recommendation[
                "recommend_recover_raw_material"
            ],
            "policy_allows": bool(
                settlement_policy.get(
                    "recover_raw_material_shortage"
                )
            ),
            "evidence_source": "Purchase Order Settlement Policy",
        },
        "processing_charges": {
            "recommended": recommendation[
                "recommend_recover_processing_charges"
            ],
            "policy_allows": bool(
                settlement_policy.get(
                    "recover_processing_charges_on_shortage"
                )
            ),
            "evidence_source": "Purchase Order Settlement Policy",
        },
    }

    evidence = {
        "policy": {
            "policy_available": bool(
                settlement_policy.get("policy_available")
            ),
            "policy_source": settlement_policy.get(
                "policy_source"
            ),
            "purchase_order": settlement_policy.get(
                "purchase_order"
            ),
            "recover_raw_material_shortage": bool(
                settlement_policy.get(
                    "recover_raw_material_shortage"
                )
            ),
            "recover_processing_charges_on_shortage": bool(
                settlement_policy.get(
                    "recover_processing_charges_on_shortage"
                )
            ),
            "settlement_basis": settlement_policy.get(
                "settlement_basis"
            ),
        },
        "facts": {
            "outstanding_qty": outstanding_qty,
            "outstanding_uom": outstanding_uom,
        },
        "classification": {
            "value": business_classification,
            "label": classification_label,
        },
    }

    integrity = {
        "is_valid": not blocking_errors,
        "warnings": warnings,
        "blocking_errors": blocking_errors,
    }

    recommendation_report = {
        "report_version": 2,
        "verdict": verdict,
        "recovery": recovery,
        "reasoning": reasoning,
        "confidence": confidence,
        "evidence": evidence,
        "integrity": integrity,

        # Temporary compatibility aliases.
        "classification": business_classification,
        "classification_label": classification_label,
        "outstanding_qty": outstanding_qty,
        "outstanding_uom": outstanding_uom,
        "recommend_recover_raw_material": recovery[
            "raw_material"
        ]["recommended"],
        "recommend_recover_processing_charges": recovery[
            "processing_charges"
        ]["recommended"],
        "recommended_settlement_basis": verdict[
            "settlement_basis"
        ],
        "recommendation_status": verdict["status"],
        "recommended_next_step": verdict["next_step"],
        "explanation": verdict["explanation"],
        "policy": evidence["policy"],
    }

    return recommendation_report


def _collect_fact_integrity_errors(
    integrity: dict[str, Any],
    blocking_errors: list[dict[str, Any]],
) -> None:
    """
    Carry forward blocking errors reported by the Fact Engine.

    A recommendation must not be treated as reliable when the underlying
    factual position contains blocking integrity errors.
    """
    for error in integrity.get("blocking_errors") or []:
        blocking_errors.append(
            {
                "code": "FACT_ENGINE_BLOCKING_ERROR",
                "message": error.get(
                    "message",
                    _("The Fact Engine reported a blocking error."),
                ),
                "source_code": error.get("code"),
                "doctype": error.get("doctype"),
                "document": error.get("document"),
            }
        )


def _validate_classification(
    business_classification: str,
    blocking_errors: list[dict[str, Any]],
) -> None:
    """Validate the supplied business classification."""
    if not business_classification:
        blocking_errors.append(
            {
                "code": "CLASSIFICATION_REQUIRED",
                "message": _(
                    "A business classification is required before a "
                    "settlement recommendation can be prepared."
                ),
            }
        )
        return

    if business_classification not in SUPPORTED_CLASSIFICATIONS:
        blocking_errors.append(
            {
                "code": "UNSUPPORTED_CLASSIFICATION",
                "message": _(
                    "Business classification {0} is not supported."
                ).format(business_classification),
            }
        )


def _validate_settlement_policy(
    settlement_policy: dict[str, Any],
    blocking_errors: list[dict[str, Any]],
) -> None:
    """Validate whether a usable Purchase Order policy is available."""
    if not settlement_policy.get("policy_available"):
        blocking_errors.append(
            {
                "code": "SETTLEMENT_POLICY_UNAVAILABLE",
                "message": _(
                    "Settlement policy is not available from the "
                    "originating Purchase Order."
                ),
            }
        )
        return

    if not settlement_policy.get("settlement_basis"):
        blocking_errors.append(
            {
                "code": "SETTLEMENT_BASIS_MISSING",
                "message": _(
                    "Settlement Basis is not defined on the originating "
                    "Purchase Order."
                ),
            }
        )


def _build_recommendation(
    business_classification: str,
    settlement_policy: dict[str, Any],
    outstanding_qty: float,
    outstanding_uom: str,
    blocking_errors: list[dict[str, Any]],
) -> dict[str, Any]:
    """Interpret the business classification and contractual policy."""
    settlement_basis = settlement_policy.get(
        "settlement_basis"
    )

    if blocking_errors:
        return {
            "recommend_recover_raw_material": False,
            "recommend_recover_processing_charges": False,
            "recommended_settlement_basis": settlement_basis,
            "recommendation_status": "Blocked",
            "recommended_next_step": "Resolve blocking errors",
            "explanation": _(
                "A reliable settlement recommendation cannot be prepared "
                "until the blocking errors are resolved."
            ),
        }

    if business_classification == "accepted_process_loss":
        return {
            "recommend_recover_raw_material": False,
            "recommend_recover_processing_charges": False,
            "recommended_settlement_basis": settlement_basis,
            "recommendation_status": "No Recovery Recommended",
            "recommended_next_step": (
                "Review and accept process loss"
            ),
            "explanation": _(
                "The outstanding quantity has been confirmed as material consumed "
                "during normal processing and accepted as process loss. No raw "
                "material or processing-charge recovery is recommended."
            ),
        }

    if business_classification == "recoverable_shortage":
        recover_raw_material = bool(
            settlement_policy.get(
                "recover_raw_material_shortage"
            )
        )

        recover_processing_charges = bool(
            settlement_policy.get(
                "recover_processing_charges_on_shortage"
            )
        )

        recovery_recommended = (
            recover_raw_material
            or recover_processing_charges
        )

        return {
            "recommend_recover_raw_material": (
                recover_raw_material
            ),
            "recommend_recover_processing_charges": (
                recover_processing_charges
            ),
            "recommended_settlement_basis": settlement_basis,
            "recommendation_status": (
                "Recovery Recommended"
                if recovery_recommended
                else "No Recovery Recommended"
            ),
            "recommended_next_step": (
                "Calculate applicable recovery"
                if recovery_recommended
                else "Review lot for closure"
            ),
            "explanation": _build_shortage_explanation(
                outstanding_qty=outstanding_qty,
                outstanding_uom=outstanding_uom,
                recover_raw_material=recover_raw_material,
                recover_processing_charges=(
                    recover_processing_charges
                ),
                settlement_basis=settlement_basis,
            ),
        }

    if business_classification == "components_returned":
        return {
            "recommend_recover_raw_material": False,
            "recommend_recover_processing_charges": False,
            "recommended_settlement_basis": settlement_basis,
            "recommendation_status": (
                "Return Verification Required"
            ),
            "recommended_next_step": (
                "Verify Return of Components"
            ),
            "explanation": _(
                "The outstanding material has been confirmed as physically returned "
                "by the processor. No raw material shortage recovery is recommended. "
                "The corresponding Return of Components transaction should be "
                "verified before the lot is closed."
            ),
        }

    if business_classification == "pending_investigation":
        return {
            "recommend_recover_raw_material": False,
            "recommend_recover_processing_charges": False,
            "recommended_settlement_basis": settlement_basis,
            "recommendation_status": "Investigation Required",
            "recommended_next_step": "Complete investigation",
            "explanation": _(
                "The physical position has not yet been confirmed. No recovery or "
                "final settlement is recommended until the missing facts are "
                "resolved."
            ),
        }

    return {
        "recommend_recover_raw_material": False,
        "recommend_recover_processing_charges": False,
        "recommended_settlement_basis": settlement_basis,
        "recommendation_status": "Blocked",
        "recommended_next_step": "Review classification",
        "explanation": _(
            "No recommendation could be prepared for the selected "
            "business classification."
        ),
    }


def _build_reasoning(
    business_classification: str,
    classification_label: str,
    settlement_policy: dict[str, Any],
    outstanding_qty: float,
    outstanding_uom: str,
    recommendation: dict[str, Any],
    blocking_errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build structured and presentation-independent reasoning."""
    reasoning: list[dict[str, Any]] = []

    reasoning.append(
        {
            "sequence": 10,
            "type": "fact",
            "code": "OUTSTANDING_QUANTITY",
            "message": _(
                "Factual outstanding quantity: {0}{1}."
            ).format(
                _format_qty(outstanding_qty),
                f" {outstanding_uom}" if outstanding_uom else "",
            ),
        }
    )

    reasoning.append(
        {
            "sequence": 20,
            "type": "classification",
            "code": "PHYSICAL_REALITY",
            "message": _(
                "Confirmed physical reality: {0}."
            ).format(classification_label or _("Not selected")),
        }
    )

    if settlement_policy.get("policy_available"):
        reasoning.append(
            {
                "sequence": 30,
                "type": "policy",
                "code": "RAW_MATERIAL_POLICY",
                "message": (
                    _(
                        "The Purchase Order permits recovery of raw "
                        "material shortage."
                    )
                    if settlement_policy.get(
                        "recover_raw_material_shortage"
                    )
                    else _(
                        "The Purchase Order does not permit recovery of "
                        "raw material shortage."
                    )
                ),
            }
        )

        reasoning.append(
            {
                "sequence": 40,
                "type": "policy",
                "code": "PROCESSING_CHARGE_POLICY",
                "message": (
                    _(
                        "The Purchase Order permits recovery of processing "
                        "charges on shortage."
                    )
                    if settlement_policy.get(
                        "recover_processing_charges_on_shortage"
                    )
                    else _(
                        "The Purchase Order does not permit recovery of "
                        "processing charges on shortage."
                    )
                ),
            }
        )

        reasoning.append(
            {
                "sequence": 50,
                "type": "policy",
                "code": "SETTLEMENT_BASIS",
                "message": _(
                    "Settlement Basis: {0}."
                ).format(
                    settlement_policy.get("settlement_basis")
                    or _("Not defined")
                ),
            }
        )

    if blocking_errors:
        reasoning.append(
            {
                "sequence": 90,
                "type": "integrity",
                "code": "RECOMMENDATION_BLOCKED",
                "message": _(
                    "The recommendation is blocked until the reported "
                    "integrity errors are resolved."
                ),
            }
        )
    else:
        reasoning.append(
            {
                "sequence": 90,
                "type": "recommendation",
                "code": "RECOMMENDATION_RESULT",
                "message": recommendation["explanation"],
            }
        )

    return reasoning


def _build_confidence(
    warnings: list[dict[str, Any]],
    blocking_errors: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assess confidence in the recommendation report."""
    if blocking_errors:
        return {
            "level": "Low",
            "score": 0,
            "message": _(
                "The recommendation is blocked because required facts or "
                "contractual terms are incomplete or inconsistent."
            ),
        }

    if warnings:
        return {
            "level": "Medium",
            "score": 70,
            "message": _(
                "A recommendation was prepared, but one or more factual "
                "conditions require user review."
            ),
        }

    return {
        "level": "High",
        "score": 100,
        "message": _(
            "All required facts and contractual settlement terms are "
            "available."
        ),
    }


def _build_shortage_explanation(
    outstanding_qty: float,
    outstanding_uom: str,
    recover_raw_material: bool,
    recover_processing_charges: bool,
    settlement_basis: str | None,
) -> str:
    """
    Return a transparent explanation for material not returned.

    The explanation uses user-facing physical-reality terminology while
    retaining the stable internal classification used by the engine.
    """
    quantity_text = _format_qty(outstanding_qty)

    if outstanding_uom:
        quantity_text = f"{quantity_text} {outstanding_uom}"

    if recover_raw_material and recover_processing_charges:
        return _(
            "The factual outstanding quantity is {0}. As the material has "
            "not been returned by the processor, the Purchase Order "
            "settlement policy recommends recovery of both raw material "
            "value and processing charges. The applicable Settlement Basis "
            "is {1}."
        ).format(
            quantity_text,
            settlement_basis or _("not defined"),
        )

    if recover_raw_material:
        return _(
            "The factual outstanding quantity is {0}. As the material has "
            "not been returned by the processor, the Purchase Order "
            "settlement policy recommends recovery of raw material value. "
            "Processing charges are not recoverable under the policy. The "
            "applicable Settlement Basis is {1}."
        ).format(
            quantity_text,
            settlement_basis or _("not defined"),
        )

    if recover_processing_charges:
        return _(
            "The factual outstanding quantity is {0}. As the material has "
            "not been returned by the processor, the Purchase Order "
            "settlement policy recommends recovery of processing charges. "
            "Raw material value is not recoverable under the policy. The "
            "applicable Settlement Basis is {1}."
        ).format(
            quantity_text,
            settlement_basis or _("not defined"),
        )

    return _(
        "The factual outstanding quantity is {0}. The material has not "
        "been returned by the processor, but the Purchase Order settlement "
        "policy does not permit recovery of either raw material value or "
        "processing charges. The applicable Settlement Basis is {1}."
    ).format(
        quantity_text,
        settlement_basis or _("not defined"),
    )

def _get_classification_label(
    business_classification: str,
) -> str:
    """Return a user-facing physical-reality label."""
    labels = {
        "accepted_process_loss": _(
            "Consumed as Accepted Process Loss"
        ),
        "recoverable_shortage": _(
            "Material Not Returned by Processor"
        ),
        "components_returned": _(
            "Material Physically Returned"
        ),
        "pending_investigation": _(
            "Physical Position Not Yet Confirmed"
        ),
    }

    return labels.get(
        business_classification,
        business_classification or "",
    )


def _get_outstanding_uom(
    facts: dict[str, Any],
    physical_inventory: dict[str, Any],
) -> str:
    """Return the best available UOM without accessing the database."""
    candidates = [
        physical_inventory.get("outstanding_uom"),
        physical_inventory.get("stock_uom"),
        physical_inventory.get("uom"),
        (facts.get("receipt_summary") or {}).get(
            "receipt_stock_uom"
        ),
        (facts.get("summary") or {}).get("stock_uom"),
    ]

    for value in candidates:
        if value:
            return str(value)

    return ""


def _format_qty(value: Any) -> str:
    """Return a human-readable quantity without unnecessary zeroes."""
    number = _as_number(value)
    return f"{number:,.3f}".rstrip("0").rstrip(".")


def _as_number(value: Any) -> float:
    """Return a safe numeric value without ERPNext numeric utilities."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0