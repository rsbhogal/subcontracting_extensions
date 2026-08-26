# Code to make a daily restarting PO Numbering Series based on Transaction Date of PO.

import frappe
from frappe.utils import getdate


def set_po_date_series_field(doc, method=None):
    if not doc.transaction_date:
        return

    po_date = getdate(doc.transaction_date)

    fiscal_year = get_fiscal_year_label(po_date)
    month_part = f"{po_date.month:02d}"
    day_part = f"{po_date.day:02d}"

    doc.custom_po_series_key = f"{fiscal_year}-{month_part}-{day_part}"


def get_fiscal_year_label(posting_date):
    fiscal_year = frappe.db.get_value(
        "Fiscal Year",
        {
            "year_start_date": ("<=", posting_date),
            "year_end_date": (">=", posting_date),
        },
        ["year"],
    )

    if fiscal_year:
        return fiscal_year

    # Fallback for Apr-Mar style fiscal if no Fiscal Year record is found
    year = posting_date.year
    if posting_date.month >= 4:
        return f"{year}-{str(year + 1)[-2:]}"
    else:
        return f"{year - 1}-{str(year)[-2:]}"