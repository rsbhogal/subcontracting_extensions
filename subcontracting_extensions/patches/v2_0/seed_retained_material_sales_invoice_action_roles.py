"""Seed J19C2 actions once without changing the J19C1 role decisions."""

import frappe

from subcontracting_extensions.settlement_action_authority import (
    DEFAULT_ACTION_ROLES, initial_action_rows,
)


NEW_ACTIONS = (
    "CREATE_DRAFT_RETAINED_MATERIAL_SALES_INVOICE",
    "SUBMIT_RETAINED_MATERIAL_SALES_INVOICE",
)


def execute():
    settings = frappe.get_single("Subcontracting Settlement Settings")
    configured = {row.action_code for row in (settings.get("controlled_action_roles") or [])}
    missing = set(NEW_ACTIONS) - configured
    if not missing:
        return
    roles = {role for code in missing for role in DEFAULT_ACTION_ROLES[code]}
    absent = sorted(role for role in roles if not frappe.db.exists("Role", role))
    if absent:
        frappe.throw("Cannot seed Sales Invoice authorities; missing Roles: " + ", ".join(absent))
    for row in initial_action_rows():
        if row["action_code"] in missing:
            settings.append("controlled_action_roles", row)
    settings.save(ignore_permissions=True)
