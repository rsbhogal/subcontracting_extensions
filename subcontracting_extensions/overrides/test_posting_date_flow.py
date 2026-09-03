# Copyright (c) 2026, R S Bhogal and contributors
# See license.txt

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot_receipt import (
	processor_lot_receipt,
)
from subcontracting_extensions.overrides import purchase_receipt
from subcontracting_extensions.overrides import subcontracting_receipt
from subcontracting_extensions.overrides import subcontracting_order
from subcontracting_extensions.overrides.posting_date_flow import (
	set_downstream_posting_datetime,
)


class TestPostingDateFlow(FrappeTestCase):
	"""Regression coverage for PLR receipt-journey posting dates."""

	def test_sco_mapper_defaults_transaction_date(self):
		sco = SimpleNamespace(
			transaction_date=date(2026, 7, 16),
		)
		stock_entry = SimpleNamespace(
			posting_date=date(2026, 8, 21),
			set_posting_time=0,
		)

		with patch(
			"erpnext.controllers.subcontracting_controller.make_rm_stock_entry",
			return_value=stock_entry,
		), patch.object(
			subcontracting_order.frappe,
			"get_doc",
			return_value=sco,
		):
			result = subcontracting_order.make_rm_stock_entry(
				"SCO-TEST"
			)

		self.assertEqual(result.posting_date, date(2026, 7, 16))
		self.assertEqual(result.set_posting_time, 1)

	def test_purchase_order_terms_replace_supplier_default(self):
		invoice = SimpleNamespace(
			items=[SimpleNamespace(purchase_order="PO-TEST")],
			payment_terms_template="Supplier Default",
			payment_schedule=[SimpleNamespace(name="OLD-SCHEDULE")],
			set=lambda fieldname, value: setattr(invoice, fieldname, value),
			set_payment_schedule=lambda: setattr(
				invoice,
				"schedule_rebuilt",
				True,
			),
		)
		po_terms = [
			SimpleNamespace(
				name="PO-TEST",
				payment_terms_template="PO Contract Terms",
			)
		]

		with patch.object(
			purchase_receipt.frappe,
			"get_all",
			return_value=po_terms,
		):
			purchase_receipt._set_purchase_order_payment_terms(invoice)

		self.assertEqual(
			invoice.payment_terms_template,
			"PO Contract Terms",
		)
		self.assertEqual(invoice.payment_schedule, [])
		self.assertTrue(invoice.schedule_rebuilt)

	def test_plr_date_defaults_scr_without_replacing_time(self):
		target_time = timedelta(seconds=36000)
		target = SimpleNamespace(
			posting_date=date(2026, 8, 18),
			posting_time=target_time,
			set_posting_time=0,
		)
		source = SimpleNamespace(
			physical_receipt_date=date(2026, 8, 14),
		)

		processor_lot_receipt._set_mapped_scr_posting_date(
			target,
			source,
		)

		self.assertEqual(target.posting_date, date(2026, 8, 14))
		self.assertEqual(target.posting_time, target_time)
		self.assertEqual(target.set_posting_time, 1)

	def test_scr_date_defaults_pr_without_replacing_time(self):
		target_time = timedelta(seconds=36600)
		target = SimpleNamespace(
			posting_date=date(2026, 8, 18),
			posting_time=target_time,
			set_posting_time=0,
		)
		source = SimpleNamespace(
			posting_date=date(2026, 8, 14),
			posting_time=timedelta(seconds=36000),
		)

		subcontracting_receipt._set_mapped_pr_posting_date(
			target,
			source,
		)

		self.assertEqual(target.posting_date, date(2026, 8, 14))
		self.assertEqual(target.posting_time, target_time)
		self.assertEqual(target.set_posting_time, 1)

	def test_pr_date_defaults_pi_without_replacing_time(self):
		target_time = timedelta(seconds=37200)
		target = SimpleNamespace(
			posting_date=date(2026, 8, 18),
			posting_time=target_time,
			set_posting_time=0,
		)
		source = SimpleNamespace(
			posting_date=date(2026, 8, 14),
			posting_time=timedelta(seconds=36600),
		)

		purchase_receipt._set_mapped_pi_posting_date(
			target,
			source,
		)

		self.assertEqual(target.posting_date, date(2026, 8, 14))
		self.assertEqual(target.posting_time, target_time)
		self.assertEqual(target.set_posting_time, 1)

	def test_purchase_invoice_mapper_applies_controlled_pr_date(self):
		target_time = timedelta(seconds=37800)
		source = SimpleNamespace(
			name="PR-TEST",
			posting_date=date(2026, 8, 14),
			posting_time=timedelta(seconds=37200),
			subcontracting_receipt="SCR-TEST",
		)
		target = SimpleNamespace(
			posting_date=date(2026, 8, 18),
			posting_time=target_time,
			set_posting_time=0,
		)

		with patch.object(
			purchase_receipt.frappe,
			"get_doc",
			return_value=source,
		), patch.object(
			purchase_receipt.frappe,
			"conf",
			frappe._dict(v2_processor_first_draft_pi=1),
		), patch.object(
			purchase_receipt,
			"_is_processor_lot_purchase_receipt",
			return_value=True,
		), patch.object(
			purchase_receipt,
			"_set_purchase_order_payment_terms",
		), patch(
			"subcontracting_extensions.scripts.purchase_invoice."
			"validate_processor_first_draft_purchase_invoice",
		), patch(
			"erpnext.stock.doctype.purchase_receipt."
			"purchase_receipt.make_purchase_invoice",
			return_value=target,
		) as standard_mapper:
			result = purchase_receipt.make_purchase_invoice(
				"PR-TEST",
				args={"merge_taxes": True},
			)

		standard_mapper.assert_called_once_with(
			"PR-TEST",
			target_doc=None,
			args={"merge_taxes": True},
		)
		self.assertIs(result, target)
		self.assertEqual(result.posting_date, date(2026, 8, 14))
		self.assertEqual(result.posting_time, target_time)
		self.assertEqual(result.set_posting_time, 1)

	def test_downstream_time_advances_when_generated_time_is_earlier(self):
		target = SimpleNamespace(
			posting_date=date(2026, 8, 19),
			posting_time=timedelta(seconds=22679),
			set_posting_time=0,
		)
		source = SimpleNamespace(
			posting_date=date(2026, 8, 18),
			posting_time=timedelta(seconds=60000),
		)

		set_downstream_posting_datetime(target, source)

		self.assertEqual(target.posting_date, date(2026, 8, 18))
		self.assertEqual(
			target.posting_time,
			timedelta(seconds=60001),
		)
		self.assertEqual(target.set_posting_time, 1)

	def test_downstream_time_rolls_to_next_day_after_day_end(self):
		target = SimpleNamespace(
			posting_date=date(2026, 8, 19),
			posting_time=timedelta(seconds=100),
			set_posting_time=0,
		)
		source = SimpleNamespace(
			posting_date=date(2026, 8, 18),
			posting_time=timedelta(
				days=1,
				seconds=-1,
			),
		)

		set_downstream_posting_datetime(target, source)

		self.assertEqual(target.posting_date, date(2026, 8, 19))
		self.assertEqual(target.posting_time, timedelta(0))
		self.assertEqual(target.set_posting_time, 1)
