"""Immutable J19B2J Sales Invoice draft-creation evidence."""

import frappe
from frappe import _
from frappe.model.document import Document


class ProcessorLotSalesInvoiceDraftCreationEvent(Document):
    def before_insert(self):
        if not self.flags.get("controlled_draft_creation_event_insert"):
            frappe.throw(_("Draft-creation events require the controlled service."))

    def validate(self):
        if not self.is_new():
            frappe.throw(_("Draft-creation events are immutable."))
        if not self.draft_only or any((self.submission_authorized,
                self.stock_posting_authorized, self.accounting_posting_authorized,
                self.tax_posting_authorized, self.lot_closure_authorized)):
            frappe.throw(_("Draft creation cannot authorize submission or posting."))

    def before_rename(self, olddn, newdn, merge=False):
        frappe.throw(_("Draft-creation events cannot be renamed."))

    def on_trash(self):
        frappe.throw(_("Draft-creation events are permanent audit evidence."))
