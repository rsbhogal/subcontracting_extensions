"""Pure J19B1C commercial-classification identity and decision policy."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy


POLICY_VERSION = "J19B1C"
RAW_MATERIAL = "Raw Material"
FINISHED_ITEM = "Finished Item"
SHORTAGE = "Shortage"
EXCESS = "Excess"

CLASSIFICATIONS = {
    SHORTAGE: {
        "PENDING_INVESTIGATION",
        "PROCESSOR_RESPONSIBLE",
        "COMPANY_RESPONSIBLE",
        "DISPUTED",
        "NO_COMMERCIAL_ACTION_REQUIRED",
    },
    EXCESS: {
        "PENDING_OWNERSHIP_INVESTIGATION",
        "COMPANY_OWNED",
        "SUPPLIER_OWNED",
        "MIXED_OR_UNRESOLVED",
        "NO_COMMERCIAL_ACTION_REQUIRED",
    },
}

UNRESOLVED_EXCESS = {
    "PENDING_OWNERSHIP_INVESTIGATION",
    "MIXED_OR_UNRESOLVED",
}

NO_ACTION_EVIDENCE_CODES = {
    "NO_RAW_MATERIAL_RECOVERY",
    "RAW_MATERIAL_CREDIT_ACCOUNTED",
    "NO_PROCESSING_RECOVERY",
    "PROCESSING_RECOVERY_POLICY_DISABLED",
}

EXCESS_METHODS_BY_CLASSIFICATION = {
    "PENDING_OWNERSHIP_INVESTIGATION": {"PENDING_OWNERSHIP_INVESTIGATION"},
    "MIXED_OR_UNRESOLVED": {"PENDING_OWNERSHIP_INVESTIGATION"},
    "COMPANY_OWNED": {
        "CARRY_FORWARD_TO_LOT",
        "REALLOCATE_COMPANY_MATERIAL",
        "ACCEPT_WITHOUT_ADDITIONAL_CHARGE",
        "RETURN_OR_REJECT_EXCESS",
    },
    "SUPPLIER_OWNED": {
        "SUPPLEMENTARY_SUBCONTRACTED_PO",
        "PURCHASE_SUPPLIER_OWNED_EXCESS",
        "RETURN_OR_REJECT_EXCESS",
    },
    "NO_COMMERCIAL_ACTION_REQUIRED": set(),
}


class CommercialClassificationError(ValueError):
    """Raised when a persisted commercial decision would be ambiguous."""


def canonical_scope(scope):
    """Return a normalized exact scope, rejecting mixed or incomplete identity."""
    row = deepcopy(scope or {})
    scope_type = row.get("scope_type")
    processor_lot = _required(row, "processor_lot")
    if scope_type == RAW_MATERIAL:
        values = {
            "scope_type": scope_type,
            "processor_lot": processor_lot,
            "sco_supplied_item": _required(row, "sco_supplied_item"),
            "sco_finished_item": _required(row, "sco_finished_item"),
            "component_item": _required(row, "component_item"),
            "stock_uom": _required(row, "stock_uom"),
            "purchase_order_item": None,
        }
    elif scope_type == FINISHED_ITEM:
        values = {
            "scope_type": scope_type,
            "processor_lot": processor_lot,
            "sco_supplied_item": None,
            "sco_finished_item": _required(row, "sco_finished_item"),
            "component_item": None,
            "stock_uom": row.get("stock_uom") or None,
            "purchase_order_item": _required(row, "purchase_order_item"),
        }
    else:
        raise CommercialClassificationError("Unsupported commercial scope type")
    return values


def make_scope_key(scope):
    """Return a stable digest; descriptive evidence is not part of finished identity."""
    row = canonical_scope(scope)
    if row["scope_type"] == RAW_MATERIAL:
        parts = [row[field] for field in (
            "scope_type", "processor_lot", "sco_supplied_item",
            "sco_finished_item", "component_item", "stock_uom",
        )]
    else:
        parts = [row[field] for field in (
            "scope_type", "processor_lot", "sco_finished_item", "purchase_order_item",
        )]
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


def make_event_key(scope_key, sequence):
    if not scope_key or not isinstance(sequence, int) or sequence < 1:
        raise CommercialClassificationError("A positive event sequence is required")
    return hashlib.sha256(f"{scope_key}|{sequence}".encode()).hexdigest()


def validate_classification(direction, classification):
    if classification not in CLASSIFICATIONS.get(direction, set()):
        raise CommercialClassificationError(
            f"Classification {classification or '(blank)'} is not valid for {direction or '(blank)'}"
        )
    return classification


def validate_classification_for_evidence(evidence_code, direction, classification):
    """Keep a persisted conclusion compatible with the authoritative evidence."""
    validate_classification(direction, classification)
    if (
        evidence_code in NO_ACTION_EVIDENCE_CODES
        and classification != "NO_COMMERCIAL_ACTION_REQUIRED"
    ):
        raise CommercialClassificationError(
            f"Evidence {evidence_code} permits only NO_COMMERCIAL_ACTION_REQUIRED"
        )
    return classification


def validate_treatment(direction, classification, method_code):
    """Validate classification compatibility in addition to catalogue direction."""
    validate_classification(direction, classification)
    if direction == EXCESS:
        permitted = EXCESS_METHODS_BY_CLASSIFICATION[classification]
        if method_code not in permitted:
            raise CommercialClassificationError(
                f"Settlement method {method_code or '(blank)'} is not valid for excess ownership {classification}"
            )
    elif classification in {"COMPANY_RESPONSIBLE", "NO_COMMERCIAL_ACTION_REQUIRED"}:
        raise CommercialClassificationError(
            "This shortage classification does not permit a settlement method"
        )
    return method_code


def validate_reason(reason):
    value = (reason or "").strip()
    if not value:
        raise CommercialClassificationError("A decision reason is required")
    return value


def _required(row, field):
    value = row.get(field)
    if value in (None, ""):
        raise CommercialClassificationError(f"Commercial scope requires {field}")
    return value
