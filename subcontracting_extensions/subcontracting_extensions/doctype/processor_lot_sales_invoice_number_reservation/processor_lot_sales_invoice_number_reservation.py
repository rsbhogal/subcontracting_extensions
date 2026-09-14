"""Immutable J19B2H outward Sales Invoice number reservation."""

import frappe
from frappe import _
from frappe.model.document import Document


class ProcessorLotSalesInvoiceNumberReservation(Document):
    def before_insert(self):
        if not self.flags.get("controlled_number_reservation_insert"):
            frappe.throw(_("Invoice-number reservations require the controlled reservation service."))

    def validate(self):
        if not self.is_new():
            frappe.throw(_("Invoice-number reservations are immutable."))
        if any((self.commercial_document_creation_enabled,
                self.commercial_document_authorized,
                self.stock_document_authorized,
                self.accounting_posting_authorized,
                self.tax_posting_authorized,
                self.lot_closure_authorized)):
            frappe.throw(_("Invoice-number reservation cannot authorize execution."))

    def before_rename(self, olddn, newdn, merge=False):
        frappe.throw(_("Invoice-number reservations cannot be renamed."))

    def on_trash(self):
        frappe.throw(_("Invoice-number reservations are permanent audit evidence."))
