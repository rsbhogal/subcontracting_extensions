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
    return dict(report, enabled=True)


def _read_component_return_report(processor_lot):
    report = get_material_position(processor_lot)
    lot = frappe.get_doc("Processor Lot", processor_lot)
    report = assess_component_action_readiness(
        report, can_write=bool(lot.has_permission("write"))
    )
    return read_component_return_preview(frappe, report)


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
