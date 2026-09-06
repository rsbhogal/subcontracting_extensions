"""J14 opt-in UI endpoint; J13 remains the authoritative read-only reader."""

import frappe
from frappe.utils import cint

from subcontracting_extensions.material_reconciliation_reader import get_material_position
from subcontracting_extensions.component_action_readiness import assess_component_action_readiness


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
    return dict(report, enabled=True)
