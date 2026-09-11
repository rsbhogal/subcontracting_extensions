"""Immutable commercial decision-event safety tests with a Frappe stub."""

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
        self.commercial_document_authorized = 0
        self.stock_document_authorized = 0
        self.lot_closure_authorized = 0
        self._new = True

    def is_new(self):
        return self._new


class TestCommercialDecisionEvent(unittest.TestCase):
    def setUp(self):
        frappe = SimpleNamespace(
            throw=lambda message: (_ for _ in ()).throw(Rejected(message)),
            _=lambda value: value,
        )
        path = Path(__file__).parent / "subcontracting_extensions" / "doctype" / \
            "processor_lot_commercial_decision_event" / \
            "processor_lot_commercial_decision_event.py"
        spec = importlib.util.spec_from_file_location("commercial_event_under_test", path)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {
            "frappe": frappe,
            "frappe.model": SimpleNamespace(),
            "frappe.model.document": SimpleNamespace(Document=BaseDocument),
        }):
            spec.loader.exec_module(self.module)

    def test_direct_insert_is_rejected(self):
        event = self.module.ProcessorLotCommercialDecisionEvent()
        with self.assertRaisesRegex(Rejected, "controlled decision service"):
            event.before_insert()

    def test_any_execution_authorization_is_rejected(self):
        for field in ("commercial_document_authorized", "stock_document_authorized",
                      "lot_closure_authorized"):
            event = self.module.ProcessorLotCommercialDecisionEvent()
            event.flags["controlled_commercial_decision_insert"] = True
            setattr(event, field, 1)
            with self.assertRaisesRegex(Rejected, "cannot authorize execution"):
                event.before_insert()


if __name__ == "__main__":
    unittest.main()
