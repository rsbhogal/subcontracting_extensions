"""J19C1 configuration and contextual authority regressions (no DB writes)."""

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from subcontracting_extensions import settlement_action_authority as authority


class TestSettlementActionAuthority(unittest.TestCase):
    def test_default_catalogue_and_roles(self):
        rows = authority.initial_action_rows()
        self.assertEqual(len(rows), 18)
        self.assertEqual({r["action_code"] for r in rows}, set(authority.ACTION_LABELS))
        self.assertEqual(len({(r["action_code"], r["role"]) for r in rows}), len(rows))
        self.assertTrue(all(r["enabled"] for r in rows))

    def test_admin_removal_and_disabling_fail_closed(self):
        rows = authority.initial_action_rows()
        rows = [r for r in rows if r["action_code"] != "APPROVE_WAIVER"]
        self.assertFalse(authority.allowed_for_roles(rows, "APPROVE_WAIVER", {"System Manager"}))
        rows[0]["enabled"] = 0
        self.assertFalse(authority.allowed_for_roles(rows, rows[0]["action_code"], {rows[0]["role"]}))
        self.assertFalse(authority.allowed_for_roles([], "CREATE_DRAFT_DEBIT_NOTE", {"Administrator", "System Manager"}))

    def test_role_union_only_for_the_named_action(self):
        rows = authority.initial_action_rows()
        self.assertTrue(authority.allowed_for_roles(rows, "CREATE_DRAFT_DEBIT_NOTE", {"Accounts User"}))
        self.assertTrue(authority.allowed_for_roles(rows, "SUBMIT_SETTLEMENT_DEBIT_NOTE", {"Accounts Manager"}))
        self.assertFalse(authority.allowed_for_roles(rows, "SUBMIT_SETTLEMENT_DEBIT_NOTE", {"Purchase Manager"}))
        self.assertFalse(authority.allowed_for_roles(rows, "APPROVE_WAIVER", {"Accounts Manager"}))

    def test_invalid_code_duplicate_role_and_enabled_value(self):
        rows = authority.initial_action_rows()
        with self.assertRaisesRegex(authority.SettlementActionAuthorityError, "Unknown"):
            authority.normalize_action_rows([{"action_code": "CLOSE_LOT", "role": "System Manager", "enabled": 1}])
        with self.assertRaisesRegex(authority.SettlementActionAuthorityError, "Duplicate"):
            authority.normalize_action_rows(rows + [deepcopy(rows[0])])
        rows[0]["enabled"] = "maybe"
        with self.assertRaisesRegex(authority.SettlementActionAuthorityError, "Invalid enabled"):
            authority.normalize_action_rows(rows)

    def test_runtime_uses_current_settings_and_roles(self):
        rows = authority.initial_action_rows()
        settings = SimpleNamespace(get=lambda key: rows if key == "controlled_action_roles" else None)
        with patch.object(authority.frappe, "get_single", return_value=settings), \
             patch.object(authority.frappe, "get_roles", return_value=["Accounts User"]), \
             patch.object(authority.frappe, "session", SimpleNamespace(user="current")):
            self.assertTrue(authority.can_settlement_action("CREATE_DRAFT_DEBIT_NOTE"))
            with self.assertRaises(authority.frappe.PermissionError):
                authority.require_settlement_action("APPROVE_WAIVER")


class TestSettlementActionRoleValidation(unittest.TestCase):
    def test_missing_role_is_rejected(self):
        rows = authority.initial_action_rows()
        with self.assertRaisesRegex(authority.SettlementActionAuthorityError, "Unknown Role"):
            authority.normalize_action_rows(rows, role_exists=lambda role: role != "Purchase Manager")

    def test_label_is_fixed(self):
        rows = authority.initial_action_rows()
        rows[0]["action_label"] = "Override Approval"
        self.assertEqual(authority.normalize_action_rows(rows)[0]["action_label"],
                         "Create Draft Debit Note")


class TestPurchaseInvoiceContext(unittest.TestCase):
    def test_ordinary_invoice_and_unrelated_return_are_unaffected(self):
        from subcontracting_extensions.scripts import purchase_invoice
        for is_return in (0, 1):
            doc = SimpleNamespace(name="UNRELATED", get=lambda key: {"is_return": is_return}.get(key))
            with patch.object(purchase_invoice.frappe, "get_all", return_value=[]), \
                 patch.object(purchase_invoice.frappe, "get_doc") as get_doc:
                purchase_invoice.require_settlement_debit_note_submit(doc)
                get_doc.assert_not_called()

    def test_removed_header_cannot_bypass_current_lot_link(self):
        from subcontracting_extensions.scripts import purchase_invoice
        doc = SimpleNamespace(name="CONTROLLED", get=lambda key: {"is_return": 1}.get(key))
        with patch.object(purchase_invoice.frappe, "get_all", return_value=[{"name": "PL-1"}]):
            with self.assertRaises(Exception):
                purchase_invoice.require_settlement_debit_note_submit(doc)

    def test_ambiguous_current_links_are_rejected(self):
        from subcontracting_extensions.scripts import purchase_invoice
        doc = SimpleNamespace(name="CONTROLLED", get=lambda key: {
            "custom_processor_lot_settlement": "PL-1", "is_return": 1,
        }.get(key))
        with patch.object(purchase_invoice.frappe, "get_all", return_value=[
            {"name": "PL-1"}, {"name": "PL-2"},
        ]):
            with self.assertRaises(Exception):
                purchase_invoice.require_settlement_debit_note_submit(doc)

    def test_current_link_requires_authority_and_native_submit(self):
        from subcontracting_extensions.scripts import purchase_invoice
        from subcontracting_extensions import settlement_action_authority
        fields = {"custom_processor_lot_settlement": "PL-1", "is_return": 1}
        calls = []
        doc = SimpleNamespace(name="CONTROLLED", get=fields.get,
                              check_permission=lambda kind: calls.append(kind))
        lot_fields = {"debit_note": "CONTROLLED", "generated_document": "CONTROLLED",
                      "generated_document_type": "Purchase Invoice"}
        lot = SimpleNamespace(get=lot_fields.get)
        with patch.object(purchase_invoice.frappe, "get_all", return_value=[{"name": "PL-1"}]), \
             patch.object(purchase_invoice.frappe, "get_doc", return_value=lot), \
             patch.object(settlement_action_authority, "require_settlement_action") as require:
            purchase_invoice.require_settlement_debit_note_submit(doc)
        require.assert_called_once_with("SUBMIT_SETTLEMENT_DEBIT_NOTE")
        self.assertEqual(calls, ["submit"])


class TestOneTimeSeed(unittest.TestCase):
    def test_existing_configuration_is_never_restored(self):
        from subcontracting_extensions.patches.v2_0 import seed_settlement_action_roles
        calls = []
        settings = SimpleNamespace(get=lambda key: ["configured"],
                                   append=lambda *args: calls.append(args))
        with patch.object(seed_settlement_action_roles.frappe, "get_single", return_value=settings):
            seed_settlement_action_roles.execute()
        self.assertEqual(calls, [])

    def test_empty_configuration_is_seeded_once(self):
        from subcontracting_extensions.patches.v2_0 import seed_settlement_action_roles
        calls = []
        settings = SimpleNamespace(get=lambda key: [],
                                   append=lambda field, row: calls.append((field, row)),
                                   save=lambda **kwargs: calls.append(("save", kwargs)))
        with patch.object(seed_settlement_action_roles.frappe, "get_single", return_value=settings), \
             patch.object(seed_settlement_action_roles.frappe.db, "exists", return_value=True):
            seed_settlement_action_roles.execute()
        self.assertEqual(len(calls), 19)
        self.assertEqual(calls[-1], ("save", {"ignore_permissions": True}))


class TestProcessorLotAccountsReadMigration(unittest.TestCase):
    def test_inserts_only_two_read_only_custom_permissions(self):
        from subcontracting_extensions.patches.v2_0 import install_processor_lot_accounts_read as migration
        created = []
        class NewRow:
            def __init__(self, values): self.values = values
            def insert(self, **kwargs): created.append((self.values, kwargs))
        with patch.object(migration.frappe, "get_all", return_value=[]), \
             patch.object(migration.frappe, "get_doc", side_effect=NewRow), \
             patch.object(migration.frappe, "clear_cache") as clear_cache:
            migration.execute()
        self.assertEqual([v["role"] for v, _ in created], ["Accounts User", "Accounts Manager"])
        self.assertTrue(all(v["read"] == 1 and "write" not in v and "submit" not in v
                            and opts == {"ignore_permissions": True} for v, opts in created))
        clear_cache.assert_called_once_with(doctype="Processor Lot")

    def test_existing_wider_custom_permission_is_never_silently_replaced(self):
        from subcontracting_extensions.patches.v2_0 import install_processor_lot_accounts_read as migration
        existing = SimpleNamespace(get=lambda key: {"permlevel": 0, "read": 1,
                                                     "write": 1}.get(key))
        with patch.object(migration.frappe, "get_all", return_value=[{"name": "EXISTING"}]), \
             patch.object(migration.frappe, "get_doc", return_value=existing), \
             patch.object(migration.frappe, "throw", side_effect=ValueError) as fail:
            with self.assertRaises(ValueError):
                migration.execute()
        self.assertTrue(fail.called)
