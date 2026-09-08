"""J19B1B Purchase Order policy tests; no documents are written."""

import unittest
import frappe

from subcontracting_extensions.scripts.purchase_order import (
    validate_commercial_settlement_defaults,
)
from subcontracting_extensions.settlement_method_policy import initial_method_rows


class Doc(frappe._dict):
    pass


class TestPurchaseOrderCommercialPolicy(unittest.TestCase):
    @staticmethod
    def reject(message, **kwargs):
        raise frappe.ValidationError(message)

    def validate(self, **changes):
        doc = Doc(is_subcontracted=1, supplier="SUPPLIER")
        doc.update(changes)

        def value(doctype, name, fieldname):
            if doctype == "Supplier":
                return "CUSTOMER" if name == "SUPPLIER" else None
            if doctype == "Customer":
                return 0 if name == "CUSTOMER" else None

        validate_commercial_settlement_defaults(
            doc,
            method_rows=initial_method_rows(),
            db_get_value=value,
            throw=self.reject,
        )
        return doc

    def test_safe_defaults_and_supplier_customer_are_inherited(self):
        doc = self.validate()
        self.assertEqual(doc.custom_shortage_settlement_method,
                         "PENDING_INVESTIGATION")
        self.assertEqual(doc.custom_excess_settlement_method,
                         "PENDING_OWNERSHIP_INVESTIGATION")
        self.assertEqual(doc.custom_recovery_customer, "CUSTOMER")

    def test_sales_invoice_requires_authoritative_binding(self):
        with self.assertRaisesRegex(frappe.ValidationError, "Recovery Customer is required"):
            self.validate(custom_shortage_settlement_method="SALES_INVOICE",
                          custom_recovery_customer=None, supplier="UNBOUND")

    def test_arbitrary_customer_is_rejected(self):
        with self.assertRaisesRegex(frappe.ValidationError, "must match"):
            self.validate(custom_recovery_customer="OTHER")

    def test_disabled_method_is_rejected(self):
        with self.assertRaisesRegex(frappe.ValidationError, "disabled"):
            self.validate(custom_shortage_settlement_method="COMMERCIAL_WAIVER")

    def test_normal_purchase_order_is_unchanged(self):
        doc = Doc(is_subcontracted=0, supplier="SUPPLIER")
        validate_commercial_settlement_defaults(doc)
        self.assertNotIn("custom_shortage_settlement_method", doc)


if __name__ == "__main__":
    unittest.main()
