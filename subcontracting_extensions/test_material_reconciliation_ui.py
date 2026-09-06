"""J14 endpoint contract with a scoped Frappe stub; no site or writes."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class TestMaterialReconciliationUI(unittest.TestCase):
    def setUp(self):
        self.frappe = SimpleNamespace(conf={}, whitelist=Mock(return_value=lambda fn: fn))
        spec = importlib.util.spec_from_file_location("j14_endpoint_under_test",
            Path(__file__).with_name("material_reconciliation_ui.py"))
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict("sys.modules", {"frappe": self.frappe,
                "frappe.utils": SimpleNamespace(cint=lambda value: int(value or 0))}):
            spec.loader.exec_module(self.module)
        self.reader = Mock(return_value={"settlement_enabled": False, "components": []})
        self.module.get_material_position = self.reader
        self.module.assess_component_action_readiness = Mock(
            side_effect=lambda report, can_write: dict(
                report,
                component_action_contract_version="J17",
                commercial_review_permitted=can_write,
            )
        )

    def test_disabled_by_default_without_evidence_reads(self):
        self.assertEqual(self.module.get_material_panel("LOT"), {"enabled": False})
        self.reader.assert_not_called()

    def test_enabled_delegates_to_permission_checked_reader(self):
        self.frappe.conf["v2_processor_first_material_facts"] = "1"
        lot = SimpleNamespace(has_permission=Mock(return_value=True))
        self.frappe.get_doc = Mock(return_value=lot)
        result = self.module.get_material_panel("LOT")
        self.reader.assert_called_once_with("LOT")
        self.frappe.get_doc.assert_called_once_with("Processor Lot", "LOT")
        lot.has_permission.assert_called_once_with("write")
        self.module.assess_component_action_readiness.assert_called_once_with(
            self.reader.return_value,
            can_write=True,
        )
        self.assertTrue(result["enabled"])
        self.assertFalse(result["settlement_enabled"])
        self.assertEqual(result["component_action_contract_version"], "J17")
        self.assertTrue(result["commercial_review_permitted"])
        self.assertNotIn("enabled", self.reader.return_value)

    def test_permission_error_propagates_without_partial_report(self):
        self.frappe.conf["v2_processor_first_material_facts"] = 1
        self.reader.side_effect = PermissionError
        with self.assertRaises(PermissionError):
            self.module.get_material_panel("LOT")

    def test_read_only_permission_is_reported_without_enabling_action(self):
        self.frappe.conf["v2_processor_first_material_facts"] = 1
        lot = SimpleNamespace(has_permission=Mock(return_value=False))
        self.frappe.get_doc = Mock(return_value=lot)
        result = self.module.get_material_panel("LOT")
        self.assertFalse(result["commercial_review_permitted"])

    def check_single_item_routing(self, enabled):
        spec = importlib.util.spec_from_file_location("j14_position_under_test",
            Path(__file__).with_name("receipt_item_position.py"))
        module = importlib.util.module_from_spec(spec)
        self.frappe._ = lambda value: value
        with patch.dict("sys.modules", {"frappe": self.frappe,
                "frappe.utils": SimpleNamespace(cint=lambda value: int(value or 0), flt=float)}):
            spec.loader.exec_module(module)
        self.frappe.conf.update(v2_processor_first_draft_entry=1,
                                v2_processor_first_material_facts=enabled)
        lot = SimpleNamespace(name="LOT", subcontracting_order="SCO", check_permission=Mock())
        sco = SimpleNamespace(name="SCO", items=[{}], check_permission=Mock())
        self.frappe.get_doc = Mock(side_effect=[lot, sco])
        module.position_for_lot = Mock(return_value={"items": [], "journeys": [], "is_multi_item": False})
        result = module.get_item_position("LOT")
        if enabled:
            self.assertTrue(result["use_item_panels"])
            module.position_for_lot.assert_called_once_with(lot, sco)
        else:
            self.assertEqual(result, {"enabled": True, "is_multi_item": False})
            module.position_for_lot.assert_not_called()
        lot.check_permission.assert_called_once_with("read")
        sco.check_permission.assert_called_once_with("read")

    def test_single_item_uses_evidence_panels_when_opted_in(self):
        self.check_single_item_routing(1)

    def test_single_item_legacy_rendering_retained_without_opt_in(self):
        self.check_single_item_routing(0)

    def test_whitelist_does_not_enable_guest_access(self):
        self.frappe.whitelist.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
