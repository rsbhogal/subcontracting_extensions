"""J10 Purchase Invoice submission boundary tests; no documents are saved."""

import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from subcontracting_extensions.scripts import purchase_invoice


class Record(SimpleNamespace):
	def get(self, key, default=None):
		return getattr(self, key, default)


class TestProcessorFirstPISubmit(unittest.TestCase):
	def setUp(self):
		self.pr = Record(
			name="PR-V2",
			posting_date=date(2026, 9, 1),
			posting_time=timedelta(hours=12),
		)
		self.scr = Record(name="SCR-V2")
		self.plr = Record(name="PLR-V2")
		self.pi = Record(
			name="PI-V2",
			posting_date=date(2026, 9, 1),
			posting_time=timedelta(hours=11),
			set_posting_time=1,
		)
		self.context = (self.pr, self.scr, self.plr)

	def test_submission_requires_separate_j10_opt_in(self):
		with patch.dict(
			purchase_invoice.frappe.conf,
			{"v2_processor_first_pi_submit": 0},
		), patch.object(
			purchase_invoice, "_checkpoint_context",
			return_value=self.context,
		), self.assertRaisesRegex(
			frappe.ValidationError, "submission is not enabled"
		):
			purchase_invoice.prevent_processor_first_purchase_invoice_submit(
				self.pi
			)

	def test_exact_purchase_invoice_can_submit_and_moves_after_pr(self):
		with patch.dict(
			purchase_invoice.frappe.conf,
			{"v2_processor_first_pi_submit": 1},
		), patch.object(
			purchase_invoice, "_checkpoint_context",
			return_value=self.context,
		), patch.object(
			purchase_invoice,
			"validate_processor_first_draft_purchase_invoice",
		) as validate:
			purchase_invoice.prevent_processor_first_purchase_invoice_submit(
				self.pi
			)

		validate.assert_called_once_with(self.pi)
		self.assertEqual(self.pi.posting_date, self.pr.posting_date)
		self.assertEqual(
			self.pi.posting_time,
			self.pr.posting_time + timedelta(seconds=1),
		)
		self.assertEqual(self.pi.set_posting_time, 1)

	def test_later_purchase_invoice_timestamp_is_not_moved_back(self):
		self.pi.posting_time = timedelta(hours=13)
		with patch.dict(
			purchase_invoice.frappe.conf,
			{"v2_processor_first_pi_submit": 1},
		), patch.object(
			purchase_invoice, "_checkpoint_context",
			return_value=self.context,
		), patch.object(
			purchase_invoice,
			"validate_processor_first_draft_purchase_invoice",
		):
			purchase_invoice.prevent_processor_first_purchase_invoice_submit(
				self.pi
			)

		self.assertEqual(self.pi.posting_time, timedelta(hours=13))

	def test_failed_revalidation_stops_before_timestamp_change(self):
		with patch.dict(
			purchase_invoice.frappe.conf,
			{"v2_processor_first_pi_submit": 1},
		), patch.object(
			purchase_invoice, "_checkpoint_context",
			return_value=self.context,
		), patch.object(
			purchase_invoice,
			"validate_processor_first_draft_purchase_invoice",
			side_effect=frappe.ValidationError("changed row"),
		), self.assertRaisesRegex(
			frappe.ValidationError, "changed row"
		):
			purchase_invoice.prevent_processor_first_purchase_invoice_submit(
				self.pi
			)

		self.assertEqual(self.pi.posting_time, timedelta(hours=11))

	def test_legacy_purchase_invoice_keeps_existing_submit_path(self):
		with patch.object(
			purchase_invoice, "_checkpoint_context", return_value=None,
		), patch.object(
			purchase_invoice,
			"validate_processor_first_draft_purchase_invoice",
		) as validate:
			purchase_invoice.prevent_processor_first_purchase_invoice_submit(
				self.pi
			)

		validate.assert_not_called()


if __name__ == "__main__":
	unittest.main()
