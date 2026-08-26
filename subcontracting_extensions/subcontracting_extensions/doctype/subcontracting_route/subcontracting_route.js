// Copyright (c) 2026, R.S. Bhogal and contributors
// For license information, please see license.txt

frappe.ui.form.on("Subcontracting Route", {
    refresh(frm) {
        set_finished_item_query(frm);
        set_service_item_query(frm);
        set_manufacturing_bom_query(frm);
        set_route_component_warehouse_query(frm);
        render_manufacturing_bom_summary(frm);
        set_route_component_properties(frm);
        highlight_missing_source_warehouses(frm);
    },

    finished_item(frm) {
        set_manufacturing_bom_query(frm);

        /*
        * A previously selected BOM may belong to the old Finished Item.
        * Clear it and its derived components when Finished Item changes.
        */
        frm.set_value("manufacturing_bom", null);

        frm.clear_table("route_components");
        frm.refresh_field("route_components");

        clear_manufacturing_bom_summary(frm);
    },

    manufacturing_bom(frm) {
        if (!frm.doc.manufacturing_bom) {
            frm.clear_table("route_components");
            frm.refresh_field("route_components");

            clear_manufacturing_bom_summary(frm);
            return;
        }

        render_manufacturing_bom_summary(frm);
        fetch_route_components_from_bom(frm);
    }
});

frappe.ui.form.on("Subcontracting Route Component", {
    source_warehouse(frm) {
        highlight_missing_source_warehouses(frm);
    }
});

/**
 * Restrict Finished Item selection to active stock Items.
 */
function set_finished_item_query(frm) {
    frm.set_query("finished_item", () => {
        return {
            filters: {
                is_stock_item: 1,
                disabled: 0
            }
        };
    });
}

/**
 * Restrict Manufacturing BOM selection to submitted, active BOMs
 * belonging to the selected Finished Item.
 */
function set_manufacturing_bom_query(frm) {
    frm.set_query("manufacturing_bom", () => {
        const filters = {
            docstatus: 1,
            is_active: 1
        };

        if (frm.doc.finished_item) {
            filters.item = frm.doc.finished_item;
        }

        return {
            filters
        };
    });
}

/**
 * Restrict Route Component Source Warehouse to active leaf warehouses.
 */
function set_route_component_warehouse_query(frm) {
    frm.set_query(
        "source_warehouse",
        "route_components",
        () => {
            return {
                filters: {
                    is_group: 0,
                    disabled: 0
                }
            };
        }
    );
}

/**
 * Restrict Service Item selection to non-stock Items.
 *
 * A Subcontracting Route service represents the processing/service
 * purchased from the subcontractor and therefore must not be a stock item.
 */
function set_service_item_query(frm) {
    frm.set_query("service_item", () => {
        return {
            filters: {
                is_stock_item: 0,
                disabled: 0
            }
        };
    });
}

/**
 * Load and render the selected Manufacturing BOM's read-only summary.
 */
function render_manufacturing_bom_summary(frm) {
    if (!frm.doc.manufacturing_bom) {
        clear_manufacturing_bom_summary(frm);
        return;
    }

    frappe.call({
        method: [
            "subcontracting_extensions",
            "subcontracting_extensions",
            "doctype",
            "subcontracting_route",
            "subcontracting_route",
            "get_bom_summary"
        ].join("."),
        args: {
            manufacturing_bom:
                frm.doc.manufacturing_bom
        },
        callback(r) {
            const summary = r.message || {};

            if (!summary.bom) {
                clear_manufacturing_bom_summary(frm);
                return;
            }

            draw_manufacturing_bom_summary(
                frm,
                summary
            );
        }
    });
}


/**
 * Draw the Manufacturing BOM summary panel.
 */
function draw_manufacturing_bom_summary(
    frm,
    summary
) {
    const field =
        frm.get_field(
            "manufacturing_bom_summary"
        );

    if (!field || !field.$wrapper) {
        return;
    }

    const submitted =
        summary.docstatus === 1;

    const status_label =
        submitted
            ? __("Submitted")
            : __("Not Submitted");

    const active_label =
        summary.is_active
            ? __("Active")
            : __("Inactive");

    const default_label =
        summary.is_default_bom
            ? __("Item Default BOM")
            : __("Different from Item Default");

    const status_indicator =
        submitted && summary.is_active
            ? "green"
            : "orange";

    const default_indicator =
        summary.is_default_bom
            ? "blue"
            : "orange";

    const components_html =
        (summary.components || [])
            .map(component => {
                return `
                    <tr>
                        <td style="
                            padding: 7px 10px;
                            border-bottom: 1px solid var(--border-color);
                        ">
                            ${frappe.utils.escape_html(
                                component.item_code || ""
                            )}
                        </td>

                        <td style="
                            padding: 7px 10px;
                            text-align: right;
                            border-bottom: 1px solid var(--border-color);
                            white-space: nowrap;
                        ">
                            ${format_number(
                                flt(component.qty),
                                null,
                                3
                            )}
                            ${frappe.utils.escape_html(
                                component.uom || ""
                            )}
                        </td>
                    </tr>
                `;
            })
            .join("");

    const default_bom_note =
        summary.is_default_bom
            ? ""
            : `
                <div style="
                    margin-top: 10px;
                    padding: 8px 10px;
                    border-radius: 6px;
                    background: var(--orange-50);
                    border: 1px solid var(--orange-200);
                ">
                    <strong>
                        ${__(
                            "Selected BOM differs from Item Default BOM"
                        )}
                    </strong>

                    ${
                        summary.default_bom
                            ? `
                                <div class="text-muted"
                                     style="margin-top:3px;">
                                    ${__(
                                        "Item Default BOM: {0}",
                                        [
                                            frappe.utils.escape_html(
                                                summary.default_bom
                                            )
                                        ]
                                    )}
                                </div>
                            `
                            : `
                                <div class="text-muted"
                                     style="margin-top:3px;">
                                    ${__(
                                        "No Default BOM is set on the Finished Item."
                                    )}
                                </div>
                            `
                    }
                </div>
            `;

    const html = `
        <div style="
            border: 1px solid var(--border-color);
            border-radius: 8px;
            overflow: hidden;
            background: var(--card-bg);
        ">
            <div style="
                padding: 10px 12px;
                background: var(--subtle-fg);
                border-bottom: 1px solid var(--border-color);
                display: flex;
                gap: 8px;
                flex-wrap: wrap;
                align-items: center;
            ">
                <span class="indicator-pill ${status_indicator}">
                    ${status_label}
                </span>

                <span class="indicator-pill ${status_indicator}">
                    ${active_label}
                </span>

                <span class="indicator-pill ${default_indicator}">
                    ${default_label}
                </span>
            </div>

            <div style="
                display: grid;
                grid-template-columns:
                    minmax(220px, 1fr)
                    minmax(140px, 0.35fr);
                gap: 18px;
                padding: 12px;
            ">
                <div>
                    <div class="text-muted"
                         style="font-size:12px;">
                        ${__("Finished Item")}
                    </div>

                    <div style="
                        margin-top: 3px;
                        font-weight: 600;
                    ">
                        ${frappe.utils.escape_html(
                            summary.finished_item || ""
                        )}
                    </div>
                </div>

                <div>
                    <div class="text-muted"
                         style="font-size:12px;">
                        ${__("BOM Quantity")}
                    </div>

                    <div style="
                        margin-top: 3px;
                        font-weight: 600;
                    ">
                        ${format_number(
                            flt(summary.bom_qty),
                            null,
                            3
                        )}
                        ${frappe.utils.escape_html(
                            summary.bom_uom || ""
                        )}
                    </div>
                </div>
            </div>

            <div style="
                padding: 0 12px 12px;
            ">
                <div style="
                    margin-bottom: 6px;
                    font-weight: 600;
                ">
                    ${__("BOM Components")}
                </div>

                <table style="
                    width: 100%;
                    border-collapse: collapse;
                    border: 1px solid var(--border-color);
                    border-radius: 6px;
                    overflow: hidden;
                ">
                    <thead>
                        <tr style="
                            background: var(--subtle-fg);
                        ">
                            <th style="
                                padding: 7px 10px;
                                text-align: left;
                                border-bottom:
                                    1px solid var(--border-color);
                            ">
                                ${__("Component Item")}
                            </th>

                            <th style="
                                padding: 7px 10px;
                                text-align: right;
                                border-bottom:
                                    1px solid var(--border-color);
                            ">
                                ${__("Required Quantity")}
                            </th>
                        </tr>
                    </thead>

                    <tbody>
                        ${
                            components_html
                            || `
                                <tr>
                                    <td colspan="2"
                                        class="text-muted"
                                        style="
                                            padding:10px;
                                            text-align:center;
                                        ">
                                        ${__("No BOM Components")}
                                    </td>
                                </tr>
                            `
                        }
                    </tbody>
                </table>

                ${default_bom_note}
            </div>
        </div>
    `;

    field.$wrapper.html(html);
}


/**
 * Clear the BOM summary panel.
 */
function clear_manufacturing_bom_summary(frm) {
    const field =
        frm.get_field(
            "manufacturing_bom_summary"
        );

    if (!field || !field.$wrapper) {
        return;
    }

    field.$wrapper.empty();
}

/**
 * Fetch the selected Manufacturing BOM's component rows.
 *
 * Component Item, quantity, UOM and BOM Detail are controlled by the BOM.
 * The configurator only selects the Source Warehouse.
 */
function fetch_route_components_from_bom(frm) {
    /*
     * Preserve Source Warehouse values by BOM Item child-row name.
     *
     * This allows the BOM-derived rows to be refreshed without forcing the
     * user to reselect warehouses when the same BOM remains selected.
     */
    const existing_warehouses = {};

    (frm.doc.route_components || []).forEach(row => {
        if (row.bom_detail && row.source_warehouse) {
            existing_warehouses[row.bom_detail] =
                row.source_warehouse;
        }
    });

    frappe.call({
        method: [
            "subcontracting_extensions",
            "subcontracting_extensions",
            "doctype",
            "subcontracting_route",
            "subcontracting_route",
            "get_bom_route_components"
        ].join("."),
        args: {
            manufacturing_bom:
                frm.doc.manufacturing_bom
        },
        freeze: true,
        freeze_message: __(
            "Loading components from Manufacturing BOM..."
        ),
        callback(r) {
            const components = r.message || [];

            frm.clear_table("route_components");

            components.forEach(component => {
                const row = frm.add_child(
                    "route_components"
                );

                row.component_item =
                    component.component_item;

                row.required_qty_per_bom_qty =
                    component.required_qty_per_bom_qty;

                row.stock_uom =
                    component.stock_uom;

                row.bom_detail =
                    component.bom_detail;

                row.source_warehouse =
                    existing_warehouses[
                        component.bom_detail
                    ] || null;
            });

            frm.refresh_field("route_components");
            set_route_component_properties(frm);
            highlight_missing_source_warehouses(frm);

            if (!components.length) {
                frappe.msgprint({
                    title: __("No BOM Components"),
                    indicator: "orange",
                    message: __(
                        "Manufacturing BOM {0} does not contain any component rows.",
                        [
                            frappe.utils.escape_html(
                                frm.doc.manufacturing_bom
                            )
                        ]
                    )
                });
            }
        }
    });
}


/**
 * Keep BOM-controlled Route Component fields read-only.
 *
 * Source Warehouse remains editable because it is the route-specific
 * configuration added on top of the Manufacturing BOM.
 */
function set_route_component_properties(frm) {
    const grid = frm.get_field(
        "route_components"
    )?.grid;

    if (!grid) {
        return;
    }

    [
        "component_item",
        "required_qty_per_bom_qty",
        "stock_uom",
        "bom_detail"
    ].forEach(fieldname => {
        grid.update_docfield_property(
            fieldname,
            "read_only",
            1
        );
    });

    grid.update_docfield_property(
        "source_warehouse",
        "read_only",
        0
    );

    /*
     * Route Component rows are derived exactly from the selected BOM.
     * Users must not add or delete rows independently.
     */
    grid.df.cannot_add_rows = 1;
    grid.df.cannot_delete_rows = 1;

    grid.wrapper.find(".grid-add-row").hide();
    grid.wrapper.find(".grid-remove-rows").hide();
    grid.wrapper.find(".grid-remove-all-rows").hide();

    grid.refresh();
}

/**
 * Highlight blank Source Warehouse fields in Route Components.
 *
 * Users are expected to consciously select a Source Warehouse for every
 * BOM-derived component row before the route is saved.
 */
function highlight_missing_source_warehouses(frm) {
    const grid =
        frm.get_field("route_components")?.grid;

    if (!grid) {
        return;
    }

    (frm.doc.route_components || []).forEach(row => {
        const grid_row =
            grid.grid_rows_by_docname[row.name];

        if (!grid_row) {
            return;
        }

        const field =
            grid_row.on_grid_fields_dict?.source_warehouse;

        if (!field || !field.$wrapper) {
            return;
        }

        const $control =
            field.$wrapper.find(
                "input.form-control, .control-input .form-control"
            ).first();

        if (!$control.length) {
            return;
        }

        const control =
            $control.get(0);

        const needs_attention =
            !row.source_warehouse;

        if (needs_attention) {
            control.style.setProperty(
                "border",
                "1px solid #ff6b6b",
                "important"
            );

            control.style.setProperty(
                "border-radius",
                "7px",
                "important"
            );

            control.style.setProperty(
                "background-color",
                "#fff7f7",
                "important"
            );

            return;
        }

        control.style.removeProperty("border");
        control.style.removeProperty("border-radius");
        control.style.removeProperty("background-color");
    });
}