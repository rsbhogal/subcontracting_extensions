"""Add read-only accounting access without changing live Processor Lot overrides."""

import frappe


READ_ONLY_ROLES = ("Accounts User", "Accounts Manager")
RIGHTS = (
    "select", "read", "write", "create", "delete", "submit", "cancel",
    "amend", "report", "export", "import", "share", "print", "email",
)


def execute():
    created = False
    for role in READ_ONLY_ROLES:
        rows = frappe.get_all(
            "Custom DocPerm", filters={"parent": "Processor Lot", "role": role},
            fields=["name"], limit_page_length=2,
        )
        if rows:
            if len(rows) != 1:
                frappe.throw(f"Ambiguous Processor Lot permission rows for {role}")
            existing = frappe.get_doc("Custom DocPerm", rows[0]["name"])
            if (existing.get("permlevel") != 0 or existing.get("if_owner")
                    or any(int(existing.get(right) or 0) != int(right == "read")
                           for right in RIGHTS)):
                frappe.throw(f"Unexpected Processor Lot permission for {role}; review before migration")
            continue
        frappe.get_doc({
            "doctype": "Custom DocPerm",
            "parent": "Processor Lot",
            "parenttype": "DocType",
            "parentfield": "permissions",
            "role": role,
            "permlevel": 0,
            "read": 1,
        }).insert(ignore_permissions=True)
        created = True
    if created:
        frappe.clear_cache(doctype="Processor Lot")
