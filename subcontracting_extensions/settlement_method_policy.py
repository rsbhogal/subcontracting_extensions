"""Pure J19B1 settlement-method catalogue and settings validation."""

from copy import deepcopy


POLICY_VERSION = "J19B1"


class SettlementMethodPolicyError(ValueError):
    """Raised when an administrator supplies an unsafe settings contract."""


METHODS = (
    {
        "method_code": "PURCHASE_DEBIT_NOTE",
        "method_label": "Purchase Debit Note",
        "variance_direction": "Shortage",
        "requires_customer": False,
        "stock_treatment": "No direct stock update",
        "accounting_document_type": "Purchase Invoice Return",
        "initially_enabled": True,
    },
    {
        "method_code": "SALES_INVOICE",
        "method_label": "Sales Invoice",
        "variance_direction": "Shortage",
        "requires_customer": True,
        "stock_treatment": "No direct stock update",
        "accounting_document_type": "Sales Invoice",
        "initially_enabled": True,
    },
    {
        "method_code": "SUPPLIER_CREDIT_NOTE",
        "method_label": "Supplier Credit Note",
        "variance_direction": "Shortage",
        "requires_customer": False,
        "stock_treatment": "Evidence only",
        "accounting_document_type": "Supplier Credit Note Evidence",
        "initially_enabled": True,
    },
    {
        "method_code": "COMPONENT_RETURN",
        "method_label": "Component Return",
        "variance_direction": "Shortage",
        "requires_customer": False,
        "stock_treatment": "Controlled component stock return",
        "accounting_document_type": "Stock Entry",
        "initially_enabled": True,
    },
    {
        "method_code": "COMMERCIAL_WAIVER",
        "method_label": "Commercial Waiver",
        "variance_direction": "Shortage",
        "requires_customer": False,
        "stock_treatment": "No stock update",
        "accounting_document_type": "No accounting document",
        "initially_enabled": False,
    },
    {
        "method_code": "PENDING_INVESTIGATION",
        "method_label": "Pending Investigation",
        "variance_direction": "Shortage",
        "requires_customer": False,
        "stock_treatment": "Blocked pending investigation",
        "accounting_document_type": "No accounting document",
        "initially_enabled": True,
        "initially_default": True,
    },
    {
        "method_code": "CARRY_FORWARD_TO_LOT",
        "method_label": "Carry Forward to Another Lot",
        "variance_direction": "Excess",
        "requires_customer": False,
        "stock_treatment": "Controlled lot allocation",
        "accounting_document_type": "No accounting document",
        "initially_enabled": True,
    },
    {
        "method_code": "REALLOCATE_COMPANY_MATERIAL",
        "method_label": "Reallocate Bhogals-Owned Material",
        "variance_direction": "Excess",
        "requires_customer": False,
        "stock_treatment": "Controlled company-material reallocation",
        "accounting_document_type": "No accounting document",
        "initially_enabled": False,
    },
    {
        "method_code": "SUPPLEMENTARY_SUBCONTRACTED_PO",
        "method_label": "Supplementary Subcontracted Purchase Order",
        "variance_direction": "Excess",
        "requires_customer": False,
        "stock_treatment": "Future receipt through subcontracting flow",
        "accounting_document_type": "Purchase Order",
        "initially_enabled": True,
    },
    {
        "method_code": "PURCHASE_SUPPLIER_OWNED_EXCESS",
        "method_label": "Purchase Supplier-Owned Excess",
        "variance_direction": "Excess",
        "requires_customer": False,
        "stock_treatment": "Future receipt through normal Purchase Receipt",
        "accounting_document_type": "Purchase Order",
        "initially_enabled": True,
    },
    {
        "method_code": "ACCEPT_WITHOUT_ADDITIONAL_CHARGE",
        "method_label": "Accept Without Additional Charge",
        "variance_direction": "Excess",
        "requires_customer": False,
        "stock_treatment": "Requires verified ownership and valuation",
        "accounting_document_type": "No accounting document",
        "initially_enabled": False,
    },
    {
        "method_code": "RETURN_OR_REJECT_EXCESS",
        "method_label": "Return or Reject Excess",
        "variance_direction": "Excess",
        "requires_customer": False,
        "stock_treatment": "Controlled excess return",
        "accounting_document_type": "Controlled Return",
        "initially_enabled": True,
    },
    {
        "method_code": "PENDING_OWNERSHIP_INVESTIGATION",
        "method_label": "Pending Ownership Investigation",
        "variance_direction": "Excess",
        "requires_customer": False,
        "stock_treatment": "Blocked pending ownership investigation",
        "accounting_document_type": "No accounting document",
        "initially_enabled": True,
        "initially_default": True,
    },
)


METHOD_BY_CODE = {row["method_code"]: row for row in METHODS}


def initial_method_rows():
    """Return the safe initial catalogue without sharing mutable state."""
    return [_normalized_row(method, {}) for method in METHODS]


def normalize_method_rows(rows):
    """Restore fixed metadata and validate administrator-controlled choices."""
    supplied = {}
    for raw in rows or []:
        row = _as_dict(raw)
        code = row.get("method_code")
        if code not in METHOD_BY_CODE:
            raise SettlementMethodPolicyError(
                f"Unknown subcontracting settlement method: {code or '(blank)'}"
            )
        if code in supplied:
            raise SettlementMethodPolicyError(
                f"Duplicate subcontracting settlement method: {code}"
            )
        supplied[code] = row

    if not supplied:
        normalized = initial_method_rows()
    else:
        normalized = [
            _normalized_row(method, supplied.get(method["method_code"], {}),
                missing=method["method_code"] not in supplied)
            for method in METHODS
        ]

    _validate_defaults(normalized)
    return normalized


def enabled_methods(rows, direction=None):
    """Return detached enabled method contracts, optionally by direction."""
    normalized = normalize_method_rows(rows)
    return [deepcopy(row) for row in normalized
            if row["enabled"] and (not direction or row["variance_direction"] == direction)]


def get_method_contract(rows, method_code, direction=None, *, require_enabled=True):
    """Return one validated configured method contract."""
    configured = {row["method_code"]: row for row in normalize_method_rows(rows)}
    method = configured.get(method_code)
    if not method:
        raise SettlementMethodPolicyError(
            f"Unknown subcontracting settlement method: {method_code or '(blank)'}"
        )
    if direction and method["variance_direction"] != direction:
        raise SettlementMethodPolicyError(
            f"Settlement method {method_code} is not valid for {direction} settlement"
        )
    if require_enabled and not method["enabled"]:
        raise SettlementMethodPolicyError(
            f"Subcontracting settlement method is disabled: {method_code}"
        )
    return deepcopy(method)


def get_default_method(rows, direction):
    """Return the single enabled default contract for a variance direction."""
    defaults = [row for row in normalize_method_rows(rows)
                if row["variance_direction"] == direction and row["is_default"]]
    if len(defaults) != 1 or not defaults[0]["enabled"]:
        raise SettlementMethodPolicyError(
            f"Exactly one enabled default is required for {direction} settlement"
        )
    return deepcopy(defaults[0])


def _normalized_row(method, supplied, missing=False):
    enabled = False if missing else _flag(
        supplied.get("enabled", method.get("initially_enabled", False))
    )
    default = False if missing else _flag(
        supplied.get("is_default", method.get("initially_default", False))
    )
    return {
        "enabled": enabled,
        "is_default": default,
        "method_code": method["method_code"],
        "method_label": method["method_label"],
        "variance_direction": method["variance_direction"],
        "approval_role": supplied.get("approval_role") or None,
        "requires_customer": bool(method["requires_customer"]),
        "stock_treatment": method["stock_treatment"],
        "accounting_document_type": method["accounting_document_type"],
    }


def _validate_defaults(rows):
    for row in rows:
        if row["is_default"] and not row["enabled"]:
            raise SettlementMethodPolicyError(
                f"Default settlement method must be enabled: {row['method_code']}"
            )
    for direction in ("Shortage", "Excess"):
        defaults = [
            row for row in rows
            if row["variance_direction"] == direction and row["is_default"]
        ]
        if len(defaults) != 1:
            raise SettlementMethodPolicyError(
                f"Exactly one enabled default is required for {direction} settlement"
            )


def _as_dict(row):
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "as_dict"):
        return dict(row.as_dict())
    return {key: getattr(row, key, None) for key in (
        "enabled", "is_default", "method_code", "approval_role"
    )}


def _flag(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)
