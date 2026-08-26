# Copyright (c) 2026, R S Bhogal and contributors
# For license information, please see license.txt

"""Posting timestamp helpers for the controlled receipt journey."""

from datetime import timedelta

from frappe.utils import get_timedelta, getdate


ONE_DAY = timedelta(days=1)


def set_downstream_posting_datetime(target, source) -> None:
	"""Carry the source date forward and preserve chronological ordering.

	The mapped document keeps its generated posting time when that time is
	already later than the source.  When backdating would place it before (or
	at exactly the same time as) the source, advance it by one second.
	"""
	if not getattr(source, "posting_date", None):
		return

	target.set_posting_time = 1
	target.posting_date = source.posting_date

	source_value = getattr(source, "posting_time", None)
	if not source_value:
		return

	source_time = get_timedelta(source_value)
	target_value = getattr(target, "posting_time", None)
	target_time = get_timedelta(target_value) if target_value else None

	if source_time is None or (
		target_time is not None and target_time > source_time
	):
		return

	next_time = source_time + timedelta(seconds=1)
	if next_time < ONE_DAY:
		target.posting_time = next_time
		return

	# A source posted at the end of the day cannot have a later time on the
	# same date.  Move the downstream timestamp to midnight on the next day.
	target.posting_date = getdate(source.posting_date) + timedelta(days=1)
	target.posting_time = timedelta(0)
