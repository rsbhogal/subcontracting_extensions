// Opt-in workspace entry. Preview mode stays browser-only; J2 permits draft saving.
frappe.provide("frappe.subcontracting_entry_preview");

(() => {
    const api = frappe.subcontracting_entry_preview;
    const method = "subcontracting_extensions.receipt_entry_preview.get_candidates";
    const escape = value => frappe.utils.escape_html(String(value == null ? "" : value));
    const quantity = value => Number(value || 0).toFixed(3);

    api.open = async function () {
        const mode = await frappe.call({method: "subcontracting_extensions.receipt_entry_preview.get_entry_mode"});
        const draft_mode = Boolean(mode.message.draft_entry_enabled);
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
            title: draft_mode ? __("Receive from Processor — Draft Entry") : __("Receive from Processor — Preview"),
            size: "extra-large",
            fields: [
                {fieldtype: "HTML", options: `<p>${escape(__(draft_mode
                    ? "Select the processor and warehouse, then the invoiced items. Draft saving affects lot balances; downstream documents remain disabled."
                    : "Select the processor and warehouse, then find compatible lots. This trial cannot save a receipt or reserve capacity."))}</p>`},
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
            primary_action_label: draft_mode ? __("Open Receipt Draft") : __("Open Unsaved Receipt Preview"),
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
                __v2_entry_preview: draft_mode ? 0 : 1, receipt_structure_version: "V2 Itemized",
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

    // Dedicated form panel: native six-decimal child fields remain intact and
    // hidden. Three-decimal strings are presentation only, never a round-trip
    // replacement for an unchanged six-decimal quantity.
    const header_inputs = ["company", "supplier", "supplier_warehouse", "physical_receipt_date",
        "vehicle_no", "supplier_challan_number", "supplier_challan_date", "remarks"];
    const item_inputs = ["name", "item_key", "processed_item", "measurement_method", "measurement_basis",
        "company_accepted_qty", "supplier_invoice_qty", "remarks"];
    const weighment_inputs = ["name", "weighment_stage", "weighment_date", "weighment_time", "weighbridge",
        "slip_number", "scale_weight", "measurement_uom", "receipt_item_key", "adjustment_qty", "adjustment_reason", "remarks"];
    const project = (object, fields) => Object.fromEntries(fields.map(field => [field, object[field] ?? null]));
    const numeric = (value, original, places = 6, signed = false) => {
        const text = String(value ?? "").trim();
        if (original !== undefined && text === quantity(original)) return Number(original || 0);
        const expression = new RegExp(`^${signed ? "-?" : ""}\\d+(?:\\.\\d{1,${places}})?$`);
        if (!expression.test(text) || !Number.isFinite(Number(text))) {
            frappe.throw(__(`Enter a valid ${signed ? "" : "non-negative "}number with at most ${places} decimal places.`));
        }
        return Number(text);
    };
    api.draft_numeric = numeric;
    api.draft_payload = frm => {
        const child_rows = (rows, fields) => (rows || []).map(row => {
            const result = project(row, fields);
            if (frm.is_new() || row.__islocal) delete result.name;
            return result;
        });
        return {...project(frm.doc, header_inputs),
            name: frm.is_new() ? null : frm.doc.name, modified: frm.doc.modified || null,
            receipt_items: child_rows(frm.doc.receipt_items, item_inputs),
            item_weighments: child_rows(frm.doc.item_weighments, weighment_inputs),
        };
    };
    const signature = frm => JSON.stringify({inputs: api.draft_payload(frm),
        allocations: (frm.doc.lot_allocations || []).map(row => project(row, ["receipt_item_key", "processor_lot",
            "subcontracting_order_item", "purchase_order_item", "allocated_accepted_qty", "allocated_invoice_qty"]))});
    const is_linked = frm => Boolean(frm.doc.docstatus || frm.doc.subcontracting_receipt || frm.doc.purchase_receipt || frm.doc.purchase_invoice);
    const changed = frm => {
        frm.__j2_state.reviewed = null;
        frm.dirty();
        paint_draft(frm);
    };

    api.render_draft = async function (frm) {
        if (!frm.__j2_original_fields) {
            frm.__j2_original_fields = frm.meta.fields.map(field => project(field, ["fieldname", "hidden", "read_only", "label"]));
        }
        if (!frm.__j2_state || frm.__j2_state.name !== frm.doc.name) {
            frm.__j2_state = {name: frm.doc.name, enabled: false, reviewed: null, request: 0};
        }
        const state = frm.__j2_state;
        const current_request = ++state.request;
        state.enabled = false;
        frm.meta.fields.forEach(field => frm.toggle_display(field.fieldname, field.fieldname === "processor_first_draft_html"));
        frm.clear_custom_buttons();
        frm.disable_save();
        paint_draft(frm);
        let response;
        try {
            response = await frappe.call({method: "subcontracting_extensions.receipt_entry_preview.get_entry_mode"});
        } catch (error) {
            if (frm.__j2_state === state && state.request === current_request) {
                state.enabled = false;
                frm.set_intro(__("Could not verify the draft-entry setting. Reload before editing."), "red");
            }
            return;
        }
        if (frm.__j2_state !== state || state.request !== current_request || frm.doc.name !== state.name) return;
        state.enabled = Boolean(response.message.draft_entry_enabled) && !is_linked(frm);
        if (state.enabled) frm.enable_save();
        if (!frm.is_new() && !frm.is_dirty()) state.reviewed = signature(frm);
        frm.set_intro(__(state.enabled
            ? "J4 DRAFT ONLY: enter measurements and quantities, then Review FIFO Allocations before Save. Saving changes lot balances but creates no stock or accounting documents."
            : "Draft entry is disabled on this site, or this receipt already has downstream documents. This view is read-only."), "orange");
        paint_draft(frm);
    };

    api.restore_draft = function (frm) {
        if (!frm.__j2_original_fields) return;
        frm.__j2_original_fields.forEach(field => ["hidden", "read_only", "label"].forEach(property =>
            frm.set_df_property(field.fieldname, property, field[property] ?? (property === "label" ? "" : 0))));
        delete frm.__j2_original_fields;
        delete frm.__j2_state;
        frm.enable_save();
        frm.set_intro("");
    };

    api.validate_draft = function (frm) {
        if (!frm.__j2_state?.enabled || is_linked(frm)) frappe.throw(__("Draft entry is not available."));
        if (frm.__j2_state.busy || frm.__j2_state.reviewed !== signature(frm)) {
            frappe.throw(__("Review FIFO Allocations after your latest changes before saving."));
        }
    };

    function paint_draft(frm) {
        const state = frm.__j2_state;
        const wrapper = frm.fields_dict.processor_first_draft_html.$wrapper;
        const disabled = !state.enabled || state.busy;
        const button = (action, label, index = "") => `<button type="button" class="btn btn-default btn-sm" data-j2-action="${action}" data-index="${index}" ${disabled ? "disabled" : ""}>${escape(__(label))}</button>`;
        const doc = frm.doc;
        const items = doc.receipt_items || [];
        wrapper.html(`
            <div class="p-3">
                <h4>${escape(__("Processor Context"))}</h4>
                <p><strong>${escape(doc.supplier)}</strong> · ${escape(doc.company)} · ${escape(doc.supplier_warehouse)}</p>
                <p>${escape(__("Receipt date"))}: ${escape(doc.physical_receipt_date)} · ${escape(__("Vehicle"))}: ${escape(doc.vehicle_no || "—")}
                · ${escape(__("Challan"))}: ${escape(doc.supplier_challan_number || "—")} ${escape(doc.supplier_challan_date || "")}</p>
                ${button("facts", "Edit Receipt Facts")}
                <p class="text-muted mt-2">${escape(__("Processor context and selected items come from the workspace. Start again there to change them. Invoice number/date remain populated from the later Purchase Invoice; use Remarks for a reference during this checkpoint."))}</p>
                ${doc.remarks ? `<p>${escape(doc.remarks)}</p>` : ""}
                <h4 class="mt-4">${escape(__("Receipt Items"))}</h4>
                <div class="table-responsive"><table class="table table-bordered table-sm">
                    <thead><tr><th>${escape(__("Item / UOM"))}</th><th>${escape(__("Measurement"))}</th><th>${escape(__("Invoice Qty"))}</th><th>${escape(__("Accepted Qty"))}</th><th>${escape(__("Invoice − Accepted"))}</th><th></th></tr></thead>
                    <tbody>${items.map((item, index) => `<tr><td>${escape(item.item_key)} · ${escape(item.processed_item)}<br>${escape(item.stock_uom)}</td>
                        <td>${escape(item.measurement_method || "—")}<br>${escape(item.measurement_basis || "—")}</td>
                        <td>${quantity(item.supplier_invoice_qty)}</td><td>${quantity(item.company_accepted_qty)}</td>
                        <td>${quantity(Number(item.supplier_invoice_qty || 0) - Number(item.company_accepted_qty || 0))}</td><td>${button("item", "Edit Values", index)}</td></tr>`).join("")}</tbody>
                </table></div>
                ${items.some(item => item.measurement_basis === "Truck Differential Weight") || doc.item_weighments?.length ? `
                    <h4 class="mt-4">${escape(__("Truck Weighments"))}</h4>
                    <p>${escape(__("Record Arrival Loaded once, followed by After Unloading for each item. The last unloading reading may be the truck tare. Count items do not use these readings. Repeated readings for one weight item are allowed."))}</p>
                    ${button("weighments", "Record / Edit Truck Weighments")}
                    <div class="table-responsive mt-2"><table class="table table-bordered table-sm">
                        <thead><tr><th>${escape(__("Stage / Time"))}</th><th>${escape(__("Item Key"))}</th><th>${escape(__("Scale Weight"))}</th><th>${escape(__("Adjustment"))}</th><th>${escape(__("Derived Accepted"))}</th></tr></thead>
                        <tbody>${(doc.item_weighments || []).map((row, index) => `<tr><td>${escape(row.weighment_stage)}<br>${escape(row.weighment_date)} ${escape(String(row.weighment_time || "").split(".")[0])}</td><td>${escape(row.receipt_item_key || "—")}</td>
                            <td>${quantity(row.scale_weight)} ${escape(row.measurement_uom)}</td><td>${quantity(row.adjustment_qty)}</td><td>${quantity(row.accepted_qty)}</td></tr>`).join("")}</tbody>
                    </table></div>` : ""}
                <h4 class="mt-4">${escape(__("Lot Allocations"))}</h4>
                ${button("review", "Review FIFO Allocations")}
                <p class="mt-2 ${state.reviewed === signature(frm) ? "text-success" : "text-warning"}">${escape(__(state.reviewed === signature(frm)
                    ? "Allocations match the current inputs. Save will revalidate available capacity."
                    : "Allocations need review. Previously shown allocations may be stale after edits."))}</p>
                <div class="table-responsive"><table class="table table-bordered table-sm">
                    <thead><tr><th>${escape(__("Item Key"))}</th><th>${escape(__("Processor Lot / SCO"))}</th><th>${escape(__("Available"))}</th><th>${escape(__("Accepted"))}</th><th>${escape(__("Invoice"))}</th></tr></thead>
                    <tbody>${(doc.lot_allocations || []).map(row => `<tr><td>${escape(row.receipt_item_key)}<br>${escape(row.stock_uom)}</td><td>${escape(row.processor_lot)}<br>${escape(row.subcontracting_order)}</td>
                        <td>${quantity(row.available_qty)}</td><td>${quantity(row.allocated_accepted_qty)}</td><td>${quantity(row.allocated_invoice_qty)}</td></tr>`).join("")}</tbody>
                </table></div>
                <p class="text-muted">${escape(__("J4 supports distinct finished items within a lot using item-specific FIFO. Duplicate item/UOM source rows, allocation overrides, material credit and downstream document actions remain disabled."))}</p>
            </div>`);
        wrapper.off("click.processorFirst").on("click.processorFirst", "[data-j2-action]", function () {
            if (!state.enabled || state.busy) return;
            const action = this.dataset.j2Action;
            const index = Number(this.dataset.index);
            if (action === "facts") edit_facts(frm);
            if (action === "item") edit_item(frm, index);
            if (action === "weighments") api.edit_weighments(frm);
            if (action === "review") review_draft(frm);
        });
    }

    function edit_facts(frm) {
        const fields = [
            {fieldname: "physical_receipt_date", fieldtype: "Date", label: __("Physical Receipt Date"), reqd: 1},
            {fieldname: "vehicle_no", fieldtype: "Data", label: __("Vehicle Number")},
            {fieldname: "supplier_challan_number", fieldtype: "Data", label: __("Supplier Challan Number")},
            {fieldname: "supplier_challan_date", fieldtype: "Date", label: __("Supplier Challan Date")},
            {fieldname: "remarks", fieldtype: "Small Text", label: __("Remarks")},
        ].map(field => ({...field, default: frm.doc[field.fieldname]}));
        const dialog = new frappe.ui.Dialog({title: __("Receipt Facts"), fields,
            primary_action_label: __("Apply to Draft"), primary_action(values) {
                Object.assign(frm.doc, values);
                dialog.hide(); changed(frm);
            }});
        dialog.show();
    }

    function edit_item(frm, index) {
        const item = frm.doc.receipt_items[index];
        const bases = {
            Weight: ["Truck Differential Weight", "Separate Item Weight", "Manual Verified Quantity"],
            Count: ["In-house Weigh Count", "Direct Count", "Manual Verified Quantity"],
        };
        let dialog;
        dialog = new frappe.ui.Dialog({title: `${item.item_key} · ${item.processed_item}`, fields: [
            {fieldname: "measurement_method", fieldtype: "Select", label: __("Measurement Method"), options: "\nWeight\nCount", reqd: 1,
                default: item.measurement_method, onchange: () => update_basis()},
            {fieldname: "measurement_basis", fieldtype: "Select", label: __("Measurement Basis"), reqd: 1,
                options: ["", ...(bases[item.measurement_method] || [])], default: item.measurement_basis, onchange: () => update_accepted()},
            {fieldname: "supplier_invoice_qty", fieldtype: "Data", label: `${__("Supplier Invoice Qty")} (${item.stock_uom})`, reqd: 1, default: quantity(item.supplier_invoice_qty)},
            {fieldname: "company_accepted_qty", fieldtype: "Data", label: `${__("Company Accepted Qty")} (${item.stock_uom})`, reqd: 1, default: quantity(item.company_accepted_qty)},
            {fieldtype: "HTML", options: `<p class="text-muted">${escape(__("Displays use three decimals; unchanged values retain full precision. Truck differential quantity is calculated on allocation review. For In-house Weigh Count, enter the verified converted quantity in Stock UOM; no weighbridge row is required."))}</p>`},
        ], primary_action_label: __("Apply to Draft"), primary_action(values) {
            const invoice = numeric(values.supplier_invoice_qty, item.supplier_invoice_qty);
            const accepted = values.measurement_basis === "Truck Differential Weight"
                ? item.company_accepted_qty : numeric(values.company_accepted_qty, item.company_accepted_qty);
            Object.assign(item, {measurement_method: values.measurement_method, measurement_basis: values.measurement_basis,
                supplier_invoice_qty: invoice, company_accepted_qty: accepted});
            dialog.hide(); changed(frm);
        }});
        function update_basis() {
            if (!dialog) return;
            const options = bases[dialog.get_value("measurement_method")] || [];
            dialog.set_df_property("measurement_basis", "options", ["", ...options]);
            if (!options.includes(dialog.get_value("measurement_basis"))) dialog.set_value("measurement_basis", "");
            update_accepted();
        }
        function update_accepted() {
            if (!dialog) return;
            dialog.set_df_property("company_accepted_qty", "read_only", dialog.get_value("measurement_basis") === "Truck Differential Weight");
        }
        dialog.show(); update_accepted();
    }

    // Pure sequence validation also powers local previews and no-record tests.
    // Work on copies so closing the dialog never changes the form.
    api.prepare_weighments = function (readings, items) {
        const rows = JSON.parse(JSON.stringify(readings));
        const eligible = new Map(items.filter(item => item.measurement_method === "Weight"
            && item.measurement_basis === "Truck Differential Weight").map(item => [item.item_key, item]));
        const number = (value, signed = false) => {
            const text = String(value ?? "").trim();
            if (!(signed ? /^-?\d+(?:\.\d{1,3})?$/ : /^\d+(?:\.\d{1,3})?$/).test(text)
                || !Number.isFinite(Number(text))) throw new Error(__("Enter scale weights and adjustments with at most three decimals."));
            return Number(text);
        };
        if (!rows.length) throw new Error(__("Enter an Arrival Loaded reading first."));
        if (rows.length > 200) throw new Error(__("At most 200 weighment readings are supported."));
        let previous_weight;
        let previous_moment;
        const uom = rows[0].measurement_uom;
        if (!uom) throw new Error(__("Scale UOM is required."));
        for (const [index, row] of rows.entries()) {
            const expected = index ? "After Unloading" : "Arrival Loaded";
            if (row.weighment_stage !== expected) throw new Error(__("Use one Arrival Loaded reading followed by After Unloading readings."));
            const day = String(row.weighment_date || "");
            const date = new Date(`${day}T00:00:00Z`);
            if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || !Number.isFinite(date.getTime())
                || date.toISOString().slice(0, 10) !== day) throw new Error(__("Every reading needs a valid date."));
            const clock = String(row.weighment_time || "00:00:00");
            if (!/^(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d{1,6})?)?$/.test(clock)) {
                throw new Error(__("Use a valid 24-hour time for each reading."));
            }
            const [hours, minutes, seconds = "0"] = clock.split(":");
            const moment = date.getTime() * 1000 + Math.round((Number(hours) * 3600 + Number(minutes) * 60 + Number(seconds)) * 1000000);
            if (previous_moment !== undefined && moment < previous_moment) throw new Error(__("Reading times must be chronological; a blank time means midnight."));
            if (row.measurement_uom !== uom) throw new Error(__("All readings must use the same Scale UOM."));
            const scale = number(row.scale_weight);
            const adjustment = number(row.adjustment_qty ?? 0, true);
            if (!index && adjustment) throw new Error(__("Arrival Loaded cannot have an unloading adjustment."));
            if (index && scale > previous_weight) throw new Error(__("A scale reading cannot exceed the preceding weight."));
            if (adjustment && !String(row.adjustment_reason || "").trim()) throw new Error(__("Explain every non-zero adjustment."));
            const item = index ? eligible.get(row.receipt_item_key) : null;
            if (index && !item) throw new Error(__("Select a truck-differential Weight item for each unloading."));
            if (item && item.stock_uom !== uom) throw new Error(__("The unloaded item's Stock UOM must match the Scale UOM."));
            const derived = index ? Number((previous_weight - scale).toFixed(3)) : 0;
            const accepted = Number((derived + adjustment).toFixed(3));
            if (accepted < 0) throw new Error(__("An adjustment cannot make Accepted Qty negative."));
            Object.assign(row, {scale_weight: scale, adjustment_qty: adjustment,
                receipt_item_key: item ? item.item_key : null, processed_item: item ? item.processed_item : null,
                derived_unloaded_qty: derived, accepted_qty: accepted});
            previous_weight = scale; previous_moment = moment;
        }
        return rows;
    };

    api.make_weighment_control = function (parent, row, field, index, changed) {
        const original = row[field] || "";
        const display = field === "weighment_time" ? String(original).split(".")[0] : original;
        let ready = false;
        let edited = false;
        const control = frappe.ui.form.make_control({parent, render_input: true, df: {
            fieldname: `${field}_${index}`, fieldtype: field === "weighment_time" ? "Time" : "Date",
            label: __(field === "weighment_time" ? "Time" : "Date"),
            onchange() {
                if (!ready) return;
                const value = control.get_value() || "";
                // Merely displaying a fractional legacy timestamp must not
                // silently truncate its stored value when Apply is clicked.
                if (!edited && value === display) return;
                edited = true;
                row[field] = value;
                changed();
            },
        }});
        control.set_input(display);
        ready = true;
        return control;
    };

    api.edit_weighments = function (frm) {
        if (!frm.__j2_state?.enabled || frm.__j2_state.busy || is_linked(frm)) return;
        const origin = signature(frm);
        const state = frm.__j2_state;
        const items = frm.doc.receipt_items.filter(item => item.measurement_method === "Weight"
            && item.measurement_basis === "Truck Differential Weight");
        const source = frm.doc.item_weighments || [];
        const rows = JSON.parse(JSON.stringify(source));
        const initial_uom = rows[0]?.measurement_uom || items[0]?.stock_uom || "";
        const new_row = (arrival, day, bridge, uom) => ({
            weighment_stage: arrival ? "Arrival Loaded" : "After Unloading",
            receipt_item_key: !arrival && items.length === 1 ? items[0].item_key : null,
            weighment_date: day, weighment_time: "", weighbridge: bridge || "", slip_number: "",
            measurement_uom: uom, scale_weight: "", adjustment_qty: 0, adjustment_reason: "",
        });
        if (!rows.length) {
            rows.push(new_row(true, frm.doc.physical_receipt_date, "", initial_uom));
            rows.push(new_row(false, frm.doc.physical_receipt_date, "", initial_uom));
        }
        const dialog = new frappe.ui.Dialog({title: __("Truck Weighments — Consecutive Pairs"), size: "extra-large", fields: [
            {fieldname: "new_date", fieldtype: "Date", label: __("Date for New Readings"), default: rows[rows.length - 1].weighment_date},
            {fieldname: "new_bridge", fieldtype: "Data", label: __("Weighbridge for New Readings"), default: rows[rows.length - 1].weighbridge},
            {fieldname: "new_uom", fieldtype: "Select", label: __("Scale UOM for New Readings"),
                options: Array.from(new Set([initial_uom, ...items.map(item => item.stock_uom)].filter(Boolean))), default: initial_uom},
            {fieldname: "readings", fieldtype: "HTML"},
        ], primary_action_label: __("Apply Weighments"), primary_action() {
            if (frm.__j2_state !== state || !state.enabled || state.busy || is_linked(frm) || signature(frm) !== origin) {
                frappe.throw(__("The receipt changed while this dialog was open. Close it and reopen Truck Weighments."));
            }
            let prepared;
            try { prepared = rows.length ? api.prepare_weighments(rows, items) : []; }
            catch (error) { frappe.throw(error.message); }
            // Validation completes before any form mutation. Keep existing row
            // objects/names, including audit details not edited in this dialog.
            const existing = new Map(source.map(row => [row.name, row]));
            frm.doc.item_weighments = [];
            prepared.forEach((values, index) => {
                let target = values.name && existing.get(values.name);
                if (target) frm.doc.item_weighments.push(target);
                else target = frm.add_child("item_weighments");
                Object.assign(target, values, {idx: index + 1});
            });
            dialog.hide(); changed(frm);
        }});
        const wrapper = dialog.fields_dict.readings.$wrapper;
        const input = (row, index, field, type = "text") => {
            const numeric_field = ["scale_weight", "adjustment_qty"].includes(field);
            const raw = row[field];
            const value = numeric_field && /^-?\d+(?:\.\d{1,3})?$/.test(String(raw ?? "")) && Number.isFinite(Number(raw)) ? quantity(raw) : (raw || "");
            return `<input class="form-control input-sm" type="${type}"
                data-reading="${index}" data-field="${field}" aria-label="${escape(field)}" value="${escape(value)}"
                ${numeric_field ? 'inputmode="decimal"' : ""}>`;
        };
        let date_controls = [];
        function destroy_date_controls() {
            date_controls.forEach(control => control.datepicker?.destroy?.());
            date_controls = [];
        }
        dialog.$wrapper?.on("hidden.bs.modal.j4", destroy_date_controls);
        function paint() {
            destroy_date_controls();
            wrapper.html(`<p>${escape(__("Arrival is entered once. Each unloading pairs its new weight with the previous reading automatically. Defaults above apply only to newly added readings. Closing without Apply leaves the draft unchanged."))}</p>
                <div class="table-responsive"><table class="table table-bordered table-sm">
                <thead><tr><th>${escape(__("Reading / Item Unloaded"))}</th><th>${escape(__("Previous Weight"))}</th><th>${escape(__("New Scale Weight"))}</th>
                <th>${escape(__("Adjustment"))}</th><th>${escape(__("Accepted Preview"))}</th><th></th></tr></thead>
                <tbody>${rows.map((row, index) => `<tr>
                    <td>${index ? `${index}. ${escape(__("After Unloading"))}<select class="form-control input-sm" data-reading="${index}" data-field="receipt_item_key" aria-label="${escape(__("Item unloaded"))}">
                        <option value=""></option>${items.map(item => `<option value="${escape(item.item_key)}" ${item.item_key === row.receipt_item_key ? "selected" : ""}>${escape(item.item_key)} · ${escape(item.processed_item)}</option>`).join("")}
                        ${row.receipt_item_key && !items.some(item => item.item_key === row.receipt_item_key) ? `<option value="${escape(row.receipt_item_key)}" selected>${escape(row.receipt_item_key)} (${escape(__("not eligible"))})</option>` : ""}</select>` : `<strong>${escape(__("Arrival Loaded"))}</strong>`}
                        <small>${escape(row.measurement_uom || "—")}</small></td>
                    <td data-previous="${index}">—</td><td>${input(row, index, "scale_weight")}</td>
                    <td>${index ? input(row, index, "adjustment_qty") : "0.000"}</td><td data-accepted="${index}">—</td>
                    <td>${index ? `<button type="button" class="btn btn-default btn-xs" data-remove-reading="${index}">${escape(__("Remove"))}</button>` : ""}</td></tr>
                    <tr><td colspan="6"><div class="row">
                        <div class="col-sm-3" data-date-control="${index}"></div>
                        <div class="col-sm-3" data-time-control="${index}"></div>
                        <div class="col-sm-3"><label>${escape(__("Weighbridge"))}</label>${input(row, index, "weighbridge")}</div>
                        <div class="col-sm-3"><label>${escape(__("Slip Reference"))}</label>${input(row, index, "slip_number")}</div>
                    </div>${index ? `<label>${escape(__("Adjustment Reason"))}</label>${input(row, index, "adjustment_reason")}` : ""}</td></tr>`).join("")}
                </tbody></table></div>
                <button type="button" class="btn btn-default btn-sm" data-add-reading>${escape(__("Add Reading"))}</button>
                <button type="button" class="btn btn-default btn-sm" data-clear-readings>${escape(__("Clear Readings"))}</button>
                <p class="mt-2" data-sequence-status role="status"></p>
                <p class="text-muted">${escape(__("These are previews only. After Apply, Review FIFO Allocations again before Save. You can apply partial readings to the open draft, but saving still requires complete valid measurements."))}</p>`);
            rows.forEach((row, index) => {
                date_controls.push(api.make_weighment_control(wrapper.find(`[data-date-control="${index}"]`), row, "weighment_date", index, preview));
                date_controls.push(api.make_weighment_control(wrapper.find(`[data-time-control="${index}"]`), row, "weighment_time", index, preview));
            });
            preview();
        }
        function preview() {
            if (!rows.length) {
                wrapper.find("[data-sequence-status]").text(__("No readings. Apply will clear the draft's weighments; FIFO review is still required before saving."));
                return;
            }
            rows.forEach((row, index) => {
                const previous = index && rows[index - 1].scale_weight !== "" ? Number(rows[index - 1].scale_weight) : NaN;
                const scale = row.scale_weight !== "" ? Number(row.scale_weight) : NaN;
                const adjustment = Number(row.adjustment_qty || 0);
                const accepted = previous - scale + adjustment;
                wrapper.find(`[data-previous="${index}"]`).text(Number.isFinite(previous) ? quantity(previous) : "—");
                wrapper.find(`[data-accepted="${index}"]`).text(!index ? "0.000" : Number.isFinite(accepted) ? quantity(accepted) : "—");
            });
            try {
                api.prepare_weighments(rows, items);
                wrapper.find("[data-sequence-status]").text(__("Reading sequence is valid. Apply, then review FIFO before saving."));
            } catch (error) {
                wrapper.find("[data-sequence-status]").text(error.message);
            }
        }
        wrapper.on("input.weighmentSequence change.weighmentSequence", "[data-reading][data-field]", function () {
            rows[Number(this.dataset.reading)][this.dataset.field] = this.value;
            preview();
        });
        wrapper.on("click.weighmentSequence", "[data-add-reading]", () => {
            if (rows.length >= 200) { frappe.msgprint(__("At most 200 readings are supported.")); return; }
            rows.push(new_row(!rows.length, dialog.get_value("new_date"), dialog.get_value("new_bridge"), dialog.get_value("new_uom")));
            paint();
        });
        wrapper.on("click.weighmentSequence", "[data-clear-readings]", () => {
            frappe.confirm(__("Clear all readings in this dialog? The draft changes only when you click Apply Weighments."), () => {
                rows.splice(0); paint();
            });
        });
        wrapper.on("click.weighmentSequence", "[data-remove-reading]", function () {
            const index = Number(this.dataset.removeReading);
            frappe.confirm(__("Remove this reading? The next pair will use the preceding remaining weight."), () => {
                rows.splice(index, 1); paint();
            });
        });
        dialog.show(); paint();
    };

    async function review_draft(frm) {
        const state = frm.__j2_state;
        const before = signature(frm);
        state.busy = true; state.reviewed = null; paint_draft(frm);
        try {
            const response = await frappe.call({method: "subcontracting_extensions.receipt_entry_preview.review_draft",
                args: {payload: JSON.stringify(api.draft_payload(frm))}});
            if (frm.__j2_state !== state || signature(frm) !== before) {
                frappe.msgprint(__("The draft changed during review. Review again."));
                return;
            }
            const data = response.message;
            // Keep client child names until Save assigns permanent ones.
            for (const field of ["receipt_items", "item_weighments"]) {
                data[field].forEach((result, index) => {
                    const target = frm.doc[field][index];
                    const {name, parent, parenttype, parentfield, doctype, idx, __islocal, ...values} = result;
                    Object.assign(target, values);
                });
            }
            frm.clear_table("lot_allocations");
            data.lot_allocations.forEach(result => {
                const {name, parent, parenttype, parentfield, doctype, idx, __islocal, ...values} = result;
                Object.assign(frm.add_child("lot_allocations"), values);
            });
            frm.dirty();
            state.reviewed = signature(frm);
        } finally {
            if (frm.__j2_state === state) {
                state.busy = false; paint_draft(frm);
            }
        }
    }

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
