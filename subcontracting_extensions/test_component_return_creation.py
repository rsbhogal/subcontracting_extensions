"""J18B controlled draft-creation tests; all documents are in-memory fakes."""

import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from subcontracting_extensions.component_return_creation import (
    create_component_return_draft,
    enable_component_return_creation,
    set_component_return_posting_datetime,
)


class Row(dict):
    __getattr__ = dict.get


def ready_report(**changes):
    row = dict(
        sco_supplied_item="RM-A",
        sco_finished_item="FG-A",
        component_return_code="READY_TO_PREPARE_COMPONENT_RETURN",
        component_return_prepare_permitted=True,
        return_qty_available_to_prepare=1,
        draft_component_returns=[],
        component_return_identity=dict(
            subcontracting_order="SCO",
            company="Company",
            supplier="Supplier",
            source_warehouse="Supplier - C",
            target_warehouse="Cutting - C",
            component_item="Blank",
            subcontracted_item="Finished Blank",
            stock_uom="Units",
            sco_supplied_item="RM-A",
        ),
    )
    row.update(changes)
    return {"components": [row]}


class FakeStockEntry(Row):
    def __init__(self):
        super().__init__(name=None, items=[], doc_references=[])
        self.posting_date = date(2026, 9, 6)
        self.posting_time = timedelta(hours=13, minutes=52)
        self.set_posting_time = 0
        self.type_set = False
        self.inserted = False

    def update(self, values):
        super().update(values)

    def append(self, fieldname, values):
        self[fieldname].append(Row(values))

    def set_stock_entry_type(self):
        self.type_set = True

    def insert(self):
        self.inserted = True
        self.name = "STE-DRAFT"


def api(stock=None, *, create=True, serial=False, batch=False, locked=True,
        settlement_status="Draft", exact_source=True):
    lot = Row(docstatus=1, settlement_status=settlement_status,
              settlement_date=date(2026, 7, 20))
    lot.check_permission = Mock()
    stock = stock or FakeStockEntry()
    error = RuntimeError
    throw = Mock(side_effect=lambda message, exc=None: (_ for _ in ()).throw((exc or error)(message)))
    return SimpleNamespace(
        PermissionError=PermissionError,
        db=SimpleNamespace(sql=Mock(return_value=[("RM-A",)] if locked else [])),
        get_doc=Mock(return_value=lot),
        has_permission=Mock(return_value=create),
        get_cached_doc=Mock(return_value=Row(has_serial_no=serial, has_batch_no=batch)),
        get_all=Mock(side_effect=lambda doctype, **kwargs: (
            [Row(parent="STE-SOURCE", s_warehouse="Raw - C",
                 t_warehouse="Supplier - C")]
                if doctype == "Stock Entry Detail" and exact_source
            else [Row(
                name="STE-SOURCE", is_return=0, subcontracting_order="SCO",
                company="Company", supplier="Supplier",
                posting_date=date(2026, 7, 20),
                posting_time=timedelta(hours=12, minutes=57),
            )] if doctype == "Stock Entry" and exact_source
            else []
        )),
        new_doc=Mock(return_value=stock),
        throw=throw,
    ), stock, lot


class TestComponentReturnCreation(unittest.TestCase):
    def test_action_requires_flag_and_exact_ready_state(self):
        disabled = enable_component_return_creation(ready_report(), enabled=False)
        enabled = enable_component_return_creation(ready_report(), enabled=True)
        self.assertFalse(disabled["components"][0]["component_return_action_available"])
        self.assertTrue(enabled["components"][0]["component_return_action_available"])
        self.assertEqual(enabled["components"][0]["component_return_expected_qty"], 1)
        self.assertFalse(enabled["component_return_submission_enabled"])

    def test_creates_full_exact_row_draft_with_native_validation(self):
        fake, stock, lot = api()
        result = create_component_return_draft(fake, Mock(return_value=ready_report()),
                                               "LOT", "RM-A", "1")
        self.assertEqual(result, {"status": "created", "doctype": "Stock Entry",
                                  "name": "STE-DRAFT", "created": True})
        lot.check_permission.assert_called_once_with("write")
        fake.db.sql.assert_called_once()
        self.assertEqual(stock["purpose"], "Material Transfer")
        self.assertEqual(stock["is_return"], 1)
        self.assertEqual(stock["subcontracting_order"], "SCO")
        item = stock["items"][0]
        self.assertEqual((item.sco_rm_detail, item.item_code, item.qty, item.stock_uom),
                         ("RM-A", "Blank", 1.0, "Units"))
        self.assertEqual((item.s_warehouse, item.t_warehouse),
                         ("Supplier - C", "Cutting - C"))
        self.assertEqual(item.subcontracted_item, "Finished Blank")
        self.assertEqual(stock["doc_references"], [
            {"link_doctype": "Stock Entry", "link_name": "STE-SOURCE"}
        ])
        self.assertEqual(stock.posting_date, date(2026, 7, 20))
        self.assertEqual(stock.posting_time, timedelta(hours=12, minutes=57, seconds=1))
        self.assertEqual(stock.set_posting_time, 1)
        detail_call, header_call = fake.get_all.call_args_list
        self.assertEqual(detail_call.args[0], "Stock Entry Detail")
        self.assertEqual(
            detail_call.kwargs["filters"]["sco_rm_detail"], "RM-A"
        )
        self.assertEqual(header_call.args[0], "Stock Entry")
        self.assertEqual(
            header_call.kwargs["filters"]["name"], ["in", ["STE-SOURCE"]]
        )
        self.assertTrue(stock.type_set)
        self.assertTrue(stock.inserted)

    def test_existing_valid_draft_is_idempotent(self):
        existing = ready_report(
            component_return_code="OPEN_EXISTING_DRAFT_RETURN",
            draft_component_returns=[{"name": "STE-OLD", "issues": []}],
        )
        fake, stock, _lot = api()
        result = create_component_return_draft(fake, Mock(return_value=existing),
                                               "LOT", "RM-A", 1)
        self.assertEqual(result["name"], "STE-OLD")
        self.assertFalse(result["created"])
        fake.new_doc.assert_not_called()
        self.assertFalse(stock.inserted)

    def test_stale_quantity_and_blocked_state_do_not_insert(self):
        fake, _stock, _lot = api()
        with self.assertRaises(RuntimeError):
            create_component_return_draft(fake, Mock(return_value=ready_report()),
                                          "LOT", "RM-A", 2)
        fake.new_doc.assert_not_called()

    def test_create_permission_is_required_before_lock(self):
        fake, _stock, _lot = api(create=False)
        with self.assertRaises(PermissionError):
            create_component_return_draft(fake, Mock(return_value=ready_report()),
                                          "LOT", "RM-A", 1)
        fake.db.sql.assert_not_called()

    def test_completed_lot_is_blocked_before_create_or_lock(self):
        fake, _stock, _lot = api(settlement_status="Completed")
        with self.assertRaises(RuntimeError):
            create_component_return_draft(fake, Mock(return_value=ready_report()),
                                          "LOT", "RM-A", 1)
        fake.db.sql.assert_not_called()
        fake.new_doc.assert_not_called()

    def test_started_commercial_settlement_is_blocked(self):
        fake, _stock, _lot = api(settlement_status="Debit Note Created")
        with self.assertRaises(RuntimeError):
            create_component_return_draft(fake, Mock(return_value=ready_report()),
                                          "LOT", "RM-A", 1)
        fake.db.sql.assert_not_called()
        fake.new_doc.assert_not_called()

    def test_serial_or_batch_items_fail_closed(self):
        for options in ({"serial": True}, {"batch": True}):
            with self.subTest(options=options):
                fake, _stock, _lot = api(**options)
                with self.assertRaises(RuntimeError):
                    create_component_return_draft(fake, Mock(return_value=ready_report()),
                                                  "LOT", "RM-A", 1)
                fake.new_doc.assert_not_called()

    def test_missing_locked_identity_fails_closed(self):
        fake, _stock, _lot = api(locked=False)
        with self.assertRaises(RuntimeError):
            create_component_return_draft(fake, Mock(return_value=ready_report()),
                                          "LOT", "RM-A", 1)
        fake.new_doc.assert_not_called()

    def test_posting_time_rolls_to_next_day_after_end_of_day_source(self):
        target = SimpleNamespace()
        source = SimpleNamespace(
            posting_date=date(2026, 7, 20),
            posting_time=timedelta(hours=23, minutes=59, seconds=59),
        )
        set_component_return_posting_datetime(
            target, date(2026, 7, 20), source
        )
        self.assertEqual(target.posting_date, date(2026, 7, 21))
        self.assertEqual(target.posting_time, timedelta(0))
        self.assertEqual(target.set_posting_time, 1)

    def test_missing_exact_source_stock_entry_fails_closed(self):
        fake, _stock, _lot = api(exact_source=False)
        with self.assertRaises(RuntimeError):
            create_component_return_draft(fake, Mock(return_value=ready_report()),
                                          "LOT", "RM-A", 1)


if __name__ == "__main__":
    unittest.main()
