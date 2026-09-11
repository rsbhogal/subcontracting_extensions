"""Immutable J19B2E retained-material policy reconciliation evidence."""

import frappe
from frappe import _
from frappe.model.document import Document

from subcontracting_extensions.commercial_classification_policy import make_event_key


class ProcessorLotPolicyReconciliationEvent(Document):
    def before_insert(self):
        if not self.flags.get("controlled_policy_reconciliation_insert"):
            frappe.throw(_("Policy reconciliation events must be created by the controlled reconciliation service."))
        self.event_key = make_event_key(self.scope_key, int(self.event_sequence or 0))

    def validate(self):
        if not self.is_new():
            frappe.throw(_("Policy reconciliation events are immutable."))
        if any((self.commercial_document_creation_enabled,
                self.commercial_document_authorized,
                self.stock_document_authorized,
                self.lot_closure_authorized)):
            frappe.throw(_("Policy reconciliation cannot authorize execution."))

    def before_rename(self, olddn, newdn, merge=False):
        frappe.throw(_("Policy reconciliation events cannot be renamed."))

    def on_trash(self):
        frappe.throw(_("Policy reconciliation events are permanent audit evidence."))
