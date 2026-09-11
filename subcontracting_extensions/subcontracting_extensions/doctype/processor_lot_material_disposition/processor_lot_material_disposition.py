"""Exact-scope J19B2B material-disposition projection."""

import frappe
from frappe import _
from frappe.model.document import Document

from subcontracting_extensions.commercial_classification_policy import (
    RAW_MATERIAL,
    canonical_scope,
    make_scope_key,
)


IDENTITY_FIELDS = (
    "processor_lot", "subcontracting_order", "purchase_order", "scope_key",
    "sco_supplied_item", "sco_finished_item", "component_item", "stock_uom",
    "finished_item", "company", "supplier",
)
PROJECTION_FIELDS = (
    "current_disposition", "current_disposition_qty", "disposition_revision",
    "last_disposition_event", "last_decision_by", "last_decision_at",
)


class ProcessorLotMaterialDisposition(Document):
    def before_insert(self):
        if not self.flags.get("controlled_material_disposition_insert"):
            frappe.throw(_("Material dispositions must be created by the controlled disposition service."))
        normalized = canonical_scope(dict(self.as_dict(), scope_type=RAW_MATERIAL))
        for field, value in normalized.items():
            if field != "scope_type":
                self.set(field, value)
        self.scope_key = make_scope_key(normalized)

    def validate(self):
        if self.is_new():
            return
        old = self.get_doc_before_save()
        if not old:
            return
        changed = [field for field in IDENTITY_FIELDS if old.get(field) != self.get(field)]
        if changed:
            frappe.throw(_("Material disposition identity is immutable: {0}").format(", ".join(changed)))
        changed = [field for field in PROJECTION_FIELDS if old.get(field) != self.get(field)]
        if changed and not self.flags.get("controlled_material_disposition_update"):
            frappe.throw(_("Material disposition state may change only through the controlled disposition service."))

    def before_rename(self, olddn, newdn, merge=False):
        frappe.throw(_("Material disposition records cannot be renamed."))

    def on_trash(self):
        frappe.throw(_("Material disposition records are permanent audit evidence."))
