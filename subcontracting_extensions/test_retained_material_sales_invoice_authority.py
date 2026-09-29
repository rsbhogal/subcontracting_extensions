"""J19C2 authority separation and controlled-submission regressions."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from subcontracting_extensions import settlement_action_authority as authority
from subcontracting_extensions.patches.v2_0 import (
    seed_retained_material_sales_invoice_action_roles as migration,
)


ROOT = Path(__file__).resolve().parent
DRAFT = "CREATE_DRAFT_RETAINED_MATERIAL_SALES_INVOICE"
SUBMIT = "SUBMIT_RETAINED_MATERIAL_SALES_INVOICE"


class TestSalesInvoiceAuthority(unittest.TestCase):
    def test_separate_initial_roles_and_configurable_denial(self):
        rows = authority.initial_action_rows()
        self.assertEqual([r["role"] for r in rows if r["action_code"] == DRAFT], ["System Manager"])
        self.assertEqual([r["role"] for r in rows if r["action_code"] == SUBMIT], ["System Manager"])
        rows = [row for row in rows if row["action_code"] != DRAFT]
        self.assertFalse(authority.allowed_for_roles(rows, DRAFT, {"System Manager"}))
        self.assertTrue(authority.allowed_for_roles(rows, SUBMIT, {"System Manager"}))
        self.assertFalse(authority.allowed_for_roles(rows, SUBMIT, {"Accounts Manager"}))

    def test_resolver_accepts_injected_frappe_context(self):
        rows = authority.initial_action_rows()
        api = SimpleNamespace(
            session=SimpleNamespace(user="current"),
            get_single=lambda _: SimpleNamespace(get=lambda field: rows),
            get_roles=lambda user: ["System Manager"],
        )
        self.assertTrue(authority.can_settlement_action(DRAFT, api=api))
        authority.require_settlement_action(SUBMIT, api=api)

    def test_migration_adds_only_new_actions_to_existing_settings(self):
        rows = [SimpleNamespace(action_code="CREATE_DRAFT_DEBIT_NOTE")]
        added = []
        settings = SimpleNamespace(get=lambda field: rows,
                                   append=lambda field, row: added.append((field, row)),
                                   save=Mock())
        with patch.object(migration.frappe, "get_single", return_value=settings), \
             patch.object(migration.frappe.db, "exists", return_value=True):
            migration.execute()
        self.assertEqual([row["action_code"] for _, row in added], [DRAFT, SUBMIT])
        settings.save.assert_called_once_with(ignore_permissions=True)

    def test_migration_preserves_existing_action_decisions(self):
        rows = [SimpleNamespace(action_code=DRAFT), SimpleNamespace(action_code=SUBMIT)]
        settings = SimpleNamespace(get=lambda field: rows, append=Mock(), save=Mock())
        with patch.object(migration.frappe, "get_single", return_value=settings):
            migration.execute()
        settings.append.assert_not_called()
        settings.save.assert_not_called()

    def test_services_require_action_before_document_lock(self):
        for filename, code in (
            ("retained_material_sales_invoice_draft_creation.py", DRAFT),
            ("retained_material_sales_invoice_submission.py", SUBMIT),
        ):
            source = (ROOT / filename).read_text()
            self.assertLess(source.index(f'require_settlement_action("{code}"'),
                            source.index('_lock(api,'))
            self.assertIn('lot.check_permission("read")', source)
            self.assertNotIn('lot.check_permission("write")', source)


class TestSalesInvoiceSubmissionHook(unittest.TestCase):
    def test_ordinary_invoice_is_unaffected(self):
        from subcontracting_extensions.retained_material_sales_invoice_draft_creation import (
            prevent_uncontrolled_submission,
        )
        doc = SimpleNamespace(get=lambda field: None, flags={})
        prevent_uncontrolled_submission(doc)

    def test_controlled_invoice_needs_exact_service_document(self):
        import frappe
        from subcontracting_extensions import retained_material_sales_invoice_draft_creation as draft
        fields = {"custom_invoice_number_reservation": "RES-1"}
        doc = SimpleNamespace(get=fields.get,
                              flags={"controlled_retained_material_submission": True},
                              check_permission=Mock())
        with patch.object(frappe, "conf", {"v2_retained_material_sales_invoice_submission": 1}), \
             patch.object(frappe, "flags", {"controlled_retained_material_submission_doc": object()}), \
             patch.object(frappe, "throw", side_effect=ValueError):
            with self.assertRaises(ValueError):
                draft.prevent_uncontrolled_submission(doc)
        doc.check_permission.assert_not_called()

    def test_controlled_invoice_requires_action_and_native_submit(self):
        import frappe
        from subcontracting_extensions import retained_material_sales_invoice_draft_creation as draft
        fields = {"custom_invoice_number_reservation": "RES-1"}
        doc = SimpleNamespace(get=fields.get,
                              flags={"controlled_retained_material_submission": True},
                              check_permission=Mock())
        with patch.object(frappe, "conf", {"v2_retained_material_sales_invoice_submission": 1}), \
             patch.object(frappe, "flags", {"controlled_retained_material_submission_doc": doc}), \
             patch.object(draft, "require_settlement_action") as require:
            draft.prevent_uncontrolled_submission(doc)
        require.assert_called_once_with(SUBMIT)
        doc.check_permission.assert_called_once_with("submit")

    def test_ui_suppresses_action_without_role_or_native_permission(self):
        from subcontracting_extensions import material_reconciliation_ui as ui
        readiness = {"controlled_submission_available": True, "sales_invoice": "SI-1"}
        report = {"components": [{"retained_material_sales_invoice_submission_readiness": readiness}]}
        with patch.object(ui, "can_settlement_action", return_value=False):
            ui._restrict_controlled_sales_invoice_buttons(report)
        self.assertFalse(readiness["controlled_submission_available"])
        readiness["controlled_submission_available"] = True
        api = SimpleNamespace(get_doc=lambda *args: SimpleNamespace(has_permission=lambda kind: False))
        with patch.object(ui, "can_settlement_action", return_value=True):
            ui._restrict_controlled_sales_invoice_buttons(report, api=api)
        self.assertFalse(readiness["controlled_submission_available"])
