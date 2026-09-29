"""One-time seed of the Purchase handoff action; preserve configured decisions."""

import frappe

from subcontracting_extensions.settlement_action_authority import initial_action_rows


ACTION = "RELEASE_RETAINED_MATERIAL_INVOICE_PREPARATION"


def execute():
    settings = frappe.get_single("Subcontracting Settlement Settings")
    if any(row.action_code == ACTION for row in settings.get("controlled_action_roles") or []):
        return
    for row in initial_action_rows():
        if row["action_code"] == ACTION:
            if not frappe.db.exists("Role", row["role"]):
                frappe.throw("Missing invoice preparation release role: " + row["role"])
            settings.append("controlled_action_roles", row)
    settings.save(ignore_permissions=True)
