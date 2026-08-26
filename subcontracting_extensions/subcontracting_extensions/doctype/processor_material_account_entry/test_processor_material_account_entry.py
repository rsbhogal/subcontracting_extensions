# Copyright (c) 2026, R.S. Bhogal and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from . import processor_material_account_entry as controller


class TestProcessorMaterialAccountEntry(FrappeTestCase):
	"""Regression coverage for PLR excess-credit reversal controls."""

	def test_material_credit_status_lifecycle(self):
		self.assertEqual(
			controller._get_material_credit_status(170.0, 0.0),
			"Recorded",
		)
		self.assertEqual(
			controller._get_material_credit_status(170.0, 30.0),
			"Partly Applied",
		)
		self.assertEqual(
			controller._get_material_credit_status(170.0, 170.0),
			"Fully Applied",
		)

	def _entry(self):
		return SimpleNamespace(
			name="PMA-TEST-CREDIT",
			processor_lot_receipt="PLR-TEST-CREDIT",
			material_credit_stock_entry="STE-TEST-CREDIT",
		)

	def test_credit_cancellation_requires_stock_documents_reversed(self):
		entry = self._entry()
		plr = SimpleNamespace(
			name="PLR-TEST-CREDIT",
			material_credit_stock_entry="STE-TEST-CREDIT",
			subcontracting_receipt="SCR-TEST-BACKED",
		)

		def get_value(doctype, name, fieldname):
			return {
				"Stock Entry": 1,
				"Subcontracting Receipt": 1,
			}[doctype]

		with patch.object(
			controller.frappe,
			"get_doc",
			return_value=plr,
		), patch.object(
			controller.frappe.db,
			"get_value",
			side_effect=get_value,
		):
			with self.assertRaisesRegex(
				frappe.ValidationError,
				"cancel Stock Entry.*cancel Subcontracting Receipt",
			):
				(
					controller.ProcessorMaterialAccountEntry
					._validate_source_documents_before_cancel(entry)
				)

	def test_credit_cancellation_allows_reversed_stock_documents(self):
		entry = self._entry()
		plr = SimpleNamespace(
			name="PLR-TEST-CREDIT",
			material_credit_stock_entry="STE-TEST-CREDIT",
			subcontracting_receipt="SCR-TEST-BACKED",
		)

		with patch.object(
			controller.frappe,
			"get_doc",
			return_value=plr,
		), patch.object(
			controller.frappe.db,
			"get_value",
			return_value=2,
		):
			(
				controller.ProcessorMaterialAccountEntry
				._validate_source_documents_before_cancel(entry)
			)