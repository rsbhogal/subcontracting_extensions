// Copyright (c) 2026, R S Bhogal and contributors
// For license information, please see license.txt

frappe.ui.form.on("Purchase Order", {
    setup(frm) {
        frm.set_query(
            "custom_processing_route",
            "items",
            (doc, cdt, cdn) => {
                const row = locals[cdt][cdn];

                if (!row.fg_item || !row.item_code) {
                    return {
                        filters: {
                            is_active: 1,
                        },
                    };
                }

                return {
                    filters: {
                        is_active: 1,
                        finished_item: row.fg_item,
                        service_item: row.item_code,
                    },
                };
            }
        );
    },

    before_save(frm) {
        return confirm_default_finished_good_qty(frm);
    },

    before_submit(frm) {
        return confirm_default_finished_good_qty(frm);
    },

    refresh(frm) {
        configure_supplier_warehouse(frm);
        configure_processing_route(frm);
        toggle_purchase_order_workspace_button(frm);
    },

    is_subcontracted(frm) {
        configure_supplier_warehouse(frm);
        configure_processing_route(frm);
        toggle_purchase_order_workspace_button(frm);
    },

    supplier_warehouse(frm) {
        configure_supplier_warehouse(frm);
    }
});

/**
 * Show the Subcontracting button only for subcontracted POs.
 */
function toggle_purchase_order_workspace_button(frm) {
    frm.remove_custom_button(__("Subcontracting"));

    if (frm.doc.is_subcontracted) {
        add_subcontracting_workspace_button(frm);
    }
}

/**
 * Require Processing Route on item rows of subcontracted Purchase Orders.
 *
 * Server-side validation in purchase_order.py remains authoritative.
 * This client-side setting provides Frappe's native mandatory indication
 * and validation feedback to the user.
 */
function configure_processing_route(frm) {
    const is_subcontracted =
        Boolean(frm.doc.is_subcontracted);

    frm.fields_dict.items.grid.update_docfield_property(
        "custom_processing_route",
        "reqd",
        is_subcontracted ? 1 : 0
    );

    frm.refresh_field("items");
}

/**
 * Require and highlight Supplier Warehouse for subcontracted Purchase Orders.
 */
function configure_supplier_warehouse(frm) {
    const is_subcontracted =
        Boolean(frm.doc.is_subcontracted);

    frm.toggle_reqd(
        "supplier_warehouse",
        is_subcontracted
    );

    highlight_supplier_warehouse(frm);
}


/**
 * Highlight the actual Supplier Warehouse field while it is required
 * but has not yet been selected.
 */
function highlight_supplier_warehouse(frm) {
    const field =
        frm.get_field("supplier_warehouse");

    if (!field || !field.$wrapper) {
        return;
    }

    const needs_attention =
        Boolean(frm.doc.is_subcontracted)
        && !frm.doc.supplier_warehouse;

    const $control =
        field.$wrapper.find(
            "input.form-control, .control-input .form-control"
        ).first();

    const $label =
        field.$wrapper.find(
            ".control-label"
        );

    if (!$control.length) {
        return;
    }

    const control =
        $control.get(0);

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

        control.style.setProperty(
            "box-shadow",
            "none",
            "important"
        );

        $label.css({
            "color": "#c62828",
            "font-weight": "600"
        });

        return;
    }

    control.style.removeProperty("border");
    control.style.removeProperty("border-radius");
    control.style.removeProperty("background-color");
    control.style.removeProperty("box-shadow");

    $label.css({
        "color": "",
        "font-weight": ""
    });
}

/**
 * Ask the user to confirm subcontracted PO rows whose Finished Good Qty
 * remains at ERPNext's potentially unnoticed default value of 1.
 *
 * The same unchanged document state is confirmed only once during the
 * current form session. Reloading the form requires confirmation again.
 */
function confirm_default_finished_good_qty(frm) {
    if (!frm.doc.is_subcontracted) {
        return Promise.resolve();
    }

    const suspicious_rows =
        get_default_finished_good_qty_rows(frm);

    if (!suspicious_rows.length) {
        return Promise.resolve();
    }

    const signature =
        build_finished_good_qty_signature(
            suspicious_rows
        );

    /*
     * Avoid asking twice for the same unchanged document state, such as
     * once on Save and immediately again on Submit.
     */
    if (
        frm.__confirmed_finished_good_qty_signature
        === signature
    ) {
        return Promise.resolve();
    }

    const row_details = suspicious_rows
        .map(row => {
            const item =
                row.item_code
                || __("Item not selected");

            const finished_item =
                row.fg_item
                || __("Finished Item not selected");

            return `
                <li style="margin-bottom:8px;">
                    <strong>${__(
                        "Row {0}",
                        [row.idx]
                    )}</strong><br>
                    ${frappe.utils.escape_html(item)}<br>
                    <span class="text-muted">
                        ${__("Finished Item")}:
                        ${frappe.utils.escape_html(
                            finished_item
                        )}
                    </span>
                </li>
            `;
        })
        .join("");

    const message = `
        <div>
            <p>
                ${__(
                    "The following subcontracted Purchase Order rows have " +
                    "Finished Good Qty equal to 1."
                )}
            </p>

            <ul style="padding-left:20px;">
                ${row_details}
            </ul>

            <p style="margin-bottom:0;">
                <strong>
                    ${__(
                        "Please confirm that 1 is the intended finished " +
                        "quantity and not an unnoticed default value."
                    )}
                </strong>
            </p>
        </div>
    `;

    return new Promise((resolve, reject) => {
        frappe.confirm(
            message,
            () => {
                frm.__confirmed_finished_good_qty_signature =
                    signature;

                resolve();
            },
            () => {
                frappe.validated = false;
                reject(
                    new Error(
                        "Finished Good Qty confirmation cancelled."
                    )
                );
            }
        );
    });
}


/**
 * Return subcontracted PO rows whose Finished Good Qty is exactly 1.
 */
function get_default_finished_good_qty_rows(frm) {
    return (frm.doc.items || []).filter(row => {
        return (
            row.item_code
            && flt(row.fg_item_qty, 3) === 1
        );
    });
}


/**
 * Build a stable signature for the suspicious rows.
 *
 * If the user changes an item, finished item or Finished Good Qty, the
 * signature changes and confirmation is required again.
 */
function build_finished_good_qty_signature(rows) {
    return rows
        .map(row => {
            return [
                row.name,
                row.item_code || "",
                row.fg_item || "",
                flt(row.fg_item_qty, 3)
            ].join("|");
        })
        .sort()
        .join("::");
}