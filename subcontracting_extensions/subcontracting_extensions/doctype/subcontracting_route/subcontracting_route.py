# Copyright (c) 2026, R S Bhogal and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt


class SubcontractingRoute(Document):
    """
    Defines one valid subcontracting manufacturing route.

    A route links:
        Finished Item
            +
        Service Item
            +
        Manufacturing BOM

    Multiple routes may exist for the same Finished Item, provided they
    represent different subcontracting operations.
    """

    def validate(self):
        self._validate_finished_item()
        self._validate_service_item()
        self._validate_bom()
        self._validate_finished_goods_target_warehouse()
        self._validate_route_components()
        self._validate_duplicate_route()

    # ---------------------------------------------------------------------
    # Finished Item
    # ---------------------------------------------------------------------

    def _validate_finished_item(self):

        item = frappe.get_cached_doc("Item", self.finished_item)

        if item.disabled:
            frappe.throw(
                _("Finished Item {0} is disabled.").format(
                    frappe.bold(item.name)
                )
            )

        if not item.is_stock_item:
            frappe.throw(
                _("Finished Item {0} must be a Stock Item.").format(
                    frappe.bold(item.name)
                )
            )

    # ---------------------------------------------------------------------
    # Service Item
    # ---------------------------------------------------------------------

    def _validate_service_item(self):

        item = frappe.get_cached_doc("Item", self.service_item)

        if item.disabled:
            frappe.throw(
                _("Service Item {0} is disabled.").format(
                    frappe.bold(item.name)
                )
            )

        if item.is_stock_item:
            frappe.throw(
                _("Service Item {0} must be a non-stock Item.").format(
                    frappe.bold(item.name)
                )
            )

    # ---------------------------------------------------------------------
    # Finished Goods Target Warehouse
    # ---------------------------------------------------------------------

    def _validate_finished_goods_target_warehouse(self):
        """Validate a configured target; require it for active V2 routes."""
        warehouse_name = self.get("finished_goods_target_warehouse")

        if not warehouse_name:
            if self.is_active and cint(
                frappe.conf.get("v2_processor_first_draft_entry")
            ):
                frappe.throw(
                    _("Finished Goods Target Warehouse is required for an active V2 route."),
                    title=_("Target Warehouse Required"),
                )
            return

        warehouse = frappe.db.get_value(
            "Warehouse",
            warehouse_name,
            ["is_group", "disabled"],
            as_dict=True,
        )

        if not warehouse:
            frappe.throw(_("Finished Goods Target Warehouse {0} does not exist.").format(
                frappe.bold(warehouse_name)
            ))
        if warehouse.is_group:
            frappe.throw(_("Finished Goods Target Warehouse {0} is a group warehouse.").format(
                frappe.bold(warehouse_name)
            ))
        if warehouse.disabled:
            frappe.throw(_("Finished Goods Target Warehouse {0} is disabled.").format(
                frappe.bold(warehouse_name)
            ))

    # ---------------------------------------------------------------------
    # Manufacturing BOM
    # ---------------------------------------------------------------------

    def _validate_bom(self):

        bom = frappe.get_cached_doc(
            "BOM",
            self.manufacturing_bom,
        )

        if bom.docstatus != 1:
            frappe.throw(
                _("Manufacturing BOM must be Submitted.")
            )

        if not bom.is_active:
            frappe.throw(
                _("Manufacturing BOM must be Active.")
            )

        if bom.item != self.finished_item:
            frappe.throw(
                _(
                    "Manufacturing BOM {0} produces {1}, "
                    "not Finished Item {2}."
                ).format(
                    frappe.bold(bom.name),
                    frappe.bold(bom.item),
                    frappe.bold(self.finished_item),
                )
            )

    # ---------------------------------------------------------------------
    # Duplicate Route
    # ---------------------------------------------------------------------

    def _validate_duplicate_route(self) -> None:
        """
        Prevent duplicate active routes having the same Finished Item,
        Service Item and Manufacturing BOM.
        """
        if not self.is_active:
            return

        filters = {
            "is_active": 1,
            "finished_item": self.finished_item,
            "service_item": self.service_item,
            "manufacturing_bom": self.manufacturing_bom,
        }

        # Exclude the current record only when that document already
        # exists in the database. For a new in-memory document,
        # self.is_new() may return None rather than True.
        if self.name and frappe.db.exists(
            "Subcontracting Route",
            self.name,
        ):
            filters["name"] = ["!=", self.name]

        duplicate = frappe.db.exists(
            "Subcontracting Route",
            filters,
        )

        if duplicate:
            frappe.throw(
                _(
                    "Active Subcontracting Route {0} already exists "
                    "for Finished Item {1}, Service Item {2}, "
                    "and Manufacturing BOM {3}."
                ).format(
                    frappe.bold(duplicate),
                    frappe.bold(self.finished_item),
                    frappe.bold(self.service_item),
                    frappe.bold(self.manufacturing_bom),
                )
            )

    def _validate_route_components(self) -> None:
        """
        Ensure Route Components exactly match the selected Manufacturing BOM.

        The route may add a Source Warehouse, but it must not change the BOM's
        component identity, quantity, UOM, or BOM-detail reference.
        """
        bom = frappe.get_doc(
            "BOM",
            self.manufacturing_bom,
        )

        bom_rows = {
            row.name: {
                "component_item": row.item_code,
                "required_qty_per_bom_qty": row.qty,
                "stock_uom": row.uom,
            }
            for row in bom.items
        }

        route_rows = self.get("route_components") or []

        if len(route_rows) != len(bom_rows):
            frappe.throw(
                _(
                    "Route Components must contain exactly {0} row(s), "
                    "matching Manufacturing BOM {1}."
                ).format(
                    len(bom_rows),
                    frappe.bold(bom.name),
                ),
                title=_("Route Components Do Not Match BOM"),
            )

        seen_bom_details = set()

        for row in route_rows:
            if not row.bom_detail:
                frappe.throw(
                    _("Row {0}: BOM Detail is required.").format(row.idx),
                    title=_("Route Components Do Not Match BOM"),
                )

            if row.bom_detail in seen_bom_details:
                frappe.throw(
                    _(
                        "Row {0}: BOM Detail {1} is duplicated."
                    ).format(
                        row.idx,
                        frappe.bold(row.bom_detail),
                    ),
                    title=_("Duplicate Route Component"),
                )

            seen_bom_details.add(row.bom_detail)

            bom_row = bom_rows.get(row.bom_detail)

            if not bom_row:
                frappe.throw(
                    _(
                        "Row {0}: BOM Detail {1} does not belong to "
                        "Manufacturing BOM {2}."
                    ).format(
                        row.idx,
                        frappe.bold(row.bom_detail),
                        frappe.bold(bom.name),
                    ),
                    title=_("Route Components Do Not Match BOM"),
                )

            if row.component_item != bom_row["component_item"]:
                frappe.throw(
                    _(
                        "Row {0}: Component Item must be {1}, as defined "
                        "by Manufacturing BOM {2}."
                    ).format(
                        row.idx,
                        frappe.bold(bom_row["component_item"]),
                        frappe.bold(bom.name),
                    ),
                    title=_("Route Components Do Not Match BOM"),
                )

            if flt(row.required_qty_per_bom_qty) != flt(
                bom_row["required_qty_per_bom_qty"]
            ):
                frappe.throw(
                    _(
                        "Row {0}: Required Qty per BOM Qty must be {1}."
                    ).format(
                        row.idx,
                        frappe.bold(
                            bom_row["required_qty_per_bom_qty"]
                        ),
                    ),
                    title=_("Route Components Do Not Match BOM"),
                )

            if row.stock_uom != bom_row["stock_uom"]:
                frappe.throw(
                    _(
                        "Row {0}: Stock UOM must be {1}."
                    ).format(
                        row.idx,
                        frappe.bold(bom_row["stock_uom"]),
                    ),
                    title=_("Route Components Do Not Match BOM"),
                )

            warehouse = frappe.db.get_value(
                "Warehouse",
                row.source_warehouse,
                ["is_group", "disabled"],
                as_dict=True,
            )

            if not warehouse:
                frappe.throw(
                    _(
                        "Row {0}: Source Warehouse {1} does not exist."
                    ).format(
                        row.idx,
                        frappe.bold(row.source_warehouse),
                    ),
                    title=_("Invalid Source Warehouse"),
                )

            if warehouse.is_group:
                frappe.throw(
                    _(
                        "Row {0}: Source Warehouse {1} is a group warehouse."
                    ).format(
                        row.idx,
                        frappe.bold(row.source_warehouse),
                    ),
                    title=_("Invalid Source Warehouse"),
                )

            if warehouse.disabled:
                frappe.throw(
                    _(
                        "Row {0}: Source Warehouse {1} is disabled."
                    ).format(
                        row.idx,
                        frappe.bold(row.source_warehouse),
                    ),
                    title=_("Invalid Source Warehouse"),
                )

@frappe.whitelist()
def get_bom_route_components(
    manufacturing_bom: str,
) -> list[dict]:
    """
    Return controlled Route Component values from one Manufacturing BOM.

    BOM Item row names are returned as ``bom_detail`` so users never need
    to enter ERPNext's internal child-row references manually.
    """
    if not manufacturing_bom:
        frappe.throw(
            _("Manufacturing BOM is required.")
        )

    if not frappe.db.exists(
        "BOM",
        manufacturing_bom,
    ):
        frappe.throw(
            _("Manufacturing BOM {0} does not exist.").format(
                frappe.bold(manufacturing_bom)
            )
        )

    bom = frappe.get_doc(
        "BOM",
        manufacturing_bom,
    )

    if bom.docstatus != 1:
        frappe.throw(
            _("Manufacturing BOM must be Submitted.")
        )

    if not bom.is_active:
        frappe.throw(
            _("Manufacturing BOM must be Active.")
        )

    return [
        {
            "component_item": row.item_code,
            "required_qty_per_bom_qty": row.qty,
            "stock_uom": row.uom,
            "bom_detail": row.name,
        }
        for row in bom.items
    ]

@frappe.whitelist()
def get_bom_summary(
    manufacturing_bom: str,
) -> dict:
    """
    Return read-only Manufacturing BOM information for display on the
    Subcontracting Route form.

    This is informational only. Route validation remains authoritative.
    """
    if not manufacturing_bom:
        return {}

    if not frappe.db.exists(
        "BOM",
        manufacturing_bom,
    ):
        frappe.throw(
            _("Manufacturing BOM {0} does not exist.").format(
                frappe.bold(manufacturing_bom)
            )
        )

    bom = frappe.get_doc(
        "BOM",
        manufacturing_bom,
    )

    default_bom = frappe.db.get_value(
        "Item",
        bom.item,
        "default_bom",
    )

    return {
        "bom": bom.name,
        "docstatus": bom.docstatus,
        "is_active": bom.is_active,
        "finished_item": bom.item,
        "bom_qty": flt(bom.quantity),
        "bom_uom": bom.uom,
        "default_bom": default_bom,
        "is_default_bom": (
            bool(default_bom)
            and default_bom == bom.name
        ),
        "components": [
            {
                "item_code": row.item_code,
                "qty": flt(row.qty),
                "uom": row.uom,
            }
            for row in bom.items
        ],
    }
