"""J19B2F exact retained-treatment selection tests; execution stays impossible."""

import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from subcontracting_extensions.commercial_classification_policy import make_scope_key
from subcontracting_extensions.retained_material_treatment_selection import (
    select_retained_material_treatment,
)
from subcontracting_extensions.test_retained_material_policy_reconciliation import (
    API,
    policy,
)


class SelectionAPI(API):
    def get_all(self, doctype, filters, fields, limit_page_length, **kwargs):
        if doctype == "Processor Lot Policy Reconciliation Event":
            rows = []
            for (candidate, _name), doc in self.docs.items():
                if candidate == doctype and all(doc.get(key) == value for key, value in filters.items()):
                    rows.append({field: doc.get(field) for field in fields})
            return rows
        return super().get_all(doctype, filters, fields, limit_page_length)


class TestRetainedMaterialTreatmentSelection(unittest.TestCase):
    def setUp(self):
        self.api = SelectionAPI()
        self.current_policy = policy(
            shortage="SALES_INVOICE", customer="Customer"
        )
        self.lot = self.api.add(
            "Processor Lot", "LOT", modified="LOT-MOD", docstatus=0,
            settlement_status="Draft", override_settlement_policy=0,
            settlement_policy_source="Purchase Order", subcontracting_order="SCO",
            purchase_order="PO", supplier="Supplier", company="Company",
            generated_document=None, generated_document_type=None, debit_note=None,
            **self.current_policy,
        )
        self.api.add("Subcontracting Order", "SCO", docstatus=1,
                     purchase_order="PO", supplier="Supplier")
        self.api.add(
            "Purchase Order", "PO", modified="PO-MOD", docstatus=1,
            is_subcontracted=1, supplier="Supplier",
            **{"custom_" + key: value for key, value in self.current_policy.items()},
        )
        self.supplier = self.api.add(
            "Supplier", "Supplier", modified="SUP-MOD",
            custom_recovery_customer="Customer",
        )
        self.customer = self.api.add(
            "Customer", "Customer", modified="CUS-MOD", disabled=0,
        )
        self.identity = dict(sco_supplied_item="RM-ROW", sco_finished_item="FG-ROW",
                             component_item="Wire Rod Coil", stock_uom="Kg")
        self.scope_key = make_scope_key(dict(
            self.identity, scope_type="Raw Material", processor_lot="LOT"
        ))
        self.row = dict(
            self.identity, commercial_scope_key=self.scope_key,
            commercial_decision_code="RAW_MATERIAL_RETAINED_BY_PROCESSOR",
            material_disposition_current=True, suggested_recovery_quantity=20000,
            suggested_recovery_rate=58.35, suggested_recovery_amount=1167000,
            persisted_material_disposition={
                "name": "PLMD-1", "disposition": "RETAINED_BY_PROCESSOR",
                "disposition_revision": 1, "last_disposition_event": "PLMDE-1",
            },
            persisted_classification={
                "name": "PLCC-1", "classification": "PROCESSOR_RESPONSIBLE",
                "classification_revision": 1, "treatment_revision": 0,
                "last_decision_event": "PLCD-1", "selected_treatment_method": None,
            },
            retained_material_treatment_readiness={
                "readiness_code": "RETAINED_MATERIAL_TREATMENT_READY_FOR_FUTURE_EXECUTION_DESIGN",
                "blocking_issues": [], "recommended_treatment": "SALES_INVOICE",
                "recovery_customer_ready": True, "future_update_stock": 1,
                "tax_calculation_status": "DEFERRED_TO_STANDARD_ERPNEXT_SALES_INVOICE_TAX_RESOLUTION",
                "quantity_ready": True, "rate_ready": True, "amount_ready": True,
                "stock_consequence_ready": True,
            },
        )
        self.api.add("Processor Lot Material Disposition", "PLMD-1")
        self.api.add("Processor Lot Commercial Classification", "PLCC-1")
        self.event = self.api.add(
            "Processor Lot Policy Reconciliation Event", "PLPRE-1",
            processor_lot="LOT", purchase_order="PO", scope_key=self.scope_key,
            supplier_bound_customer="Customer",
            target_shortage_settlement_method="SALES_INVOICE",
            recovery_quantity=20000, disposition_revision=1,
            last_disposition_event="PLMDE-1", classification_revision=1,
            treatment_revision=0, last_decision_event="PLCD-1",
            purchase_order_policy_after=json.dumps(self.current_policy),
            processor_lot_policy_after=json.dumps(self.current_policy),
            commercial_document_creation_enabled=0, commercial_document_authorized=0,
            stock_document_authorized=0, lot_closure_authorized=0,
        )

    def preview(self, processor_lot):
        return {"processor_lot": processor_lot, "subcontracting_order": "SCO",
                "components": [deepcopy(self.row)], "policy_issues": [],
                "classification_issues": [], "material_disposition_issues": [],
                "settlement_evidence": []}

    def call(self, **changes):
        values = dict(
            api=self.api, read_preview=self.preview, processor_lot="LOT",
            scope_identity=self.identity, selected_treatment_method="SALES_INVOICE",
            reason="Approved retained-material treatment selection",
            expected_purchase_order_modified="PO-MOD",
            expected_processor_lot_modified="LOT-MOD",
            expected_supplier_modified="SUP-MOD", expected_customer_modified="CUS-MOD",
            expected_policy_reconciliation_event="PLPRE-1",
            expected_recovery_quantity=20000, expected_disposition_revision=1,
            expected_last_disposition_event="PLMDE-1",
            expected_classification_revision=1, expected_treatment_revision=0,
            expected_last_decision_event="PLCD-1",
        )
        values.update(changes)
        selected = {
            "commercial_classification": "PLCC-1", "decision_event": "PLCD-2",
            "classification": "PROCESSOR_RESPONSIBLE",
            "selected_treatment_method": "SALES_INVOICE",
        }
        with patch(
            "subcontracting_extensions.retained_material_treatment_selection.record_commercial_decision",
            return_value=selected,
        ) as persist:
            result = select_retained_material_treatment(**values)
        return result, persist

    def test_selects_only_treatment_and_keeps_every_authorization_false(self):
        result, persist = self.call()
        self.assertEqual(result["selection_code"], "RETAINED_MATERIAL_TREATMENT_SELECTED")
        self.assertEqual(result["treatment_revision"], 1)
        self.assertEqual(result["classification_revision"], 1)
        self.assertEqual(result["selected_treatment_method"], "SALES_INVOICE")
        self.assertTrue(persist.call_args.kwargs["allow_retained_material_treatment"])
        for field in ("commercial_execution_ready", "commercial_document_creation_enabled",
                      "commercial_document_authorized", "stock_document_authorized",
                      "lot_closure_authorized"):
            self.assertFalse(result[field])

    def test_other_method_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "requires SALES_INVOICE"):
            self.call(selected_treatment_method="PURCHASE_DEBIT_NOTE")

    def test_stale_reconciliation_event_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing or ambiguous"):
            self.call(expected_policy_reconciliation_event="OTHER")

    def test_changed_policy_is_rejected(self):
        self.lot["settlement_basis"] = "Manual"
        with self.assertRaisesRegex(ValueError, "policy differs"):
            self.call()

    def test_unsafe_reconciliation_event_is_rejected(self):
        self.event["stock_document_authorized"] = 1
        with self.assertRaisesRegex(ValueError, "unsafe authorization"):
            self.call()

    def test_existing_treatment_is_rejected(self):
        self.row["persisted_classification"].update(
            treatment_revision=1, selected_treatment_method="SALES_INVOICE"
        )
        with self.assertRaisesRegex(ValueError, "Commercial treatment changed"):
            self.call()


if __name__ == "__main__":
    unittest.main()
