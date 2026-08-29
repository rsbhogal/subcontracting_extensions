# Copyright (c) 2026, Bhogals Private Limited
# For license information, please see license.txt

"""
Processor Lot.

This document manages the operational reconciliation and commercial
settlement of one Subcontracting Order.

The Processor Lot itself does not post Stock Ledger or General Ledger entries.
Submission does not automatically create an accounting document.

A draft Purchase Invoice Debit Note is created only through an explicit user
action in the settlement wizard. Immediately before document creation, the
server rebuilds the complete decision chain:

    Fact Engine
        ↓
    Recommendation Engine
        ↓
    Recovery Calculator
        ↓
    Settlement Engine

ERPNext remains responsible for all stock and accounting postings.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.desk.search import validate_and_sanitize_search_inputs
from frappe.model.document import Document
from frappe.utils import flt, get_datetime, now_datetime

from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.fact_engine import (
    apply_processor_lot_settlement_policy,
    get_sco_facts,
)
from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.settlement_recommendation import (
    recommend_settlement,
)
from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.recovery_calculator import (
    calculate_recovery,
)
from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.settlement_engine import (
    create_shortage_debit_note,
)
from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.settlement_netting import (
    build_settlement_netting,
)

REVERSAL_SCOPE_DEBIT_NOTE_ONLY = "Debit Note Only"
REVERSAL_SCOPE_COMPLETE = "Complete Settlement"

class ProcessorLot(Document):
    """Manage one SCO-wise processor reconciliation and settlement."""

    def before_validate(self) -> None:
        """
        Populate immutable header information from the selected SCO.

        When the document is first created manually, settlement rows are also
        loaded from the SCO's supplied_items table.
        """
        if not self.subcontracting_order:
            return

        self._set_header_from_sco()

        if not self.items:
            self._load_items_from_sco()

        self._refresh_settlement_values()

    def before_submit(self) -> None:
        """
        Validate the Processor Lot immediately before submission.

        A fully accounted lot may close directly without settlement rows
        or a Debit Note.

        Where an outstanding component quantity remains, the normal
        reconciliation and settlement-document validations apply.
        """
        self._refresh_settlement_action()
        self._validate_header()
        self._refresh_settlement_values()

        facts = get_sco_facts(
            self.subcontracting_order
        )
        facts = apply_processor_lot_settlement_policy(
            facts,
            self,
        )

        outstanding_qty = flt(
            (
                facts.get("summary", {})
                .get("physical_inventory", {})
                .get("outstanding_qty")
            )
        )

        if outstanding_qty <= 0:
            self._validate_balanced_closure(facts)

            # No settlement rows belong on a balanced Processor Lot.
            self.set("items", [])
            return

        credit_applications = (
            self._get_submitted_credit_applications()
        )

        if self._credit_applications_fully_settle(
            facts,
            credit_applications,
        ):
            self._validate_credit_applied_closure(
                facts,
                credit_applications,
            )

            # PMA and its controlled Stock Entry / Journal Entry are the
            # settlement evidence. Debit Note settlement rows do not belong
            # on a fully credit-applied Processor Lot.
            self.set("items", [])
            return

        if _submitted_credit_applications_cover_physical_shortage(
            facts=facts,
            applications=credit_applications,
        ):
            self._validate_credit_applied_closure(
                facts,
                credit_applications,
            )
            self._validate_submitted_settlement_document()

            # The physical shortage is already closed by the submitted PMA.
            # The linked Debit Note therefore settles only the residual
            # commercial recovery and requires no material settlement row.
            self.set("items", [])
            return

        self._validate_items()
        self._validate_submitted_settlement_document()

    def on_submit(self) -> None:
        """
        Complete the Processor Lot lifecycle and close upstream
        subcontracting documents where their operational obligation
        has been fully resolved.

        The linked SCO is closed through ERPNext's standard
        update_status() method.

        The linked Purchase Order is closed only when every submitted
        SCO against that Purchase Order is already Closed or Completed.
        """
        self.db_set(
            "settlement_status",
            "Completed",
            update_modified=False,
        )

        if not self.subcontracting_order:
            return

        sco = frappe.get_doc(
            "Subcontracting Order",
            self.subcontracting_order,
        )

        if (
            sco.docstatus == 1
            and sco.status not in (
                "Closed",
                "Completed",
            )
        ):
            sco.update_status("Closed")

        if not sco.purchase_order:
            return

        submitted_scos = frappe.get_all(
            "Subcontracting Order",
            filters={
                "purchase_order": sco.purchase_order,
                "docstatus": 1,
            },
            fields=[
                "name",
                "status",
            ],
        )

        unresolved_scos = [
            row
            for row in submitted_scos
            if row.status not in (
                "Closed",
                "Completed",
            )
        ]

        if unresolved_scos:
            return

        po = frappe.get_doc(
            "Purchase Order",
            sco.purchase_order,
        )

        if (
            po.docstatus == 1
            and po.status not in (
                "Closed",
                "Completed",
            )
        ):
            po.update_status("Closed")

    def on_cancel(self) -> None:
        """
        Prevent cancellation while an active generated Debit Note exists.

        A draft Debit Note may be deleted first. A submitted Debit Note must be
        cancelled before the settlement can be cancelled.
        """
        if not self.debit_note:
            return

        debit_note_status = frappe.db.get_value(
            "Purchase Invoice",
            self.debit_note,
            "docstatus",
        )

        if debit_note_status is None:
            return

        if debit_note_status == 0:
            frappe.throw(
                _(
                    "Delete draft Debit Note {0} before cancelling this settlement."
                ).format(
                    frappe.utils.get_link_to_form(
                        "Purchase Invoice",
                        self.debit_note,
                    )
                )
            )

        if debit_note_status == 1:
            frappe.throw(
                _(
                    "Cancel submitted Debit Note {0} before cancelling this settlement."
                ).format(
                    frappe.utils.get_link_to_form(
                        "Purchase Invoice",
                        self.debit_note,
                    )
                )
            )

    # ---------------------------------------------------------------------
    # Header and SCO loading
    # ---------------------------------------------------------------------

    def _get_sco(self):
        """Return the selected submitted Subcontracting Order."""
        if not self.subcontracting_order:
            frappe.throw(_("Subcontracting Order is required."))

        sco = frappe.get_doc(
            "Subcontracting Order",
            self.subcontracting_order,
        )

        if sco.docstatus == 2:
            frappe.throw(
                _("Subcontracting Order {0} is cancelled.").format(
                    frappe.bold(sco.name)
                )
            )

        if sco.docstatus != 1:
            frappe.throw(
                _("Subcontracting Order {0} must be submitted.").format(
                    frappe.bold(sco.name)
                )
            )

        return sco

    def _set_settlement_policy_from_purchase_order(
        self,
        purchase_order_name,
    ) -> None:
        """
        Copy the contractual processor-settlement policy from the Purchase Order.

        This method is used:

        - when the Processor Lot is first created; and
        - when an authorized user reverts an override before settlement begins.

        The caller supplies the Purchase Order name. The method loads the Purchase
        Order and copies its current settlement-policy values into the Processor Lot.
        """
        if not purchase_order_name:
            return

        purchase_order = frappe.get_doc(
            "Purchase Order",
            purchase_order_name,
        )

        self.recover_raw_material_shortage = (
            purchase_order.custom_recover_raw_material_shortage
        )

        self.recover_processing_charges_on_shortage = (
            purchase_order
            .custom_recover_processing_charges_on_shortage
        )

        self.settlement_basis = (
            purchase_order.custom_settlement_basis
        )

        self.settlement_remarks = (
            purchase_order.custom_settlement_remarks
        )

        self.settlement_policy_source = "Purchase Order"

    def _refresh_settlement_action(self) -> None:
        """
        Derive the read-only Settlement Action from the effective policy.

        The two recovery terms are contractually independent. Settlement
        Action summarizes their combined commercial effect in plain language.

        This value is always server-controlled and must not be treated as
        a user-entered settlement decision.
        """
        recover_material = bool(
            self.recover_raw_material_shortage
        )

        recover_processing = bool(
            self.recover_processing_charges_on_shortage
        )

        if recover_material and recover_processing:
            self.settlement_action = (
                "Recover Raw Material and Processing Charges"
            )
        elif recover_material:
            self.settlement_action = (
                "Recover Raw Material Shortage"
            )
        elif recover_processing:
            self.settlement_action = (
                "Recover Processing Charges Only"
            )
        else:
            self.settlement_action = "No Recovery"

    def _set_header_from_sco(self) -> None:
        """Copy controlled header values from the SCO."""
        sco = self._get_sco()

        self.company = sco.company
        self.purchase_order = sco.purchase_order
        self.supplier = sco.supplier
        self.supplier_warehouse = sco.supplier_warehouse

        purchase_order = None

        if sco.purchase_order:
            purchase_order = frappe.get_doc(
                "Purchase Order",
                sco.purchase_order,
            )

        # Preserve the Purchase Order settlement policy as a one-time snapshot
        # when this Processor Lot is first created.
        if self.is_new():
            self._set_settlement_policy_from_purchase_order(
                sco.purchase_order
            )

        self.cost_center = (
            getattr(sco, "cost_center", None)
            or getattr(purchase_order, "cost_center", None)
        )

        self.branch = (
            getattr(sco, "branch", None)
            or getattr(purchase_order, "branch", None)
        )

        if not self.settlement_status:
            self.settlement_status = "Draft"

    def _load_items_from_sco(self) -> None:
        """
        Load only SCO component rows having an unsettled quantity.

        Quantity is determined from the specific supplied_items row, not from
        the aggregate Supplier Warehouse balance.
        """
        sco = self._get_sco()

        self.set("items", [])

        for supplied_row in sco.supplied_items:
            previous_qty = get_previous_settled_qty(
                subcontracting_order=sco.name,
                sco_supplied_item=supplied_row.name,
                exclude_settlement=self.name,
            )

            returned_qty = flt(
                getattr(supplied_row, "returned_qty", 0)
            )

            outstanding_qty = flt(
                flt(supplied_row.supplied_qty)
                - flt(supplied_row.consumed_qty)
                - returned_qty
                - previous_qty
            )

            if outstanding_qty <= 0:
                continue

            warehouse_balance = get_warehouse_balance(
                item_code=supplied_row.rm_item_code,
                warehouse=sco.supplier_warehouse,
            )

            self.append(
                "items",
                {
                    "sco_supplied_item": supplied_row.name,
                    "sco_finished_item": supplied_row.reference_name,
                    "component_item": supplied_row.rm_item_code,
                    "finished_item": supplied_row.main_item_code,
                    "stock_uom": supplied_row.stock_uom,
                    "required_qty": supplied_row.required_qty,
                    "supplied_qty": supplied_row.supplied_qty,
                    "consumed_qty": supplied_row.consumed_qty,
                    "returned_qty": returned_qty,
                    "previously_settled_qty": previous_qty,
                    "outstanding_qty": outstanding_qty,
                    "warehouse_balance": warehouse_balance,
                    "settlement_qty": outstanding_qty,
                    "component_valuation_rate": supplied_row.rate,
                    "recovery_rate": supplied_row.rate,
                    "recovery_amount": outstanding_qty
                    * flt(supplied_row.rate),
                },
            )

    def _set_material_settlement_snapshot(
        self,
        facts: dict[str, Any],
        recovery: dict[str, Any],
    ) -> None:
        """
        Replace Material Summary rows with the authoritative settlement snapshot.

        The snapshot is derived from the Fact Engine and Recovery Calculator.
        Existing browser-entered or legacy settlement rows are not trusted.

        Version 1 currently supports one outstanding material component.
        """
        snapshot_rows = build_material_settlement_snapshot(
            facts=facts,
            recovery=recovery,
        )

        self.set("items", [])

        for snapshot_row in snapshot_rows:
            self.append(
                "items",
                snapshot_row,
            )

    def validate(self) -> None:
        """
        Validate the Processor Lot during its operational lifecycle.

        Settlement-specific item validation is deferred until the Processor Lot
        itself is submitted.
        """
        self._validate_settlement_policy_override()
        self._refresh_settlement_action()
        self._validate_header()
        self._refresh_settlement_values()

    def _validate_settlement_policy_override(self) -> None:
        """
        Enforce controlled changes to the inherited settlement-policy snapshot.

        Rules
        -----
        - The Purchase Order policy remains the default source.
        - Only System Manager may override the policy.
        - Override is permitted only on a Draft Processor Lot.
        - Override is blocked after a Debit Note has been created.
        - Override Reason is mandatory.
        - Audit identity and timestamp are populated by the server.
        - A System Manager may revert to the Purchase Order policy before
        settlement begins.
        - Reverting restores all policy values from the Purchase Order.
        """
        policy_fields = (
            "recover_raw_material_shortage",
            "recover_processing_charges_on_shortage",
            "settlement_basis",
            "settlement_remarks",
        )

        audit_fields = (
            "settlement_policy_source",
            "overridden_by",
            "settlement_policy_overridden_on",
        )

        previous_doc = self.get_doc_before_save()

        is_system_manager = (
            frappe.session.user == "Administrator"
            or "System Manager" in frappe.get_roles()
        )

        # A newly inherited Processor Lot requires no override handling.
        if not previous_doc:
            if not self.override_settlement_policy:
                self.settlement_policy_source = "Purchase Order"
                self.settlement_policy_override_reason = None
                self.overridden_by = None
                self.settlement_policy_overridden_on = None
                return

            if not is_system_manager:
                frappe.throw(
                    _(
                        "Only a System Manager may override the "
                        "Processor Settlement Policy."
                    )
                )

            if not self.settlement_policy_override_reason:
                frappe.throw(
                    _("Override Reason is required.")
                )

            self.settlement_policy_source = "Overridden"
            self.overridden_by = frappe.session.user
            self.settlement_policy_overridden_on = now_datetime()
            return

        changed_policy_fields = [
            fieldname
            for fieldname in policy_fields
            if self.get(fieldname) != previous_doc.get(fieldname)
        ]

        override_flag_changed = (
            self.override_settlement_policy
            != previous_doc.override_settlement_policy
        )

        override_reason_changed = (
            self.settlement_policy_override_reason
            != previous_doc.settlement_policy_override_reason
        )

        audit_fields_changed = []

        for fieldname in audit_fields:
            current_value = self.get(fieldname)
            previous_value = previous_doc.get(fieldname)

            # Form JSON carries Datetime values as strings while the saved
            # document uses datetime objects. Compare the authoritative audit
            # timestamp as a datetime so an unchanged Draft override does not
            # appear modified during the Draft-to-Submitted transition.
            if fieldname == "settlement_policy_overridden_on":
                current_value = (
                    get_datetime(current_value) if current_value else None
                )
                previous_value = (
                    get_datetime(previous_value) if previous_value else None
                )

            if current_value != previous_value:
                audit_fields_changed.append(fieldname)

        override_related_change = bool(
            changed_policy_fields
            or override_flag_changed
            or override_reason_changed
            or audit_fields_changed
        )

        if not override_related_change:
            return

        if not is_system_manager:
            frappe.throw(
                _(
                    "Only a System Manager may change the "
                    "Processor Settlement Policy or its override details."
                )
            )

        if previous_doc.docstatus != 0 or self.docstatus != 0:
            frappe.throw(
                _(
                    "Settlement Policy cannot be overridden after "
                    "the Processor Lot has been submitted."
                )
            )

        if self.debit_note or previous_doc.debit_note:
            frappe.throw(
                _(
                    "Settlement Policy cannot be overridden after "
                    "a Debit Note has been created."
                )
            )

        if changed_policy_fields and not self.override_settlement_policy:
            frappe.throw(
                _(
                    "Enable Override Settlement Policy before changing "
                    "the inherited settlement terms."
                )
            )

        if self.override_settlement_policy:
            if not self.settlement_policy_override_reason:
                frappe.throw(
                    _("Override Reason is required.")
                )

            meaningful_override_change = bool(
                changed_policy_fields
                or (
                    not previous_doc.override_settlement_policy
                    and self.override_settlement_policy
                )
                or override_reason_changed
            )

            self.settlement_policy_source = "Overridden"

            if meaningful_override_change:
                self.overridden_by = (
                    frappe.session.user
                )
                self.settlement_policy_overridden_on = now_datetime()

        else:
            # A System Manager may revert the lot to the original Purchase
            # Order policy until settlement has begun.
            #
            # Do not merely clear the override flag. Restore every policy
            # value from the Purchase Order so that a mixed state cannot exist.
            self._set_settlement_policy_from_purchase_order(
                self.purchase_order
            )

            self.settlement_policy_override_reason = None
            self.overridden_by = None
            self.settlement_policy_overridden_on = None

    # ---------------------------------------------------------------------
    # Validation and recalculation
    # ---------------------------------------------------------------------

    def _validate_header(self) -> None:
        """Validate controlled document-level values."""
        sco = self._get_sco()

        purchase_order = None

        if sco.purchase_order:
            purchase_order = frappe.get_doc(
                "Purchase Order",
                sco.purchase_order,
            )

        expected_values = {
            "company": sco.company,
            "purchase_order": sco.purchase_order,
            "supplier": sco.supplier,
            "supplier_warehouse": sco.supplier_warehouse,
            "cost_center": (
                getattr(sco, "cost_center", None)
                or getattr(purchase_order, "cost_center", None)
            ),
            "branch": (
                getattr(sco, "branch", None)
                or getattr(purchase_order, "branch", None)
            ),
        }

        for fieldname, expected_value in expected_values.items():
            actual_value = self.get(fieldname)

            if actual_value != expected_value:
                label = self.meta.get_label(fieldname)

                frappe.throw(
                    _(
                        "{0} must be {1}, as defined by Subcontracting Order {2}."
                    ).format(
                        frappe.bold(label),
                        frappe.bold(expected_value or _("blank")),
                        frappe.bold(sco.name),
                    )
                )

        if not self.supplier_warehouse:
            frappe.throw(
                _(
                    "Supplier Warehouse is not set in Subcontracting Order {0}."
                ).format(frappe.bold(sco.name))
            )

        warehouse_company = frappe.db.get_value(
            "Warehouse",
            self.supplier_warehouse,
            "company",
        )

        if warehouse_company != self.company:
            frappe.throw(
                _(
                    "Supplier Warehouse {0} belongs to company {1}, not {2}."
                ).format(
                    frappe.bold(self.supplier_warehouse),
                    frappe.bold(warehouse_company or _("Unknown")),
                    frappe.bold(self.company),
                )
            )

        valid_settlement_actions = {
            "Recover Raw Material and Processing Charges",
            "Recover Raw Material Shortage",
            "Recover Processing Charges Only",
            "No Recovery",
        }

        if self.settlement_action not in valid_settlement_actions:
            frappe.throw(
                _(
                    "Settlement Action {0} is not supported."
                ).format(
                    frappe.bold(
                        self.settlement_action or _("blank")
                    )
                )
            )

        if not self.cost_center:
            frappe.throw(
                _(
                    "Cost Center is not available on Subcontracting Order {0} "
                    "or its Purchase Order."
                ).format(frappe.bold(sco.name))
            )

        if not self.branch:
            frappe.throw(
                _(
                    "Branch is not available on Subcontracting Order {0} "
                    "or its Purchase Order."
                ).format(frappe.bold(sco.name))
            )

    def _refresh_settlement_values(self) -> None:
        """
        Reload authoritative SCO quantities and current warehouse balance.

        Browser-supplied values are never trusted.
        """
        if not self.subcontracting_order or not self.items:
            return

        sco = self._get_sco()

        supplied_rows = {
            row.name: row
            for row in sco.supplied_items
        }

        for row in self.items:
            supplied_row = supplied_rows.get(row.sco_supplied_item)

            if not supplied_row:
                frappe.throw(
                    _(
                        "Row {0}: SCO Supplied Item {1} does not belong to "
                        "Subcontracting Order {2}."
                    ).format(
                        row.idx,
                        frappe.bold(row.sco_supplied_item),
                        frappe.bold(sco.name),
                    )
                )

            previous_qty = get_previous_settled_qty(
                subcontracting_order=sco.name,
                sco_supplied_item=supplied_row.name,
                exclude_settlement=self.name,
            )

            returned_qty = flt(
                getattr(supplied_row, "returned_qty", 0)
            )

            outstanding_qty = flt(
                flt(supplied_row.supplied_qty)
                - flt(supplied_row.consumed_qty)
                - returned_qty
                - previous_qty
            )

            warehouse_balance = get_warehouse_balance(
                item_code=supplied_row.rm_item_code,
                warehouse=sco.supplier_warehouse,
            )

            row.sco_finished_item = supplied_row.reference_name
            row.component_item = supplied_row.rm_item_code
            row.finished_item = supplied_row.main_item_code
            row.stock_uom = supplied_row.stock_uom
            row.required_qty = supplied_row.required_qty
            row.supplied_qty = supplied_row.supplied_qty
            row.consumed_qty = supplied_row.consumed_qty
            row.returned_qty = returned_qty
            row.previously_settled_qty = previous_qty
            row.outstanding_qty = outstanding_qty
            row.warehouse_balance = warehouse_balance
            row.component_valuation_rate = supplied_row.rate
            row.recovery_amount = flt(row.settlement_qty) * flt(
                row.recovery_rate
            )

    def _validate_items(self) -> None:
        """Validate every settlement row."""
        if not self.items:
            frappe.throw(
                _(
                    "No unsettled component quantity is available in "
                    "Subcontracting Order {0}."
                ).format(frappe.bold(self.subcontracting_order))
            )

        seen_rows: set[str] = set()

        for row in self.items:
            if not row.sco_supplied_item:
                frappe.throw(
                    _("Row {0}: SCO Supplied Item is required.").format(
                        row.idx
                    )
                )

            if row.sco_supplied_item in seen_rows:
                frappe.throw(
                    _(
                        "Row {0}: SCO Supplied Item {1} is repeated."
                    ).format(
                        row.idx,
                        frappe.bold(row.sco_supplied_item),
                    )
                )

            seen_rows.add(row.sco_supplied_item)

            if flt(row.outstanding_qty) <= 0:
                frappe.throw(
                    _(
                        "Row {0}: No unsettled quantity remains for component {1}."
                    ).format(
                        row.idx,
                        frappe.bold(row.component_item),
                    )
                )

            if flt(row.settlement_qty) <= 0:
                frappe.throw(
                    _(
                        "Row {0}: Settlement Qty must be greater than zero."
                    ).format(row.idx)
                )

            if flt(row.settlement_qty) > flt(row.outstanding_qty):
                frappe.throw(
                    _(
                        "Row {0}: Settlement Qty {1} cannot exceed the "
                        "SCO-specific outstanding quantity {2}."
                    ).format(
                        row.idx,
                        frappe.bold(row.settlement_qty),
                        frappe.bold(row.outstanding_qty),
                    )
                )

            # Warehouse balance is intentionally not validated here.
            #
            # Processor Lot performs commercial settlement of the
            # SCO-specific outstanding quantity determined by the
            # Fact Engine.
            #
            # Physical stock movements have already been completed
            # through ERPNext subcontracting transactions
            # (SCR / Return of Components), so the Supplier Warehouse
            # balance may legitimately be lower than, or even zero,
            # when the commercial settlement is finalized.

            if flt(row.recovery_rate) <= 0:
                frappe.throw(
                    _(
                        "Row {0}: Recovery Rate must be greater than zero."
                    ).format(row.idx)
                )

            row.recovery_amount = flt(row.settlement_qty) * flt(
                row.recovery_rate
            )

    def _validate_balanced_closure(
        self,
        facts: dict[str, Any],
    ) -> None:
        """
        Validate that a Processor Lot with no outstanding component
        quantity is genuinely ready for direct closure.

        A balanced lot requires no reconciliation settlement or Debit Note,
        but its ERP receipt cycle and factual integrity must be complete.
        """
        summary = facts.get("summary") or {}
        physical = summary.get("physical_inventory") or {}
        comparisons = summary.get("comparisons") or {}
        integrity = facts.get("integrity") or {}

        outstanding_qty = flt(
            physical.get("outstanding_qty")
        )

        if outstanding_qty > 0:
            frappe.throw(
                _(
                    "Processor Lot still has {0} outstanding component "
                    "quantity and must complete reconciliation before "
                    "submission."
                ).format(
                    frappe.bold(outstanding_qty)
                )
            )

        blocking_errors = (
            integrity.get("blocking_errors") or []
        )

        if blocking_errors:
            frappe.throw(
                _(
                    "Processor Lot cannot be closed while Fact Engine "
                    "blocking errors remain."
                ),
                title=_("Lot Integrity Error"),
            )

        comparison_fields = (
            "transfer_vs_sco_supplied",
            "scr_received_vs_sco_received",
            "scr_consumed_vs_sco_consumed",
            "purchase_receipt_vs_invoice",
        )

        unbalanced = {
            fieldname: flt(
                comparisons.get(fieldname)
            )
            for fieldname in comparison_fields
            if abs(
                flt(comparisons.get(fieldname))
            ) > 0.000001
        }

        if unbalanced:
            frappe.throw(
                _(
                    "Processor Lot cannot be closed because its ERP "
                    "document quantities are not fully aligned. Review "
                    "Lot Facts before submission."
                ),
                title=_("Receipt Cycle Incomplete"),
            )

        commercial_variance_qty = flt(
            comparisons.get("invoice_vs_scr_received")
        )

        if commercial_variance_qty > 0.000001:
            settlement_policy = facts.get("settlement_policy") or {}

            if not settlement_policy.get("policy_available"):
                frappe.throw(
                    _(
                        "Processor Lot has a positive commercial "
                        "quantity variance of {0}, but its effective "
                        "settlement policy is unavailable."
                    ).format(
                        frappe.bold(commercial_variance_qty)
                    ),
                    title=_("Settlement Policy Unavailable"),
                )

            if settlement_policy.get(
                "recover_processing_charges_on_shortage"
            ):
                self._validate_submitted_settlement_document()

        received_qty = flt(
            physical.get("scr_received_qty")
        )

        if received_qty <= 0:
            frappe.throw(
                _(
                    "Complete the Subcontracting Receipt cycle before "
                    "closing this Processor Lot."
                ),
                title=_("Receipt Cycle Incomplete"),
            )

        journey = get_processor_lot_receipt_journey(
            self.name
        )

        journey_summary = (
            journey.get("summary") or {}
        )

        if flt(
            journey_summary.get("pending_count")
        ) > 0:
            frappe.throw(
                _(
                    "Complete the Subcontracting Receipt, Purchase "
                    "Receipt and Purchase Invoice cycle for every "
                    "Processor Lot Receipt before closing this "
                    "Processor Lot."
                ),
                title=_("Receipt Cycle Incomplete"),
            )

    def _validate_submitted_settlement_document(self) -> None:
        """
        Validate the linked settlement document before Processor Lot submission.

        The linked Debit Note must:

        - exist;
        - be submitted;
        - be linked back to this Processor Lot.
        """
        if not self.debit_note:
            frappe.throw(
                _(
                    "A submitted Debit Note is required before this "
                    "Processor Lot can be submitted."
                )
            )

        debit_note = frappe.db.get_value(
            "Purchase Invoice",
            self.debit_note,
            [
                "docstatus",
                "custom_processor_lot_settlement",
            ],
            as_dict=True,
        )

        if not debit_note:
            frappe.throw(
                _(
                    "Linked Debit Note {0} does not exist."
                ).format(
                    frappe.bold(self.debit_note)
                )
            )

        if debit_note.docstatus != 1:
            frappe.throw(
                _(
                    "Debit Note {0} must be submitted before this "
                    "Processor Lot can be submitted."
                ).format(
                    frappe.utils.get_link_to_form(
                        "Purchase Invoice",
                        self.debit_note,
                    )
                )
            )

        if (
            debit_note.custom_processor_lot_settlement
            != self.name
        ):
            frappe.throw(
                _(
                    "Debit Note {0} is not linked back to Processor Lot {1}."
                ).format(
                    frappe.utils.get_link_to_form(
                        "Purchase Invoice",
                        self.debit_note,
                    ),
                    frappe.bold(self.name),
                )
            )

    def _get_submitted_credit_applications(self) -> list[dict[str, Any]]:
        """Return effective submitted PMA applications for this lot."""
        return [
            dict(row)
            for row in frappe.get_all(
                "Processor Material Account Entry",
                filters={
                    "entry_type": "Credit Applied",
                    "source_event": "Processor Lot Shortage",
                    "processor_lot": self.name,
                    "subcontracting_order": self.subcontracting_order,
                    "account_direction": "Debit",
                    "docstatus": 1,
                    "is_reversed": 0,
                },
                fields=[
                    "name",
                    "account_qty",
                    "commercial_qty",
                    "application_stock_entry",
                    "application_journal_entry",
                ],
                order_by="posting_date asc, creation asc, name asc",
            )
        ]

    def _credit_applications_fully_settle(
        self,
        facts: dict[str, Any],
        applications: list[dict[str, Any]],
    ) -> bool:
        """Return whether submitted PMAs exactly cover both gross variances."""
        if not applications:
            return False

        summary = facts.get("summary") or {}
        physical = summary.get("physical_inventory") or {}
        comparisons = summary.get("comparisons") or {}

        physical_shortage_qty = max(
            flt(physical.get("outstanding_qty")),
            0.0,
        )
        commercial_variance_qty = max(
            flt(comparisons.get("invoice_vs_scr_received")),
            0.0,
        )
        applied_qty = sum(
            flt(row.get("account_qty"))
            for row in applications
        )
        commercially_matched_qty = sum(
            flt(row.get("commercial_qty"))
            for row in applications
        )
        tolerance = 0.000001

        return (
            abs(applied_qty - physical_shortage_qty) <= tolerance
            and abs(
                commercially_matched_qty
                - commercial_variance_qty
            ) <= tolerance
        )

    def _validate_credit_applied_closure(
        self,
        facts: dict[str, Any],
        applications: list[dict[str, Any]],
    ) -> None:
        """Validate PMA-only closure and all controlled settlement documents."""
        integrity = facts.get("integrity") or {}
        if integrity.get("blocking_errors"):
            frappe.throw(
                _(
                    "Processor Lot cannot be closed while Fact Engine "
                    "blocking errors remain."
                ),
                title=_("Lot Integrity Error"),
            )

        summary = facts.get("summary") or {}
        physical = summary.get("physical_inventory") or {}
        commercial = summary.get("commercial") or {}
        comparisons = summary.get("comparisons") or {}

        comparison_fields = (
            "transfer_vs_sco_supplied",
            "scr_received_vs_sco_received",
            "scr_consumed_vs_sco_consumed",
            "purchase_receipt_vs_invoice",
        )
        unbalanced = {
            fieldname: flt(comparisons.get(fieldname))
            for fieldname in comparison_fields
            if abs(flt(comparisons.get(fieldname))) > 0.000001
        }
        if unbalanced:
            frappe.throw(
                _(
                    "Processor Lot cannot be closed because ERP document "
                    "quantities other than the PMA-covered variances are "
                    "not fully aligned. Review Lot Facts before submission."
                ),
                title=_("Receipt Cycle Incomplete"),
            )

        received_qty = flt(physical.get("scr_received_qty"))
        purchase_receipt_qty = flt(
            commercial.get("purchase_receipt_qty")
        )
        purchase_invoice_qty = flt(
            commercial.get("purchase_invoice_qty")
        )
        if (
            received_qty <= 0
            or purchase_receipt_qty < received_qty
            or purchase_invoice_qty < received_qty
        ):
            frappe.throw(
                _(
                    "Complete the Subcontracting Receipt, Purchase Receipt "
                    "and Purchase Invoice cycle before closing this "
                    "Processor Lot."
                ),
                title=_("Receipt Cycle Incomplete"),
            )

        from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.settlement_application_engine import (
            validate_credit_application_documents,
        )

        for row in applications:
            application = frappe.get_doc(
                "Processor Material Account Entry",
                row["name"],
            )
            validate_credit_application_documents(application)

    # # ---------------------------------------------------------------------
    # # Debit Note generation
    # # ---------------------------------------------------------------------

    # def _create_draft_debit_note(self):
    #     """
    #     Backward-compatible wrapper.

    #     Debit Note creation is now handled by the Settlement Engine.
    #     """
    #     return create_shortage_debit_note(self)

    # def _build_debit_note_remarks(self) -> str:
    #     """
    #     Backward-compatible wrapper.

    #     This method will be removed after the Settlement Engine migration
    #     is complete.
    #     """
    #     return build_shortage_debit_note_remarks(self)

def _submitted_credit_applications_cover_physical_shortage(
    facts: dict[str, Any],
    applications: list[dict[str, Any]],
) -> bool:
    """Return whether submitted PMAs exactly cover the physical shortage."""
    if not applications:
        return False

    physical = (
        (facts.get("summary") or {}).get("physical_inventory")
        or {}
    )
    physical_shortage_qty = max(
        flt(physical.get("outstanding_qty")),
        0.0,
    )
    applied_qty = flt(
        sum(
            flt(row.get("account_qty"))
            for row in applications
        )
    )

    return (
        physical_shortage_qty > 0
        and abs(applied_qty - physical_shortage_qty) <= 0.000001
    )


def build_settlement_reversal_forecast(
    facts: dict[str, Any],
    applications: list[dict[str, Any]],
    source_positions: dict[str, dict[str, Any]],
    commercial_settlement_document_submitted: bool = False,
) -> dict[str, Any]:
    """Return the current and restored quantities for a reversal preview.

    This function is deliberately database-free so the arithmetic can be
    regression-tested without creating ERPNext documents or test masters.
    """
    summary = facts.get("summary") or {}
    physical = summary.get("physical_inventory") or {}
    comparisons = summary.get("comparisons") or {}

    gross_physical_outstanding = max(
        flt(physical.get("outstanding_qty")),
        0.0,
    )
    gross_commercial_variance = max(
        flt(comparisons.get("invoice_vs_scr_received")),
        0.0,
    )
    applied_physical_qty = flt(
        sum(flt(row.get("account_qty")) for row in applications),
        3,
    )
    applied_commercial_qty = flt(
        sum(flt(row.get("commercial_qty")) for row in applications),
        3,
    )
    source_credit_remaining_before = flt(
        sum(
            flt(row.get("remaining_credit"))
            for row in source_positions.values()
        ),
        3,
    )

    return {
        "gross": {
            "physical_outstanding_qty": flt(
                gross_physical_outstanding,
                3,
            ),
            "commercial_variance_qty": flt(
                gross_commercial_variance,
                3,
            ),
        },
        "current": {
            "physical_credit_applied_qty": applied_physical_qty,
            "commercial_credit_applied_qty": applied_commercial_qty,
            "net_physical_outstanding_qty": flt(
                max(
                    gross_physical_outstanding
                    - applied_physical_qty,
                    0.0,
                ),
                3,
            ),
            "open_commercial_variance_qty": flt(
                0.0
                if commercial_settlement_document_submitted
                else max(
                    gross_commercial_variance
                    - applied_commercial_qty,
                    0.0,
                ),
                3,
            ),
            "source_credit_remaining_qty": (
                source_credit_remaining_before
            ),
        },
        "debit_note_only": {
            "net_physical_outstanding_qty": flt(
                max(
                    gross_physical_outstanding
                    - applied_physical_qty,
                    0.0,
                ),
                3,
            ),
            "open_commercial_variance_qty": flt(
                max(
                    gross_commercial_variance
                    - applied_commercial_qty,
                    0.0,
                ),
                3,
            ),
            "source_credit_remaining_qty": (
                source_credit_remaining_before
            ),
        },
        "complete_settlement": {
            "net_physical_outstanding_qty": flt(
                gross_physical_outstanding,
                3,
            ),
            "open_commercial_variance_qty": flt(
                gross_commercial_variance,
                3,
            ),
            "source_credit_remaining_qty": flt(
                source_credit_remaining_before
                + applied_physical_qty,
                3,
            ),
        },
    }


def build_settlement_reversal_sequence(
    scope: str,
    debit_note: str | None,
    applications: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the immutable document order for the selected reversal scope."""
    if scope not in (
        REVERSAL_SCOPE_DEBIT_NOTE_ONLY,
        REVERSAL_SCOPE_COMPLETE,
    ):
        raise ValueError("Unsupported settlement reversal scope")

    sequence = [
        {
            "doctype": "Purchase Invoice",
            "name": debit_note,
            "action": "Cancel residual Debit Note",
        }
    ]
    if scope == REVERSAL_SCOPE_COMPLETE:
        for row in applications:
            sequence.extend(
                [
                    {
                        "doctype": "Processor Material Account Entry",
                        "name": row.get("name"),
                        "action": "Cancel PMA application",
                    },
                    {
                        "doctype": "Journal Entry",
                        "name": row.get("application_journal_entry"),
                        "action": "Cancel linked Journal Entry",
                    },
                    {
                        "doctype": "Stock Entry",
                        "name": row.get("application_stock_entry"),
                        "action": "Cancel linked Stock Entry",
                    },
                ]
            )
    return sequence


def settlement_reversal_sco_temporary_status(
    current_status: str | None,
) -> str | None:
    """Return the non-blocking SCO status used only during SE cancellation."""
    if current_status == "Closed":
        return "Completed"
    return current_status


def build_material_settlement_snapshot(
    facts: dict[str, Any],
    recovery: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Build server-controlled material settlement snapshot rows.

    The rows are derived only from the authoritative Fact Engine and
    Recovery Calculator outputs. Browser values and existing editable
    Processor Lot child-row rates are not used.

    Version 1 currently supports one outstanding component per
    Processor Lot. The function returns a list so it can later be
    extended naturally for mixed-component lots.
    """
    components = [
        component
        for component in (facts.get("components") or [])
        if flt(component.get("outstanding_qty")) > 0
    ]

    if len(components) != 1:
        frappe.throw(
            _(
                "Material settlement snapshot currently requires exactly "
                "one outstanding component. Found {0}."
            ).format(len(components))
        )

    quantity = recovery.get("quantity") or {}
    raw_material = recovery.get("raw_material") or {}
    processing_charges = (
        recovery.get("processing_charges") or {}
    )
    integrity = recovery.get("integrity") or {}

    if not integrity.get("is_valid"):
        frappe.throw(
            _(
                "Material settlement snapshot cannot be built because "
                "the Recovery Calculator result is not valid."
            )
        )

    settlement_qty = flt(
        quantity.get("shortage_qty")
    )

    recovery_rate = flt(
        raw_material.get("rate")
    )

    recovery_amount = flt(
        raw_material.get("amount")
    )

    processing_only_recovery = bool(
        not raw_material.get("recommended")
        and recovery_amount <= 0
        and processing_charges.get("recommended")
        and flt(processing_charges.get("quantity")) > 0
        and flt(processing_charges.get("amount")) > 0
    )

    if settlement_qty <= 0 and processing_only_recovery:
        return []

    if settlement_qty <= 0:
        frappe.throw(
            _("Settlement quantity must be greater than zero.")
        )

    if raw_material.get("recommended") and recovery_rate <= 0:
        frappe.throw(
            _(
                "Raw-material recovery rate must be greater than zero "
                "when raw-material recovery is recommended."
            )
        )

    component = components[0]

    return [
        {
            "sco_supplied_item":
                component.get("sco_supplied_item"),
            "sco_finished_item":
                component.get("sco_finished_item"),
            "component_item":
                component.get("component_item"),
            "finished_item":
                component.get("finished_item"),
            "stock_uom":
                component.get("stock_uom"),
            "required_qty":
                flt(component.get("required_qty")),
            "supplied_qty":
                flt(component.get("supplied_qty")),
            "consumed_qty":
                flt(component.get("consumed_qty")),
            "returned_qty":
                flt(component.get("returned_qty")),
            "previously_settled_qty":
                flt(component.get("previously_settled_qty")),
            "outstanding_qty":
                flt(component.get("outstanding_qty")),
            "warehouse_balance":
                flt(component.get("warehouse_balance")),
            "settlement_qty":
                settlement_qty,
            "component_valuation_rate":
                flt(component.get("component_rate")),
            "recovery_rate":
                recovery_rate,
            "recovery_amount":
                recovery_amount,
        }
    ]

# -------------------------------------------------------------------------
# Whitelisted form methods
# -------------------------------------------------------------------------

@frappe.whitelist()
def get_processor_lot_settlement_reversal_preview(
    processor_lot: str,
) -> dict[str, Any]:
    """Build a read-only settlement-reversal plan for one submitted lot.

    Stage 1 performs no cancellation, save or database mutation. It exposes
    the valid stopping points and their forecast so the user can review the
    dependency chain before execution support is enabled.
    """
    if not processor_lot:
        frappe.throw(_("Processor Lot is required."))

    lot = frappe.get_doc("Processor Lot", processor_lot)
    lot.check_permission("read")

    general_blockers: list[str] = []
    debit_note_blockers: list[str] = []
    complete_blockers: list[str] = []

    if lot.docstatus != 1:
        general_blockers.append(
            _("Processor Lot must be submitted before settlement can be reopened.")
        )
    if lot.settlement_status != "Completed":
        general_blockers.append(
            _("Settlement Status must be Completed; current status is {0}.").format(
                frappe.bold(lot.settlement_status or _("Not Set"))
            )
        )

    facts = get_sco_facts(lot.subcontracting_order)
    integrity = facts.get("integrity") or {}
    for error in integrity.get("blocking_errors") or []:
        general_blockers.append(str(error))

    debit_note_state: dict[str, Any] = {
        "name": lot.debit_note,
        "docstatus": None,
        "status": None,
        "rounded_total": 0.0,
        "outstanding_amount": 0.0,
        "is_fully_unallocated": False,
    }
    if not lot.debit_note:
        debit_note_blockers.append(
            _("No settlement Debit Note is linked to this Processor Lot.")
        )
    elif not frappe.db.exists("Purchase Invoice", lot.debit_note):
        debit_note_blockers.append(
            _("Linked Debit Note {0} does not exist.").format(
                frappe.bold(lot.debit_note)
            )
        )
    else:
        debit_note = frappe.get_doc("Purchase Invoice", lot.debit_note)
        settlement_total = flt(
            debit_note.rounded_total
            or debit_note.grand_total,
            2,
        )
        fully_unallocated = (
            abs(
                flt(debit_note.outstanding_amount, 2)
                - settlement_total
            )
            <= 0.01
        )
        debit_note_state.update(
            {
                "docstatus": debit_note.docstatus,
                "status": debit_note.status,
                "posting_date": debit_note.posting_date,
                "rounded_total": settlement_total,
                "outstanding_amount": flt(
                    debit_note.outstanding_amount,
                    2,
                ),
                "is_return": bool(debit_note.is_return),
                "processor_lot": debit_note.get(
                    "custom_processor_lot_settlement"
                ),
                "is_fully_unallocated": fully_unallocated,
            }
        )
        if debit_note.docstatus != 1:
            debit_note_blockers.append(
                _("Debit Note {0} must be submitted.").format(
                    frappe.bold(debit_note.name)
                )
            )
        if not debit_note.is_return:
            debit_note_blockers.append(
                _("Purchase Invoice {0} is not a Debit Note return.").format(
                    frappe.bold(debit_note.name)
                )
            )
        if (
            debit_note.get("custom_processor_lot_settlement")
            != lot.name
        ):
            debit_note_blockers.append(
                _("Debit Note is not linked back to this Processor Lot.")
            )
        if not fully_unallocated:
            debit_note_blockers.append(
                _(
                    "Debit Note has been allocated or adjusted. Reverse its "
                    "downstream allocation before reopening settlement."
                )
            )

    application_rows = frappe.get_all(
        "Processor Material Account Entry",
        filters={
            "entry_type": "Credit Applied",
            "source_event": "Processor Lot Shortage",
            "processor_lot": lot.name,
            "account_direction": "Debit",
            "docstatus": 1,
            "is_reversed": 0,
        },
        fields=[
            "name",
            "against_entry",
            "account_qty",
            "commercial_qty",
            "account_uom",
            "application_stock_entry",
            "application_journal_entry",
        ],
        order_by="posting_date asc, creation asc, name asc",
    )

    applications: list[dict[str, Any]] = []
    source_positions: dict[str, dict[str, Any]] = {}
    for row in application_rows:
        application = dict(row)
        stock_entry = application.get("application_stock_entry")
        journal_entry = application.get("application_journal_entry")
        application["stock_entry_docstatus"] = (
            frappe.db.get_value("Stock Entry", stock_entry, "docstatus")
            if stock_entry
            else None
        )
        application["journal_entry_docstatus"] = (
            frappe.db.get_value("Journal Entry", journal_entry, "docstatus")
            if journal_entry
            else None
        )

        if not stock_entry:
            complete_blockers.append(
                _("PMA {0} has no linked Stock Entry.").format(
                    frappe.bold(application["name"])
                )
            )
        elif application["stock_entry_docstatus"] != 1:
            complete_blockers.append(
                _("Stock Entry {0} must be submitted.").format(
                    frappe.bold(stock_entry)
                )
            )
        if not journal_entry:
            complete_blockers.append(
                _("PMA {0} has no linked Journal Entry.").format(
                    frappe.bold(application["name"])
                )
            )
        elif application["journal_entry_docstatus"] != 1:
            complete_blockers.append(
                _("Journal Entry {0} must be submitted.").format(
                    frappe.bold(journal_entry)
                )
            )

        source_name = application.get("against_entry")
        if not source_name or not frappe.db.exists(
            "Processor Material Account Entry",
            source_name,
        ):
            complete_blockers.append(
                _("PMA {0} has no valid source credit.").format(
                    frappe.bold(application["name"])
                )
            )
        elif source_name not in source_positions:
            source_credit_qty = flt(
                frappe.db.get_value(
                    "Processor Material Account Entry",
                    source_name,
                    "account_qty",
                ),
                3,
            )
            applied_qty = flt(
                frappe.db.sql(
                    """
                    SELECT COALESCE(SUM(account_qty), 0)
                    FROM `tabProcessor Material Account Entry`
                    WHERE against_entry = %s
                      AND docstatus = 1
                      AND IFNULL(is_reversed, 0) = 0
                    """,
                    (source_name,),
                )[0][0],
                3,
            )
            source_positions[source_name] = {
                "name": source_name,
                "source_credit_qty": source_credit_qty,
                "remaining_credit": flt(
                    source_credit_qty - applied_qty,
                    3,
                ),
            }

        applications.append(application)

    if not applications:
        complete_blockers.append(
            _("No effective submitted material-credit application was found.")
        )

    forecast = build_settlement_reversal_forecast(
        facts=facts,
        applications=applications,
        source_positions=source_positions,
        commercial_settlement_document_submitted=bool(
            debit_note_state.get("docstatus") == 1
            and debit_note_state.get("processor_lot") == lot.name
        ),
    )
    components = facts.get("components") or []
    account_uom = (
        applications[0].get("account_uom")
        if applications
        else (
            components[0].get("stock_uom")
            if components
            else None
        )
    )

    debit_note_blockers = general_blockers + debit_note_blockers
    complete_blockers = debit_note_blockers + complete_blockers

    return {
        "stage": "Preview Only",
        "processor_lot": lot.name,
        "settlement_status": lot.settlement_status,
        "company": lot.company,
        "currency": frappe.get_cached_value(
            "Company",
            lot.company,
            "default_currency",
        ),
        "account_uom": account_uom,
        "debit_note": debit_note_state,
        "applications": applications,
        "source_positions": list(source_positions.values()),
        "forecast": forecast,
        "readiness": {
            "debit_note_only": not debit_note_blockers,
            "complete_settlement": not complete_blockers,
            "debit_note_blockers": debit_note_blockers,
            "complete_settlement_blockers": complete_blockers,
        },
        "preserved": [
            {
                "doctype": "Processor Lot",
                "name": lot.name,
                "status": _("Submitted and preserved"),
            },
            {
                "doctype": "Subcontracting Order",
                "name": lot.subcontracting_order,
                "status": _("Operational status preserved"),
            },
            {
                "doctype": "Purchase Order",
                "name": lot.purchase_order,
                "status": _("Operational status preserved"),
            },
        ]
        + [
            {
                "doctype": "Processor Material Account Entry",
                "name": row["name"],
                "status": _("Source credit preserved"),
            }
            for row in source_positions.values()
        ],
    }


def resolve_cancelled_workflow_state(states: list[Any]) -> str:
    """Return the unique workflow state representing docstatus Cancelled."""
    cancelled_states = [
        row.state
        for row in states
        if int(row.doc_status or 0) == 2
    ]

    if len(cancelled_states) != 1:
        raise ValueError(
            "An active workflow must define exactly one Cancelled state."
        )

    return cancelled_states[0]


def synchronize_cancelled_workflow_state(document: Document) -> None:
    """Keep workflow metadata aligned after a controlled cancellation."""
    workflow_names = frappe.get_all(
        "Workflow",
        filters={
            "document_type": document.doctype,
            "is_active": 1,
        },
        pluck="name",
        limit_page_length=2,
    )

    if not workflow_names:
        return

    if len(workflow_names) != 1:
        frappe.throw(
            _(
                "Document type {0} has multiple active workflows."
            ).format(frappe.bold(document.doctype)),
            title=_("Workflow State Synchronization Failed"),
        )

    workflow = frappe.get_doc("Workflow", workflow_names[0])
    state_field = (workflow.workflow_state_field or "").strip()
    if not state_field or not document.meta.has_field(state_field):
        frappe.throw(
            _(
                "Active workflow {0} has no valid workflow state field "
                "for {1}."
            ).format(
                frappe.bold(workflow.name),
                frappe.bold(document.doctype),
            ),
            title=_("Workflow State Synchronization Failed"),
        )

    try:
        cancelled_state = resolve_cancelled_workflow_state(
            workflow.states
        )
    except ValueError:
        frappe.throw(
            _(
                "Active workflow {0} must define exactly one state "
                "whose document status is Cancelled."
            ).format(frappe.bold(workflow.name)),
            title=_("Workflow State Synchronization Failed"),
        )

    if document.get(state_field) == cancelled_state:
        return

    frappe.db.set_value(
        document.doctype,
        document.name,
        state_field,
        cancelled_state,
        update_modified=False,
    )
    document.set(state_field, cancelled_state)


@frappe.whitelist()
def execute_processor_lot_settlement_reversal(
    processor_lot: str,
    scope: str,
    reason: str,
    confirmation: str,
) -> dict[str, Any]:
    """Execute one preflighted settlement reversal as a single transaction."""
    frappe.only_for("System Manager")

    reason = (reason or "").strip()
    confirmation = (confirmation or "").strip()
    if scope not in (
        REVERSAL_SCOPE_DEBIT_NOTE_ONLY,
        REVERSAL_SCOPE_COMPLETE,
    ):
        frappe.throw(_("Select a valid settlement-reversal scope."))
    if len(reason) < 10:
        frappe.throw(
            _("Enter a reversal reason containing at least 10 characters.")
        )
    if confirmation != processor_lot:
        frappe.throw(
            _("Type Processor Lot {0} exactly to confirm reversal.").format(
                frappe.bold(processor_lot)
            )
        )

    preview = get_processor_lot_settlement_reversal_preview(processor_lot)
    readiness = preview.get("readiness") or {}
    readiness_key = (
        "complete_settlement"
        if scope == REVERSAL_SCOPE_COMPLETE
        else "debit_note_only"
    )
    blockers_key = f"{readiness_key}_blockers"
    if not readiness.get(readiness_key):
        blockers = readiness.get(blockers_key) or []
        frappe.throw(
            "<br>".join(str(blocker) for blocker in blockers)
            or _("Settlement reversal preflight did not pass."),
            title=_("Settlement Reversal Blocked"),
        )

    lot = frappe.get_doc("Processor Lot", processor_lot)
    lot.check_permission("write")
    sequence = build_settlement_reversal_sequence(
        scope=scope,
        debit_note=(preview.get("debit_note") or {}).get("name"),
        applications=preview.get("applications") or [],
    )

    # This transient status is the sole server-side gate that permits a
    # submitted lot's Credit Applied PMA to pass its normal cancel guard.
    # Clear the settlement links in the same transaction before cancelling
    # the Debit Note; Frappe's link-integrity guard otherwise correctly blocks
    # cancellation. Any later exception rolls this update back with the rest
    # of the reversal transaction.
    frappe.db.set_value(
        "Processor Lot",
        lot.name,
        {
            "debit_note": None,
            "generated_document_type": None,
            "generated_document": None,
            "settlement_status": "Reversal In Progress",
        },
        update_modified=False,
    )

    cancelled: list[dict[str, Any]] = []
    for step in sequence:
        document = frappe.get_doc(step["doctype"], step["name"])
        if step["doctype"] == "Processor Material Account Entry":
            document.flags.allow_processor_settlement_reversal = True
        original_sco_status = None
        temporary_sco_status = None
        if (
            step["doctype"] == "Stock Entry"
            and document.get("subcontracting_order")
        ):
            original_sco_status = frappe.db.get_value(
                "Subcontracting Order",
                document.subcontracting_order,
                "status",
            )
            temporary_sco_status = (
                settlement_reversal_sco_temporary_status(
                    original_sco_status
                )
            )
            if temporary_sco_status != original_sco_status:
                frappe.db.set_value(
                    "Subcontracting Order",
                    document.subcontracting_order,
                    "status",
                    temporary_sco_status,
                    update_modified=False,
                )
        try:
            document.cancel()
            synchronize_cancelled_workflow_state(document)
        finally:
            if temporary_sco_status != original_sco_status:
                frappe.db.set_value(
                    "Subcontracting Order",
                    document.subcontracting_order,
                    "status",
                    original_sco_status,
                    update_modified=False,
                )
        cancelled.append(step)

    frappe.db.set_value(
        "Processor Lot",
        lot.name,
        "settlement_status",
        "Reopened",
        update_modified=False,
    )

    cancelled_lines = "<br>".join(
        f"{frappe.utils.escape_html(step['doctype'])}: "
        f"{frappe.utils.escape_html(step['name'])}"
        for step in cancelled
    )
    lot.add_comment(
        "Info",
        _(
            "Processor Lot settlement reopened.<br>"
            "Executed by: {0}<br>Scope: {1}<br>Reason: {2}<br>"
            "Cancelled documents:<br>{3}"
        ).format(
            frappe.utils.escape_html(frappe.session.user),
            frappe.utils.escape_html(scope),
            frappe.utils.escape_html(reason),
            cancelled_lines,
        ),
    )

    forecast_key = (
        "complete_settlement"
        if scope == REVERSAL_SCOPE_COMPLETE
        else "debit_note_only"
    )
    return {
        "processor_lot": lot.name,
        "scope": scope,
        "settlement_status": "Reopened",
        "cancelled": cancelled,
        "restored_position": (preview.get("forecast") or {}).get(
            forecast_key
        ),
        "preserved": preview.get("preserved") or [],
        "message": _(
            "Settlement for Processor Lot {0} has been reopened."
        ).format(frappe.bold(lot.name)),
    }


@frappe.whitelist()
def complete_reopened_processor_lot_settlement(
    processor_lot: str,
) -> dict[str, Any]:
    """Revalidate and complete a submitted Processor Lot after reopening."""
    frappe.only_for("System Manager")

    lot = frappe.get_doc("Processor Lot", processor_lot)
    lot.check_permission("write")
    if lot.docstatus != 1:
        frappe.throw(_("Processor Lot must remain submitted."))
    if lot.settlement_status not in (
        "Reopened",
        "Reopened - Debit Note Created",
    ):
        frappe.throw(
            _("Only a reopened settlement can be completed again.")
        )

    # Reuse the same authoritative validations used on initial submission,
    # without submitting the immutable operational document a second time.
    lot.before_submit()
    frappe.db.set_value(
        "Processor Lot",
        lot.name,
        "settlement_status",
        "Completed",
        update_modified=False,
    )
    lot.add_comment(
        "Info",
        _(
            "Reopened Processor Lot settlement was revalidated and marked "
            "Completed by {0}."
        ).format(frappe.utils.escape_html(frappe.session.user)),
    )
    return {
        "processor_lot": lot.name,
        "settlement_status": "Completed",
        "message": _(
            "Settlement for Processor Lot {0} is Completed again."
        ).format(frappe.bold(lot.name)),
    }


@frappe.whitelist()
def create_processor_lot_debit_note(
    processor_lot: str,
    business_classification: str,
) -> dict[str, Any]:
    """
    Create the draft Processor Lot Debit Note through an explicit user action.

    The browser supplies only:

    - Processor Lot identity; and
    - selected business classification.

    Quantities, rates, recommendations and amounts are regenerated
    server-side by the authoritative settlement pipeline.

    Workflow
    --------
    Load Processor Lot fresh
        ↓
    Validate current document state
        ↓
    Fact Engine
        ↓
    Recommendation Engine
        ↓
    Recovery Calculator
        ↓
    Settlement Engine
        ↓
    Draft Purchase Invoice Debit Note
    """
    if not processor_lot:
        frappe.throw(_("Processor Lot is required."))

    if not business_classification:
        frappe.throw(_("Business Classification is required."))

    if not frappe.db.exists("Processor Lot", processor_lot):
        frappe.throw(
            _("Processor Lot {0} does not exist.").format(
                frappe.bold(processor_lot)
            )
        )

    # A residual Debit Note is the final settlement step. Every generated
    # material-credit bundle must first complete its controlled submission
    # sequence: Stock Entry, Journal Entry, then PMA.
    from subcontracting_extensions.subcontracting_extensions.doctype.processor_lot.settlement_application_engine import (
        validate_credit_application_documents_complete,
    )

    validate_credit_application_documents_complete(processor_lot)

    lot = frappe.get_doc(
        "Processor Lot",
        processor_lot,
    )

    if lot.docstatus == 2:
        frappe.throw(
            _(
                "Cancelled Processor Lot {0} cannot create a Debit Note."
            ).format(
                frappe.bold(lot.name)
            )
        )

    # Reload and validate the controlled operational facts before handing
    # execution to the Settlement Engine.
    lot._validate_header()
    lot._refresh_settlement_values()

    settlement_result = create_shortage_debit_note(
        processor_lot=lot,
        business_classification=business_classification,
    )

    debit_note = settlement_result["debit_note"]

    snapshot_rows = build_material_settlement_snapshot(
        facts=settlement_result["facts"],
        recovery=settlement_result["recovery"],
    )

    if lot.docstatus == 1:
        if lot.settlement_status != "Reopened":
            frappe.throw(
                _("A submitted Processor Lot must have a Reopened settlement.")
            )
        if snapshot_rows:
            frappe.throw(
                _(
                    "A reopened submitted lot currently supports only a "
                    "residual processing-charge Debit Note after material "
                    "credit application."
                ),
                title=_("Material Settlement Evidence Required"),
            )
        frappe.db.set_value(
            "Processor Lot",
            lot.name,
            {
                "generated_document_type": "Purchase Invoice",
                "generated_document": debit_note.name,
                "debit_note": debit_note.name,
                "settlement_status": "Reopened - Debit Note Created",
            },
            update_modified=False,
        )
        resulting_status = "Reopened - Debit Note Created"
    else:
        lot.set("items", [])
        for snapshot_row in snapshot_rows:
            lot.append("items", snapshot_row)
        lot.generated_document_type = "Purchase Invoice"
        lot.generated_document = debit_note.name
        lot.debit_note = debit_note.name
        lot.settlement_status = "Debit Note Created"
        lot.save()
        resulting_status = "Debit Note Created"

    return {
        "doctype": "Purchase Invoice",
        "name": debit_note.name,
        "docstatus": debit_note.docstatus,
        "status": debit_note.status,
        "processor_lot": lot.name,
        "settlement_status": resulting_status,
        "message": _(
            "Draft Debit Note {0} has been created for review."
        ).format(
            frappe.utils.get_link_to_form(
                "Purchase Invoice",
                debit_note.name,
            )
        ),
    }

def unlink_processor_lot_settlement_debit_note(
    doc,
    method=None,
) -> None:
    """
    Clear a Draft Processor Lot settlement Debit Note link before deletion.

    This applies only to Purchase Invoices created as Processor Lot
    settlement documents through ``custom_processor_lot_settlement``.

    The exact matching links are cleared so an unrelated replacement
    document is never disturbed.
    """
    processor_lot = getattr(
        doc,
        "custom_processor_lot_settlement",
        None,
    )

    if not processor_lot:
        return

    if not frappe.db.exists(
        "Processor Lot",
        processor_lot,
    ):
        return

    lot = frappe.get_doc(
        "Processor Lot",
        processor_lot,
    )

    reopened_submitted_lot = bool(
        lot.docstatus == 1
        and lot.settlement_status == "Reopened - Debit Note Created"
    )
    if lot.docstatus != 0 and not reopened_submitted_lot:
        frappe.throw(
            _(
                "Processor Lot {0} is not in Draft status. "
                "Its settlement Debit Note cannot be deleted."
            ).format(
                frappe.bold(processor_lot)
            )
        )

    if lot.debit_note == doc.name:
        lot.debit_note = None

    if (
        lot.generated_document_type
        == "Purchase Invoice"
        and lot.generated_document == doc.name
    ):
        lot.generated_document_type = None
        lot.generated_document = None

    if reopened_submitted_lot:
        frappe.db.set_value(
            "Processor Lot",
            lot.name,
            {
                "debit_note": None,
                "generated_document_type": None,
                "generated_document": None,
                "settlement_status": "Reopened",
            },
            update_modified=False,
        )
        return

    if lot.settlement_status == "Debit Note Created":
        lot.settlement_status = "Draft"

    lot.save()

def _get_effective_processor_lot_facts(
    subcontracting_order: str,
    processor_lot: str | None = None,
) -> dict[str, Any]:
    """Return SCO facts with a specific lot's effective policy snapshot."""
    facts = get_sco_facts(subcontracting_order)

    if not processor_lot:
        return facts

    lot = frappe.get_doc("Processor Lot", processor_lot)

    if lot.subcontracting_order != subcontracting_order:
        frappe.throw(
            _(
                "Processor Lot {0} does not belong to Subcontracting "
                "Order {1}."
            ).format(
                frappe.bold(lot.name),
                frappe.bold(subcontracting_order),
            )
        )

    return apply_processor_lot_settlement_policy(
        facts,
        lot,
    )


@frappe.whitelist()
def get_processor_lot_fact_summary(
    subcontracting_order: str,
    processor_lot: str | None = None,
) -> dict[str, Any]:
    """
    Return the read-only Fact Engine result for one Subcontracting Order.

    This method does not save, submit, cancel or create any document.
    """
    return _get_effective_processor_lot_facts(
        subcontracting_order=subcontracting_order,
        processor_lot=processor_lot,
    )

@frappe.whitelist()
def get_processor_lot_lifecycle_state(
    processor_lot: str,
) -> dict[str, Any]:
    """
    Return the current operational lifecycle state of one Processor Lot.

    This method is read-only and performs no document changes.
    """
    return get_processor_lot_lifecycle(
        processor_lot
    )

@frappe.whitelist()
def get_processor_lot_recommendation(
    subcontracting_order: str,
    business_classification: str,
    processor_lot: str | None = None,
) -> dict[str, Any]:
    """
    Return the current settlement recommendation for one
    Subcontracting Order and business classification.

    The Recommendation Engine consumes the read-only Fact Engine output,
    the selected business classification and the contractual settlement
    policy defined on the Purchase Order.

    This method does not save, submit, cancel or create any document.
    """
    if not subcontracting_order:
        frappe.throw(_("Subcontracting Order is required."))

    if not business_classification:
        frappe.throw(_("Business Classification is required."))

    facts = _get_effective_processor_lot_facts(
        subcontracting_order=subcontracting_order,
        processor_lot=processor_lot,
    )

    return recommend_settlement(
        facts,
        business_classification,
    )

@frappe.whitelist()
def get_processor_lot_recovery(
    subcontracting_order: str,
    business_classification: str,
    processor_lot: str | None = None,
) -> dict[str, Any]:
    """
    Return the calculated commercial recovery for one
    Subcontracting Order.

    Workflow
    --------
    Fact Engine
        ↓
    Recommendation Engine
        ↓
    Recovery Calculator

    This method is completely read-only.

    It does not create, modify, submit or cancel any ERPNext document.
    """

    if not subcontracting_order:
        frappe.throw(
            _("Subcontracting Order is required.")
        )

    if not business_classification:
        frappe.throw(
            _("Business Classification is required.")
        )

    facts = _get_effective_processor_lot_facts(
        subcontracting_order=subcontracting_order,
        processor_lot=processor_lot,
    )

    recommendation = recommend_settlement(
        facts,
        business_classification,
    )

    recovery = calculate_recovery(
        facts,
        recommendation,
    )

    recovery["settlement_netting"] = build_settlement_netting(
        subcontracting_order=subcontracting_order,
        facts=facts,
        gross_recovery=recovery,
    )

    return recovery

@frappe.whitelist()
def get_sco_settlement_details(
    subcontracting_order: str,
    settlement_name: str | None = None,
) -> dict[str, Any]:
    """
    Return controlled header and component data for the selected SCO.

    This method is used by the Processor Lot form script.
    """
    if not subcontracting_order:
        frappe.throw(_("Subcontracting Order is required."))

    sco = frappe.get_doc(
        "Subcontracting Order",
        subcontracting_order,
    )

    if sco.docstatus != 1:
        frappe.throw(
            _("Subcontracting Order {0} must be submitted.").format(
                frappe.bold(sco.name)
            )
        )

    purchase_order = None

    if sco.purchase_order:
        purchase_order = frappe.get_doc(
            "Purchase Order",
            sco.purchase_order,
        )

    items = []

    for supplied_row in sco.supplied_items:
        previous_qty = get_previous_settled_qty(
            subcontracting_order=sco.name,
            sco_supplied_item=supplied_row.name,
            exclude_settlement=settlement_name,
        )

        returned_qty = flt(
            getattr(supplied_row, "returned_qty", 0)
        )

        outstanding_qty = flt(
            flt(supplied_row.supplied_qty)
            - flt(supplied_row.consumed_qty)
            - returned_qty
            - previous_qty
        )

        if outstanding_qty <= 0:
            continue

        warehouse_balance = get_warehouse_balance(
            supplied_row.rm_item_code,
            sco.supplier_warehouse,
        )

        items.append(
            {
                "sco_supplied_item": supplied_row.name,
                "sco_finished_item": supplied_row.reference_name,
                "component_item": supplied_row.rm_item_code,
                "finished_item": supplied_row.main_item_code,
                "stock_uom": supplied_row.stock_uom,
                "required_qty": supplied_row.required_qty,
                "supplied_qty": supplied_row.supplied_qty,
                "consumed_qty": supplied_row.consumed_qty,
                "returned_qty": returned_qty,
                "previously_settled_qty": previous_qty,
                "outstanding_qty": outstanding_qty,
                "warehouse_balance": warehouse_balance,
                "settlement_qty": outstanding_qty,
                "component_valuation_rate": supplied_row.rate,
                "recovery_rate": supplied_row.rate,
                "recovery_amount": outstanding_qty
                * flt(supplied_row.rate),
            }
        )

    return {
        "company": sco.company,
        "purchase_order": sco.purchase_order,
        "supplier": sco.supplier,
        "supplier_warehouse": sco.supplier_warehouse,
        "cost_center": (
            getattr(sco, "cost_center", None)
            or getattr(purchase_order, "cost_center", None)
        ),
        "branch": (
            getattr(sco, "branch", None)
            or getattr(purchase_order, "branch", None)
        ),
        "recover_raw_material_shortage": (
            getattr(
                purchase_order,
                "custom_recover_raw_material_shortage",
                0,
            )
            if purchase_order
            else 0
        ),
        "recover_processing_charges_on_shortage": (
            getattr(
                purchase_order,
                "custom_recover_processing_charges_on_shortage",
                0,
            )
            if purchase_order
            else 0
        ),
        "settlement_basis": (
            getattr(
                purchase_order,
                "custom_settlement_basis",
                None,
            )
            if purchase_order
            else None
        ),
        "settlement_remarks": (
            getattr(
                purchase_order,
                "custom_settlement_remarks",
                None,
            )
            if purchase_order
            else None
        ),
        "settlement_policy_source": "Purchase Order",
        "items": items,
    }

def _get_submitted_credit_application_totals(
    processor_lot: str,
    subcontracting_order: str,
) -> dict[str, float]:
    """Return effective submitted PMA quantities applied to one lot."""
    applications = frappe.get_all(
        "Processor Material Account Entry",
        filters={
            "entry_type": "Credit Applied",
            "source_event": "Processor Lot Shortage",
            "processor_lot": processor_lot,
            "subcontracting_order": subcontracting_order,
            "account_direction": "Debit",
            "docstatus": 1,
            "is_reversed": 0,
        },
        fields=[
            "account_qty",
            "processed_qty",
            "commercial_qty",
        ],
    )

    return {
        "account_qty": flt(
            sum(
                flt(row.account_qty)
                for row in applications
            )
        ),
        "processed_qty": flt(
            sum(
                flt(row.processed_qty)
                for row in applications
            )
        ),
        "commercial_qty": flt(
            sum(
                flt(row.commercial_qty)
                for row in applications
            )
        ),
    }


def _get_submitted_advance_credit_totals(
    processor_lot: str,
    subcontracting_order: str,
) -> dict[str, float]:
    """Return effective PLR-excess credits originating from this lot."""
    credits = frappe.get_all(
        "Processor Material Account Entry",
        filters={
            "entry_type": "Advance Credit",
            "source_event": "PLR Excess",
            "processor_lot": processor_lot,
            "subcontracting_order": subcontracting_order,
            "account_direction": "Credit",
            "docstatus": 1,
            "is_reversed": 0,
        },
        fields=[
            "processed_qty",
            "account_qty",
            "commercial_qty",
            "material_credit_stock_entry",
        ],
    )
    effective = [
        row
        for row in credits
        if row.material_credit_stock_entry
        and frappe.db.get_value(
            "Stock Entry",
            row.material_credit_stock_entry,
            "docstatus",
        ) == 1
    ]

    return {
        "account_qty": flt(
            sum(flt(row.account_qty) for row in effective)
        ),
        "processed_qty": flt(
            sum(flt(row.processed_qty) for row in effective)
        ),
        "commercial_qty": flt(
            sum(flt(row.commercial_qty) for row in effective)
        ),
    }


@frappe.whitelist()
def get_processor_lot_physical_position(
    processor_lot: str,
) -> dict[str, Any]:
    """
    Return the physical receipt position for one Processor Lot.

    Expected quantity is derived from the finished-item commitment in the
    linked Subcontracting Order.

    Accepted quantity is derived from Processor Lot Receipts.

    Submitted SCR, Purchase Receipt and Purchase Invoice quantities are
    intentionally not used here.
    """
    if not processor_lot:
        frappe.throw(_("Processor Lot is required."))

    if not frappe.db.exists("Processor Lot", processor_lot):
        frappe.throw(
            _("Processor Lot {0} does not exist.").format(
                frappe.bold(processor_lot)
            )
        )

    lot = frappe.get_doc(
        "Processor Lot",
        processor_lot,
    )

    if not lot.subcontracting_order:
        frappe.throw(
            _("Processor Lot {0} has no Subcontracting Order.").format(
                frappe.bold(lot.name)
            )
        )

    sco = frappe.get_doc(
        "Subcontracting Order",
        lot.subcontracting_order,
    )

    finished_rows = [
        row
        for row in sco.items
        if row.item_code and flt(row.qty) > 0
    ]

    if not finished_rows:
        frappe.throw(
            _(
                "Subcontracting Order {0} has no finished-item quantity."
            ).format(
                frappe.bold(sco.name)
            )
        )

    finished_items = {
        row.item_code
        for row in finished_rows
    }

    finished_uoms = {
        row.stock_uom
        for row in finished_rows
        if row.stock_uom
    }

    if len(finished_items) != 1:
        frappe.throw(
            _(
                "Physical Receipt Position currently supports one "
                "finished item per Processor Lot. Subcontracting Order "
                "{0} contains: {1}"
            ).format(
                frappe.bold(sco.name),
                ", ".join(sorted(finished_items)),
            )
        )

    if len(finished_uoms) != 1:
        frappe.throw(
            _(
                "Physical Receipt Position currently supports one "
                "finished-item Stock UOM per Processor Lot."
            )
        )

    finished_item = next(iter(finished_items))
    stock_uom = next(iter(finished_uoms))

    expected_qty = flt(
        sum(
            flt(row.qty)
            for row in finished_rows
        )
    )

    receipts = _get_processor_lot_allocated_receipts(
        lot.name,
        exclude_cancelled=True,
    )

    receipt_items = {
        row.processed_item
        for row in receipts
        if row.processed_item
    }

    receipt_uoms = {
        row.stock_uom
        for row in receipts
        if row.stock_uom
    }

    if receipt_items and receipt_items != {finished_item}:
        frappe.throw(
            _(
                "Processor Lot Receipt item {0} does not match "
                "Subcontracting Order finished item {1}."
            ).format(
                frappe.bold(", ".join(sorted(receipt_items))),
                frappe.bold(finished_item),
            )
        )

    if receipt_uoms and receipt_uoms != {stock_uom}:
        frappe.throw(
            _(
                "Processor Lot Receipt Stock UOM {0} does not match "
                "Subcontracting Order Stock UOM {1}."
            ).format(
                frappe.bold(", ".join(sorted(receipt_uoms))),
                frappe.bold(stock_uom),
            )
        )

    company_accepted_qty = flt(
        sum(
            flt(row.allocated_accepted_qty)
            for row in receipts
        )
    )

    physical_balance_qty = flt(
        expected_qty - company_accepted_qty
    )

    applied_credit = _get_submitted_credit_application_totals(
        processor_lot=lot.name,
        subcontracting_order=sco.name,
    )
    physical_credit_applied_qty = flt(
        applied_credit.get("processed_qty")
    )
    net_physical_balance_qty = flt(
        physical_balance_qty - physical_credit_applied_qty
    )

    if net_physical_balance_qty > 0:
        position_status = "Pending"
    elif net_physical_balance_qty < 0:
        position_status = "Excess Accepted"
    elif physical_credit_applied_qty > 0:
        position_status = "Covered by Material Credit"
    else:
        position_status = "Complete"

    return {
        "processor_lot": lot.name,
        "subcontracting_order": sco.name,
        "processed_item": finished_item,
        "stock_uom": stock_uom,
        "expected_qty": expected_qty,
        "company_accepted_qty": company_accepted_qty,
        "physical_balance_qty": physical_balance_qty,
        "physical_credit_applied_qty": (
            physical_credit_applied_qty
        ),
        "net_physical_balance_qty": (
            net_physical_balance_qty
        ),
        "position_status": position_status,
    }

# -------------------------------------------------------------------------
# Receipt Summary
# -------------------------------------------------------------------------

def _get_processor_lot_allocated_receipts(
    processor_lot: str,
    exclude_cancelled: bool = False,
) -> list[frappe._dict]:
    """Return PLRs allocated to one lot with quantities scoped to that lot."""
    allocations = frappe.get_all(
        "Processor Lot Receipt Allocation",
        filters={
            "processor_lot": processor_lot,
            "parenttype": "Processor Lot Receipt",
            "parentfield": "lot_allocations",
        },
        fields=[
            "parent",
            "idx",
            "processed_item",
            "stock_uom",
            "allocated_accepted_qty",
            "allocated_invoice_qty",
        ],
        order_by="parent asc, idx asc",
    )

    if not allocations:
        return []

    receipt_names = list(dict.fromkeys(
        row.parent for row in allocations if row.parent
    ))

    receipt_filters = {"name": ["in", receipt_names]}
    if exclude_cancelled:
        receipt_filters["docstatus"] = ["!=", 2]

    receipt_rows = frappe.get_all(
        "Processor Lot Receipt",
        filters=receipt_filters,
        fields=[
            "name",
            "physical_receipt_date",
            "creation",
            "modified",
            "docstatus",
            "processed_item",
            "stock_uom",
            "company_accepted_qty",
            "company_net_weight",
            "supplier_net_weight",
            "supplier_invoice_qty",
            "processor_material_credit_qty",
            "material_credit_status",
            "subcontracting_receipt",
            "purchase_receipt",
            "purchase_invoice",
        ],
    )
    receipts_by_name = {row.name: row for row in receipt_rows}
    allocated_receipts = []

    for allocation in allocations:
        receipt = receipts_by_name.get(allocation.parent)
        if not receipt:
            continue

        allocated_accepted_qty = flt(
            allocation.allocated_accepted_qty
        )
        allocated_invoice_qty = flt(
            allocation.allocated_invoice_qty
        )
        company_weight_ratio = (
            allocated_accepted_qty / flt(receipt.company_accepted_qty)
            if flt(receipt.company_accepted_qty)
            else 0.0
        )
        supplier_weight_ratio = (
            allocated_invoice_qty / flt(receipt.supplier_invoice_qty)
            if flt(receipt.supplier_invoice_qty)
            else 0.0
        )

        allocated_receipts.append(frappe._dict({
            **receipt,
            "processed_item": (
                allocation.processed_item or receipt.processed_item
            ),
            "stock_uom": allocation.stock_uom or receipt.stock_uom,
            "allocation_idx": allocation.idx,
            "allocated_accepted_qty": allocated_accepted_qty,
            "allocated_invoice_qty": allocated_invoice_qty,
            "allocated_company_net_weight": flt(
                receipt.company_net_weight
            ) * company_weight_ratio,
            "allocated_supplier_net_weight": flt(
                receipt.supplier_net_weight
            ) * supplier_weight_ratio,
        }))

    allocated_receipts.sort(
        key=lambda row: (
            str(row.physical_receipt_date or row.creation or ""),
            str(row.creation or ""),
            row.allocation_idx,
        )
    )
    return allocated_receipts

def refresh_processor_lot_receipt_summary(
    processor_lot: str,
) -> None:
    """
    Refresh the operational receipt summary for one Processor Lot.

    The summary is always recomputed from the linked Processor Lot
    Receipts. No incremental updates are performed, ensuring that the
    displayed totals always reflect the authoritative operational data.
    """
    if not processor_lot:
        return

    receipts = _get_processor_lot_allocated_receipts(
        processor_lot,
        exclude_cancelled=True,
    )

    receipt_count = len(receipts)
    last_receipt_date = None
    stock_uom = None

    total_company_net_weight = 0.0
    total_company_accepted_qty = 0.0
    total_supplier_net_weight = 0.0
    total_supplier_invoice_qty = 0.0

    uoms = set()
    processed_items = set()

    for receipt in receipts:
        if receipt.physical_receipt_date:
            last_receipt_date = receipt.physical_receipt_date

        if receipt.processed_item:
            processed_items.add(receipt.processed_item)

        if receipt.stock_uom:
            uoms.add(receipt.stock_uom)

        total_company_accepted_qty += flt(
            receipt.allocated_accepted_qty
        )

        total_company_net_weight += flt(
            receipt.allocated_company_net_weight
        )
        total_supplier_net_weight += flt(
            receipt.allocated_supplier_net_weight
        )
        total_supplier_invoice_qty += flt(
            receipt.allocated_invoice_qty
        )

    if len(processed_items) > 1:
        frappe.throw(
            _(
                "Processor Lot {0} contains Processor Lot Receipts "
                "for multiple processed items: {1}"
            ).format(
                frappe.bold(processor_lot),
                ", ".join(sorted(processed_items)),
            )
        )

    if len(uoms) > 1:
        frappe.throw(
            _(
                "Processor Lot {0} contains Processor Lot Receipts "
                "with multiple Stock UOMs: {1}"
            ).format(
                frappe.bold(processor_lot),
                ", ".join(sorted(uoms)),
            )
        )

    if uoms:
        stock_uom = next(iter(uoms))

    frappe.db.set_value(
        "Processor Lot",
        processor_lot,
        {
            "receipt_count": receipt_count,
            "last_receipt_date": last_receipt_date,
            "receipt_stock_uom": stock_uom,
            "total_company_accepted_qty": (
                total_company_accepted_qty
            ),
            "total_company_net_weight": (
                total_company_net_weight
            ),
            "total_supplier_net_weight": (
                total_supplier_net_weight
            ),
            "total_supplier_invoice_qty": (
                total_supplier_invoice_qty
            ),
        },
        update_modified=False,
    )

# -------------------------------------------------------------------------
# Receipt Journey
# -------------------------------------------------------------------------


@frappe.whitelist()
def get_processor_lot_receipt_journey(
    processor_lot: str,
) -> dict:
    """
    Return the live document journey for every receipt in a Processor Lot.

    Processor Lot Receipt remains the operational record for one truck.
    Standard ERPNext documents remain authoritative for stock and accounting.

    No journey status is stored. The current position is derived from the
    linked Subcontracting Receipt, Purchase Receipt and Purchase Invoice.
    """
    if not processor_lot:
        return {
            "summary": {
                "receipt_count": 0,
                "completed_count": 0,
                "pending_count": 0,
            },
            "receipts": [],
        }

    if not frappe.db.exists("Processor Lot", processor_lot):
        frappe.throw(
            _("Processor Lot {0} does not exist.").format(
                frappe.bold(processor_lot)
            )
        )

    receipts = _get_processor_lot_allocated_receipts(
        processor_lot,
    )

    journey_rows = []
    completed_count = 0

    for receipt in receipts:
        scr_state = _get_receipt_journey_document_state(
            "Subcontracting Receipt",
            receipt.subcontracting_receipt,
        )
        pr_state = _get_receipt_journey_document_state(
            "Purchase Receipt",
            receipt.purchase_receipt,
        )
        pi_state = _get_receipt_journey_document_state(
            "Purchase Invoice",
            receipt.purchase_invoice,
        )

        progress = _get_receipt_journey_progress(
            receipt_docstatus=receipt.docstatus,
            scr_state=scr_state,
            pr_state=pr_state,
            pi_state=pi_state,
        )

        if progress["is_complete"]:
            completed_count += 1

        commercial_qty_variance = flt(
            flt(receipt.allocated_invoice_qty)
            - flt(receipt.allocated_accepted_qty),
            3,
        )

        journey_rows.append(
            {
                "processor_lot_receipt": receipt.name,
                "receipt_date": receipt.physical_receipt_date,
                "receipt_docstatus": receipt.docstatus,
                "stock_uom": receipt.stock_uom,
                "company_accepted_qty": flt(
                    receipt.allocated_accepted_qty
                ),
                "company_net_weight": flt(
                    receipt.allocated_company_net_weight
                ),
                "supplier_net_weight": flt(
                    receipt.allocated_supplier_net_weight
                ),
                "supplier_invoice_qty": flt(
                    receipt.allocated_invoice_qty
                ),
                "commercial_qty_variance": (
                    commercial_qty_variance
                ),
                "has_commercial_qty_variance": bool(
                    commercial_qty_variance
                ),
                "processor_material_credit_qty": flt(
                    receipt.processor_material_credit_qty
                ),
                "material_credit_status": (
                    receipt.material_credit_status
                ),
                "subcontracting_receipt": scr_state,
                "purchase_receipt": pr_state,
                "purchase_invoice": pi_state,
                "workflow_state": progress["workflow_state"],
                "current_stage": progress["current_stage"],
                "next_action": progress["next_action"],
                "journey_state": progress["journey_state"],
                "is_complete": progress["is_complete"],
            }
        )

    receipt_count = len(journey_rows)

    return {
        "summary": {
            "receipt_count": receipt_count,
            "completed_count": completed_count,
            "pending_count": receipt_count - completed_count,
        },
        "receipts": journey_rows,
    }


def _get_receipt_journey_document_state(
    doctype: str,
    document_name: str | None,
) -> dict:
    """
    Return a normalized state for one linked ERPNext document.

    Broken references are reported separately from documents that have not
    yet been created. Posting Date is returned for journey chronology.
    """
    if not document_name:
        return {
            "doctype": doctype,
            "name": None,
            "docstatus": None,
            "status": None,
            "posting_date": None,
            "state": "not_created",
            "label": _("Not Created"),
        }

    values = frappe.db.get_value(
        doctype,
        document_name,
        [
            "docstatus",
            "status",
            "posting_date",
        ],
        as_dict=True,
    )

    if not values:
        return {
            "doctype": doctype,
            "name": document_name,
            "docstatus": None,
            "status": None,
            "posting_date": None,
            "state": "broken_link",
            "label": _("Broken Link"),
        }

    docstatus = values.docstatus

    if docstatus == 2:
        state = "cancelled"
        label = _("Cancelled")
    elif docstatus == 1:
        state = "submitted"
        label = _("Submitted")
    else:
        state = "draft"
        label = _("Draft")

    return {
        "doctype": doctype,
        "name": document_name,
        "docstatus": docstatus,
        "status": values.status,
        "posting_date": values.posting_date,
        "state": state,
        "label": label,
    }


def _get_receipt_journey_progress(
    receipt_docstatus: int,
    scr_state: dict,
    pr_state: dict,
    pi_state: dict,
) -> dict:
    """Determine where one truck receipt has stopped and what comes next."""
    # if receipt_docstatus == 2:
    #     return {
    #         "current_stage": _("PLR Cancelled"),
    #         "next_action": _("Review Cancelled Receipt"),
    #         "journey_state": "danger",
    #         "is_complete": False,
    #     }

    # if receipt_docstatus == 0:
    #     return {
    #         "current_stage": _("PLR Draft"),
    #         "next_action": _("Complete Processor Lot Receipt"),
    #         "journey_state": "warning",
    #         "is_complete": False,
    #     }

    if scr_state["state"] == "broken_link":
        return {
            "workflow_state": "scr_link_broken",
            "current_stage": _("SCR Link Broken"),
            "next_action": _("Review Subcontracting Receipt Link"),
            "journey_state": "danger",
            "is_complete": False,
        }

    if scr_state["state"] == "cancelled":
        return {
            "workflow_state": "scr_cancelled",
            "current_stage": _("SCR Cancelled"),
            "next_action": _("Create or Amend Subcontracting Receipt"),
            "journey_state": "danger",
            "is_complete": False,
        }

    if scr_state["state"] == "not_created":
        return {
            "workflow_state": "awaiting_scr",
            "current_stage": _("Awaiting SCR"),
            "next_action": _("Create Subcontracting Receipt"),
            "journey_state": "warning",
            "is_complete": False,
        }

    if scr_state["state"] == "draft":
        return {
            "workflow_state": "scr_draft",
            "current_stage": _("SCR Draft"),
            "next_action": _("Submit Subcontracting Receipt"),
            "journey_state": "warning",
            "is_complete": False,
        }

    if pr_state["state"] == "broken_link":
        return {
            "workflow_state": "pr_link_broken",
            "current_stage": _("PR Link Broken"),
            "next_action": _("Review Purchase Receipt Link"),
            "journey_state": "danger",
            "is_complete": False,
        }

    if pr_state["state"] == "cancelled":
        return {
            "workflow_state": "pr_cancelled",
            "current_stage": _("PR Cancelled"),
            "next_action": _("Create or Amend Purchase Receipt"),
            "journey_state": "danger",
            "is_complete": False,
        }

    if pr_state["state"] == "not_created":
        return {
            "workflow_state": "awaiting_pr",
            "current_stage": _("Awaiting PR"),
            "next_action": _("Create Purchase Receipt"),
            "journey_state": "warning",
            "is_complete": False,
        }

    if pr_state["state"] == "draft":
        return {
            "workflow_state": "pr_draft",
            "current_stage": _("PR Draft"),
            "next_action": _("Submit Purchase Receipt"),
            "journey_state": "warning",
            "is_complete": False,
        }

    if pi_state["state"] == "broken_link":
        return {
            "workflow_state": "pi_link_broken",
            "current_stage": _("PI Link Broken"),
            "next_action": _("Review Purchase Invoice Link"),
            "journey_state": "danger",
            "is_complete": False,
        }

    if pi_state["state"] == "cancelled":
        return {
            "workflow_state": "pi_cancelled",
            "current_stage": _("PI Cancelled"),
            "next_action": _("Create or Amend Purchase Invoice"),
            "journey_state": "danger",
            "is_complete": False,
        }

    if pi_state["state"] == "not_created":
        return {
            "workflow_state": "awaiting_pi",
            "current_stage": _("Awaiting PI"),
            "next_action": _("Create Purchase Invoice"),
            "journey_state": "warning",
            "is_complete": False,
        }

    if pi_state["state"] == "draft":
        return {
            "workflow_state": "pi_draft",
            "current_stage": _("PI Draft"),
            "next_action": _("Submit Purchase Invoice"),
            "journey_state": "warning",
            "is_complete": False,
        }

    return {
        "workflow_state": "commercially_complete",
        "current_stage": _("Commercially Complete"),
        "next_action": _("Receipt Cycle Complete"),
        "journey_state": "good",
        "is_complete": True,
    }


# -------------------------------------------------------------------------
# Shared helpers
# -------------------------------------------------------------------------

def get_processor_lot_lifecycle(
    processor_lot: str,
) -> dict[str, Any]:
    """
    Return the current operational lifecycle state of one Processor Lot.

    This helper performs no rendering and changes no document.

    Current authoritative facts
    ---------------------------
    - Entrusted quantity comes from submitted SCO supplied-item rows.
    - Material-transfer references come from submitted Stock Entries linked
      to the SCO with purpose "Send to Subcontractor".
    - Receipt activity comes from Processor Lot Receipt records.
    - Company Accepted Qty comes from Processor Lot Receipts.

    A later phase will add an explicit physical-receipt-completion fact
    before the state "ready_for_reconciliation" is introduced.
    """
    if not processor_lot:
        frappe.throw(_("Processor Lot is required."))

    if not frappe.db.exists("Processor Lot", processor_lot):
        frappe.throw(
            _("Processor Lot {0} does not exist.").format(
                frappe.bold(processor_lot)
            )
        )

    lot = frappe.get_doc(
        "Processor Lot",
        processor_lot,
    )

    if not lot.subcontracting_order:
        frappe.throw(
            _("Processor Lot {0} has no Subcontracting Order.").format(
                frappe.bold(lot.name)
            )
        )

    sco = frappe.get_doc(
        "Subcontracting Order",
        lot.subcontracting_order,
    )

    entrusted_component_qty = flt(
        sum(
            flt(row.supplied_qty)
            for row in sco.supplied_items
        )
    )

    component_uoms = {
        row.stock_uom
        for row in sco.supplied_items
        if row.stock_uom
    }

    component_uom = (
        next(iter(component_uoms))
        if len(component_uoms) == 1
        else ""
    )

    expected_finished_qty = flt(
        sum(
            flt(row.qty)
            for row in sco.items
        )
    )

    finished_uoms = {
        row.stock_uom
        for row in sco.items
        if row.stock_uom
    }

    finished_uom = (
        next(iter(finished_uoms))
        if len(finished_uoms) == 1
        else ""
    )

    receipts = _get_processor_lot_allocated_receipts(
        lot.name,
        exclude_cancelled=True,
    )
    receipt_count = len(receipts)
    journey_summary = (
        get_processor_lot_receipt_journey(lot.name).get("summary")
        or {}
    )
    company_accepted_qty = flt(
        sum(
            flt(row.allocated_accepted_qty)
            for row in receipts
        )
    )

    applied_credit = _get_submitted_credit_application_totals(
        processor_lot=lot.name,
        subcontracting_order=sco.name,
    )
    physical_credit_applied_qty = flt(
        applied_credit.get("processed_qty")
    )
    commercial_credit_applied_qty = flt(
        applied_credit.get("commercial_qty")
    )
    advance_credit = _get_submitted_advance_credit_totals(
        processor_lot=lot.name,
        subcontracting_order=sco.name,
    )
    advance_credit_processed_qty = flt(
        advance_credit.get("processed_qty")
    )
    advance_credit_commercial_qty = flt(
        advance_credit.get("commercial_qty")
    )
    gross_physical_shortage_qty = max(
        flt(expected_finished_qty - company_accepted_qty),
        0.0,
    )
    net_physical_shortage_qty = max(
        flt(
            gross_physical_shortage_qty
            - physical_credit_applied_qty
        ),
        0.0,
    )

    material_transfer_stock_entries = [
        row.name
        for row in frappe.get_all(
            "Stock Entry",
            filters={
                "subcontracting_order": sco.name,
                "purpose": "Send to Subcontractor",
                "docstatus": 1,
            },
            fields=["name"],
            order_by=(
                "posting_date, posting_time, creation"
            ),
        )
    ]

    if entrusted_component_qty <= 0:
        state = "material_not_entrusted"
    elif receipt_count == 0:
        state = "material_with_processor"
    else:
        state = "receiving_in_progress"

    return {
        "processor_lot": lot.name,
        "subcontracting_order": sco.name,
        "state": state,
        "entrusted_component_qty": entrusted_component_qty,
        "component_uom": component_uom,
        "expected_finished_qty": expected_finished_qty,
        "finished_uom": finished_uom,
        "receipt_count": receipt_count,
        "completed_receipt_count": (
            journey_summary.get("completed_count") or 0
        ),
        "pending_receipt_count": (
            journey_summary.get("pending_count") or 0
        ),
        "company_accepted_qty": company_accepted_qty,
        "gross_physical_shortage_qty": (
            gross_physical_shortage_qty
        ),
        "physical_credit_applied_qty": (
            physical_credit_applied_qty
        ),
        "net_physical_shortage_qty": (
            net_physical_shortage_qty
        ),
        "commercial_credit_applied_qty": (
            commercial_credit_applied_qty
        ),
        "advance_credit_processed_qty": (
            advance_credit_processed_qty
        ),
        "advance_credit_commercial_qty": (
            advance_credit_commercial_qty
        ),
        "material_transfer_stock_entries": (
            material_transfer_stock_entries
        ),
    }

def get_previous_settled_qty(
    subcontracting_order: str,
    sco_supplied_item: str,
    exclude_settlement: str | None = None,
) -> float:
    """
    Total submitted settlement quantity for a specific SCO supplied-item row.

    Cancelled settlements and the currently edited settlement are excluded.
    """
    conditions = [
        "parent.docstatus = 1",
        "parent.subcontracting_order = %(subcontracting_order)s",
        "child.sco_supplied_item = %(sco_supplied_item)s",
    ]

    values = {
        "subcontracting_order": subcontracting_order,
        "sco_supplied_item": sco_supplied_item,
    }

    if exclude_settlement:
        conditions.append("parent.name != %(exclude_settlement)s")
        values["exclude_settlement"] = exclude_settlement

    result = frappe.db.sql(
        f"""
        SELECT COALESCE(SUM(child.settlement_qty), 0)
        FROM `tabProcessor Lot Settlement Item` child
        INNER JOIN `tabProcessor Lot` parent
            ON parent.name = child.parent
        WHERE {" AND ".join(conditions)}
        """,
        values=values,
    )

    return flt(result[0][0] if result else 0)


def get_warehouse_balance(
    item_code: str,
    warehouse: str,
) -> float:
    """Return current Bin actual quantity for an item and warehouse."""
    if not item_code or not warehouse:
        return 0.0

    return flt(
        frappe.db.get_value(
            "Bin",
            {
                "item_code": item_code,
                "warehouse": warehouse,
            },
            "actual_qty",
        )
        or 0
    )

@frappe.whitelist()
@validate_and_sanitize_search_inputs
def get_unsettled_subcontracting_orders(
    doctype: str,
    txt: str,
    searchfield: str,
    start: int,
    page_len: int,
    filters: dict | None = None,
) -> list[list[Any]]:
    """
    Return submitted Subcontracting Orders having unsettled components.

    The outstanding quantity is calculated for each specific SCO supplied-item
    row as:

        supplied_qty
        - consumed_qty
        - returned_qty
        - submitted Processor Lot quantity

    The Supplier Warehouse Stock Ledger balance is deliberately not used to
    allocate quantity to an SCO because the warehouse may contain stock from
    several simultaneous Subcontracting Orders.
    """
    filters = filters or {}

    company = filters.get("company")
    supplier = filters.get("supplier")

    conditions = [
        "sco.docstatus = 1",
        """
        (
            COALESCE(si.supplied_qty, 0)
            - COALESCE(si.consumed_qty, 0)
            - COALESCE(si.returned_qty, 0)
            - COALESCE(previous_settlement.settled_qty, 0)
        ) > 0
        """,
        """
        (
            sco.name LIKE %(txt)s
            OR sco.supplier LIKE %(txt)s
            OR COALESCE(sco.purchase_order, '') LIKE %(txt)s
            OR COALESCE(sco.supplier_warehouse, '') LIKE %(txt)s
            OR si.rm_item_code LIKE %(txt)s
            OR si.main_item_code LIKE %(txt)s
        )
        """,
    ]

    values: dict[str, Any] = {
        "txt": f"%{txt}%",
        "start": int(start),
        "page_len": int(page_len),
    }

    if company:
        conditions.append("sco.company = %(company)s")
        values["company"] = company

    if supplier:
        conditions.append("sco.supplier = %(supplier)s")
        values["supplier"] = supplier

    return frappe.db.sql(
        f"""
		SELECT
			sco.name,
			sco.supplier,
			CASE
				WHEN sco.status = 'Closed'
					THEN '🔴 Closed'
				WHEN sco.status = 'Partially Received'
					THEN '🟡 Partially Received'
				ELSE COALESCE(sco.status, '')
			END,
			COALESCE(sco.purchase_order, ''),
			COALESCE(sco.supplier_warehouse, ''),
			CONCAT(
				'Outstanding: ',
				GROUP_CONCAT(
					CONCAT(
						si.rm_item_code,
						': ',
						FORMAT(
							(
								COALESCE(si.supplied_qty, 0)
								- COALESCE(si.consumed_qty, 0)
								- COALESCE(si.returned_qty, 0)
								- COALESCE(previous_settlement.settled_qty, 0)
							),
							3
						),
						' ',
						COALESCE(si.stock_uom, '')
					)
					ORDER BY si.idx
					SEPARATOR ' | '
				)
			) AS outstanding_details
        FROM `tabSubcontracting Order` sco
        INNER JOIN `tabSubcontracting Order Supplied Item` si
            ON si.parent = sco.name
            AND si.parenttype = 'Subcontracting Order'
            AND si.parentfield = 'supplied_items'
        LEFT JOIN (
            SELECT
                settlement_item.sco_supplied_item,
                SUM(settlement_item.settlement_qty) AS settled_qty
            FROM `tabProcessor Lot Settlement Item` settlement_item
            INNER JOIN `tabProcessor Lot` settlement
                ON settlement.name = settlement_item.parent
            WHERE settlement.docstatus = 1
            GROUP BY settlement_item.sco_supplied_item
        ) previous_settlement
            ON previous_settlement.sco_supplied_item = si.name
        WHERE {" AND ".join(conditions)}
		GROUP BY
			sco.name,
			sco.supplier,
			sco.status,
			sco.purchase_order,
			sco.supplier_warehouse,
			sco.modified
        ORDER BY sco.modified DESC
        LIMIT %(start)s, %(page_len)s
        """,
        values=values,
        as_list=True,
    )
