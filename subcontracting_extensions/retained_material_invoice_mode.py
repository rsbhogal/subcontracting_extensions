"""Pinned coordination mode for controlled retained-material Sales Invoices."""

DISABLED = "DISABLED"
TALLY = "ERPNEXT_FORECAST_WITH_TALLY_COORDINATION"
ERPNEXT_PRIMARY = "ERPNEXT_PRIMARY"


def creation_mode(settings):
    mode = settings.get("sales_invoice_number_coordination_mode") or DISABLED
    if mode not in (TALLY, ERPNEXT_PRIMARY):
        raise ValueError("Controlled Sales Invoice creation mode is disabled or invalid")
    return mode


def event_mode(event):
    """Pre-mode events always belonged to the Tally-led workflow."""
    mode = event.get("coordination_mode") or TALLY
    if mode not in (TALLY, ERPNEXT_PRIMARY):
        raise ValueError("Controlled Sales Invoice creation mode is invalid")
    if mode == TALLY and not (event.get("reservation") and event.get("tally_confirmation")):
        raise ValueError("Tally-led draft lineage is incomplete")
    if mode == ERPNEXT_PRIMARY and (event.get("reservation") or event.get("tally_confirmation")):
        raise ValueError("ERPNext-primary draft contains Tally lineage")
    return mode


def invoice_mode(invoice, event):
    mode = event_mode(event)
    marker = invoice.get("custom_retained_material_invoice_mode") or (TALLY if mode == TALLY else None)
    if marker != mode:
        raise ValueError("Controlled Sales Invoice mode differs from creation evidence")
    if mode == TALLY:
        if (invoice.get("custom_invoice_number_reservation") != event.get("reservation")
                or invoice.get("custom_tally_reservation_confirmation") != event.get("tally_confirmation")):
            raise ValueError("Tally-led Sales Invoice lineage changed")
    elif (invoice.get("custom_invoice_number_reservation")
          or invoice.get("custom_tally_reservation_confirmation")):
        raise ValueError("ERPNext-primary Sales Invoice contains Tally lineage")
    return mode


def verify_draft_allocation(expected_name, actual_name, counter_before, counter_after,
                            docstatus):
    """Verify the rule allocated exactly the reviewed next number on Draft insert."""
    if docstatus != 0 or actual_name != expected_name:
        raise ValueError("ERPNext assigned a different Draft Sales Invoice number")
    if int(counter_after) != int(counter_before) + 1:
        raise ValueError("Document Naming Rule counter did not advance once")


def erpnext_einvoice_request(applicable, api_enabled, auto_generate,
                            combined_ewaybill_generation=False):
    """Fail before submission when applicable e-Invoice cannot be requested."""
    if applicable and not (api_enabled and auto_generate):
        raise ValueError("ERPNext e-Invoice API and automatic generation must be ready")
    if applicable and combined_ewaybill_generation:
        raise ValueError("Combined e-Invoice and e-Waybill generation conflicts with no physical movement")
    return bool(applicable)
