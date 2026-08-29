"""Idempotent ownership corrections for extracted records."""

import frappe


REPORT_NAME = "Subcontracting Outstanding Status"
TARGET_MODULE = "Subcontracting Extensions"


def ensure_subcontracting_report_ownership() -> None:
    """Assign the extracted Script Report to its owning module."""
    if not frappe.db.exists("Report", REPORT_NAME):
        return

    current_module = frappe.db.get_value(
        "Report",
        REPORT_NAME,
        "module",
    )

    if current_module == TARGET_MODULE:
        return

    frappe.db.set_value(
        "Report",
        REPORT_NAME,
        "module",
        TARGET_MODULE,
        update_modified=False,
    )
