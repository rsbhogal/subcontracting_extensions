"""Plain unittest; warehouse reads mocked and no documents are saved."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from subcontracting_extensions.subcontracting_extensions.doctype.subcontracting_route import subcontracting_route


def route(target=None, active=1):
    result = SimpleNamespace(finished_goods_target_warehouse=target, is_active=active)
    result.get = lambda key, default=None: getattr(result, key, default)
    return result


class TestSubcontractingRouteTarget(unittest.TestCase):
    def setUp(self):
        self.conf = patch.object(
            subcontracting_route.frappe, "conf",
            frappe._dict(v2_processor_first_draft_entry=1),
        )
        self.conf.start()
        self.addCleanup(self.conf.stop)

    def validate(self, doc):
        return subcontracting_route.SubcontractingRoute._validate_finished_goods_target_warehouse(doc)

    def test_active_v2_route_requires_target(self):
        with self.assertRaisesRegex(frappe.ValidationError, "Target Warehouse is required"):
            self.validate(route())

    def test_inactive_v2_route_can_remain_unconfigured(self):
        self.validate(route(active=0))

    def test_v1_route_can_remain_unconfigured(self):
        subcontracting_route.frappe.conf.v2_processor_first_draft_entry = 0
        self.validate(route())

    def test_valid_target_is_accepted(self):
        with patch.object(subcontracting_route.frappe.db, "get_value", return_value=frappe._dict(is_group=0, disabled=0)):
            self.validate(route("TARGET"))

    def test_missing_group_and_disabled_targets_are_rejected(self):
        for warehouse, message in (
            (None, "does not exist"),
            (frappe._dict(is_group=1, disabled=0), "group warehouse"),
            (frappe._dict(is_group=0, disabled=1), "disabled"),
        ):
            with self.subTest(message=message), patch.object(
                subcontracting_route.frappe.db, "get_value", return_value=warehouse
            ), self.assertRaisesRegex(frappe.ValidationError, message):
                self.validate(route("TARGET"))

    def test_browser_endpoint_stops_before_reads_when_disabled(self):
        purchase_order = __import__("subcontracting_extensions.scripts.purchase_order", fromlist=["x"])
        purchase_order.frappe.conf.v2_processor_first_draft_entry = 0
        with patch.object(purchase_order.frappe, "get_doc") as read:
            self.assertEqual(purchase_order.get_v2_route_targets(["ROUTE"], "COMPANY"), {"enabled": False, "targets": {}})
            read.assert_not_called()

    def test_browser_endpoint_checks_route_access(self):
        purchase_order = __import__("subcontracting_extensions.scripts.purchase_order", fromlist=["x"])
        purchase_order.frappe.conf.v2_processor_first_draft_entry = 1
        doc = SimpleNamespace(check_permission=Mock(side_effect=frappe.PermissionError))
        with patch.object(purchase_order.frappe, "get_doc", return_value=doc), self.assertRaises(frappe.PermissionError):
            purchase_order.get_v2_route_targets(["ROUTE"], "COMPANY")


if __name__ == "__main__":
    unittest.main()
