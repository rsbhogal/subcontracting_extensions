"""J19B2E controlled policy-reconciliation tests; no ERP documents execute."""

import json
import sys
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from subcontracting_extensions.commercial_classification_policy import make_scope_key
from subcontracting_extensions.retained_material_policy_reconciliation import (
    POLICY_FIELDS,
    reconcile_retained_material_policy,
)
from subcontracting_extensions.settlement_method_policy import initial_method_rows


class Doc(dict):
    def __init__(self, **values):
        super().__init__(**values)
        self.flags = SimpleNamespace()

    def __getattr__(self, key):
        if key == "name":
            return self.get("name")
        return super().get(key)

    def check_permission(self, permission):
        if self.get("deny_write") and permission == "write":
            raise PermissionError("write denied")

    def insert(self, ignore_permissions=False):
        self["name"] = "PLPRE-00001"
        self._api.docs[(self["doctype"], self["name"])] = self
        return self


class DB:
    def __init__(self, api):
        self.api = api
        self.updates = []

    def sql(self, query, values):
        return []

    def get_value(self, doctype, filters, fieldname):
        for (candidate, _name), doc in self.api.docs.items():
            if candidate == doctype and all(doc.get(key) == value for key, value in filters.items()):
                return doc.get(fieldname)
        return None

    def set_value(self, doctype, name, values, update_modified=True):
        self.api.docs[(doctype, name)].update(values)
        self.updates.append((doctype, name, deepcopy(values)))


class API:
    def __init__(self):
        self.session = SimpleNamespace(user="Administrator")
        self.docs = {}
        self.db = DB(self)
        self.purchase_invoices = []
        self.material_accounts = []
        self.stock_details = []

    def add(self, doctype, name, **values):
        doc = Doc(name=name, **values)
        self.docs[(doctype, name)] = doc
        return doc

    def get_doc(self, doctype, name=None):
        if isinstance(doctype, dict):
            doc = Doc(**doctype)
            doc._api = self
            return doc
        return self.docs[(doctype, name)]

    def get_single(self, doctype):
        return Doc(allowed_settlement_methods=initial_method_rows())

    def get_roles(self):
        return ["System Manager"]

    def get_all(self, doctype, filters, fields, limit_page_length):
        rows = {
            "Purchase Invoice": self.purchase_invoices,
            "Processor Material Account Entry": self.material_accounts,
            "Stock Entry Detail": self.stock_details,
        }.get(doctype, [])
        return deepcopy(rows[:limit_page_length] if limit_page_length else rows)


def policy(raw=1, processing=1, basis="Company Accepted Quantity",
           remarks=None, shortage="PENDING_INVESTIGATION",
           excess="PENDING_OWNERSHIP_INVESTIGATION", customer=None):
    return dict(
        recover_raw_material_shortage=raw,
        recover_processing_charges_on_shortage=processing,
        settlement_basis=basis,
        settlement_remarks=remarks,
        shortage_settlement_method=shortage,
        excess_settlement_method=excess,
        recovery_customer=customer,
    )


class TestRetainedMaterialPolicyReconciliation(unittest.TestCase):
    def setUp(self):
        self.api = API()
        self.po_policy = policy()
        self.lot_policy = policy(raw=0, processing=0, basis=None)
        self.lot = self.api.add(
            "Processor Lot", "LOT", modified="LOT-MOD", docstatus=0,
            settlement_status="Draft", override_settlement_policy=0,
            subcontracting_order="SCO", purchase_order="PO", supplier="Supplier",
            company="Company", generated_document=None,
            generated_document_type=None, debit_note=None, **self.lot_policy,
        )
        self.sco = self.api.add(
            "Subcontracting Order", "SCO", docstatus=1, purchase_order="PO",
            supplier="Supplier",
        )
        self.po = self.api.add(
            "Purchase Order", "PO", modified="PO-MOD", docstatus=1,
            is_subcontracted=1, supplier="Supplier",
            **{"custom_" + key: value for key, value in self.po_policy.items()},
        )
        self.supplier = self.api.add(
            "Supplier", "Supplier", modified="SUP-MOD",
            custom_recovery_customer="Customer",
        )
        self.customer = self.api.add(
            "Customer", "Customer", modified="CUS-MOD", disabled=0,
        )
        self.identity = dict(
            sco_supplied_item="RM-ROW", sco_finished_item="FG-ROW",
            component_item="Wire Rod Coil", stock_uom="Kg",
        )
        self.scope_key = make_scope_key(dict(
            self.identity, scope_type="Raw Material", processor_lot="LOT"
        ))
        self.row = dict(
            self.identity,
            commercial_decision_code="RAW_MATERIAL_RETAINED_BY_PROCESSOR",
            material_disposition_current=True,
            suggested_recovery_quantity=20000,
            suggested_recovery_rate=58.35,
            suggested_recovery_amount=1167000,
            recovery_quantity_source="PERSISTED_FULL_RESIDUAL_MATERIAL_DISPOSITION",
            suggested_recovery_rate_source="HISTORICAL_SEND_TO_SUBCONTRACTOR",
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
                "blocking_issues": [
                    "PROCESSOR_LOT_POLICY_DIFFERS_FROM_PURCHASE_ORDER",
                    "SHORTAGE_SETTLEMENT_METHOD_PENDING_INVESTIGATION",
                    "RECOVERY_CUSTOMER_NOT_SNAPSHOTTED_ON_PURCHASE_ORDER",
                    "RECOVERY_CUSTOMER_NOT_SNAPSHOTTED_ON_PROCESSOR_LOT",
                ],
                "quantity_ready": True, "rate_ready": True,
                "amount_ready": True, "stock_consequence_ready": True,
            },
        )
        self.api.add("Processor Lot Material Disposition", "PLMD-1")
        self.api.add("Processor Lot Commercial Classification", "PLCC-1")

    def preview(self, processor_lot):
        return {
            "processor_lot": processor_lot,
            "components": [deepcopy(self.row)],
            "policy_issues": ["PROCESSOR_LOT_POLICY_DIFFERS_FROM_PURCHASE_ORDER"],
            "classification_issues": [], "material_disposition_issues": [],
            "settlement_evidence": [],
        }

    def call(self, **changes):
        args = dict(
            api=self.api, read_preview=self.preview, processor_lot="LOT",
            scope_identity=self.identity,
            target_shortage_settlement_method="SALES_INVOICE",
            target_recovery_customer="Customer", reason="Approved contractual reconciliation",
            expected_purchase_order_modified="PO-MOD",
            expected_processor_lot_modified="LOT-MOD",
            expected_supplier_modified="SUP-MOD", expected_customer_modified="CUS-MOD",
            expected_purchase_order_policy=self.po_policy,
            expected_processor_lot_policy=self.lot_policy,
            expected_recovery_quantity=20000, expected_disposition_revision=1,
            expected_last_disposition_event="PLMDE-1",
            expected_classification_revision=1, expected_treatment_revision=0,
            expected_last_decision_event="PLCD-1",
        )
        args.update(changes)
        fake_utils = SimpleNamespace(now_datetime=lambda: "2026-09-11 12:00:00")
        with patch.dict(sys.modules, {"frappe.utils": fake_utils}):
            return reconcile_retained_material_policy(**args)

    def test_reconciles_po_and_complete_lot_snapshot_without_authorizing_execution(self):
        result = self.call()
        self.assertEqual(result["reconciliation_code"], "RETAINED_MATERIAL_POLICY_RECONCILED")
        self.assertEqual(self.po["custom_shortage_settlement_method"], "SALES_INVOICE")
        self.assertEqual(self.po["custom_recovery_customer"], "Customer")
        for field in POLICY_FIELDS:
            expected = "SALES_INVOICE" if field == "shortage_settlement_method" else (
                "Customer" if field == "recovery_customer" else self.po_policy[field]
            )
            self.assertEqual(self.lot[field], expected)
        event = self.api.docs[("Processor Lot Policy Reconciliation Event", "PLPRE-00001")]
        self.assertEqual(json.loads(event["processor_lot_policy_before"]), self.lot_policy)
        for field in ("commercial_document_creation_enabled", "commercial_document_authorized",
                      "stock_document_authorized", "lot_closure_authorized"):
            self.assertFalse(result[field])
            self.assertEqual(event[field], 0)

    def test_stale_po_timestamp_fails_before_writes(self):
        with self.assertRaisesRegex(ValueError, "Purchase Order changed"):
            self.call(expected_purchase_order_modified="STALE")
        self.assertEqual(self.api.db.updates, [])

    def test_changed_supplier_binding_fails_before_writes(self):
        self.supplier["custom_recovery_customer"] = "Other Customer"
        with self.assertRaisesRegex(ValueError, "live Customer bound"):
            self.call()
        self.assertEqual(self.api.db.updates, [])

    def test_changed_retained_quantity_fails_before_writes(self):
        self.row["suggested_recovery_quantity"] = 19999
        with self.assertRaisesRegex(ValueError, "Retained quantity changed"):
            self.call()
        self.assertEqual(self.api.db.updates, [])

    def test_existing_purchase_invoice_fails_before_writes(self):
        self.api.purchase_invoices = [{"name": "PINV-1"}]
        with self.assertRaisesRegex(ValueError, "linked Purchase Invoice"):
            self.call()
        self.assertEqual(self.api.db.updates, [])

    def test_disabled_customer_fails_before_writes(self):
        self.customer["disabled"] = 1
        with self.assertRaisesRegex(ValueError, "Recovery Customer is disabled"):
            self.call()
        self.assertEqual(self.api.db.updates, [])

    def test_changed_classification_revision_fails_before_writes(self):
        self.row["persisted_classification"]["classification_revision"] = 2
        with self.assertRaisesRegex(ValueError, "Commercial classification changed"):
            self.call()
        self.assertEqual(self.api.db.updates, [])

    def test_existing_treatment_fails_before_writes(self):
        classification = self.row["persisted_classification"]
        classification["treatment_revision"] = 1
        classification["selected_treatment_method"] = "SALES_INVOICE"
        with self.assertRaisesRegex(ValueError, "Commercial treatment changed"):
            self.call()
        self.assertEqual(self.api.db.updates, [])

    def test_submitted_processor_lot_fails_before_writes(self):
        self.lot["docstatus"] = 1
        with self.assertRaisesRegex(ValueError, "pre-settlement state"):
            self.call()
        self.assertEqual(self.api.db.updates, [])

    def test_non_system_manager_fails_before_evidence_reads(self):
        self.api.session.user = "user@example.com"
        self.api.get_roles = lambda: ["Accounts Manager"]
        with self.assertRaisesRegex(PermissionError, "System Manager"):
            self.call()
        self.assertEqual(self.api.db.updates, [])


if __name__ == "__main__":
    unittest.main()
