"""J19C1 controlled-settlement action authority, separate from method approval."""

import frappe


class SettlementActionAuthorityError(ValueError):
    pass


ACTION_LABELS = {
    "CREATE_DRAFT_DEBIT_NOTE": "Create Draft Debit Note",
    "SUBMIT_SETTLEMENT_DEBIT_NOTE": "Submit Settlement Debit Note",
    "CARRY_FORWARD_OBLIGATIONS": "Carry Forward Obligations",
    "RECORD_OBLIGATION_SETTLEMENT": "Record Obligation Settlement",
    "APPROVE_WAIVER": "Approve Waiver",
    "REVERSE_OBLIGATION_ENTRY": "Reverse Obligation Entry",
    "CREATE_DRAFT_RETAINED_MATERIAL_SALES_INVOICE": "Create Retained-Material Sales Invoice Draft",
    "SUBMIT_RETAINED_MATERIAL_SALES_INVOICE": "Submit Retained-Material Sales Invoice",
}

DEFAULT_ACTION_ROLES = {
    "CREATE_DRAFT_DEBIT_NOTE": (
        "Purchase Manager", "Accounts User", "Accounts Manager", "System Manager",
    ),
    "SUBMIT_SETTLEMENT_DEBIT_NOTE": (
        "Accounts User", "Accounts Manager", "System Manager",
    ),
    "CARRY_FORWARD_OBLIGATIONS": ("Purchase Manager", "System Manager"),
    "RECORD_OBLIGATION_SETTLEMENT": ("Accounts Manager", "System Manager"),
    "APPROVE_WAIVER": ("Director - Sales", "System Manager"),
    "REVERSE_OBLIGATION_ENTRY": ("System Manager",),
    "CREATE_DRAFT_RETAINED_MATERIAL_SALES_INVOICE": ("System Manager",),
    "SUBMIT_RETAINED_MATERIAL_SALES_INVOICE": ("System Manager",),
}


def initial_action_rows():
    return [
        {"action_code": code, "action_label": ACTION_LABELS[code],
         "role": role, "enabled": 1}
        for code, roles in DEFAULT_ACTION_ROLES.items() for role in roles
    ]


def normalize_action_rows(rows, *, role_exists=None):
    """Validate settings without restoring intentionally removed authorities."""
    result, seen = [], set()
    for raw in rows or ():
        get = raw.get if hasattr(raw, "get") else lambda key: getattr(raw, key, None)
        code, role = get("action_code"), get("role")
        if code not in ACTION_LABELS:
            raise SettlementActionAuthorityError(f"Unknown settlement action: {code or '(blank)'}")
        if not role or not isinstance(role, str) or role != role.strip():
            raise SettlementActionAuthorityError(f"A valid Role is required for {code}")
        if (code, role) in seen:
            raise SettlementActionAuthorityError(f"Duplicate settlement action role: {code} / {role}")
        if role_exists is not None and not role_exists(role):
            raise SettlementActionAuthorityError(f"Unknown Role for {code}: {role}")
        seen.add((code, role))
        enabled = get("enabled")
        if enabled not in (0, 1, False, True, "0", "1"):
            raise SettlementActionAuthorityError(f"Invalid enabled value for {code} / {role}")
        result.append({"action_code": code, "action_label": ACTION_LABELS[code],
                       "role": role, "enabled": int(enabled)})
    return result


def allowed_for_roles(rows, action_code, roles):
    if action_code not in ACTION_LABELS:
        raise SettlementActionAuthorityError(f"Unknown settlement action: {action_code or '(blank)'}")
    configured = normalize_action_rows(rows)
    return any(row["enabled"] and row["action_code"] == action_code
               and row["role"] in roles for row in configured)


def can_settlement_action(action_code, *, user=None, api=None):
    """UI aid only; mutating services must call require_settlement_action."""
    if action_code not in ACTION_LABELS:
        raise SettlementActionAuthorityError(f"Unknown settlement action: {action_code or '(blank)'}")
    context = api or frappe
    settings = context.get_single("Subcontracting Settlement Settings")
    return allowed_for_roles(
        settings.get("controlled_action_roles"), action_code,
        context.get_roles(user or context.session.user),
    )


def require_settlement_action(action_code, *, user=None, api=None):
    if not can_settlement_action(action_code, user=user, api=api):
        raise frappe.PermissionError(f"Controlled settlement action requires authority: {action_code}")
