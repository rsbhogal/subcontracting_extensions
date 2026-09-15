"""Immutable J19B2L operational statutory-evidence determination."""

import frappe
from frappe import _
from frappe.model.document import Document


class ProcessorLotSalesInvoiceStatutoryEvidenceConfirmation(Document):
    def before_insert(self):
        if not self.flags.get("controlled_statutory_confirmation_insert"):
            frappe.throw(_("Statutory evidence requires the controlled confirmation service."))

    def validate(self):
        if not self.is_new():
            frappe.throw(_("Statutory-evidence confirmations are immutable."))
        if (self.evidence_outcome != "NOT_APPLICABLE_NO_PHYSICAL_MOVEMENT"
                or not self.confirmation_attested):
            frappe.throw(_("An explicit no-physical-movement attestation is required."))
        if any((self.submission_authorized, self.stock_posting_authorized,
                self.accounting_posting_authorized,
                self.statutory_generation_authorized, self.tax_posting_authorized,
                self.lot_closure_authorized)):
            frappe.throw(_("Statutory evidence cannot authorize execution."))

    def before_rename(self, olddn, newdn, merge=False):
        frappe.throw(_("Statutory-evidence confirmations cannot be renamed."))

    def on_trash(self):
        frappe.throw(_("Statutory-evidence confirmations are permanent audit evidence."))
