"""J18A component-return preview tests; no document writes."""

import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

from subcontracting_extensions.component_return_preview import (
    assess_component_return_preview,
    read_component_return_preview,
)


class Row(dict):
    __getattr__ = dict.get


def component(**changes):
    row = dict(
        sco_supplied_item="RM-A",
        sco_finished_item="FG-A",
        component_item="Wire",
        stock_uom="Kg",
        physical_remaining_qty=10,
        unaccounted_remaining_qty=10,
        evidence_consistent=True,
        reserve_warehouse="Raw - C",
        reserve_warehouse_company="Company",
        reserve_warehouse_disabled=False,
    )
    row.update(changes)
    return row


def report(*rows):
    return dict(
        processor_lot="LOT",
        subcontracting_order="SCO",
        company="Company",
        supplier="Supplier",
        supplier_warehouse="Supplier - C",
        components=list(rows or [component()]),
    )


def draft(**changes):
    row = dict(
        doctype="Stock Entry",
        name="STE-DRAFT",
        row_name="SED-A",
        docstatus=0,
        subcontracting_order="SCO",
        company="Company",
        supplier="Supplier",
        is_return=1,
        item_code="Wire",
        stock_uom="Kg",
        stock_qty=4,
        sco_rm_detail="RM-A",
        s_warehouse="Supplier - C",
        t_warehouse="Raw - C",
    )
    row.update(changes)
    return row


class TestComponentReturnPreview(unittest.TestCase):
    def test_no_draft_is_ready_but_creation_stays_disabled(self):
        result = assess_component_return_preview(report(), can_prepare=True)
        row = result["components"][0]
        self.assertEqual(row["component_return_code"], "READY_TO_PREPARE_COMPONENT_RETURN")
        self.assertEqual(row["return_qty_available_to_prepare"], 10)
        self.assertFalse(row["component_return_action_available"])
        self.assertFalse(result["component_return_creation_enabled"])

    def test_exact_valid_draft_reserves_only_its_component_uom(self):
        result = assess_component_return_preview(report(), [draft()], can_prepare=True)
        row = result["components"][0]
        self.assertEqual(row["draft_return_reserved_qty"], 4)
        self.assertEqual(row["return_qty_available_to_prepare"], 6)
        self.assertEqual(row["component_return_code"], "OPEN_EXISTING_DRAFT_RETURN")

    def test_same_item_component_rows_are_not_cross_reserved(self):
        second = component(sco_supplied_item="RM-B", sco_finished_item="FG-B")
        result = assess_component_return_preview(report(component(), second), [draft()], can_prepare=True)
        self.assertEqual(
            [row["draft_return_reserved_qty"] for row in result["components"]],
            [4, 0],
        )

    def test_mixed_uom_rows_keep_independent_target_warehouses(self):
        second = component(sco_supplied_item="RM-B", sco_finished_item="FG-B",
            component_item="Blank", stock_uom="Units", reserve_warehouse="Cutting - C")
        result = assess_component_return_preview(report(component(), second), can_prepare=True)
        self.assertEqual(
            [(row["stock_uom"], row["component_return_target_warehouse"])
             for row in result["components"]],
            [("Kg", "Raw - C"), ("Units", "Cutting - C")],
        )
        self.assertNotIn("total_return_qty", result)

    def test_wrong_route_draft_blocks_without_reservation(self):
        row = assess_component_return_preview(
            report(), [draft(t_warehouse="Wrong - C")], can_prepare=True
        )["components"][0]
        self.assertIn("DRAFT_RETURN_ROUTE_MISMATCH", row["component_return_blockers"])
        self.assertEqual(row["draft_return_reserved_qty"], 0)
        self.assertEqual(row["draft_component_returns"][0]["name"], "STE-DRAFT")
        self.assertIn("DRAFT_RETURN_ROUTE_MISMATCH", row["draft_component_returns"][0]["issues"])
        self.assertEqual(row["component_return_code"], "REVIEW_RETURN_EVIDENCE")
        self.assertEqual(row["component_return_label"], "Component return blocked")
        self.assertIn("before any component return is prepared", row["component_return_detail"])
        self.assertNotIn("another return", row["component_return_detail"])

    def test_wrong_item_or_uom_draft_blocks(self):
        row = assess_component_return_preview(
            report(), [draft(stock_uom="Units")], can_prepare=True
        )["components"][0]
        self.assertIn("DRAFT_RETURN_ITEM_UOM_MISMATCH", row["component_return_blockers"])

    def test_over_reserved_and_multiple_drafts_fail_closed(self):
        row = assess_component_return_preview(
            report(), [draft(name="A", stock_qty=7), draft(name="B", row_name="B", stock_qty=6)],
            can_prepare=True,
        )["components"][0]
        self.assertIn("DRAFT_RETURN_EXCEEDS_UNACCOUNTED", row["component_return_blockers"])
        self.assertIn("MULTIPLE_ACTIVE_DRAFT_RETURNS", row["component_return_blockers"])

    def test_no_unaccounted_balance_has_no_return(self):
        row = assess_component_return_preview(
            report(component(physical_remaining_qty=0, unaccounted_remaining_qty=0)),
            can_prepare=True,
        )["components"][0]
        self.assertEqual(row["component_return_code"], "NO_COMPONENT_RETURN_REQUIRED")
        self.assertFalse(row["component_return_prepare_permitted"])

    def test_permission_is_reported_without_enabling_action(self):
        row = assess_component_return_preview(report(), can_prepare=False)["components"][0]
        self.assertEqual(row["component_return_code"], "READ_ONLY_COMPONENT_RETURN")
        self.assertFalse(row["component_return_prepare_permitted"])

    def test_missing_or_disabled_reserve_warehouse_blocks(self):
        missing = assess_component_return_preview(
            report(component(reserve_warehouse=None)), can_prepare=True
        )["components"][0]
        disabled = assess_component_return_preview(
            report(component(reserve_warehouse_disabled=True)), can_prepare=True
        )["components"][0]
        self.assertIn("INCOMPLETE_COMPONENT_RETURN_IDENTITY", missing["component_return_blockers"])
        self.assertIn("RESERVE_WAREHOUSE_DISABLED", disabled["component_return_blockers"])

    def test_inputs_are_unchanged(self):
        source, drafts = report(), [draft()]
        before_source, before_drafts = deepcopy(source), deepcopy(drafts)
        assess_component_return_preview(source, drafts, can_prepare=True)
        self.assertEqual(source, before_source)
        self.assertEqual(drafts, before_drafts)

    def test_reader_discovers_exact_draft_and_native_reserve_warehouse(self):
        lot = Row(name="LOT", supplied_items=[])
        lot.check_permission = Mock()
        lot.has_permission = Mock(return_value=True)
        supplied = Row(name="RM-A", reserve_warehouse="Raw - C")
        sco = Row(name="SCO", company="Company", supplier="Supplier",
            supplier_warehouse="Supplier - C", supplied_items=[supplied])
        sco.check_permission = Mock()
        warehouse = Row(name="Raw - C", company="Company", disabled=0)
        warehouse.check_permission = Mock()
        item = Row(name="SED-A", sco_rm_detail="RM-A", item_code="Wire", stock_uom="Kg",
            transfer_qty=4, s_warehouse="Supplier - C", t_warehouse="Raw - C")
        stock = Row(name="STE-DRAFT", docstatus=0, subcontracting_order="SCO", company="Company",
            supplier="Supplier", is_return=1, items=[item])
        stock.check_permission = Mock()
        docs = {("Processor Lot", "LOT"): lot, ("Subcontracting Order", "SCO"): sco,
            ("Warehouse", "Raw - C"): warehouse, ("Stock Entry", "STE-DRAFT"): stock}
        api = SimpleNamespace(
            get_doc=Mock(side_effect=lambda doctype, name: docs[(doctype, name)]),
            get_all=Mock(return_value=[Row(parent="STE-DRAFT")]),
            has_permission=Mock(return_value=True),
        )
        result = read_component_return_preview(api, report())
        row = result["components"][0]
        self.assertEqual(row["component_return_target_warehouse"], "Raw - C")
        self.assertEqual(row["draft_return_reserved_qty"], 4)
        api.get_all.assert_called_once()
        stock.check_permission.assert_called_once_with("read")


if __name__ == "__main__":
    unittest.main()
