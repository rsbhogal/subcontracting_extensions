"""Immutable J19B2I Tally reservation confirmation."""

import frappe
from frappe import _
from frappe.model.document import Document


class ProcessorLotSalesInvoiceNumberConfirmation(Document):
    def before_insert(self):
        if not self.flags.get("controlled_tally_confirmation_insert"):
            frappe.throw(_("Tally confirmations require the controlled confirmation service."))

    def validate(self):
        if not self.is_new():
            frappe.throw(_("Tally reservation confirmations are immutable."))
        if self.confirmation_status != "CONFIRMED" or not self.confirmation_attested:
            frappe.throw(_("An explicit confirmed attestation is required."))
        if any((self.commercial_document_creation_enabled,
                self.commercial_document_authorized, self.stock_document_authorized,
                self.accounting_posting_authorized, self.tax_posting_authorized,
                self.lot_closure_authorized)):
            frappe.throw(_("Tally confirmation cannot authorize execution."))

    def before_rename(self, olddn, newdn, merge=False):
        frappe.throw(_("Tally confirmations cannot be renamed."))

    def on_trash(self):
        frappe.throw(_("Tally confirmations are permanent audit evidence."))
