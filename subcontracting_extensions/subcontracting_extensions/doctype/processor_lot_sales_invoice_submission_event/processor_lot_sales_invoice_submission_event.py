"""Immutable J19B2M Sales Invoice submission evidence."""

import frappe
from frappe import _
from frappe.model.document import Document


class ProcessorLotSalesInvoiceSubmissionEvent(Document):
    def before_insert(self):
        if not self.flags.get("controlled_sales_invoice_submission_event_insert"):
            frappe.throw(_("Submission events require the controlled submission service."))

    def validate(self):
        if not self.is_new():
            frappe.throw(_("Sales Invoice submission events are immutable."))
        if (not self.submission_confirmed
                or not self.erpnext_statutory_generation_suppressed
                or not self.blank_transport_override_applied):
            frappe.throw(_("Complete controlled submission evidence is required."))
        if self.lot_closure_authorized:
            frappe.throw(_("Sales Invoice submission cannot authorize lot closure."))

    def before_rename(self, olddn, newdn, merge=False):
        frappe.throw(_("Sales Invoice submission events cannot be renamed."))

    def on_trash(self):
        frappe.throw(_("Sales Invoice submission events are permanent audit evidence."))
