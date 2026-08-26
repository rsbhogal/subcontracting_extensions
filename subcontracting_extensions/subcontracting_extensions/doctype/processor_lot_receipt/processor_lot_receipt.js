// Copyright (c) 2026, R.S. Bhogal and contributors
// For license information, please see license.txt

frappe.ui.form.on("Processor Lot Receipt", {
    refresh(frm) {
        clear_unlinked_stock_uom_default(frm);
        calculate_physical_weights(frm);
        calculate_commercial_reconciliation(frm);
        toggle_measurement_fields(frm);
        set_physical_receipt_editability(frm);
        show_physical_receipt_lock_banner(frm);
        setup_subcontracting_receipt_actions(frm);
        setup_material_credit_stock_entry_actions(frm);
        if (!frm.is_new()) {
            toggle_measurement_fields(frm);
        }
    },

    after_save(frm) {
        prompt_to_refresh_stale_draft_scr(frm);
    },

    supplier_gross_weight(frm) {
        calculate_physical_weights(frm);
        calculate_commercial_reconciliation(frm);
    },

    supplier_tare_weight(frm) {
        calculate_physical_weights(frm);
        calculate_commercial_reconciliation(frm);
    },

    company_gross_weight(frm) {
        calculate_physical_weights(frm);
        calculate_commercial_reconciliation(frm);
        toggle_measurement_fields(frm);
        set_physical_receipt_editability(frm);
    },

    company_tare_weight(frm) {
        calculate_physical_weights(frm);
        calculate_commercial_reconciliation(frm);
        toggle_measurement_fields(frm);
        set_physical_receipt_editability(frm);
    },

    company_weighment_uom(frm) {
        calculate_physical_weights(frm);
        calculate_commercial_reconciliation(frm);
        toggle_measurement_fields(frm);
        set_physical_receipt_editability(frm);
    },

    measurement_method(frm) {
        toggle_measurement_fields(frm);
        calculate_physical_weights(frm);
        calculate_commercial_reconciliation(frm);
        set_physical_receipt_editability(frm);
    },


    company_accepted_qty(frm) {
        calculate_commercial_reconciliation(frm);
    },

    processor_lot(frm) {
        fetch_processor_lot_receipt_context(frm);
    },
});

/**
 * Lock physical receipt facts once an active SCR is linked.
 *
 * Commercial invoice facts may still be recorded later, so the form itself
 * remains saveable.
 */
function set_physical_receipt_editability(frm) {
    const physical_receipt_locked =
        Boolean(frm.doc.subcontracting_receipt);

    const protected_fields = [
        "physical_receipt_date",
        "vehicle_no",
        "supplier_challan_number",
        "supplier_challan_date",
        "measurement_method",

        "supplier_weighbridge",
        "supplier_weighbridge_slip_number",
        "supplier_weighment_date",
        "supplier_gross_weight",
        "supplier_tare_weight",
        "supplier_net_weight",
        "supplier_weighment_uom",

        "company_weighbridge",
        "company_weighbridge_slip_number",
        "company_weighment_date",
        "company_gross_weight",
        "company_tare_weight",
        "company_net_weight",
        "company_weighment_uom",

        "company_accepted_qty",
        "company_accepted_uom"
    ];

    protected_fields.forEach(fieldname => {
        frm.toggle_enable(
            fieldname,
            !physical_receipt_locked
        );
    });

        /*
     * A PLR belongs permanently to the Processor Lot selected when the
     * document is first created.
     */
    frm.toggle_enable(
        "processor_lot",
        frm.is_new()
    );

    /*
     * These fields are derived from Processor Lot and must never be
     * entered or altered independently.
     */
    [
        "subcontracting_order",
        "supplier",
        "company",
        "supplier_warehouse",
        "processed_item",
        "stock_uom"
    ].forEach(fieldname => {
        frm.toggle_enable(
            fieldname,
            false
        );
    });

    /*
     * Before an SCR exists, Weight-derived fields retain their normal
     * method-specific editability.
     */
    if (!physical_receipt_locked) {
        const accepted_qty_is_derived =
            can_derive_company_accepted_qty(frm);

        /*
        * Net quantities and system-controlled UOM remain read-only.
        */
        frm.toggle_enable(
            "supplier_net_weight",
            false
        );

        frm.toggle_enable(
            "company_net_weight",
            false
        );

        frm.toggle_enable(
            "company_accepted_uom",
            false
        );

        /*
        * Company Accepted Qty is:
        *
        * - read-only when Net Qty can be copied directly because
        *   Measurement UOM equals Stock UOM;
        * - editable when Count is used;
        * - editable when Weight is used but the UOMs differ.
        */
        frm.toggle_enable(
            "company_accepted_qty",
            !accepted_qty_is_derived
        );
    }
}


/**
 * Configure the PLR form according to the selected measurement method.
 *
 * Blank:
 * - Hide Physical Receipt Measurement.
 * - Hide Commercial Reconciliation.
 * - Highlight Measurement Method.
 *
 * Weight:
 * - Show physical and commercial sections.
 * - Require Company Gross Qty, Company Tare Qty and Company Measurement UOM.
 * - Keep Company Accepted Qty read-only.
 *
 * Count:
 * - Hide Physical Receipt Measurement.
 * - Show Commercial Reconciliation.
 * - Require Company Accepted Qty and keep it editable.
 */
function toggle_measurement_fields(frm) {
    const method =
        frm.doc.measurement_method || "";

    const has_method =
        Boolean(method);

    const is_weight =
        method === "Weight";

    const is_count =
        method === "Count";

    frm.toggle_display(
        "weighment_section",
        is_weight
    );

    frm.toggle_display(
        "quantity_reconciliation_section",
        has_method
    );

    [
        "company_gross_weight",
        "company_tare_weight",
        "company_weighment_uom"
    ].forEach(fieldname => {
        frm.toggle_reqd(
            fieldname,
            is_weight
        );
    });

    const accepted_qty_is_derived =
        can_derive_company_accepted_qty(frm);

    const accepted_qty_is_manual =
        has_method
        && !accepted_qty_is_derived;

    /*
    * Manual entry is required for:
    *
    * - Count receipts.
    * - Weight receipts where Measurement UOM and Stock UOM differ.
    */
    frm.toggle_reqd(
        "company_accepted_qty",
        accepted_qty_is_manual
    );

    /*
    * Automatically derived quantity is read-only.
    * Manual quantity remains editable until an SCR exists.
    */
    frm.set_df_property(
        "company_accepted_qty",
        "read_only",
        accepted_qty_is_derived ? 1 : 0
    );

    frm.set_df_property(
        "company_accepted_qty",
        "description",
        accepted_qty_is_derived
            ? __(
                "Calculated from Company Net Qty because Measurement UOM " +
                "matches Stock UOM."
            )
            : (
                is_weight
                    ? __(
                        "Enter the accepted finished-item quantity in Stock UOM. " +
                        "Physical measurement is recorded in a different UOM."
                    )
                    : __(
                        "Enter the physically accepted count in Stock UOM."
                    )
            )
    );

    frm.refresh_field(
        "company_accepted_qty"
    );

    frm.refresh_field(
        "company_accepted_qty"
    );

    highlight_measurement_method(frm);
}

/**
 * Highlight Measurement Method while no selection has been made.
 *
 * Styling is applied directly to ERPNext's actual Select control so the
 * standard field dimensions and rounded corners remain unchanged.
 */
function highlight_measurement_method(frm) {
    const field =
        frm.get_field("measurement_method");

    if (!field || !field.$wrapper) {
        return;
    }

    const is_blank =
        !frm.doc.measurement_method;

    const $control =
        field.$wrapper.find(
            "select.form-control, .control-input .form-control"
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

    if (is_blank) {
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
 * Adds the operational action for creating or opening the truck's SCR.
 */
/**
 * Add the operational actions for creating, refreshing or opening
 * the truck's Subcontracting Receipt.
 *
 * Behaviour
 * ---------
 * No linked SCR:
 *     Create a new Draft SCR through the standard mapper.
 *
 * Linked Draft SCR:
 *     Allow the user to open it or rebuild it from the current PLR.
 *
 * Linked submitted SCR:
 *     Allow only viewing. A submitted stock document must not be rebuilt.
 */
function setup_subcontracting_receipt_actions(frm) {
    if (frm.is_new()) {
        return;
    }

    const credit_qty = flt(
        frm.doc.processor_material_credit_qty,
        3
    );

    if (
        credit_qty > 0
        && !frm.doc.subcontracting_receipt
        && frm.doc.material_credit_status !== "Recorded"
    ) {
        setup_material_credit_record_action(frm);
        return;
    }

    /*
     * No linked SCR: retain the existing standard mapped-document flow.
     */
    if (!frm.doc.subcontracting_receipt) {
        frm.add_custom_button(
            __("Subcontracting Receipt"),
            () => {
                frappe.model.open_mapped_doc({
                    method:
                        "subcontracting_extensions.subcontracting_extensions.doctype." +
                        "processor_lot_receipt." +
                        "processor_lot_receipt." +
                        "make_subcontracting_receipt",
                    frm: frm,
                    freeze_message: __(
                        "Creating Subcontracting Receipt ..."
                    )
                });
            },
            __("Create")
        );

        return;
    }

    const subcontracting_receipt =
        frm.doc.subcontracting_receipt;

    /*
     * An existing SCR can always be opened for review.
     */
    frm.add_custom_button(
        __("Subcontracting Receipt"),
        () => {
            frappe.set_route(
                "Form",
                "Subcontracting Receipt",
                subcontracting_receipt
            );
        },
        __("View")
    );

    /*
     * Determine whether the linked SCR is still Draft.
     *
     * Only a Draft SCR may be deleted and rebuilt from the current PLR.
     */
    frappe.db.get_value(
        "Subcontracting Receipt",
        subcontracting_receipt,
        "docstatus"
    ).then(r => {
        const values = r.message || {};

        if (values.docstatus !== 0) {
            return;
        }

        frm.add_custom_button(
            __("Refresh Draft SCR"),
            () => {
                confirm_refresh_draft_scr(
                    frm,
                    subcontracting_receipt
                );
            }
        );
    });
}

function setup_material_credit_record_action(frm) {
    frm.add_custom_button(
        __("Material Credit Record"),
        () => {
            frappe.confirm(
                __(
                    "Create a Draft Processor Material Account Entry for " +
                    "{0} {1}? It must be reviewed and submitted before " +
                    "the Subcontracting Receipt can be created.",
                    [
                        format_number(
                            flt(frm.doc.processor_material_credit_qty, 3),
                            null,
                            3
                        ),
                        frm.doc.stock_uom
                    ]
                ),
                () => {
                    frappe.call({
                        method: [
                            "subcontracting_extensions",
                            "subcontracting_extensions",
                            "doctype",
                            "processor_lot_receipt",
                            "processor_lot_receipt",
                            "create_material_credit_record"
                        ].join("."),
                        args: {
                            processor_lot_receipt: frm.doc.name
                        },
                        freeze: true,
                        freeze_message: __(
                            "Creating Material Credit Record ..."
                        ),
                        callback(r) {
                            const result = r.message || {};
                            if (!result.name) {
                                return;
                            }
                            frappe.set_route(
                                "Form",
                                result.doctype,
                                result.name
                            );
                        }
                    });
                }
            );
        },
        __("Create")
    );
}

/**
 * Add the controlled Processor Material Credit stock-document action.
 *
 * A recorded credit with no linked Stock Entry may create one Draft Material
 * Receipt.  Once linked, the action opens that document instead.  The server
 * remains authoritative for PMA, SCR, valuation, account and duplicate checks.
 */
function setup_material_credit_stock_entry_actions(frm) {
    if (
        frm.is_new()
        || flt(frm.doc.processor_material_credit_qty, 3) <= 0
        || frm.doc.material_credit_status !== "Recorded"
    ) {
        return;
    }

    if (frm.doc.material_credit_stock_entry) {
        frm.add_custom_button(
            __("Material Credit Stock Entry"),
            () => {
                frappe.set_route(
                    "Form",
                    "Stock Entry",
                    frm.doc.material_credit_stock_entry
                );
            },
            __("View")
        );

        return;
    }

    if (!frm.doc.subcontracting_receipt) {
        return;
    }

    frappe.db.get_value(
        "Subcontracting Receipt",
        frm.doc.subcontracting_receipt,
        "docstatus"
    ).then(r => {
        const values = r.message || {};

        if (values.docstatus !== 1) {
            return;
        }

        add_create_material_credit_stock_entry_button(frm);
    });
}

/**
 * Add Create only after the linked backed SCR is submitted.
 */
function add_create_material_credit_stock_entry_button(frm) {
    frm.add_custom_button(
        __("Material Credit Stock Entry"),
        () => {
            frappe.confirm(
                __(
                    "Create a Draft Material Receipt for {0} {1} of " +
                    "Processor Material Credit? Its rates and accounts will " +
                    "be derived from the submitted backed SCR.",
                    [
                        format_number(
                            flt(frm.doc.processor_material_credit_qty, 3),
                            null,
                            3
                        ),
                        frappe.utils.escape_html(
                            frm.doc.stock_uom || ""
                        )
                    ]
                ),
                () => {
                    create_material_credit_stock_entry(frm);
                }
            );
        },
        __("Create")
    );
}

/**
 * Create the controlled Draft through the authoritative server method.
 */
function create_material_credit_stock_entry(frm) {
    frappe.call({
        method: [
            "subcontracting_extensions",
            "subcontracting_extensions",
            "doctype",
            "processor_lot_receipt",
            "processor_lot_receipt",
            "create_material_credit_stock_entry"
        ].join("."),
        args: {
            processor_lot_receipt: frm.doc.name
        },
        freeze: true,
        freeze_message: __(
            "Creating Material Credit Stock Entry..."
        ),
        callback(r) {
            const result = r.message;

            if (!result || !result.name) {
                frappe.msgprint({
                    title: __("Stock Entry Not Created"),
                    indicator: "red",
                    message: __(
                        "The server did not return a Material Credit " +
                        "Stock Entry."
                    )
                });

                return;
            }

            frm.reload_doc().then(() => {
                frappe.show_alert({
                    message:
                        result.message
                        || __(
                            "Draft Material Credit Stock Entry {0} created.",
                            [
                                frappe.utils.escape_html(
                                    result.name
                                )
                            ]
                        ),
                    indicator: "green"
                });

                frappe.set_route(
                    "Form",
                    result.doctype || "Stock Entry",
                    result.name
                );
            });
        }
    });
}

/**
 * Confirm that the user wants to replace the linked Draft SCR.
 */
function confirm_refresh_draft_scr(
    frm,
    subcontracting_receipt
) {
    frappe.confirm(
        __(
            "Draft Subcontracting Receipt {0} will be deleted and rebuilt " +
            "from the current Processor Lot Receipt values. Continue?",
            [
                frappe.utils.escape_html(
                    subcontracting_receipt
                )
            ]
        ),
        () => {
            refresh_draft_scr_from_plr(frm);
        }
    );
}


/**
 * Rebuild the linked Draft SCR through the authoritative server method.
 *
 * The server verifies document status, deletes the existing Draft SCR,
 * reruns ERPNext's standard SCO-to-SCR mapper and restores the PLR link.
 */
function refresh_draft_scr_from_plr(frm) {
    frappe.call({
        method: [
            "subcontracting_extensions",
            "subcontracting_extensions",
            "doctype",
            "processor_lot_receipt",
            "processor_lot_receipt",
            "refresh_draft_subcontracting_receipt"
        ].join("."),
        args: {
            processor_lot_receipt: frm.doc.name
        },
        freeze: true,
        freeze_message: __(
            "Refreshing Draft Subcontracting Receipt..."
        ),
        callback(r) {
            const result = r.message;

            if (!result || !result.name) {
                frappe.msgprint({
                    title: __("Draft SCR Not Refreshed"),
                    indicator: "red",
                    message: __(
                        "The server did not return a rebuilt " +
                        "Subcontracting Receipt."
                    )
                });

                return;
            }

            frm.reload_doc().then(() => {
                frappe.show_alert({
                    message:
                        result.message
                        || __(
                            "Draft Subcontracting Receipt {0} refreshed.",
                            [
                                frappe.utils.escape_html(
                                    result.name
                                )
                            ]
                        ),
                    indicator: "green"
                });

                frappe.set_route(
                    "Form",
                    result.doctype
                        || "Subcontracting Receipt",
                    result.name
                );
            });
        }
    });
}

/**
 * After saving the PLR, check whether its linked Draft SCR has become stale.
 *
 * A submitted SCR remains protected by server-side validation. This helper
 * applies only to a Draft SCR and offers to rebuild it from the newly saved
 * PLR values.
 */
function prompt_to_refresh_stale_draft_scr(frm) {
    const subcontracting_receipt =
        frm.doc.subcontracting_receipt;

    if (!subcontracting_receipt) {
        return;
    }

    frappe.db.get_value(
        "Subcontracting Receipt",
        subcontracting_receipt,
        [
            "docstatus",
            "total_qty"
        ]
    ).then(r => {
        const values = r.message || {};

        if (values.docstatus !== 0) {
            return;
        }

        const plr_qty = flt(
            frm.doc.company_accepted_qty,
            3
        );

        const scr_qty = flt(
            values.total_qty,
            3
        );

        if (plr_qty === scr_qty) {
            return;
        }

        frappe.confirm(
            __(
                "Company Accepted Qty is now {0}, while Draft " +
                "Subcontracting Receipt {1} contains {2}. " +
                "Refresh the Draft SCR from this Processor Lot Receipt now?",
                [
                    format_number(plr_qty, null, 3),
                    frappe.utils.escape_html(
                        subcontracting_receipt
                    ),
                    format_number(scr_qty, null, 3)
                ]
            ),
            () => {
                refresh_draft_scr_from_plr(frm);
            }
        );
    });
}

/**
 * Calculate supplier and company net quantities immediately in the form.
 *
 * For Weight-based receipts, Company Accepted Qty is derived from
 * Company Net Qty when the measurement UOM matches the Stock UOM.
 *
 * The Python controller repeats these calculations during Save and
 * remains authoritative.
 */
function calculate_physical_weights(frm) {
    const supplier_net = calculate_net_weight(
        frm.doc.supplier_gross_weight,
        frm.doc.supplier_tare_weight
    );

    const company_net = calculate_net_weight(
        frm.doc.company_gross_weight,
        frm.doc.company_tare_weight
    );

    set_value_if_changed(
        frm,
        "supplier_net_weight",
        supplier_net
    );

    set_value_if_changed(
        frm,
        "company_net_weight",
        company_net
    );

    set_company_accepted_qty_from_weight(
        frm,
        company_net
    );
}

/**
 * Derive Company Accepted Qty where the physical measurement UOM
 * and finished-item Stock UOM are identical.
 *
 * Where the UOMs differ, retain the user's accepted quantity because
 * physical weight cannot be treated directly as finished-item count.
 */
function set_company_accepted_qty_from_weight(
    frm,
    company_net
) {
    if (frm.doc.measurement_method !== "Weight") {
        return;
    }

    if (!can_derive_company_accepted_qty(frm)) {
        /*
         * Do not clear a manually entered accepted quantity.
         *
         * Example:
         * Physical receipt measured in Kg, finished item received in Units.
         */
        return;
    }

    set_value_if_changed(
        frm,
        "company_accepted_qty",
        company_net
    );
}


/**
 * Returns Gross Weight minus Tare Weight.
 */
function calculate_net_weight(gross_weight, tare_weight) {
    const gross = flt(gross_weight);
    const tare = flt(tare_weight);

    if (!gross && !tare) {
        return 0;
    }

    return flt(gross - tare, 3);
}


/**
 * Compares supplier invoice quantity with company accepted quantity.
 */
function calculate_commercial_reconciliation(frm) {
    const invoice_qty = flt(frm.doc.supplier_invoice_qty);
    const accepted_qty = flt(frm.doc.company_accepted_qty);

    if (!invoice_qty) {
        set_value_if_changed(
            frm,
            "supplier_invoice_vs_company_qty",
            0
        );

        set_value_if_changed(
            frm,
            "supplier_invoice_vs_company_percent",
            0
        );

        return;
    }

    const difference = flt(
        invoice_qty - accepted_qty,
        3
    );

    const difference_percent = accepted_qty
        ? flt(difference / accepted_qty * 100, 3)
        : 0;

    set_value_if_changed(
        frm,
        "supplier_invoice_vs_company_qty",
        difference
    );

    set_value_if_changed(
        frm,
        "supplier_invoice_vs_company_percent",
        difference_percent
    );
}


/**
 * Avoids repeatedly dirtying the form when the calculated value is unchanged.
 */
function set_value_if_changed(frm, fieldname, value) {
    if (flt(frm.doc[fieldname], 3) === flt(value, 3)) {
        return;
    }

    frm.set_value(fieldname, value);
}

/**
 * Show a prominent notice when an SCR has already been created.
 *
 * The banner explains why the physical receipt fields are locked and
 * provides a direct link to the linked Subcontracting Receipt.
 */
function show_physical_receipt_lock_banner(frm) {
    if (!frm.doc.subcontracting_receipt) {
        frm.dashboard.clear_headline();
        return;
    }

    const scr_link =
        frappe.utils.get_form_link(
            "Subcontracting Receipt",
            frm.doc.subcontracting_receipt,
            true
        );

    frm.dashboard.set_headline_alert(
        __(
            "Physical receipt facts are locked because Subcontracting " +
            "Receipt {0} exists. Delete the Draft SCR, or cancel the " +
            "submitted SCR, before correcting these facts.",
            [scr_link]
        ),
        "orange"
    );
}

/**
 * Return true only when Company Accepted Qty can be derived from an
 * authoritative physical measurement without UOM conversion.
 */
function can_derive_company_accepted_qty(frm) {
    return (
        frm.doc.measurement_method === "Weight"
        && Boolean(frm.doc.processor_lot)
        && Boolean(frm.doc.processed_item)
        && Boolean(frm.doc.company_weighment_uom)
        && Boolean(frm.doc.stock_uom)
        && frm.doc.company_weighment_uom
            === frm.doc.stock_uom
    );
}

/**
 * Clear Frappe's generic Stock UOM default on an unlinked new PLR.
 *
 * Stock UOM becomes authoritative only after a Processor Lot identifies
 * the processed item through its Subcontracting Order.
 */
function clear_unlinked_stock_uom_default(frm) {
    const is_unlinked_new_plr =
        frm.is_new()
        && !frm.doc.processor_lot
        && !frm.doc.processed_item;

    if (
        is_unlinked_new_plr
        && frm.doc.stock_uom
    ) {
        frm.doc.stock_uom = null;
        frm.refresh_field("stock_uom");
    }
}

/**
 * Fetch controlled Processor Lot / SCO facts before the first PLR Save.
 *
 * Server-side validation remains authoritative; this gives the client
 * the same facts early enough for UOM-aware receipt behaviour.
 */
function fetch_processor_lot_receipt_context(frm) {
    if (!frm.doc.processor_lot) {
        return;
    }

    frappe.call({
        method: [
            "subcontracting_extensions",
            "subcontracting_extensions",
            "doctype",
            "processor_lot_receipt",
            "processor_lot_receipt",
            "get_processor_lot_receipt_context"
        ].join("."),
        args: {
            processor_lot:
                frm.doc.processor_lot
        },
        callback(r) {
            const context = r.message || {};

            if (!context.processed_item) {
                return;
            }

            frappe.run_serially([
                () => frm.set_value(
                    "subcontracting_order",
                    context.subcontracting_order
                ),

                () => frm.set_value(
                    "supplier",
                    context.supplier
                ),

                () => frm.set_value(
                    "company",
                    context.company
                ),

                () => frm.set_value(
                    "supplier_warehouse",
                    context.supplier_warehouse
                ),

                () => frm.set_value(
                    "processed_item",
                    context.processed_item
                ),

                () => frm.set_value(
                    "stock_uom",
                    context.stock_uom
                ),

                () => frm.set_value(
                    "company_accepted_uom",
                    context.stock_uom
                ),

                () => frm.set_value(
                    "supplier_invoice_uom",
                    context.stock_uom
                ),

                () => {
                    calculate_commercial_reconciliation(frm);
                    toggle_measurement_fields(frm);
                }
            ]);
        }
    });
}
