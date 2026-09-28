"""One-time J19C1 authority defaults; never restore administrator removals."""

import frappe

from subcontracting_extensions.settlement_action_authority import initial_action_rows


def execute():
    settings = frappe.get_single("Subcontracting Settlement Settings")
    if settings.get("controlled_action_roles"):
        return
    defaults = initial_action_rows()
    missing = sorted({row["role"] for row in defaults
                      if not frappe.db.exists("Role", row["role"])})
    if missing:
        frappe.throw("Cannot seed settlement action authorities; missing Roles: "
                     + ", ".join(missing))
    for row in defaults:
        settings.append("controlled_action_roles", row)
    settings.save(ignore_permissions=True)
