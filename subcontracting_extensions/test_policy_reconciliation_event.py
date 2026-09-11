"""J19B2E immutable audit-event controller tests with a Frappe stub."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch


class Rejected(RuntimeError):
    pass


class BaseDocument:
    def __init__(self):
        self.flags = {}
        self.scope_key = "scope"
        self.event_sequence = 1
        self.commercial_document_creation_enabled = 0
        self.commercial_document_authorized = 0
        self.stock_document_authorized = 0
        self.lot_closure_authorized = 0
        self._new = True

    def is_new(self):
        return self._new


class TestPolicyReconciliationEvent(unittest.TestCase):
    def setUp(self):
        frappe = SimpleNamespace(throw=lambda message: (_ for _ in ()).throw(Rejected(message)))
        model_document = SimpleNamespace(Document=BaseDocument)
        path = Path(__file__).parent / "subcontracting_extensions" / "doctype" / \
            "processor_lot_policy_reconciliation_event" / \
            "processor_lot_policy_reconciliation_event.py"
        spec = importlib.util.spec_from_file_location("j19b2e_event_under_test", path)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {
            "frappe": frappe,
            "frappe.model": SimpleNamespace(),
            "frappe.model.document": model_document,
        }):
            frappe._ = lambda value: value
            spec.loader.exec_module(self.module)

    def event(self):
        return self.module.ProcessorLotPolicyReconciliationEvent()

    def test_direct_insert_is_rejected(self):
        with self.assertRaisesRegex(Rejected, "controlled reconciliation service"):
            self.event().before_insert()

    def test_update_rename_and_delete_are_rejected(self):
        event = self.event()
        event._new = False
        with self.assertRaisesRegex(Rejected, "immutable"):
            event.validate()
        with self.assertRaisesRegex(Rejected, "cannot be renamed"):
            event.before_rename("OLD", "NEW")
        with self.assertRaisesRegex(Rejected, "permanent audit evidence"):
            event.on_trash()

    def test_event_cannot_authorize_execution(self):
        event = self.event()
        event.flags["controlled_policy_reconciliation_insert"] = True
        event.before_insert()
        event.commercial_document_authorized = 1
        with self.assertRaisesRegex(Rejected, "cannot authorize execution"):
            event.validate()


if __name__ == "__main__":
    unittest.main()
