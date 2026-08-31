// Opt-in workspace usability trial. Documents created here remain browser-only.
frappe.provide("frappe.subcontracting_entry_preview");

(() => {
    const api = frappe.subcontracting_entry_preview;
    const method = "subcontracting_extensions.receipt_entry_preview.get_candidates";
    const escape = value => frappe.utils.escape_html(String(value == null ? "" : value));
    const quantity = value => Number(value || 0).toFixed(3);

    api.open = function () {
        let snapshot = null;
        let request_id = 0;
        const context = () => ({
            company: dialog.get_value("company"),
            supplier: dialog.get_value("supplier"),
            supplier_warehouse: dialog.get_value("supplier_warehouse") || null,
        });
        const invalidate = () => {
            request_id++;
            snapshot = null;
            dialog.fields_dict.candidates.$wrapper.empty();
        };
        const dialog = new frappe.ui.Dialog({
            title: __("Receive from Processor — Preview"),
            size: "extra-large",
            fields: [
                {fieldtype: "HTML", options: `<p>${escape(__("Select the processor and warehouse, then find compatible lots. This trial cannot save a receipt or reserve capacity."))}</p>`},
                {fieldname: "company", fieldtype: "Link", options: "Company", label: __("Company"), reqd: 1,
                    default: frappe.defaults.get_user_default("Company"), onchange: invalidate},
                {fieldname: "supplier", fieldtype: "Link", options: "Supplier", label: __("Processor"), reqd: 1,
                    onchange: () => { invalidate(); dialog.set_value("supplier_warehouse", ""); }},
                {fieldname: "supplier_warehouse", fieldtype: "Link", options: "Warehouse", label: __("Supplier Warehouse"),
                    onchange: invalidate,
                    get_query: () => ({filters: {company: dialog.get_value("company"), is_group: 0, disabled: 0}})},
                {fieldname: "find_lots", fieldtype: "Button", label: __("Find Compatible Lots"), click: load},
                {fieldname: "candidates", fieldtype: "HTML"},
            ],
            primary_action_label: __("Open Unsaved Receipt Preview"),
            primary_action: open_receipt,
        });

        async function load() {
            const args = context();
            if (!args.company || !args.supplier) {
                frappe.msgprint(__("Select Company and Processor first."));
                return;
            }
            invalidate();
            const current_request = request_id;
            dialog.fields_dict.candidates.$wrapper.text(__("Loading…"));
            let response;
            try {
                response = await frappe.call({method, args});
            } catch (error) {
                if (current_request === request_id) {
                    dialog.fields_dict.candidates.$wrapper.text(__("Lookup failed. Correct the reported issue and retry."));
                }
                return;
            }
            if (current_request !== request_id || JSON.stringify(args) !== JSON.stringify(context())) return;
            const data = response.message;
            if (!args.supplier_warehouse && data.warehouses.length === 1) {
                await dialog.set_value("supplier_warehouse", data.warehouses[0]);
                return load();
            }
            if (!args.supplier_warehouse) {
                dialog.fields_dict.candidates.$wrapper.html(data.warehouses.length
                    ? `<p>${escape(__("Choose a supplier warehouse and click Find Compatible Lots again:"))}</p><ul>${data.warehouses.map(w => `<li>${escape(w)}</li>`).join("")}</ul>`
                    : `<p>${escape(__("No accessible open lots were found for this processor."))}</p>`);
                return;
            }
            snapshot = {args, data};
            dialog.fields_dict.candidates.$wrapper.html(
                data.warnings.map(w => `<p class="text-warning">${escape(w)}</p>`).join("") +
                (data.items.length ? `<p>${escape(__("Tick the items on the invoice. Balances are suggestions, not reservations. No quantities will be allocated in this trial."))}</p>` :
                    `<p>${escape(__("No receivable item capacity was found. Check open SCO balances, lot settlement status and access permissions."))}</p>`) +
                data.items.map((item, index) => `
                    <div class="border rounded p-3 mb-3">
                        <label><input type="checkbox" data-preview-item="${index}">
                            ${escape(item.processed_item)} (${escape(item.stock_uom)})</label>
                        <p>${escape(__("Available"))}: ${quantity(item.available_qty)} ${escape(item.stock_uom)}</p>
                        <table class="table table-bordered table-sm">
                            <thead><tr><th>${escape(__("Processor Lot"))}</th><th>SCO / PO</th><th>${escape(__("Lot Date"))}</th><th>${escape(__("Available Qty"))}</th></tr></thead>
                            <tbody>${item.lots.map(lot => `<tr><td>${escape(lot.processor_lot)}</td><td>${escape(lot.subcontracting_order)}<br>${escape(lot.purchase_order)}</td><td>${escape(lot.lot_date)}</td><td>${quantity(lot.available_qty)}</td></tr>`).join("")}</tbody>
                        </table>
                    </div>`).join("")
            );
        }

        async function open_receipt() {
            if (!snapshot || JSON.stringify(snapshot.args) !== JSON.stringify(context())) {
                frappe.msgprint(__("Find compatible lots for the current processor and warehouse first."));
                return;
            }
            const selected = Array.from(dialog.fields_dict.candidates.$wrapper[0].querySelectorAll("input[data-preview-item]:checked"))
                .map(input => snapshot.data.items[Number(input.dataset.previewItem)]);
            if (!selected.length) {
                frappe.msgprint(__("Select at least one invoiced item."));
                return;
            }
            const selected_context = {...snapshot.args};
            await frappe.model.with_doctype("Processor Lot Receipt");
            const doc = frappe.model.get_new_doc("Processor Lot Receipt");
            Object.assign(doc, selected_context, {
                __v2_entry_preview: 1, receipt_structure_version: "V2 Itemized",
                processor_lot: null, subcontracting_order: null,
                processed_item: null, stock_uom: null,
                physical_receipt_date: frappe.datetime.get_today(),
                company_accepted_qty: 0, supplier_invoice_qty: 0,
                receipt_items: [], lot_allocations: [], item_weighments: [],
            });
            selected.forEach((item, index) => {
                const row = frappe.model.add_child(doc, "Processor Lot Receipt Item", "receipt_items");
                Object.assign(row, {
                    item_key: `ITEM-${String(index + 1).padStart(3, "0")}`,
                    processed_item: item.processed_item, stock_uom: item.stock_uom,
                    company_accepted_uom: item.stock_uom, supplier_invoice_uom: item.stock_uom,
                    measurement_method: "", measurement_basis: "",
                    company_accepted_qty: 0, supplier_invoice_qty: 0,
                });
            });
            dialog.hide();
            frappe.set_route("Form", "Processor Lot Receipt", doc.name);
        }
        dialog.show();
    };

    api.render = function (frm) {
        if (!frm.__v2_preview_original_fields) {
            frm.__v2_preview_original_fields = frm.meta.fields.map(field => ({
                fieldname: field.fieldname, hidden: field.hidden || 0,
                read_only: field.read_only || 0, label: field.label,
            }));
        }
        frm.disable_save();
        frm.clear_custom_buttons();
        const visible = new Set([
            "lot_information", "company", "supplier", "supplier_warehouse",
            "receipt_structure_version", "v2_receipt_items_section", "receipt_items",
        ]);
        frm.meta.fields.forEach(field => {
            frm.toggle_display(field.fieldname, visible.has(field.fieldname));
            if (visible.has(field.fieldname)) frm.set_df_property(field.fieldname, "read_only", 1);
        });
        frm.set_df_property("lot_information", "label", __("Processor Context — Preview Only"));
        frm.set_intro(__("UNSAVED WORKSPACE PREVIEW: items and processor context only. No lot has been chosen, no stock has been allocated, and saving is disabled. Measurement and invoice entry belong to the next checkpoint."), "orange");
        frm.add_custom_button(__("Back to Subcontracting"), () => frappe.set_route("subcontracting"));
    };

    api.restore = function (frm) {
        if (!frm.__v2_preview_original_fields) return;
        frm.__v2_preview_original_fields.forEach(field => {
            ["hidden", "read_only", "label"].forEach(property =>
                frm.set_df_property(field.fieldname, property, field[property])
            );
        });
        delete frm.__v2_preview_original_fields;
        frm.enable_save();
        frm.set_intro("");
    };

    document.addEventListener("click", event => {
        const shortcut = event.target.closest && event.target.closest(
            '.shortcut-widget-box[aria-label="Receive from Processor"]'
        );
        if (!shortcut) return;
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation();
        api.open();
    }, true);
})();
