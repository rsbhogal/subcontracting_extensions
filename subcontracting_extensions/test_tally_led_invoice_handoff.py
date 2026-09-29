"""J19C3B1 controlled handoff boundaries, without transactional fixtures."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from subcontracting_extensions import settlement_action_authority as authority
from subcontracting_extensions.patches.v2_0 import seed_tally_led_invoice_action_roles


ROOT = Path(__file__).resolve().parent


class TestTallyLedInvoiceHandoff(unittest.TestCase):
    def test_action_catalogue_keeps_distinct_roles(self):
        rows = authority.initial_action_rows()
        for code in seed_tally_led_invoice_action_roles.NEW_CODES:
            self.assertTrue(authority.allowed_for_roles(rows, code, {"Accounts User"}))
            self.assertFalse(authority.allowed_for_roles(rows, code, {"Purchase Manager"}))
        for code in ("CREATE_DRAFT_RETAINED_MATERIAL_SALES_INVOICE",
                     "SUBMIT_RETAINED_MATERIAL_SALES_INVOICE"):
            self.assertTrue(authority.allowed_for_roles(rows, code, {"Accounts User"}))

    def test_services_require_action_and_current_purchase_release(self):
        for filename, code in (
            ("sales_invoice_number_reservation.py", "RESERVE_RETAINED_MATERIAL_INVOICE_NUMBER"),
            ("tally_invoice_number_confirmation.py", "CONFIRM_RETAINED_MATERIAL_TALLY_NUMBER"),
            ("tally_statutory_evidence_confirmation.py", "CONFIRM_RETAINED_MATERIAL_STATUTORY_EVIDENCE"),
        ):
            source = (ROOT / filename).read_text()
            self.assertIn(f'require_settlement_action("{code}", api=api)', source)
            self.assertIn('lot.check_permission("read")', source)
            self.assertNotIn('lot.check_permission("write")', source)
            self.assertIn('require_current_release(api, row, processor_lot=lot.name)', source)
            self.assertNotIn('_require_system_manager(api)', source)
            self.assertNotIn('role not in api.get_roles()', source)
        for filename in ("retained_material_sales_invoice_draft_creation.py",
                         "retained_material_sales_invoice_submission.py"):
            self.assertIn('require_current_release(api, row, processor_lot=lot.name)',
                          (ROOT / filename).read_text())

    def test_irn_can_be_recorded_for_no_transport(self):
        from subcontracting_extensions.tally_statutory_evidence_confirmation import (
            _validate_no_physical_movement_statutory,
        )
        _validate_no_physical_movement_statutory({"irn": "TALLY-IRN", "ewaybill": "", "vehicle_no": ""})
        for field in ("ewaybill", "vehicle_no"):
            with self.assertRaisesRegex(ValueError, "conflicts"):
                _validate_no_physical_movement_statutory({field: "TRANSPORT"})

    def test_migration_only_adds_missing_roles_and_codes(self):
        old_codes = {"CREATE_DRAFT_RETAINED_MATERIAL_SALES_INVOICE",
                     "SUBMIT_RETAINED_MATERIAL_SALES_INVOICE"}
        existing = [SimpleNamespace(action_code=code, role="System Manager")
                    for code in old_codes]
        appended = []
        settings = SimpleNamespace(get=lambda field: existing,
                                   append=lambda field, row: appended.append(row),
                                   save=Mock())
        migration = seed_tally_led_invoice_action_roles
        with patch.object(migration.frappe, "get_single", return_value=settings), \
             patch.object(migration.frappe.db, "exists", return_value=True):
            migration.execute()
        self.assertEqual(len(appended), 8)
        settings.save.assert_called_once_with(ignore_permissions=True)
