"""J19B2B read and capability tests; no document execution."""

import unittest

from subcontracting_extensions.component_material_disposition import (
    RETAINED_BY_PROCESSOR,
    attach_material_disposition_capabilities,
    attach_material_dispositions,
)
from subcontracting_extensions.commercial_classification_policy import make_scope_key


class Doc(dict):
    def __getattr__(self, key):
        return self.get(key)

    def check_permission(self, permission):
        return None

    def has_permission(self, permission):
        return self.get("can_write", True)


class Store:
    def __init__(self, docs):
        self.docs = docs

    def get_doc(self, doctype, name):
        return self.docs[doctype, name]

    def get_all(self, doctype, filters, fields, limit_page_length):
        return [
            {field: doc.get(field) for field in fields}
            for (candidate, name), doc in self.docs.items()
            if candidate == doctype
            and all(doc.get(field) == value for field, value in filters.items())
        ]


def component(qty=7):
    return {"sco_supplied_item": "RM-A", "sco_finished_item": "FG-A",
            "component_item": "Blank", "stock_uom": "Units",
            "unaccounted_remaining_qty": qty, "evidence_consistent": True}


class TestComponentMaterialDisposition(unittest.TestCase):
    def setUp(self):
        self.lot = Doc(name="LOT", docstatus=0, settlement_status="Draft", can_write=True)
        self.scope = dict(component(), scope_type="Raw Material", processor_lot="LOT")
        self.key = make_scope_key(self.scope)
        self.projection = Doc(name="PLMD-1", processor_lot="LOT", scope_key=self.key,
            sco_supplied_item="RM-A", sco_finished_item="FG-A", component_item="Blank",
            stock_uom="Units", current_disposition=RETAINED_BY_PROCESSOR,
            current_disposition_qty=7, disposition_revision=1,
            last_disposition_event="PLMDE-1", last_decision_by="auditor@example.com",
            last_decision_at="2026-09-10 10:00:00")
        self.event = Doc(name="PLMDE-1", material_disposition="PLMD-1",
            scope_key=self.key, event_sequence=1,
            event_key=__import__("subcontracting_extensions.commercial_classification_policy",
                fromlist=["make_event_key"]).make_event_key(self.key, 1),
            supersedes_event=None, disposition=RETAINED_BY_PROCESSOR,
            disposition_qty=7, stock_uom="Units", reason="Processor confirmed retention",
            decision_by="auditor@example.com", decision_at="2026-09-10 10:00:00",
            commercial_document_authorized=0, stock_document_authorized=0,
            lot_closure_authorized=0)
        self.api = Store({("Processor Lot", "LOT"): self.lot,
            ("Processor Lot Material Disposition", "PLMD-1"): self.projection,
            ("Processor Lot Material Disposition Event", "PLMDE-1"): self.event})

    def test_exact_full_residual_is_current_and_auditable(self):
        result = attach_material_dispositions(self.api, self.lot,
            {"components": [component()]})
        row = result["components"][0]
        self.assertTrue(row["material_disposition_current"])
        self.assertEqual(row["persisted_material_disposition"]["disposition"],
                         RETAINED_BY_PROCESSOR)
        self.assertEqual(len(row["persisted_material_disposition"]["decision_events"]), 1)
        self.assertEqual(result["material_disposition_issues"], [])
        self.assertFalse(result["commercial_document_authorized"])
        self.assertFalse(result["lot_closure_authorized"])

    def test_changed_residual_marks_persisted_quantity_stale(self):
        result = attach_material_dispositions(self.api, self.lot,
            {"components": [component(6)]})
        self.assertFalse(result["components"][0]["material_disposition_current"])
        self.assertIn("MATERIAL_DISPOSITION_QUANTITY_STALE",
                      result["material_disposition_issues"])

    def test_capability_off_exposes_no_choices(self):
        report = {"processor_lot": "LOT", "components": [component()]}
        result = attach_material_disposition_capabilities(self.api, report, enabled=False)
        capability = result["components"][0]["material_disposition_capability"]
        self.assertFalse(capability["entry_available"])
        self.assertEqual(capability["allowed_dispositions"], [])

    def test_capability_offers_only_frozen_server_choices_and_exact_qty(self):
        report = {"processor_lot": "LOT", "components": [component()],
                  "material_disposition_issues": []}
        result = attach_material_disposition_capabilities(self.api, report, enabled=True)
        capability = result["components"][0]["material_disposition_capability"]
        self.assertTrue(capability["entry_available"])
        self.assertEqual([row["value"] for row in capability["allowed_dispositions"]],
                         ["PENDING_INVESTIGATION", "RETAINED_BY_PROCESSOR"])
        self.assertEqual(capability["exact_disposition_qty"], 7)
        self.assertFalse(capability["stock_document_authorized"])


if __name__ == "__main__":
    unittest.main()
