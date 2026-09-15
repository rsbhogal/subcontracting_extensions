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
    attach_decision_capabilities,
    record_commercial_decision as persist_commercial_decision,
)
from subcontracting_extensions.component_material_disposition import (
    attach_material_disposition_capabilities,
    record_material_disposition as persist_material_disposition,
)
from subcontracting_extensions.retained_material_policy_reconciliation import (
    reconcile_retained_material_policy as persist_retained_material_policy_reconciliation,
)
from subcontracting_extensions.retained_material_treatment_selection import (
    select_retained_material_treatment as persist_retained_material_treatment,
)
from subcontracting_extensions.sales_invoice_number_reservation import (
    reserve_sales_invoice_number as persist_sales_invoice_number_reservation,
)
from subcontracting_extensions.tally_invoice_number_confirmation import (
    confirm_tally_reservation as persist_tally_reservation_confirmation,
)
from subcontracting_extensions.retained_material_sales_invoice_draft_creation import (
    create_sales_invoice_draft as persist_retained_material_sales_invoice_draft,
)
from subcontracting_extensions.tally_statutory_evidence_confirmation import (
    confirm_no_physical_movement as persist_no_physical_movement_confirmation,
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
    report = attach_material_disposition_capabilities(
        frappe,
        report,
        enabled=bool(cint(frappe.conf.get("v2_component_material_disposition"))),
    )
    report = attach_decision_capabilities(
        frappe,
        report,
        enabled=bool(cint(frappe.conf.get("v2_component_commercial_classification"))),
    )
    return dict(report, enabled=True)


@frappe.whitelist()
def record_component_material_disposition(processor_lot, scope_identity,
                                          disposition, reason,
                                          expected_revision=0,
                                          expected_last_event=None):
    """Feature-gated J19B2B evidence persistence; no document is created."""
    if not cint(frappe.conf.get("v2_component_material_disposition")):
        frappe.throw("Component material disposition is not enabled")
    return persist_material_disposition(
        frappe,
        get_component_commercial_preview,
        processor_lot,
        frappe.parse_json(scope_identity) if isinstance(scope_identity, str) else scope_identity,
        disposition,
        reason,
        expected_revision,
        expected_last_event,
    )


@frappe.whitelist()
def record_commercial_decision(processor_lot, scope_type, scope_identity,
                               event_type, variance_direction, decision_value,
                               reason, expected_classification_revision=0,
                               expected_treatment_revision=0,
                               expected_last_decision_event=None):
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
        expected_classification_revision,
        expected_treatment_revision,
        expected_last_decision_event,
    )


@frappe.whitelist()
def reconcile_retained_material_policy(
    processor_lot, scope_identity, target_shortage_settlement_method,
    target_recovery_customer, reason, expected_purchase_order_modified,
    expected_processor_lot_modified, expected_supplier_modified,
    expected_customer_modified, expected_purchase_order_policy,
    expected_processor_lot_policy, expected_recovery_quantity,
    expected_disposition_revision, expected_last_disposition_event,
    expected_classification_revision, expected_treatment_revision,
    expected_last_decision_event,
):
    """Feature-gated J19B2E policy reconciliation; never executes settlement."""
    if not cint(frappe.conf.get("v2_retained_material_policy_reconciliation")):
        frappe.throw("Retained-material policy reconciliation is not enabled")
    return persist_retained_material_policy_reconciliation(
        frappe, get_component_commercial_preview, processor_lot,
        _parse_json(scope_identity), target_shortage_settlement_method,
        target_recovery_customer, reason, expected_purchase_order_modified,
        expected_processor_lot_modified, expected_supplier_modified,
        expected_customer_modified, _parse_json(expected_purchase_order_policy),
        _parse_json(expected_processor_lot_policy), expected_recovery_quantity,
        expected_disposition_revision, expected_last_disposition_event,
        expected_classification_revision, expected_treatment_revision,
        expected_last_decision_event,
    )


def _parse_json(value):
    return frappe.parse_json(value) if isinstance(value, str) else value


@frappe.whitelist()
def select_retained_material_treatment(
    processor_lot, scope_identity, selected_treatment_method, reason,
    expected_purchase_order_modified, expected_processor_lot_modified,
    expected_supplier_modified, expected_customer_modified,
    expected_policy_reconciliation_event, expected_recovery_quantity,
    expected_disposition_revision, expected_last_disposition_event,
    expected_classification_revision, expected_treatment_revision,
    expected_last_decision_event,
):
    """Feature-gated J19B2F selection; never executes the selected treatment."""
    if not cint(frappe.conf.get("v2_retained_material_treatment_selection")):
        frappe.throw("Retained-material treatment selection is not enabled")
    return persist_retained_material_treatment(
        frappe, get_component_commercial_preview, processor_lot,
        _parse_json(scope_identity), selected_treatment_method, reason,
        expected_purchase_order_modified, expected_processor_lot_modified,
        expected_supplier_modified, expected_customer_modified,
        expected_policy_reconciliation_event, expected_recovery_quantity,
        expected_disposition_revision, expected_last_disposition_event,
        expected_classification_revision, expected_treatment_revision,
        expected_last_decision_event,
    )


@frappe.whitelist()
def reserve_retained_material_sales_invoice_number(
    processor_lot, scope_identity, reason, expected_invoice_number,
    expected_naming_rule_snapshot, expected_purchase_order_modified,
    expected_processor_lot_modified, expected_supplier_modified,
    expected_customer_modified, expected_recovery_quantity,
    expected_material_content_rate, expected_net_material_amount,
    expected_supplier_warehouse_qty, expected_supplier_warehouse_valuation_rate,
    expected_supplier_warehouse_stock_value, expected_disposition_revision,
    expected_last_disposition_event, expected_classification_revision,
    expected_treatment_revision, expected_last_decision_event,
    expected_policy_reconciliation_event,
):
    """Feature-gated J19B2H number reservation; never creates an invoice."""
    if not cint(frappe.conf.get("v2_retained_material_invoice_number_reservation")):
        frappe.throw("Retained-material Sales Invoice number reservation is not enabled")
    return persist_sales_invoice_number_reservation(
        frappe, get_component_commercial_preview, processor_lot,
        _parse_json(scope_identity), reason, expected_invoice_number,
        _parse_json(expected_naming_rule_snapshot), expected_purchase_order_modified,
        expected_processor_lot_modified, expected_supplier_modified,
        expected_customer_modified, expected_recovery_quantity,
        expected_material_content_rate, expected_net_material_amount,
        expected_supplier_warehouse_qty, expected_supplier_warehouse_valuation_rate,
        expected_supplier_warehouse_stock_value, expected_disposition_revision,
        expected_last_disposition_event, expected_classification_revision,
        expected_treatment_revision, expected_last_decision_event,
        expected_policy_reconciliation_event,
    )


@frappe.whitelist()
def confirm_retained_material_invoice_number_reserved_in_tally(
    reservation, reason, confirmation_attested, expected_reservation_modified,
    expected_invoice_number, expected_naming_rule_snapshot,
    expected_purchase_order_modified, expected_processor_lot_modified,
    expected_supplier_modified, expected_customer_modified,
    expected_recovery_quantity, expected_material_content_rate,
    expected_net_material_amount, expected_supplier_warehouse_qty,
    expected_supplier_warehouse_valuation_rate,
    expected_supplier_warehouse_stock_value, expected_disposition_revision,
    expected_last_disposition_event, expected_classification_revision,
    expected_treatment_revision, expected_last_decision_event,
    expected_policy_reconciliation_event,
):
    """Feature-gated J19B2I attestation; never creates or authorizes an invoice."""
    if not cint(frappe.conf.get("v2_retained_material_tally_reservation_confirmation")):
        frappe.throw("Retained-material Tally reservation confirmation is not enabled")
    return persist_tally_reservation_confirmation(
        frappe, get_component_commercial_preview, reservation, reason,
        confirmation_attested, expected_reservation_modified,
        expected_invoice_number, _parse_json(expected_naming_rule_snapshot),
        expected_purchase_order_modified, expected_processor_lot_modified,
        expected_supplier_modified, expected_customer_modified,
        expected_recovery_quantity, expected_material_content_rate,
        expected_net_material_amount, expected_supplier_warehouse_qty,
        expected_supplier_warehouse_valuation_rate,
        expected_supplier_warehouse_stock_value, expected_disposition_revision,
        expected_last_disposition_event, expected_classification_revision,
        expected_treatment_revision, expected_last_decision_event,
        expected_policy_reconciliation_event,
    )


@frappe.whitelist()
def create_retained_material_sales_invoice_draft(
    reservation, confirmation, reason, draft_creation_confirmed,
    expected_reservation_modified, expected_confirmation_modified,
    expected_invoice_number, expected_naming_rule_snapshot,
    expected_purchase_order_modified, expected_processor_lot_modified,
    expected_supplier_modified, expected_customer_modified, expected_posting_date,
    expected_recovery_quantity, expected_material_content_rate,
    expected_net_material_amount, expected_supplier_warehouse_qty,
    expected_supplier_warehouse_valuation_rate,
    expected_supplier_warehouse_stock_value, expected_disposition_revision,
    expected_last_disposition_event, expected_classification_revision,
    expected_treatment_revision, expected_last_decision_event,
    expected_policy_reconciliation_event, expected_taxes_and_charges,
    expected_item_tax_template, expected_tax_rows, expected_total_taxes,
    expected_grand_total,
):
    """Feature-gated J19B2J: create one draft; never submit or post it."""
    if not cint(frappe.conf.get("v2_retained_material_sales_invoice_draft_creation")):
        frappe.throw("Retained-material Sales Invoice draft creation is not enabled")
    return persist_retained_material_sales_invoice_draft(
        frappe, get_component_commercial_preview, reservation, confirmation, reason,
        draft_creation_confirmed, expected_reservation_modified,
        expected_confirmation_modified, expected_invoice_number,
        _parse_json(expected_naming_rule_snapshot), expected_purchase_order_modified,
        expected_processor_lot_modified, expected_supplier_modified,
        expected_customer_modified, expected_posting_date, expected_recovery_quantity,
        expected_material_content_rate, expected_net_material_amount,
        expected_supplier_warehouse_qty, expected_supplier_warehouse_valuation_rate,
        expected_supplier_warehouse_stock_value, expected_disposition_revision,
        expected_last_disposition_event, expected_classification_revision,
        expected_treatment_revision, expected_last_decision_event,
        expected_policy_reconciliation_event, expected_taxes_and_charges,
        expected_item_tax_template, _parse_json(expected_tax_rows),
        expected_total_taxes, expected_grand_total,
    )


@frappe.whitelist()
def confirm_retained_material_no_physical_movement(
    sales_invoice, reason, confirmation_attested, expected_invoice_modified,
    expected_scope_key, expected_draft_creation_event, expected_reservation,
    expected_tally_confirmation, expected_disposition_revision,
    expected_classification_revision, expected_treatment_revision,
    expected_statutory_evidence,
):
    """Feature-gated J19B2L determination; never submits or posts the invoice."""
    if not cint(frappe.conf.get(
            "v2_retained_material_tally_statutory_evidence_confirmation")):
        frappe.throw("Retained-material Tally statutory evidence confirmation is not enabled")
    return persist_no_physical_movement_confirmation(
        frappe, get_component_commercial_preview, sales_invoice, reason,
        confirmation_attested, expected_invoice_modified, expected_scope_key,
        expected_draft_creation_event, expected_reservation,
        expected_tally_confirmation, expected_disposition_revision,
        expected_classification_revision, expected_treatment_revision,
        _parse_json(expected_statutory_evidence),
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
