"""Plain unittest; all records/reads mocked. No inserts, saves or commits."""

import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from subcontracting_extensions.scripts import purchase_order


def row(idx, service, finished, uom, route):
    result = SimpleNamespace(
        idx=idx, item_code=service, fg_item=finished,
        stock_uom=uom, custom_processing_route=route,
    )
    result.get = lambda key, default=None: getattr(result, key, default)
    return result


class TestPurchaseOrderV2Policy(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(
            purchase_order.frappe, "conf",
            frappe._dict(v2_processor_first_draft_entry=1),
        ))
        self.rows = [
            row(1, "SERVICE-A", "FINISHED-A", "Kg", "ROUTE-A"),
            row(2, "SERVICE-B", "FINISHED-B", "Units", "ROUTE-B"),
        ]
        self.doc = SimpleNamespace(is_subcontracted=1, items=self.rows)

    def test_gated_v2_accepts_distinct_finished_rows(self):
        purchase_order.validate_subcontracted_purchase_order_item_count(self.doc)

    def test_normal_purchase_order_is_unchanged(self):
        self.doc.is_subcontracted = 0
        self.rows.append(self.rows[0])
        purchase_order.validate_subcontracted_purchase_order_item_count(self.doc)

    def test_zero_and_one_row_are_accepted_without_flag(self):
        purchase_order.frappe.conf.v2_processor_first_draft_entry = 0
        for rows in ([], self.rows[:1]):
            with self.subTest(count=len(rows)):
                self.doc.items = rows
                purchase_order.validate_subcontracted_purchase_order_item_count(self.doc)

    def test_v1_policy_is_preserved_without_flag(self):
        for value in (None, 0, "0"):
            purchase_order.frappe.conf.v2_processor_first_draft_entry = value
            with self.subTest(value=value), self.assertRaisesRegex(
                frappe.ValidationError, "only one item row"
            ):
                purchase_order.validate_subcontracted_purchase_order_item_count(self.doc)

    def test_duplicate_finished_item_and_uom_is_rejected(self):
        self.rows[1].fg_item = "FINISHED-A"
        self.rows[1].stock_uom = "Kg"
        with self.assertRaisesRegex(frappe.ValidationError, "Duplicate source rows"):
            purchase_order.validate_subcontracted_purchase_order_item_count(self.doc)

    def test_same_item_with_distinct_uom_is_unambiguous(self):
        self.rows[1].fg_item = "FINISHED-A"
        purchase_order.validate_subcontracted_purchase_order_item_count(self.doc)

    def test_validate_checks_every_route_after_v2_gate(self):
        routes = {
            "ROUTE-A": frappe._dict(is_active=1, finished_item="FINISHED-A", service_item="SERVICE-A", manufacturing_bom="BOM-A", finished_goods_target_warehouse="TARGET-A"),
            "ROUTE-B": frappe._dict(is_active=1, finished_item="FINISHED-B", service_item="SERVICE-B", manufacturing_bom="BOM-B", finished_goods_target_warehouse="TARGET-B"),
        }
        def lookup(doctype, name, fields, as_dict):
            if doctype == "Subcontracting Route":
                return routes[name]
            return frappe._dict(company="COMPANY", is_group=0, disabled=0)
        self.doc.company = "COMPANY"
        with patch.object(
            purchase_order.frappe.db, "get_value", side_effect=lookup
        ) as read, patch.object(
            purchase_order, "validate_commercial_settlement_defaults"
        ):
            purchase_order.validate(self.doc)
        self.assertEqual(read.call_count, 4)
        self.assertEqual([row.warehouse for row in self.rows], ["TARGET-A", "TARGET-B"])

    def test_missing_route_is_rejected(self):
        self.rows[1].custom_processing_route = None
        with self.assertRaisesRegex(frappe.ValidationError, "Processing Route is required"):
            purchase_order.validate_subcontracting_routes(
                SimpleNamespace(is_subcontracted=1, items=self.rows[1:])
            )

    def test_inactive_route_is_rejected(self):
        route = frappe._dict(is_active=0, finished_item="FINISHED-A", service_item="SERVICE-A")
        with patch.object(purchase_order.frappe.db, "get_value", return_value=route), self.assertRaisesRegex(
            frappe.ValidationError, "inactive"
        ):
            purchase_order.validate_subcontracting_routes(SimpleNamespace(is_subcontracted=1, items=self.rows[:1]))

    def test_route_item_mismatch_is_rejected(self):
        route = frappe._dict(is_active=1, finished_item="OTHER", service_item="SERVICE-A")
        with patch.object(purchase_order.frappe.db, "get_value", return_value=route), self.assertRaisesRegex(
            frappe.ValidationError, "Finished Item"
        ):
            purchase_order.validate_subcontracting_routes(SimpleNamespace(is_subcontracted=1, items=self.rows[:1]))

    def test_missing_route_target_is_rejected_in_v2(self):
        route = frappe._dict(is_active=1, finished_item="FINISHED-A", service_item="SERVICE-A", finished_goods_target_warehouse=None)
        doc = SimpleNamespace(is_subcontracted=1, company="COMPANY", items=self.rows[:1])
        with patch.object(purchase_order.frappe.db, "get_value", return_value=route), self.assertRaisesRegex(
            frappe.ValidationError, "no Finished Goods Target Warehouse"
        ):
            purchase_order.validate_subcontracting_routes(doc)

    def test_target_must_belong_to_po_company(self):
        route = frappe._dict(finished_goods_target_warehouse="TARGET")
        warehouse = frappe._dict(company="OTHER", is_group=0, disabled=0)
        with patch.object(purchase_order.frappe.db, "get_value", return_value=warehouse), self.assertRaisesRegex(
            frappe.ValidationError, "not PO Company"
        ):
            purchase_order._get_v2_route_target_warehouse("ROUTE", "COMPANY", route)

    def test_v1_route_validation_does_not_require_or_change_target(self):
        purchase_order.frappe.conf.v2_processor_first_draft_entry = 0
        route = frappe._dict(is_active=1, finished_item="FINISHED-A", service_item="SERVICE-A", manufacturing_bom="BOM-A")
        doc = SimpleNamespace(is_subcontracted=1, company="COMPANY", items=self.rows[:1])
        self.rows[0].warehouse = "LEGACY"
        with patch.object(purchase_order.frappe.db, "get_value", return_value=route):
            purchase_order.validate_subcontracting_routes(doc)
        self.assertEqual(self.rows[0].warehouse, "LEGACY")


if __name__ == "__main__":
    unittest.main()
