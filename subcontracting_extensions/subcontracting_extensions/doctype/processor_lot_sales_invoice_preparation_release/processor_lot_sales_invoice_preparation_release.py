"""Immutable exact-scope Purchase handoff."""

import frappe
from frappe.model.document import Document


class ProcessorLotSalesInvoicePreparationRelease(Document):
    def before_insert(self):
        if not self.flags.get("controlled_invoice_preparation_release_insert"):
            frappe.throw("Invoice preparation releases require the controlled service")

    def validate(self):
        if not self.is_new():
            frappe.throw("Invoice preparation releases are immutable")
        if not (self.release_key and self.processor_lot and self.scope_key and self.treatment_decision_event
                and self.policy_reconciliation_event and self.reason and self.released_by):
            frappe.throw("Invoice preparation release evidence is incomplete")

    def before_rename(self, olddn, newdn, merge=False):
        frappe.throw("Invoice preparation releases cannot be renamed")

    def on_trash(self):
        frappe.throw("Invoice preparation releases are permanent audit evidence")
