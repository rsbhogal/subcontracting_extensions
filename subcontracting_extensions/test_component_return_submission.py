"""J18C controlled submission tests; all documents are in-memory fakes."""

import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from subcontracting_extensions.component_return_submission import (
    enable_component_return_submission,
    submit_component_return_draft,
)


class Row(dict):
    __getattr__ = dict.get


IDENTITY = dict(
    processor_lot="LOT", subcontracting_order="SCO", company="Company",
    supplier="Supplier", source_warehouse="Supplier - C",
    target_warehouse="Raw - C", component_item="Wire",
    subcontracted_item="Finished Wire", stock_uom="Kg",
    sco_supplied_item="RM-A",
)


def report(*, stock=10, blockers=None, drafts=None, code="OPEN_EXISTING_DRAFT_RETURN"):
    return {"components": [dict(
        sco_supplied_item="RM-A", component_return_identity=dict(IDENTITY),
        component_return_code=code, component_return_blockers=blockers or [],
        draft_return_reserved_qty=10, component_return_source_stock_qty=stock,
        draft_component_returns=drafts if drafts is not None else
            [{"name": "STE-DRAFT", "issues": []}],
    )]}


class StockEntry(Row):
    def __init__(self, docstatus=0):
        item = Row(
            sco_rm_detail="RM-A", item_code="Wire",
            subcontracted_item="Finished Wire", qty=10, transfer_qty=10,
            uom="Kg", stock_uom="Kg", conversion_factor=1,
            s_warehouse="Supplier - C", t_warehouse="Raw - C",
        )
        super().__init__(
            name="STE-DRAFT", docstatus=docstatus, purpose="Material Transfer",
            stock_entry_type="Material Transfer", is_return=1, company="Company",
            supplier="Supplier", subcontracting_order="SCO", items=[item],
            doc_references=[Row(link_doctype="Stock Entry",
                                link_name="STE-SOURCE")],
            posting_date=date(2026, 7, 20),
            posting_time=timedelta(hours=12, minutes=57, seconds=1),
            set_posting_time=1,
        )
        self.check_permission = Mock()
        self.submit = Mock(side_effect=self._submit)

    def _submit(self):
        self["docstatus"] = 1


def make_api(*, docstatus=0, settlement_status="Draft", locked=True,
             serial=False, batch=False):
    lot = Row(name="LOT", docstatus=1, settlement_status=settlement_status,
              settlement_date=date(2026, 7, 20))
    lot.check_permission = Mock()
    entry = StockEntry(docstatus)
    docs = {("Processor Lot", "LOT"): lot, ("Stock Entry", "STE-DRAFT"): entry}
    throw = Mock(side_effect=lambda message, exc=None:
                 (_ for _ in ()).throw((exc or RuntimeError)(message)))
    sql = Mock(return_value=[("ROW",)] if locked else [])
    api = SimpleNamespace(
        db=SimpleNamespace(sql=sql), throw=throw,
        get_doc=Mock(side_effect=lambda doctype, name: docs[(doctype, name)]),
        get_cached_doc=Mock(return_value=Row(has_serial_no=serial,
                                             has_batch_no=batch)),
        get_all=Mock(side_effect=lambda doctype, **kwargs: (
            [Row(parent="STE-SOURCE", s_warehouse="Raw - C",
                 t_warehouse="Supplier - C")]
                if doctype == "Stock Entry Detail"
            else [Row(name="STE-SOURCE", is_return=0,
                      subcontracting_order="SCO", company="Company",
                      supplier="Supplier", posting_date=date(2026, 7, 20),
                      posting_time=timedelta(hours=12, minutes=57))]
        )),
    )
    return api, lot, entry


class TestComponentReturnSubmission(unittest.TestCase):
    def test_action_layer_enables_one_clean_selected_draft(self):
        result = enable_component_return_submission(report(), enabled=True)
        row = result["components"][0]
        self.assertTrue(row["component_return_submit_action_available"])
        self.assertEqual(row["component_return_submit_stock_entry"], "STE-DRAFT")
        self.assertEqual(row["component_return_submit_expected_qty"], 10)

    def test_action_layer_is_flag_guarded_and_fails_closed(self):
        self.assertFalse(enable_component_return_submission(
            report(), enabled=False)["components"][0]["component_return_submit_action_available"])
        malformed = report(drafts=[{"name": "A", "issues": []},
                                   {"name": "B", "issues": []}])
        self.assertFalse(enable_component_return_submission(
            malformed, enabled=True)["components"][0]["component_return_submit_action_available"])

    def test_exact_selected_draft_submits_once_under_four_locks(self):
        api, lot, entry = make_api()
        result = submit_component_return_draft(
            api, Mock(return_value=report()), "LOT", "RM-A", "STE-DRAFT", 10
        )
        self.assertEqual(result, {"status": "submitted", "doctype": "Stock Entry",
                                  "name": "STE-DRAFT", "submitted": True})
        lot.check_permission.assert_called_once_with("write")
        entry.check_permission.assert_called_once_with("submit")
        entry.submit.assert_called_once_with()
        self.assertEqual(api.db.sql.call_count, 4)
        self.assertIn("tabProcessor Lot", api.db.sql.call_args_list[0].args[0])
        self.assertIn("tabSubcontracting Order Supplied Item", api.db.sql.call_args_list[1].args[0])
        self.assertIn("tabStock Entry", api.db.sql.call_args_list[2].args[0])
        self.assertIn("tabBin", api.db.sql.call_args_list[3].args[0])

    def test_valid_submitted_replay_is_idempotent(self):
        api, _lot, entry = make_api(docstatus=1)
        result = submit_component_return_draft(
            api, Mock(return_value=report(code="NO_COMPONENT_RETURN_REQUIRED", drafts=[])),
            "LOT", "RM-A", "STE-DRAFT", 10,
        )
        self.assertEqual(result["status"], "already_submitted")
        self.assertFalse(result["submitted"])
        entry.submit.assert_not_called()

    def test_changed_quantity_fails_without_submit(self):
        api, _lot, entry = make_api()
        entry["items"][0]["transfer_qty"] = 9
        with self.assertRaises(RuntimeError):
            submit_component_return_draft(
                api, Mock(return_value=report()), "LOT", "RM-A", "STE-DRAFT", 10
            )
        entry.submit.assert_not_called()

    def test_wrong_named_or_malformed_draft_fails(self):
        api, _lot, entry = make_api()
        with self.assertRaises(RuntimeError):
            submit_component_return_draft(
                api, Mock(return_value=report(drafts=[{"name": "OTHER", "issues": []}])),
                "LOT", "RM-A", "STE-DRAFT", 10,
            )
        entry.submit.assert_not_called()

    def test_changed_header_item_route_uom_and_chronology_fail(self):
        mutations = (
            lambda entry: entry.__setitem__("supplier", "Other"),
            lambda entry: entry["items"][0].__setitem__("item_code", "Other"),
            lambda entry: entry["items"][0].__setitem__("stock_uom", "Units"),
            lambda entry: entry["items"][0].__setitem__("t_warehouse", "Other - C"),
            lambda entry: entry.__setitem__("posting_time", timedelta(hours=12, minutes=58)),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                api, _lot, entry = make_api()
                mutate(entry)
                with self.assertRaises(RuntimeError):
                    submit_component_return_draft(
                        api, Mock(return_value=report()),
                        "LOT", "RM-A", "STE-DRAFT", 10,
                    )
                entry.submit.assert_not_called()

    def test_extra_item_and_changed_conversion_factor_fail(self):
        for change in ("extra", "conversion"):
            with self.subTest(change=change):
                api, _lot, entry = make_api()
                if change == "extra":
                    entry["items"].append(Row(entry["items"][0]))
                else:
                    entry["items"][0]["conversion_factor"] = 2
                with self.assertRaises(RuntimeError):
                    submit_component_return_draft(
                        api, Mock(return_value=report()),
                        "LOT", "RM-A", "STE-DRAFT", 10,
                    )
                entry.submit.assert_not_called()

    def test_missing_or_changed_itc04_reference_fails(self):
        for references in ([], [Row(link_doctype="Stock Entry", link_name="OTHER")]):
            with self.subTest(references=references):
                api, _lot, entry = make_api()
                entry["doc_references"] = references
                with self.assertRaises(RuntimeError):
                    submit_component_return_draft(
                        api, Mock(return_value=report()),
                        "LOT", "RM-A", "STE-DRAFT", 10,
                    )
                entry.submit.assert_not_called()

    def test_cancelled_entry_and_native_submit_failure_propagate(self):
        api, _lot, entry = make_api(docstatus=2)
        with self.assertRaises(RuntimeError):
            submit_component_return_draft(
                api, Mock(return_value=report()), "LOT", "RM-A", "STE-DRAFT", 10
            )
        entry.submit.assert_not_called()

        api, _lot, entry = make_api()
        entry.submit.side_effect = ValueError("native failure")
        with self.assertRaisesRegex(ValueError, "native failure"):
            submit_component_return_draft(
                api, Mock(return_value=report()), "LOT", "RM-A", "STE-DRAFT", 10
            )

    def test_insufficient_locked_source_stock_fails(self):
        api, _lot, entry = make_api()
        with self.assertRaises(RuntimeError):
            submit_component_return_draft(
                api, Mock(return_value=report(stock=9)), "LOT", "RM-A", "STE-DRAFT", 10
            )
        entry.submit.assert_not_called()

    def test_completed_and_started_settlement_states_fail(self):
        for state in ("Completed", "Debit Note Created", "Reopened"):
            with self.subTest(state=state):
                api, _lot, entry = make_api(settlement_status=state)
                with self.assertRaises(RuntimeError):
                    submit_component_return_draft(
                        api, Mock(return_value=report()), "LOT", "RM-A", "STE-DRAFT", 10
                    )
                entry.submit.assert_not_called()

    def test_serial_or_batch_items_fail(self):
        for options in ({"serial": True}, {"batch": True}):
            with self.subTest(options=options):
                api, _lot, entry = make_api(**options)
                with self.assertRaises(RuntimeError):
                    submit_component_return_draft(
                        api, Mock(return_value=report()), "LOT", "RM-A", "STE-DRAFT", 10
                    )
                entry.submit.assert_not_called()

    def test_missing_lock_fails_without_submit(self):
        api, _lot, entry = make_api(locked=False)
        with self.assertRaises(RuntimeError):
            submit_component_return_draft(
                api, Mock(return_value=report()), "LOT", "RM-A", "STE-DRAFT", 10
            )
        entry.submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
