"""J19A2 authoritative reader tests using read-only in-memory evidence."""

import unittest
from copy import deepcopy
from unittest.mock import patch

from subcontracting_extensions import component_commercial_reader as reader


class Doc(dict):
    def __getattr__(self, key):
        if key.startswith("__"):
            raise AttributeError(key)
        return self.get(key)

    def check_permission(self, permission):
        if self.get("denied"):
            raise PermissionError("Evidence unavailable")


class Store:
    def __init__(self):
        self.docs = {}
        self.queries = []

    def add(self, doctype, name, **values):
        doc = Doc(name=name, **values)
        self.docs[doctype, name] = doc
        return doc

    def get_doc(self, doctype, name):
        return self.docs[doctype, name]

    def get_all(self, doctype, filters, fields, limit_page_length):
        self.queries.append((doctype, deepcopy(filters)))
        rows = []
        for (candidate_type, _name), doc in self.docs.items():
            if candidate_type != doctype:
                continue
            matched = True
            for field, expected in filters.items():
                value = doc.get(field)
                if isinstance(expected, list):
                    operator, target = expected
                    if operator == "!=" and value == target:
                        matched = False
                elif value != expected:
                    matched = False
            if matched:
                rows.append(Doc({field: doc.get(field) for field in fields}))
        return rows


def component(key="A", **changes):
    row = dict(sco_supplied_item="RM-" + key, sco_finished_item="FG-" + key,
        component_item="RM ITEM " + key, stock_uom="Kg",
        physical_remaining_qty=0, applied_credit_qty=0,
        evidence_consistent=True, material_settlement_eligible=True)
    row.update(changes)
    return row


class TestComponentCommercialReader(unittest.TestCase):
    def setUp(self):
        self.api = Store()
        header = dict(company="Company", supplier="Supplier", supplier_warehouse="Processor",
            purchase_order="PO")
        self.lot = self.api.add("Processor Lot", "LOT", docstatus=0,
            subcontracting_order="SCO", settlement_status="Draft",
            recover_raw_material_shortage=1, recover_processing_charges_on_shortage=1,
            settlement_basis="Company Accepted Quantity", override_settlement_policy=0, **header)
        self.sco = self.api.add("Subcontracting Order", "SCO", docstatus=1,
            items=[Doc(name="FG-A", item_code="FINISHED A", stock_uom="Kg",
                purchase_order_item="PO-A")], **header)
        self.po = self.api.add("Purchase Order", "PO", docstatus=1,
            company="Company", supplier="Supplier",
            custom_recover_raw_material_shortage=1,
            custom_recover_processing_charges_on_shortage=1,
            custom_settlement_basis="Company Accepted Quantity",
            items=[Doc(name="PO-A", item_code="SERVICE A", stock_uom="Kg")])
        self.supplier = self.api.add(
            "Supplier", "Supplier", custom_recovery_customer=None
        )
        self.pi = self.api.add("Purchase Invoice", "PI", docstatus=1, is_return=0,
            items=[Doc(name="PI-A", po_detail="PO-A", stock_qty=10, stock_uom="Kg",
                rate=2.5, net_rate=2.5, net_amount=25)])
        self.material = dict(processor_lot="LOT", subcontracting_order="SCO",
            components=[component()])
        self.completion = dict(items=[Doc(subcontracting_order_item="FG-A",
            submitted_scr_qty=10, journey_complete=True, issues=[])], journeys=[Doc(
                pi_verified=True, purchase_invoice="PI", pi_detail="PI-A")])

    def read(self):
        return reader._read_component_commercial_preview(
            self.api, "LOT", material_position=self.material,
            completion_position=self.completion)

    def test_exact_verified_row_produces_no_recovery(self):
        result = self.read()
        self.assertEqual(result["commercial_reader_version"], "J19A2")
        self.assertEqual(result["commercial_decision_code"],
            "COMMERCIAL_REVIEW_COMPLETE_NO_RECOVERY")
        self.assertEqual(result["finished_items"][0]["matched_invoice_rows"][0][
            "purchase_invoice_item"], "PI-A")

    def test_safe_method_defaults_are_reported_ready_without_customer(self):
        result = self.read()
        policy = result["settlement_policy"]
        self.assertEqual(policy["shortage_settlement_method"],
                         "PENDING_INVESTIGATION")
        self.assertEqual(policy["excess_settlement_method"],
                         "PENDING_OWNERSHIP_INVESTIGATION")
        self.assertTrue(policy["shortage_method_enabled"])
        self.assertTrue(policy["excess_method_enabled"])
        self.assertFalse(policy["recovery_customer_required"])
        self.assertTrue(policy["recovery_customer_ready"])
        self.assertTrue(policy["policy_ready"])

    def test_sales_invoice_requires_exact_enabled_supplier_binding(self):
        self.supplier["custom_recovery_customer"] = "RECOVERY-CUSTOMER"
        self.api.add("Customer", "RECOVERY-CUSTOMER", disabled=0)
        self.po.update(
            custom_shortage_settlement_method="SALES_INVOICE",
            custom_recovery_customer="RECOVERY-CUSTOMER",
        )
        self.lot.update(
            shortage_settlement_method="SALES_INVOICE",
            recovery_customer="RECOVERY-CUSTOMER",
        )
        result = self.read()
        policy = result["settlement_policy"]
        self.assertTrue(policy["recovery_customer_required"])
        self.assertTrue(policy["recovery_customer_ready"])
        self.assertTrue(policy["policy_ready"])
        self.assertEqual(policy["recovery_customer"], "RECOVERY-CUSTOMER")

    def test_wrong_recovery_customer_fails_policy_readiness(self):
        self.supplier["custom_recovery_customer"] = "RECOVERY-CUSTOMER"
        self.api.add("Customer", "WRONG-CUSTOMER", disabled=0)
        self.po.update(
            custom_shortage_settlement_method="SALES_INVOICE",
            custom_recovery_customer="WRONG-CUSTOMER",
        )
        self.lot.update(
            shortage_settlement_method="SALES_INVOICE",
            recovery_customer="WRONG-CUSTOMER",
        )
        result = self.read()
        self.assertFalse(result["settlement_policy"]["policy_ready"])
        self.assertIn("RECOVERY_CUSTOMER_NOT_READY", result["policy_issues"])
        self.assertFalse(result["commercial_document_authorized"])
        self.assertFalse(result["lot_closure_authorized"])

    def test_processing_variance_is_finished_row_scoped(self):
        self.pi["items"][0].update(stock_qty=12, net_amount=30)
        result = self.read()
        row = result["finished_items"][0]
        self.assertEqual(row["commercial_variance_qty"], 2)
        self.assertEqual(row["processing_recovery_amount"], 5)

    def test_incomplete_journey_fails_closed(self):
        self.completion["items"][0].update(journey_complete=False,
            issues=["PI_ROW_LINEAGE_MISMATCH"])
        self.completion["journeys"][0]["pi_verified"] = False
        result = self.read()
        self.assertEqual(result["commercial_decision_code"],
            "REVIEW_FINISHED_ITEM_COMMERCIAL_EVIDENCE")

    def test_unrelated_invoice_row_is_not_imported(self):
        self.pi["items"].append(Doc(name="OTHER", po_detail="OTHER", stock_qty=999,
            stock_uom="Kg", net_rate=2.5, net_amount=2497.5))
        self.assertEqual(self.read()["finished_items"][0]["supplier_invoice_qty"], 10)

    def test_duplicate_verified_pi_row_is_rejected(self):
        self.completion["journeys"].append(deepcopy(self.completion["journeys"][0]))
        with self.assertRaisesRegex(ValueError, "duplicated"):
            self.read()

    def test_wrong_po_row_uom_is_rejected(self):
        self.po["items"][0]["stock_uom"] = "Units"
        with self.assertRaisesRegex(ValueError, "Purchase Order row"):
            self.read()

    def test_legacy_debit_note_is_visible_and_blocks(self):
        self.api.add("Purchase Invoice", "DN", docstatus=1,
            custom_processor_lot_settlement="LOT", items=[])
        result = self.read()
        self.assertEqual(result["commercial_decision_code"],
            "REVIEW_LEGACY_COMMERCIAL_EVIDENCE")

    def test_exact_pma_is_not_misclassified_as_legacy(self):
        self.api.add("Processor Material Account Entry", "PMA", docstatus=1,
            processor_lot="LOT", sco_supplied_item="RM-A")
        self.assertEqual(self.read()["legacy_evidence"], [])

    def test_pma_without_component_identity_is_legacy(self):
        self.api.add("Processor Material Account Entry", "PMA", docstatus=1,
            processor_lot="LOT", sco_supplied_item=None)
        self.assertEqual(self.read()["commercial_decision_code"],
            "REVIEW_LEGACY_COMMERCIAL_EVIDENCE")

    def test_sco_wide_material_settlement_evidence_is_not_ignored(self):
        self.material["settlement_evidence"] = [{
            "doctype": "Purchase Invoice",
            "name": "OTHER-LOT-DN",
            "reason": "Debit Note",
        }]
        result = self.read()
        self.assertEqual(result["commercial_decision_code"],
            "REVIEW_LEGACY_COMMERCIAL_EVIDENCE")
        self.assertEqual(result["legacy_evidence"][0]["name"], "OTHER-LOT-DN")

    def test_policy_mismatch_is_rejected(self):
        self.lot["recover_raw_material_shortage"] = 0
        result = self.read()
        self.assertEqual(result["commercial_decision_code"], "REVIEW_SETTLEMENT_POLICY")
        self.assertEqual(result["policy_issues"],
            ["PROCESSOR_LOT_POLICY_DIFFERS_FROM_PURCHASE_ORDER"])

    def test_incomplete_override_is_rejected(self):
        self.lot["override_settlement_policy"] = 1
        result = self.read()
        self.assertEqual(result["commercial_decision_code"], "REVIEW_SETTLEMENT_POLICY")
        self.assertEqual(result["policy_issues"],
            ["SETTLEMENT_POLICY_OVERRIDE_EVIDENCE_INCOMPLETE"])

    def test_material_blocker_precedes_policy_issue(self):
        self.lot["recover_raw_material_shortage"] = 0
        self.material["components"][0]["material_settlement_eligible"] = False
        result = self.read()
        self.assertEqual(result["commercial_decision_code"], "ACCOUNT_REMAINING_MATERIAL")

    def test_sco_wide_evidence_deduplicates_by_document(self):
        self.api.add("Purchase Invoice", "DN", docstatus=1,
            custom_processor_lot_settlement="LOT", items=[])
        self.material["settlement_evidence"] = [{
            "doctype": "Purchase Invoice", "name": "DN", "reason": "Debit Note"}]
        result = self.read()
        self.assertEqual(len(result["legacy_evidence"]), 1)

    def test_denied_lot_stops_before_other_reads(self):
        self.lot["denied"] = True
        with self.assertRaises(PermissionError):
            self.read()
        self.assertEqual(self.api.queries, [])

    def test_denied_invoice_aborts(self):
        self.pi["denied"] = True
        with self.assertRaises(PermissionError):
            self.read()

    def test_cancelled_lot_and_draft_sco_are_rejected(self):
        self.lot["docstatus"] = 2
        with self.assertRaisesRegex(ValueError, "active lot"):
            self.read()
        self.lot["docstatus"] = 0
        self.sco["docstatus"] = 0
        with self.assertRaisesRegex(ValueError, "submitted SCO"):
            self.read()

    def test_every_mutating_outcome_remains_disabled(self):
        result = self.read()
        self.assertFalse(result["commercial_document_creation_enabled"])
        self.assertFalse(result["commercial_document_authorized"])
        self.assertFalse(result["lot_closure_authorized"])

    def test_supplied_inputs_are_not_mutated(self):
        before = deepcopy((self.material, self.completion))
        self.read()
        self.assertEqual((self.material, self.completion), before)

    def test_console_entry_uses_authoritative_reader(self):
        expected = {"commercial_reader_version": "J19A2"}
        with patch.dict("sys.modules", {"frappe": self.api}), patch.object(
            reader, "_read_component_commercial_preview", return_value=expected
        ) as read:
            self.assertEqual(reader.get_component_commercial_preview("LOT"), expected)
        read.assert_called_once_with(self.api, "LOT")


if __name__ == "__main__":
    unittest.main()
