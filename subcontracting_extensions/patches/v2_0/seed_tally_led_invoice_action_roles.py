"""Seed new Accounts actions without restoring removed existing authorities."""

import frappe

from subcontracting_extensions.settlement_action_authority import initial_action_rows


NEW_CODES = {
    "RESERVE_RETAINED_MATERIAL_INVOICE_NUMBER",
    "CONFIRM_RETAINED_MATERIAL_TALLY_NUMBER",
    "CONFIRM_RETAINED_MATERIAL_STATUTORY_EVIDENCE",
}
EXISTING_CODES = {
    "CREATE_DRAFT_RETAINED_MATERIAL_SALES_INVOICE",
    "SUBMIT_RETAINED_MATERIAL_SALES_INVOICE",
}


def execute():
    settings = frappe.get_single("Subcontracting Settlement Settings")
    rows = settings.get("controlled_action_roles") or []
    configured = {(row.action_code, row.role) for row in rows}
    existing_actions = {row.action_code for row in rows}
    add = [row for row in initial_action_rows()
           if (row["action_code"] in NEW_CODES and row["action_code"] not in existing_actions)
           or (row["action_code"] in EXISTING_CODES and row["role"] == "Accounts User"
               and row["action_code"] in existing_actions
               and (row["action_code"], row["role"]) not in configured)]
    for row in add:
        if not frappe.db.exists("Role", row["role"]):
            frappe.throw("Missing controlled invoice role: " + row["role"])
        settings.append("controlled_action_roles", row)
    if add:
        settings.save(ignore_permissions=True)
