"""Immutable J19B1C commercial decision event."""

import frappe
from frappe import _
from frappe.model.document import Document

from subcontracting_extensions.commercial_classification_policy import make_event_key


class ProcessorLotCommercialDecisionEvent(Document):
    def before_insert(self):
        if not self.flags.get("controlled_commercial_decision_insert"):
            frappe.throw(_("Commercial decision events must be created by the controlled decision service."))
        self._validate_safety_contract()
        self.event_key = make_event_key(self.scope_key, int(self.event_sequence or 0))

    def validate(self):
        if not self.is_new():
            frappe.throw(_("Commercial decision events are immutable."))
        self._validate_safety_contract()

    def _validate_safety_contract(self):
        if any((self.commercial_document_authorized,
                self.stock_document_authorized,
                self.lot_closure_authorized)):
            frappe.throw(_("Commercial decision events cannot authorize execution."))

    def before_rename(self, olddn, newdn, merge=False):
        frappe.throw(_("Commercial decision events cannot be renamed."))

    def on_trash(self):
        frappe.throw(_("Commercial decision events are permanent audit evidence."))
