# Copyright (c) 2026, R S Bhogal and contributors
# See license.txt

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from subcontracting_extensions.scripts import purchase_document_naming


class TestPurchaseDocumentNaming(FrappeTestCase):
	"""Regression coverage for posting-date PR and PI numbering."""

	def test_normal_purchase_receipt_uses_posting_date(self):
		doc = SimpleNamespace(
			doctype="Purchase Receipt",
			naming_series="RN.-.FY.-.MM.-.DD./.###",
			posting_date=date(2026, 8, 18),
			name=None,
		)

		with patch.object(
			purchase_document_naming,
			"get_fiscal_year_label",
			return_value="2026-27",
		), patch.object(
			purchase_document_naming,
			"make_autoname",
			return_value="RN-2026-27-08-18/008",
		) as make_name:
			purchase_document_naming.set_posting_date_name(doc)

		make_name.assert_called_once_with(
			"RN-2026-27-08-18/.###",
			doc=doc,
		)
		self.assertEqual(doc.name, "RN-2026-27-08-18/008")
		self.assertEqual(
			doc.naming_series,
			"RN.-.FY.-.MM.-.DD./.###",
		)

	def test_normal_purchase_invoice_uses_posting_date(self):
		doc = SimpleNamespace(
			doctype="Purchase Invoice",
			naming_series="PI.-.FY.-.MM.-.DD./.###",
			posting_date=date(2026, 8, 18),
			name=None,
		)

		with patch.object(
			purchase_document_naming,
			"get_fiscal_year_label",
			return_value="2026-27",
		), patch.object(
			purchase_document_naming,
			"make_autoname",
			return_value="PI-2026-27-08-18/007",
		) as make_name:
			purchase_document_naming.set_posting_date_name(doc)

		make_name.assert_called_once_with(
			"PI-2026-27-08-18/.###",
			doc=doc,
		)
		self.assertEqual(doc.name, "PI-2026-27-08-18/007")
		self.assertEqual(
			doc.naming_series,
			"PI.-.FY.-.MM.-.DD./.###",
		)

	def test_alternate_purchase_invoice_series_is_untouched(self):
		doc = SimpleNamespace(
			doctype="Purchase Invoice",
			naming_series="PRET-.YY.-",
			posting_date=date(2026, 8, 18),
			name=None,
		)

		with patch.object(
			purchase_document_naming,
			"make_autoname",
		) as make_name:
			purchase_document_naming.set_posting_date_name(doc)

		make_name.assert_not_called()
		self.assertIsNone(doc.name)

	def test_user_edited_posting_date_drives_name(self):
		doc = SimpleNamespace(
			doctype="Purchase Invoice",
			naming_series="PI.-.FY.-.MM.-.DD./.###",
			posting_date="2026-08-17",
			name=None,
		)

		with patch.object(
			purchase_document_naming,
			"get_fiscal_year_label",
			return_value="2026-27",
		), patch.object(
			purchase_document_naming,
			"make_autoname",
			return_value="PI-2026-27-08-17/003",
		) as make_name:
			purchase_document_naming.set_posting_date_name(doc)

		make_name.assert_called_once_with(
			"PI-2026-27-08-17/.###",
			doc=doc,
		)
		self.assertEqual(doc.name, "PI-2026-27-08-17/003")
