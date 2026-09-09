"""Stable exact-scope registry for J19B1C commercial decisions."""

import frappe
from frappe import _
from frappe.model.document import Document

from subcontracting_extensions.commercial_classification_policy import (
    canonical_scope,
    make_scope_key,
)


IDENTITY_FIELDS = (
    "processor_lot", "subcontracting_order", "purchase_order", "company", "supplier",
    "scope_type", "scope_key", "sco_supplied_item", "sco_finished_item",
    "component_item", "stock_uom", "purchase_order_item", "finished_item",
)
PROJECTION_FIELDS = (
    "current_variance_direction", "current_classification",
    "current_treatment_method", "classification_revision", "treatment_revision",
    "last_decision_event", "last_decision_by", "last_decision_at",
)


class ProcessorLotCommercialClassification(Document):
    def before_insert(self):
        if not self.flags.get("controlled_commercial_classification_insert"):
            frappe.throw(_("Commercial classifications must be created by the controlled decision service."))
        normalized = canonical_scope(self.as_dict())
        for field, value in normalized.items():
            self.set(field, value)
        self.scope_key = make_scope_key(normalized)

    def validate(self):
        if self.is_new():
            return
        old = self.get_doc_before_save()
        if not old:
            return
        changed_identity = [field for field in IDENTITY_FIELDS if old.get(field) != self.get(field)]
        if changed_identity:
            frappe.throw(_("Commercial classification identity is immutable: {0}").format(", ".join(changed_identity)))
        changed_projection = [field for field in PROJECTION_FIELDS if old.get(field) != self.get(field)]
        if changed_projection and not self.flags.get("controlled_commercial_decision_update"):
            frappe.throw(_("Commercial classification state may change only through the controlled decision service."))

    def before_rename(self, olddn, newdn, merge=False):
        frappe.throw(_("Commercial classification records cannot be renamed."))

    def on_trash(self):
        frappe.throw(_("Commercial classification records are permanent audit evidence."))
