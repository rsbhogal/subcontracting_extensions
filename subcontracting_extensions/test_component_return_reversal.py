"""J18D controlled component-return reversal tests; no database writes."""

import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from subcontracting_extensions.component_return_reversal import (
    cancel_component_return,
    enable_component_return_reversal,
    read_component_return_reversal,
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


def candidate(*, name="STE-RETURN", qty=10, issues=None, reposts=None):
    return dict(name=name, stock_qty=qty, issues=issues or [],
                repost_item_valuations=reposts or [])


def report(*, candidates=None, target_stock=10, can_cancel=True,
           item_based=0, adjustments=None, settlement_evidence=None):
    return dict(
        processor_lot="LOT", subcontracting_order="SCO",
        processor_lot_docstatus=0, processor_lot_settlement_status="Draft",
        adjustments=adjustments or [], settlement_evidence=settlement_evidence or [],
        components=[dict(
            sco_supplied_item="RM-A", component_return_identity=dict(IDENTITY),
            component_return_target_stock_qty=target_stock,
            component_return_reversal_can_cancel=can_cancel,
            component_return_reversal_item_based_reposting=item_based,
            submitted_component_returns=(candidates if candidates is not None
                                         else [candidate()]),
        )],
    )


class StockEntry(Row):
    def __init__(self, docstatus=1):
        super().__init__(
            name="STE-RETURN", docstatus=docstatus, purpose="Material Transfer",
            stock_entry_type="Material Transfer", is_return=1, company="Company",
            supplier="Supplier", subcontracting_order="SCO",
            posting_date=date(2026, 7, 20),
            posting_time=timedelta(hours=12, minutes=57, seconds=1),
            set_posting_time=1,
            doc_references=[Row(link_doctype="Stock Entry", link_name="STE-SOURCE")],
            items=[Row(sco_rm_detail="RM-A", item_code="Wire",
                       subcontracted_item="Finished Wire", qty=10,
                       transfer_qty=10, uom="Kg", stock_uom="Kg",
                       conversion_factor=1, s_warehouse="Supplier - C",
                       t_warehouse="Raw - C")],
        )
        self.check_permission = Mock()
        self.cancel = Mock(side_effect=self._cancel)

    def _cancel(self):
        self["docstatus"] = 2


def make_api(*, docstatus=1, settlement_status="Draft", item_based=0,
             repost_status=None, locked=True, serial=False, batch=False):
    lot = Row(name="LOT", docstatus=0, settlement_status=settlement_status,
              settlement_date=date(2026, 7, 20))
    lot.check_permission = Mock()
    entry = StockEntry(docstatus)
    state = {"cancelled": False}

    def cancel():
        entry["docstatus"] = 2
        state["cancelled"] = True
    entry.cancel.side_effect = cancel

    def get_all(doctype, **kwargs):
        if doctype == "Stock Entry Detail":
            return [Row(parent="STE-SOURCE", s_warehouse="Raw - C",
                        t_warehouse="Supplier - C")]
        if doctype == "Stock Entry":
            return [Row(name="STE-SOURCE", is_return=0,
                        subcontracting_order="SCO", company="Company",
                        supplier="Supplier", posting_date=date(2026, 7, 20),
                        posting_time=timedelta(hours=12, minutes=57))]
        if doctype == "Repost Item Valuation":
            status = "Queued" if state["cancelled"] else repost_status
            return ([Row(name="RIV-1", docstatus=1, status=status,
                         based_on="Transaction", recreate_stock_ledgers=0)]
                    if status else [])
        return []

    api = SimpleNamespace(
        db=SimpleNamespace(
            sql=Mock(return_value=[("ROW",)] if locked else []),
            get_single_value=Mock(return_value=item_based),
        ),
        throw=Mock(side_effect=lambda message, exc=None:
                   (_ for _ in ()).throw((exc or RuntimeError)(message))),
        get_doc=Mock(side_effect=lambda doctype, name:
                     lot if doctype == "Processor Lot" else entry),
        get_cached_doc=Mock(return_value=Row(has_serial_no=serial,
                                             has_batch_no=batch)),
        get_all=Mock(side_effect=get_all),
    )
    return api, lot, entry


def make_reader_api(*, transfer_qty=10):
    lot = Row(name="LOT", settlement_date=date(2026, 7, 20))
    lot.check_permission = Mock()
    lot.has_permission = Mock(return_value=True)
    returned = StockEntry()
    returned["items"][0]["transfer_qty"] = transfer_qty
    source = Row(name="STE-SOURCE", is_return=0, subcontracting_order="SCO",
                 company="Company", supplier="Supplier",
                 posting_date=date(2026, 7, 20),
                 posting_time=timedelta(hours=12, minutes=57))
    source.check_permission = Mock()

    def get_doc(doctype, name):
        if doctype == "Processor Lot":
            return lot
        return returned if name == "STE-RETURN" else source

    def get_all(doctype, **kwargs):
        if doctype == "Stock Entry Detail":
            if "s_warehouse" in kwargs.get("fields", []):
                return [Row(parent="STE-SOURCE", s_warehouse="Raw - C",
                            t_warehouse="Supplier - C")]
            return [Row(parent="STE-RETURN")]
        if doctype == "Stock Entry":
            return [source]
        return []

    api = SimpleNamespace(
        db=SimpleNamespace(
            get_single_value=Mock(return_value=0),
            get_value=Mock(return_value=10),
        ),
        get_doc=Mock(side_effect=get_doc),
        get_all=Mock(side_effect=get_all),
        has_permission=Mock(return_value=True),
        throw=Mock(side_effect=lambda message, exc=None:
                   (_ for _ in ()).throw((exc or RuntimeError)(message))),
    )
    return api


class TestComponentReturnReversal(unittest.TestCase):
    def test_reader_discovers_one_exact_submitted_return(self):
        api = make_reader_api()
        result = read_component_return_reversal(api, report())
        row = result["components"][0]
        self.assertEqual([item["name"] for item in row["submitted_component_returns"]],
                         ["STE-RETURN"])
        self.assertEqual(row["submitted_component_returns"][0]["issues"], [])
        self.assertEqual(row["component_return_target_stock_qty"], 10)
        self.assertFalse(result["component_return_reversal_enabled"])

    def test_reader_turns_invalid_quantity_into_fail_closed_evidence(self):
        api = make_reader_api(transfer_qty="not-a-number")
        result = read_component_return_reversal(api, report())
        observed = result["components"][0]["submitted_component_returns"]
        self.assertIn("SUBMITTED_COMPONENT_RETURN_INVALID_QUANTITY",
                      observed[0]["issues"])
        enabled = enable_component_return_reversal(result, enabled=True)
        self.assertFalse(enabled["components"][0]["component_return_reversal_action_available"])

    def test_action_is_flag_guarded(self):
        disabled = enable_component_return_reversal(report(), enabled=False)
        self.assertFalse(disabled["components"][0]["component_return_reversal_action_available"])
        enabled = enable_component_return_reversal(report(), enabled=True)
        row = enabled["components"][0]
        self.assertTrue(row["component_return_reversal_action_available"])
        self.assertEqual(row["component_return_reversal_stock_entry"], "STE-RETURN")
        self.assertEqual(row["component_return_reversal_expected_qty"], 10)

    def test_action_fails_closed_for_nonunique_or_malformed_candidates(self):
        cases = (
            ([], "NO_SUBMITTED_COMPONENT_RETURN"),
            ([candidate(), candidate(name="OTHER")],
             "MULTIPLE_SUBMITTED_COMPONENT_RETURNS"),
            ([candidate(issues=["BAD"])],
             "SUBMITTED_COMPONENT_RETURN_REQUIRES_REVIEW"),
            ([candidate(), candidate(name="BAD", issues=["BAD"])],
             "MULTIPLE_SUBMITTED_COMPONENT_RETURNS"),
        )
        for candidates, blocker in cases:
            with self.subTest(candidates=candidates):
                row = enable_component_return_reversal(
                    report(candidates=candidates), enabled=True
                )["components"][0]
                self.assertFalse(row["component_return_reversal_action_available"])
                self.assertIn(blocker, row["component_return_reversal_blockers"])

    def test_action_blocks_item_based_reposting_and_permission(self):
        for kwargs in ({"item_based": 1}, {"can_cancel": False}):
            with self.subTest(kwargs=kwargs):
                row = enable_component_return_reversal(
                    report(**kwargs), enabled=True
                )["components"][0]
                self.assertFalse(row["component_return_reversal_action_available"])

    def test_action_blocks_dependencies_and_repost_review(self):
        variants = (
            report(adjustments=[{"name": "PMA"}]),
            report(settlement_evidence=[{"name": "DN"}]),
            report(candidates=[candidate(reposts=[{"docstatus": 1, "status": "Failed"}])]),
        )
        for value in variants:
            with self.subTest(value=value):
                self.assertFalse(enable_component_return_reversal(
                    value, enabled=True
                )["components"][0]["component_return_reversal_action_available"])

    def test_action_blocks_insufficient_target_stock_and_lifecycle(self):
        insufficient = enable_component_return_reversal(
            report(target_stock=9), enabled=True
        )["components"][0]
        self.assertFalse(insufficient["component_return_reversal_action_available"])
        value = report()
        value["processor_lot_settlement_status"] = "Completed"
        self.assertFalse(enable_component_return_reversal(
            value, enabled=True
        )["components"][0]["component_return_reversal_action_available"])

    def test_exact_return_cancels_under_five_locks_and_reports_pending_repost(self):
        api, lot, entry = make_api()
        result = cancel_component_return(
            api, Mock(side_effect=[report(), report()]),
            "LOT", "RM-A", "STE-RETURN", 10,
        )
        self.assertEqual(result["status"], "cancelled_repost_pending")
        self.assertTrue(result["cancelled"])
        self.assertEqual(api.db.sql.call_count, 5)
        lot.check_permission.assert_called_once_with("write")
        entry.check_permission.assert_called_once_with("cancel")
        entry.cancel.assert_called_once_with()

    def test_cancelled_replay_is_idempotent_without_bin_locks(self):
        api, _lot, entry = make_api(docstatus=2)
        result = cancel_component_return(
            api, Mock(return_value=report(candidates=[])),
            "LOT", "RM-A", "STE-RETURN", 10,
        )
        self.assertEqual(result["status"], "already_cancelled")
        self.assertFalse(result["cancelled"])
        self.assertEqual(api.db.sql.call_count, 3)
        entry.cancel.assert_not_called()

    def test_started_lifecycle_and_item_based_reposting_fail(self):
        for options in ({"settlement_status": "Debit Note Created"},
                        {"item_based": 1}):
            with self.subTest(options=options):
                api, _lot, entry = make_api(**options)
                with self.assertRaises(RuntimeError):
                    cancel_component_return(
                        api, Mock(return_value=report()),
                        "LOT", "RM-A", "STE-RETURN", 10,
                    )
                entry.cancel.assert_not_called()

    def test_in_progress_and_failed_reposts_block(self):
        for status in ("In Progress", "Failed"):
            with self.subTest(status=status):
                api, _lot, entry = make_api(repost_status=status)
                with self.assertRaises(RuntimeError):
                    cancel_component_return(
                        api, Mock(return_value=report()),
                        "LOT", "RM-A", "STE-RETURN", 10,
                    )
                entry.cancel.assert_not_called()

    def test_serial_or_batch_items_fail(self):
        for options in ({"serial": True}, {"batch": True}):
            with self.subTest(options=options):
                api, _lot, entry = make_api(**options)
                with self.assertRaises(RuntimeError):
                    cancel_component_return(
                        api, Mock(return_value=report()),
                        "LOT", "RM-A", "STE-RETURN", 10,
                    )
                entry.cancel.assert_not_called()

    def test_changed_shape_quantity_and_references_fail(self):
        mutations = (
            lambda entry: entry["items"][0].__setitem__("transfer_qty", 9),
            lambda entry: entry.__setitem__("supplier", "Other"),
            lambda entry: entry.__setitem__("doc_references", []),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                api, _lot, entry = make_api()
                mutate(entry)
                with self.assertRaises(RuntimeError):
                    cancel_component_return(
                        api, Mock(return_value=report()),
                        "LOT", "RM-A", "STE-RETURN", 10,
                    )
                entry.cancel.assert_not_called()

    def test_dependencies_and_insufficient_locked_target_stock_fail(self):
        api, _lot, entry = make_api()
        with self.assertRaises(RuntimeError):
            cancel_component_return(
                api, Mock(return_value=report(adjustments=[{"name": "PMA"}])),
                "LOT", "RM-A", "STE-RETURN", 10,
            )
        entry.cancel.assert_not_called()

        api, _lot, entry = make_api()
        with self.assertRaises(RuntimeError):
            cancel_component_return(
                api, Mock(side_effect=[report(), report(target_stock=9)]),
                "LOT", "RM-A", "STE-RETURN", 10,
            )
        entry.cancel.assert_not_called()

    def test_missing_lock_and_native_failure_propagate(self):
        api, _lot, entry = make_api(locked=False)
        with self.assertRaises(RuntimeError):
            cancel_component_return(
                api, Mock(return_value=report()),
                "LOT", "RM-A", "STE-RETURN", 10,
            )
        entry.cancel.assert_not_called()

        api, _lot, entry = make_api()
        entry.cancel.side_effect = ValueError("native failure")
        with self.assertRaisesRegex(ValueError, "native failure"):
            cancel_component_return(
                api, Mock(side_effect=[report(), report()]),
                "LOT", "RM-A", "STE-RETURN", 10,
            )


if __name__ == "__main__":
    unittest.main()
