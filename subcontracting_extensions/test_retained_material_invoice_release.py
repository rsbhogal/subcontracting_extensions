"""J19C3A scoped Purchase release checks; no site writes."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from subcontracting_extensions import retained_material_invoice_release as release


def row():
    return {
        "commercial_scope_key": "SCOPE-1", "sco_supplied_item": "RM-1",
        "sco_finished_item": "FG-1",
        "persisted_material_disposition": {"name": "DISP-1", "disposition_revision": 2},
        "persisted_classification": {
            "name": "CLASS-1", "classification_revision": 3,
            "treatment_revision": 1, "last_decision_event": "DECISION-1",
            "selected_treatment_method": "SALES_INVOICE",
        },
        "retained_material_sales_invoice_draft_readiness": {
            "lineage": {"policy_reconciliation_event": "POLICY-1"},
        },
    }


class TestInvoicePreparationRelease(unittest.TestCase):
    def test_current_release_requires_exact_live_evidence(self):
        fields = dict(release._identity(row()), processor_lot="LOT-1")
        doc = SimpleNamespace(get=fields.get, check_permission=Mock())
        api = SimpleNamespace(
            get_all=lambda *args, **kwargs: [{"name": "RELEASE-1"}],
            get_doc=lambda *args: doc,
        )
        self.assertIs(release.require_current_release(api, row(), processor_lot="LOT-1"), doc)
        doc.check_permission.assert_called_once_with("read")
        changed = row()
        changed["persisted_classification"]["treatment_revision"] = 2
        with self.assertRaisesRegex(ValueError, "not released"):
            release.require_current_release(api, changed, processor_lot="LOT-1")

    def test_ambiguous_matching_release_fails_closed(self):
        fields = dict(release._identity(row()), processor_lot="LOT-1")
        doc = SimpleNamespace(get=fields.get, check_permission=Mock())
        api = SimpleNamespace(get_all=lambda *args, **kwargs: [
            {"name": "RELEASE-1"}, {"name": "RELEASE-2"}],
            get_doc=lambda *args: doc)
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            release.current_release(api, row(), processor_lot="LOT-1")

    def test_release_requires_action_before_creating_evidence(self):
        api = SimpleNamespace(new_doc=Mock())
        with patch.object(release, "require_settlement_action", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                release.release_invoice_preparation(
                    api, Mock(), "LOT-1", {}, "reason", "modified",
                    "SCOPE-1", "DECISION-1", 1)
        api.new_doc.assert_not_called()

    def test_release_records_only_exact_current_evidence(self):
        class Event:
            flags = SimpleNamespace()

            def update(self, fields):
                self.fields = fields

            def insert(self, **kwargs):
                self.name = "RELEASE-1"
                self.insert_kwargs = kwargs

        event = Event()
        lot = SimpleNamespace(modified="stamp", docstatus=0, name="LOT-1",
                              get=lambda field: None, check_permission=Mock())
        api = SimpleNamespace(get_doc=lambda *args: lot, new_doc=Mock(return_value=event),
                              session=SimpleNamespace(user="actor"))
        scope = row()
        with patch.object(release, "require_settlement_action"), \
             patch.object(release, "_lock"), \
             patch.object(release, "current_release", return_value=None), \
             patch("frappe.utils.now_datetime", return_value="now"):
            result = release.release_invoice_preparation(
                api, lambda name: {"components": [scope]}, "LOT-1",
                {"sco_supplied_item": "RM-1", "sco_finished_item": "FG-1"},
                "Purchase confirms the invoice handoff", "stamp", "SCOPE-1", "DECISION-1", 1)
        self.assertEqual(result["release"], "RELEASE-1")
        self.assertFalse(result["document_created"])
        self.assertEqual(event.fields["treatment_decision_event"], "DECISION-1")
        self.assertEqual(event.insert_kwargs, {"ignore_permissions": True})
        lot.check_permission.assert_called_once_with("write")
