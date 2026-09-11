"""Immutable J19B2B material-disposition event."""

import frappe
from frappe import _
from frappe.model.document import Document

from subcontracting_extensions.commercial_classification_policy import make_event_key


class ProcessorLotMaterialDispositionEvent(Document):
    def before_insert(self):
        if not self.flags.get("controlled_material_disposition_insert"):
            frappe.throw(_("Material disposition events must be created by the controlled disposition service."))
        self.event_key = make_event_key(self.scope_key, int(self.event_sequence or 0))

    def validate(self):
        if not self.is_new():
            frappe.throw(_("Material disposition events are immutable."))

    def before_rename(self, olddn, newdn, merge=False):
        frappe.throw(_("Material disposition events cannot be renamed."))

    def on_trash(self):
        frappe.throw(_("Material disposition events are permanent audit evidence."))
