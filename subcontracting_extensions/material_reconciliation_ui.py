"""J14 opt-in UI endpoint; J13 remains the authoritative read-only reader."""

import frappe
from frappe.utils import cint

from subcontracting_extensions.material_reconciliation_reader import get_material_position
from subcontracting_extensions.component_action_readiness import assess_component_action_readiness
from subcontracting_extensions.component_return_preview import read_component_return_preview
from subcontracting_extensions.component_return_creation import (
    create_component_return_draft,
    enable_component_return_creation,
)
from subcontracting_extensions.component_return_submission import (
    enable_component_return_submission,
    submit_component_return_draft,
)
from subcontracting_extensions.component_return_reversal import (
    cancel_component_return,
    enable_component_return_reversal,
    read_component_return_reversal,
)
from subcontracting_extensions.component_commercial_reader import (
    get_component_commercial_preview,
)
from subcontracting_extensions.component_commercial_classification import (
    record_commercial_decision as persist_commercial_decision,
)


@frappe.whitelist()
def get_material_panel(processor_lot):
    if not cint(frappe.conf.get("v2_processor_first_material_facts")):
        return {"enabled": False}
    report = get_material_position(processor_lot)
    lot = frappe.get_doc("Processor Lot", processor_lot)
    report = assess_component_action_readiness(
        report,
        can_write=bool(lot.has_permission("write")),
    )
    report = read_component_return_preview(frappe, report)
    report = enable_component_return_creation(
        report,
        enabled=bool(cint(frappe.conf.get("v2_component_return_creation"))),
    )
    report = enable_component_return_submission(
        report,
        enabled=bool(cint(frappe.conf.get("v2_component_return_submission"))),
    )
    report = read_component_return_reversal(frappe, report)
    report = enable_component_return_reversal(
        report,
        enabled=bool(cint(frappe.conf.get("v2_component_return_reversal"))),
    )
    return dict(report, enabled=True)


@frappe.whitelist()
def get_commercial_preview_panel(processor_lot):
    """Feature-gated J19A3 read-only commercial preview endpoint."""
    if not cint(frappe.conf.get("v2_component_commercial_preview")):
        return {"enabled": False}
    report = get_component_commercial_preview(processor_lot)
    return dict(report, enabled=True)


@frappe.whitelist()
def record_commercial_decision(processor_lot, scope_type, scope_identity,
                               event_type, variance_direction, decision_value,
                               reason):
    """Feature-gated J19B1C persistence; no treatment execution occurs here."""
    if not cint(frappe.conf.get("v2_component_commercial_classification")):
        frappe.throw("Component commercial classification is not enabled")
    return persist_commercial_decision(
        frappe,
        get_component_commercial_preview,
        processor_lot,
        scope_type,
        frappe.parse_json(scope_identity) if isinstance(scope_identity, str) else scope_identity,
        event_type,
        variance_direction,
        decision_value,
        reason,
    )


def _read_component_return_report(processor_lot):
    report = get_material_position(processor_lot)
    lot = frappe.get_doc("Processor Lot", processor_lot)
    report = assess_component_action_readiness(
        report, can_write=bool(lot.has_permission("write"))
    )
    report = read_component_return_preview(frappe, report)
    return read_component_return_reversal(frappe, report)


@frappe.whitelist()
def prepare_component_return(processor_lot, sco_supplied_item, expected_qty):
    if not cint(frappe.conf.get("v2_component_return_creation")):
        frappe.throw("Component return creation is not enabled")
    return create_component_return_draft(
        frappe,
        _read_component_return_report,
        processor_lot,
        sco_supplied_item,
        expected_qty,
    )


@frappe.whitelist()
def submit_component_return(processor_lot, sco_supplied_item, stock_entry,
                            expected_qty):
    if not cint(frappe.conf.get("v2_component_return_submission")):
        frappe.throw("Component return submission is not enabled")
    return submit_component_return_draft(
        frappe,
        _read_component_return_report,
        processor_lot,
        sco_supplied_item,
        stock_entry,
        expected_qty,
    )


@frappe.whitelist()
def reverse_component_return(processor_lot, sco_supplied_item, stock_entry,
                             expected_qty):
    if not cint(frappe.conf.get("v2_component_return_reversal")):
        frappe.throw("Component return reversal is not enabled")
    return cancel_component_return(
        frappe,
        _read_component_return_report,
        processor_lot,
        sco_supplied_item,
        stock_entry,
        expected_qty,
    )
