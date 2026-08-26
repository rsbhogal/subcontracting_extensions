# Copyright (c) 2026, R S Bhogal and contributors
# For license information, please see license.txt

"""Posting-date naming for normal Purchase Receipts and Invoices."""

from frappe.model.naming import make_autoname
from frappe.utils import getdate

from subcontracting_extensions.scripts.purchase_order_naming import (
	get_fiscal_year_label,
)


DATED_SERIES = {
	"Purchase Receipt": {
		"selected": "RN.-.FY.-.MM.-.DD./.###",
		"prefix": "RN",
	},
	"Purchase Invoice": {
		"selected": "PI.-.FY.-.MM.-.DD./.###",
		"prefix": "PI",
	},
}


def set_posting_date_name(doc, method=None) -> None:
	"""Name a normal PR or PI from its posting date.

	Alternate and return naming series retain ERPNext's standard behaviour.
	The Frappe series generator provides a transaction-safe daily counter.
	"""
	config = DATED_SERIES.get(doc.doctype)
	if not config or doc.naming_series != config["selected"]:
		return

	if not doc.posting_date:
		return

	posting_date = getdate(doc.posting_date)
	series_key = _get_posting_date_series_key(posting_date)
	doc.name = make_autoname(
		f'{config["prefix"]}-{series_key}/.###',
		doc=doc,
	)


def _get_posting_date_series_key(posting_date) -> str:
	"""Return fiscal-year, month and day for a posting date."""
	return "-".join(
		(
			get_fiscal_year_label(posting_date),
			f"{posting_date.month:02d}",
			f"{posting_date.day:02d}",
		)
	)
