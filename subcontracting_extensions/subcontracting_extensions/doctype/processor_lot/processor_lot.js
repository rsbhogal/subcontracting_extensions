// Copyright (c) 2026, Bhogals Private Limited
// For license information, please see license.txt

/**
 * Client-side assistance for Processor Lot.
 *
 * All authoritative quantity validation remains in the Python controller.
 * This script only improves data loading and user experience.
 */

frappe.ui.form.on("Processor Lot", {
    setup(frm) {
        frm.set_query("subcontracting_order", () => {
            return {
                query: [
                    "subcontracting_extensions",
                    "subcontracting_extensions",
                    "doctype",
                    "processor_lot",
                    "processor_lot",
                    "get_unsettled_subcontracting_orders"
                ].join("."),
                filters: {
                    company: frm.doc.company || null,
                    supplier: frm.doc.supplier || null
                }
            };
        });
    },

    async refresh(frm) {
        frm.__j16_completion_report = null;
        frm.__j16_material_report = null;
        void render_j14_material_panel(frm);
        if (await render_multi_item_receipt_checkpoint(frm)) return;
        set_field_properties(frm);
        apply_settlement_policy_override_properties(frm);
        highlight_settlement_policy_source(frm);
        refresh_settlement_action(frm);
        add_receive_truck_button(frm);
        add_generated_document_button(frm);
        add_reopen_settlement_preview_button(frm);
        add_reopened_settlement_buttons(frm);
        add_credit_application_completion_button(frm);
        add_view_lot_facts_button(frm);
        set_status_indicator(frm);
        render_receipt_journey(frm);
        render_physical_receipt_position(frm);
        render_health_panel(frm);
        render_settlement_documents(frm);

        setTimeout(() => {
            hide_items_grid_controls(frm);
            highlight_settlement_policy_source(frm);
        }, 100);
    },

    items_on_form_rendered(frm) {
        hide_items_grid_controls(frm);
    },

    subcontracting_order(frm) {
        void render_j14_material_panel(frm);
        if (!frm.doc.subcontracting_order) {
            clear_sco_details(frm);
            render_receipt_journey(frm);
            render_physical_receipt_position(frm);
            render_health_panel(frm);
            return;
        }

        load_sco_details(frm);
        render_receipt_journey(frm);
        render_physical_receipt_position(frm);
        render_health_panel(frm);
    },

    override_settlement_policy(frm) {
        apply_settlement_policy_override_properties(frm);
        refresh_settlement_action(frm);

        setTimeout(() => {
            highlight_settlement_policy_source(frm);
        }, 50);
    },

    recover_raw_material_shortage(frm) {
        refresh_settlement_action(frm);
    },

    recover_processing_charges_on_shortage(frm) {
        refresh_settlement_action(frm);
    }
});


// J4: keep the legacy renderer untouched for single-item lots. Multi-item
// receipt drafts use explicit item facts; aggregate settlement is not enabled.
async function render_multi_item_receipt_checkpoint(frm) {
    const hidden_fields = ["receipt_stock_uom", "total_company_accepted_qty", "total_supplier_invoice_qty",
        "total_company_net_weight", "total_supplier_net_weight"];
    if (frm.__j4_hidden_fields) {
        for (const [field, hidden] of Object.entries(frm.__j4_hidden_fields)) frm.set_df_property(field, "hidden", hidden);
        frm.__j4_hidden_fields = null;
    }
    if (frm.is_new() || !frm.doc.subcontracting_order) return false;
    const name = frm.doc.name;
    const ticket = frm.__j4_request = (frm.__j4_request || 0) + 1;
    let report;
    try {
        const response = await frappe.call({method: "subcontracting_extensions.receipt_item_position.get_item_position",
            args: {processor_lot: name}});
        if (frm.doc.name !== name || frm.__j4_request !== ticket) return true;
        report = response.message;
    } catch (error) {
        if (frm.doc.name === name && frm.__j4_request === ticket) {
            for (const field of ["physical_receipt_position_html", "receipt_journey_html", "health_panel_html"]) {
                frm.get_field(field)?.$wrapper.html(
                    `<p class="text-danger">${__("Item-wise receipt position could not be verified. Reload after correcting the reported error.")}</p>`);
            }
        }
        return true;
    }
    if (!report?.enabled || (!report.is_multi_item && !report.use_item_panels)) return false;
    let completion;
    try {
        const response = await frappe.call({method: "subcontracting_extensions.receipt_completion.get_completion_position",
            args: {processor_lot: name}});
        if (frm.doc.name !== name || frm.__j4_request !== ticket) return true;
        completion = response.message;
    } catch (error) {
        if (frm.doc.name === name && frm.__j4_request === ticket) {
            for (const field of ["physical_receipt_position_html", "receipt_journey_html", "health_panel_html"]) {
                frm.get_field(field)?.$wrapper.html(`<p class="text-danger">${__("Completion evidence could not be verified. Check document permissions and evidence before retrying.")}</p>`);
            }
        }
        return true;
    }
    frm.__j4_hidden_fields = {};
    for (const field of hidden_fields) {
        const df = frm.get_field(field)?.df;
        if (df) { frm.__j4_hidden_fields[field] = df.hidden || 0; frm.set_df_property(field, "hidden", 1); }
    }
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const qty = value => Number(value || 0).toFixed(3);
    const table = (headers, rows) => `<div class="table-responsive"><table class="table table-bordered table-sm">
        <thead><tr>${headers.map(h => `<th>${esc(__(h))}</th>`).join("")}</tr></thead>
        <tbody>${rows.length ? rows.map(row => `<tr>${row.map(cell => `<td>${esc(cell)}</td>`).join("")}</tr>`).join("")
            : `<tr><td colspan="${headers.length}">${esc(__("No recorded receipt items."))}</td></tr>`}</tbody></table></div>`;
    const set_panel = (field, html) => frm.get_field(field)?.$wrapper.html(html);
    if (completion?.enabled) {
        frm.__j16_completion_report = completion;
        render_j12_completion_panels(frm, completion, table, qty, esc);
        render_j16_operational_guidance(frm);
        hide_items_grid_controls(frm);
        return true;
    }
    const notice = `<p class="text-warning">${esc(__("Completion verification is not enabled. PLR allocations below include drafts and do not prove submitted receipt or invoicing. Multi-item settlement remains disabled."))}</p>`;
    set_panel("physical_receipt_position_html", notice + `<p>${esc(__("Distinct trucks for this lot"))}: <strong>${report.truck_count}</strong></p>`
        + table(["Item", "UOM", "Receipts for item", "Ordered", "PLR Accepted", "Invoice", "Invoice − Accepted", "Ordered − PLR Accepted"],
            report.items.map(item => [item.processed_item, item.stock_uom, item.receipt_count,
                qty(item.ordered_qty), qty(item.accepted_qty), qty(item.invoice_qty), qty(item.invoice_vs_accepted_qty), qty(item.plr_balance_qty)]))
        + `<p class="text-muted">${esc(__("Receipt counts are distinct trucks containing each item. A truck can count once for several items, but only once for the lot. PLR quantities include saved drafts."))}</p>`);
    set_panel("health_panel_html", table(["Item", "UOM", "Net SCR Received", "PLR Qty Not Yet in Submitted SCR", "Applied Credit", "Available for Receipt"],
        report.items.map(item => [item.processed_item, item.stock_uom, qty(item.native_received_qty),
            qty(item.unposted_accepted_qty), qty(item.credit_applied_qty), qty(item.available_qty)]))
        + `<p class="text-muted">${esc(__("Availability deducts net SCR receipts, saved PLR quantities not yet recognised by a submitted SCR, and applied credits. A linked submitted SCR is not counted twice."))}</p>`);
    set_panel("receipt_journey_html", table(["Receipt", "Date", "Item", "UOM", "Accepted", "Invoice", "SCR", "PR", "PI"],
        report.journeys.map(row => [row.processor_lot_receipt, row.physical_receipt_date || "", row.processed_item,
            row.stock_uom, qty(row.accepted_qty), qty(row.invoice_qty), row.subcontracting_receipt || "—",
            row.purchase_receipt || "—", row.purchase_invoice || "—"])));
    hide_items_grid_controls(frm);
    return true;
}


function render_j12_completion_panels(frm, report, table, qty, esc) {
    const set = (field, html) => frm.get_field(field)?.$wrapper.html(html);
    const messages = {
        UNPOSTED_RESERVATIONS: "Draft receipt reservations remain",
        EXCESS_CAPACITY_COMMITMENT: "Receipt commitments exceed remaining capacity",
        NATIVE_RECEIPTS_NOT_RECONCILED_TO_ALLOCATIONS: "Submitted receipts are not fully reconciled to allocation evidence",
        JOURNEY_NOT_FULLY_VERIFIED: "Receipt or invoicing evidence is incomplete",
        RETURNS_REQUIRE_REVIEW: "Returns require review",
        MATERIAL_CREDIT_REQUIRES_REVIEW: "Applied material credit requires review",
        ORDER_QUANTITY_NOT_FULLY_RECEIVED: "Net received quantity differs from the order",
        SCR_NOT_SUBMITTED_OR_HEADER_MISMATCH: "SCR missing, not submitted, or header mismatch",
        SCR_ROW_LINEAGE_MISMATCH: "SCR row links or quantity do not match",
        PO_ROW_LINEAGE_MISMATCH: "PO row links do not match",
        PR_NOT_SUBMITTED_OR_HEADER_MISMATCH: "PR missing, not submitted, or header mismatch",
        PR_ROW_LINEAGE_MISMATCH: "PR row links or quantity do not match",
        PI_NOT_SUBMITTED_OR_HEADER_MISMATCH: "PI missing, not submitted, or header mismatch",
        PI_ROW_LINEAGE_MISMATCH: "PI row links, quantity or stock setting do not match",
    };
    const text = value => esc(value);
    const issues = codes => (codes || []).map(code => __(messages[code] || code)).join("; ") || __("None");
    const issue_tone = codes => (codes || []).some(code => code.includes("MISMATCH")
        || code === "EXCESS_CAPACITY_COMMITMENT") ? "red" : "amber";
    const legacy_text = __("Legacy receipt evidence—retained for audit but not created with V2 row-level links");
    const warning = (codes, legacy = false) => j14_badge(
        legacy && codes?.length ? legacy_text : issues(codes),
        codes?.length ? (legacy ? "neutral" : issue_tone(codes)) : "green");
    const evidence = (doctype, name, verified, legacy = false) => j14_link(doctype, name)
        + " / " + j14_badge(verified === true ? __("Verified") : legacy ? __("Legacy evidence") : __("Not verified"),
            verified === true ? "green" : legacy ? "neutral" : "amber");
    const status = report.journey_complete === true
        ? __("Receipt and invoicing journey complete—not settlement approval.")
        : __("Receipt and invoicing evidence requires review—not settlement approval.");
    const notice = `<p>${j14_badge(status, report.journey_complete === true ? "green" : report.legacy_evidence_only === true
        ? "neutral" : issue_tone(report.items.flatMap(item => item.issues || [])))}</p>
        <p class="text-muted">${esc(__("Multi-item settlement remains disabled. Figures are separate for each item and UOM."))}</p>`;
    // Physical availability has its own status; invoicing cannot make it green.
    const physical_ok = report.items.length > 0 && report.items.every(item =>
        Number(item.native_received_qty) === Number(item.ordered_qty)
        && Number(item.reserved_accepted_qty || 0) === 0 && Number(item.credit_applied_qty || 0) === 0
        && Number(item.available_qty) === 0);
    set("physical_receipt_position_html", `<p>${j14_badge(physical_ok ? __("Ordered quantities received in ERP")
        : __("Receipt position requires attention"), physical_ok ? "green" : "amber")}</p>`
        + `<p>${esc(__("Distinct trucks for this lot"))}: <strong>${esc(report.truck_count)}</strong></p>`
        + j14_table(["Item", "UOM", "Ordered", "Net SCR Received", "Draft Reserved", "Applied Credit", "Available for Receipt"],
            report.items.map(item => [text(item.processed_item), text(item.stock_uom), text(j14_qty(item.ordered_qty)),
                text(j14_qty(item.native_received_qty)), text(j14_qty(item.reserved_accepted_qty)), text(j14_qty(item.credit_applied_qty)),
                j14_badge(j14_qty(item.available_qty), Number(item.available_qty) < 0 ? "red" : Number(item.available_qty) > 0 ? "amber" : "neutral")]))
        + `<p class="text-muted">${esc(__("Availability includes Draft reservations. A negative value remains visible for review; it is not extra submitted stock."))}</p>`);
    set("health_panel_html", notice + j14_table(["Item", "UOM", "Verified SCR", "Verified PR", "Verified PI", "PI − SCR", "Journey", "Evidence warnings"],
        report.items.map(item => [text(item.processed_item), text(item.stock_uom), text(j14_qty(item.submitted_scr_qty)),
            text(j14_qty(item.submitted_pr_qty)), text(j14_qty(item.submitted_pi_qty)), text(j14_qty(item.submitted_invoice_vs_accepted_qty)),
            j14_badge(item.journey_complete === true ? __("Receipt Journey Complete")
                : item.legacy_evidence_only === true ? __("Legacy evidence") : __("Review required"),
                item.journey_complete === true ? "green" : item.legacy_evidence_only === true ? "neutral" : issue_tone(item.issues)),
            warning(item.issues, item.legacy_evidence_only === true)]))
        + `<p class="text-muted">${esc(__("Verified quantities cover linked allocation evidence only. Any invoice-versus-accepted variance still requires separate settlement review."))}</p>`);
    set("receipt_journey_html", j14_table(["Receipt", "Item", "UOM", "Allocated Accepted", "Allocated Invoice", "SCR / Evidence", "PR / Evidence", "PI / Evidence", "Warnings"],
        report.journeys.map(row => [j14_link("Processor Lot Receipt", row.processor_lot_receipt), text(row.processed_item), text(row.stock_uom),
            text(j14_qty(row.allocated_accepted_qty)), text(j14_qty(row.allocated_invoice_qty)),
            evidence("Subcontracting Receipt", row.subcontracting_receipt, row.scr_verified, row.legacy_evidence),
            evidence("Purchase Receipt", row.purchase_receipt, row.pr_verified, row.legacy_evidence),
            evidence("Purchase Invoice", row.purchase_invoice, row.pi_verified, row.legacy_evidence),
            warning(row.issues, row.legacy_evidence)])));
}


function j14_escape(value) {
    return frappe.utils.escape_html(String(value ?? ""));
}

function j14_qty(value) {
    const number = Number(value ?? 0);
    if (!Number.isFinite(number)) return "—";
    // Preserve the J13 engine's six-decimal residuals instead of displaying a
    // nonzero balance as 0.000. Keep at least three decimals for regular values.
    return number.toFixed(6).replace(/(\.\d{3}.*?)0+$/, "$1");
}

function j14_badge(label, tone) {
    const palette = {green: ["#e8f5ec", "#205b35"], amber: ["#fff3d6", "#795000"],
        red: ["#fdeaea", "#9f2525"], neutral: ["#edf3f8", "#31556f"]};
    const [background, color] = palette[tone] || palette.neutral;
    return `<span data-status-tone="${palette[tone] ? tone : "neutral"}" style="display:inline-block;border-radius:5px;
        padding:3px 7px;background:${background};color:${color};font-weight:600;white-space:normal">${j14_escape(label)}</span>`;
}

function j14_link(doctype, name) {
    if (!name) return j14_escape(__("Not created"));
    const routes = {"Processor Lot Receipt": "processor-lot-receipt", "Subcontracting Receipt": "subcontracting-receipt",
        "Purchase Receipt": "purchase-receipt", "Purchase Invoice": "purchase-invoice", "Stock Entry": "stock-entry",
        "Subcontracting Order": "subcontracting-order", "Processor Lot": "processor-lot",
        "Processor Material Account Entry": "processor-material-account-entry"};
    if (!routes[doctype]) return j14_escape(name);
    return `<a href="/app/${routes[doctype]}/${encodeURIComponent(name)}" target="_blank" rel="noopener noreferrer"
        style="text-decoration:underline;overflow-wrap:anywhere">${j14_escape(name)}</a>`;
}

// Cells are already escaped text or markup from the safe helpers above.
function j14_table(headers, rows) {
    return `<div class="table-responsive"><table class="table table-bordered table-sm" style="margin:12px 0;line-height:1.5">
        <thead style="background:#f5f7fa"><tr>${headers.map(h => `<th style="padding:8px;white-space:normal">${j14_escape(__(h))}</th>`).join("")}</tr></thead>
        <tbody>${rows.length ? rows.map(row => `<tr>${row.map(cell => `<td style="padding:8px;vertical-align:top;white-space:normal;overflow-wrap:anywhere">${cell}</td>`).join("")}</tr>`).join("")
            : `<tr><td colspan="${headers.length}">${j14_escape(__("No evidence rows."))}</td></tr>`}</tbody></table></div>`;
}

function j18_component_return_html(row) {
    const draft_links = (row.draft_component_returns || [])
        .map(draft => draft.name
            ? `<a href="/app/stock-entry/${encodeURIComponent(draft.name)}" target="_blank" rel="noopener noreferrer"
                style="text-decoration:underline;overflow-wrap:anywhere;color:#795000;font-weight:600">${j14_escape(draft.name)}</a>`
            : "")
        .filter(Boolean)
        .join(", ");
    const action = row.component_return_action_available
        ? `<button type="button" class="btn btn-xs btn-primary" style="margin-top:7px"
            data-j18-component-return="${j14_escape(encodeURIComponent(row.sco_supplied_item || ""))}"
            data-j18-expected-qty="${j14_escape(String(row.component_return_expected_qty ?? ""))}">
            ${j14_escape(__("Prepare full draft return"))}</button>`
        : "";
    const submit_action = row.component_return_submit_action_available
        ? `<button type="button" class="btn btn-xs btn-primary" style="margin-top:7px;margin-left:5px"
            data-j18c-component-return="${j14_escape(encodeURIComponent(row.sco_supplied_item || ""))}"
            data-j18c-stock-entry="${j14_escape(encodeURIComponent(row.component_return_submit_stock_entry || ""))}"
            data-j18c-expected-qty="${j14_escape(String(row.component_return_submit_expected_qty ?? ""))}">
            ${j14_escape(__("Submit component return"))}</button>`
        : "";
    return `<div>${j14_badge(__(row.component_return_label || "Component return preview unavailable"),
            (row.component_return_blockers || []).length ? "red"
                : ["READY_TO_PREPARE_COMPONENT_RETURN", "OPEN_EXISTING_DRAFT_RETURN"].includes(row.component_return_code)
                    ? "amber" : "neutral")}</div>
        <div class="text-muted" style="margin-top:5px">${j14_escape(__(row.component_return_detail || "No return action is enabled."))}</div>
        <div class="text-muted" style="margin-top:5px">${j14_escape(row.component_return_source_warehouse || "—")} → ${j14_escape(row.component_return_target_warehouse || "—")}</div>
        <div class="text-muted">${j14_escape(__("Current source stock"))}: ${j14_qty(row.component_return_source_stock_qty)} ${j14_escape(row.stock_uom || "")}</div>
        <div class="text-muted">${j14_escape(__("Draft reserved"))}: ${j14_qty(row.draft_return_reserved_qty)} ${j14_escape(row.stock_uom || "")}; ${j14_escape(__("Available"))}: ${j14_qty(row.return_qty_available_to_prepare)} ${j14_escape(row.stock_uom || "")}</div>
        ${draft_links ? `<div style="margin-top:5px">${j14_badge(__("Draft Stock Entry"), "amber")} ${draft_links}</div>` : ""}
        ${action}${submit_action}`;
}

function j18_bind_component_return_actions(frm, wrapper) {
    const buttons = wrapper?.find?.("[data-j18-component-return]");
    buttons?.off?.("click.j18").on?.("click.j18", function () {
        const button = this;
        const sco_supplied_item = decodeURIComponent(button.dataset.j18ComponentReturn || "");
        const expected_qty = button.dataset.j18ExpectedQty;
        frappe.confirm(
            __("Prepare an unsubmitted Stock Entry for the full available component quantity?"),
            async () => {
                button.disabled = true;
                try {
                    const response = await frappe.call({
                        method: "subcontracting_extensions.material_reconciliation_ui.prepare_component_return",
                        args: {processor_lot: frm.doc.name, sco_supplied_item, expected_qty},
                        freeze: true,
                        freeze_message: __("Preparing draft component return"),
                    });
                    if (response.message?.name) {
                        frappe.set_route("Form", "Stock Entry", response.message.name);
                    }
                } finally {
                    button.disabled = false;
                }
            }
        );
    });
    const submit_buttons = wrapper?.find?.("[data-j18c-component-return]");
    submit_buttons?.off?.("click.j18c").on?.("click.j18c", function () {
        const button = this;
        const sco_supplied_item = decodeURIComponent(button.dataset.j18cComponentReturn || "");
        const stock_entry = decodeURIComponent(button.dataset.j18cStockEntry || "");
        const expected_qty = button.dataset.j18cExpectedQty;
        frappe.confirm(
            __("Submit the selected component-return Stock Entry? This will post the stock movement."),
            async () => {
                button.disabled = true;
                try {
                    const response = await frappe.call({
                        method: "subcontracting_extensions.material_reconciliation_ui.submit_component_return",
                        args: {processor_lot: frm.doc.name, sco_supplied_item, stock_entry, expected_qty},
                        freeze: true,
                        freeze_message: __("Submitting component return"),
                    });
                    if (response.message?.name) {
                        frappe.set_route("Form", "Stock Entry", response.message.name);
                    }
                } finally {
                    button.disabled = false;
                }
            }
        );
    });
}

async function render_j14_material_panel(frm) {
    const wrapper = frm.get_field("material_reconciliation_html")?.$wrapper;
    if (!wrapper) return;
    frm.get_field("operational_guidance_html")?.$wrapper.html("");
    frm.set_df_property("operational_guidance_section", "hidden", 1);
    const ticket = frm.__j14_material_request = (frm.__j14_material_request || 0) + 1;
    const name = frm.doc.name, sco = frm.doc.subcontracting_order;
    wrapper.html("");
    frm.set_df_property("material_reconciliation_section", "hidden", 1);
    if (frm.is_new() || !sco) return;
    const current = () => frm.doc.name === name && frm.doc.subcontracting_order === sco
        && frm.__j14_material_request === ticket;
    try {
        const response = await frappe.call({method: "subcontracting_extensions.material_reconciliation_ui.get_material_panel",
            args: {processor_lot: name}});
        if (!current() || !response.message?.enabled) return;
        const report = response.message;
        if (report.processor_lot !== name || report.subcontracting_order !== sco) throw Error("Evidence identity mismatch");
        frm.__j16_material_report = report;
        wrapper.html(build_j14_material_panel(report));
        j18_bind_component_return_actions(frm, wrapper);
        frm.set_df_property("material_reconciliation_section", "hidden", 0);
        render_j16_operational_guidance(frm);
    } catch (error) {
        if (!current()) return;
        wrapper.html(`<p>${j14_badge(__("Material evidence unavailable"), "red")}</p>
            <p>${j14_escape(__("Check read permissions and source evidence, then reload. No material status has been verified."))}</p>`);
        frm.set_df_property("material_reconciliation_section", "hidden", 0);
    }
}

const J16_DETAIL_SECTIONS = ["processor_settlement_policy_section", "settlement_policy_override_section", "settlement_details_section",
    "receipt_summary_section", "receipt_journey_section", "physical_receipt_position_section",
    "health_panel_section", "material_reconciliation_section", "items_section",
    "commercial_details_section", "generated_document_section"];

function j16_detailed_preference() {
    try {
        return globalThis.localStorage?.getItem(`processor-lot-detailed:${frappe.session?.user || "Guest"}`) === "1";
    } catch (error) {
        return false;
    }
}

function j16_set_detailed_preference(value) {
    try {
        globalThis.localStorage?.setItem(`processor-lot-detailed:${frappe.session?.user || "Guest"}`, value ? "1" : "0");
    } catch (error) {
        // Browser storage is optional; the current form still toggles safely.
    }
}

function j16_apply_view(frm, detailed) {
    for (const fieldname of J16_DETAIL_SECTIONS) {
        if (!frm.get_field(fieldname)) continue;
        let hidden = detailed ? 0 : 1;
        if (fieldname === "generated_document_section") {
            hidden = detailed && frm.doc.docstatus === 1 && frm.doc.settlement_status === "Completed" ? 0 : 1;
        }
        frm.set_df_property(fieldname, "hidden", hidden);
    }
    frm.set_df_property("operational_guidance_section", "hidden", 0);
}

function j16_guidance_state(frm, material, completion) {
    const completed_history = frm.doc.docstatus === 1 && frm.doc.settlement_status === "Completed";
    if (material.evidence_consistent !== true) return {tone: "red", title: __("Review material evidence"),
        detail: __("Correct the highlighted material evidence before any commercial treatment or lot closure is considered."), link: ""};
    if (material.material_settlement_eligible !== true) return {tone: "amber",
        title: __(material.material_next_action_label || "Account for remaining material"),
        detail: __(material.material_next_action_detail || "Complete the component actions shown below."), link: ""};
    if (completed_history) return {tone: "green", title: __("No current material action required"),
        detail: __("This completed historical lot has accounted material quantities. Legacy receipt evidence remains available in Detailed view."), link: ""};
    const completion_items = completion?.items || [];
    const excess = completion_items.find(item => (item.issues || []).includes("EXCESS_CAPACITY_COMMITMENT"));
    if (excess) {
        const journey = (completion.journeys || []).find(row => row.subcontracting_order_item === excess.subcontracting_order_item
            && row.scr_verified !== true);
        const link = journey ? j14_link("Processor Lot Receipt", journey.processor_lot_receipt) : "";
        return {tone: "amber", title: __("Review draft receipt reservation"),
            detail: __(`A draft receipt reserves ${j14_qty(excess.excess_reserved_qty)} ${excess.stock_uom} beyond remaining capacity. Correct or remove it before continuing.`), link};
    }
    if (completion?.enabled && completion.journey_complete !== true) return {tone: "amber",
        title: __("Complete receipt and invoicing evidence"),
        detail: __("Open Detailed view to see the first unverified SCR, Purchase Receipt, or Purchase Invoice."), link: ""};
    if (material.commercial_policy_status === "DEFERRED") return {tone: "amber",
        title: __("Material position reconciled"),
        detail: __("Material and receipt evidence are complete. Commercial treatment has not been determined. No commercial document or lot closure is authorised."), link: ""};
    return {tone: "green", title: __("Ready for closure review"),
        detail: __("Material and receipt evidence are complete. Review commercial settlement before closing the lot."), link: ""};
}

function render_j16_operational_guidance(frm) {
    const field = frm.get_field("operational_guidance_html");
    const material = frm.__j16_material_report;
    if (!field?.$wrapper || !material || frm.is_new()) return;
    const detailed = j16_detailed_preference();
    const state = j16_guidance_state(frm, material, frm.__j16_completion_report);
    const rows = (material.components || []).map(row => [j14_escape(row.component_item), j14_escape(row.stock_uom),
        j14_qty(row.physical_remaining_qty), j14_qty(row.applied_credit_qty), j14_qty(row.unaccounted_remaining_qty),
        j14_badge(__(row.material_next_action_label || "Review component evidence"),
            row.evidence_consistent !== true ? "red" : row.material_settlement_eligible === true ? "green" : "amber"),
        j18_component_return_html(row),
        `<div>${j14_badge(__(row.commercial_treatment_label || "Commercial treatment not determined"), "neutral")}</div>
            <div class="text-muted" style="margin-top:5px">${j14_escape(__(row.component_action_detail || "No commercial document is authorised."))}</div>`]);
    field.$wrapper.html(`<div style="border:1px solid #8baecb;border-radius:8px;padding:14px;background:#f7fbfe">
        <div style="font-size:15px;font-weight:600;margin-bottom:8px">${j14_escape(__("What to do next"))}</div>
        <div>${j14_badge(state.title, state.tone)} ${state.link}</div>
        <div style="margin-top:8px">${j14_escape(state.detail)}</div>
        ${j14_table(["Component", "UOM", "Physical Balance", "Applied Credit", "Unaccounted", "Material Action", "Component Return", "Commercial Treatment"], rows)}
        <label style="display:inline-flex;align-items:center;gap:7px;margin:4px 0 0;cursor:pointer;font-weight:500">
            <input type="checkbox" data-j16-detailed ${detailed ? "checked" : ""}>
            ${j14_escape(__("Show detailed audit evidence"))}
        </label>
    </div>`);
    j18_bind_component_return_actions(frm, field.$wrapper);
    j16_apply_view(frm, detailed);
    const toggle = field.$wrapper.find?.("[data-j16-detailed]");
    toggle?.off?.("change.j16").on?.("change.j16", function () {
        const value = Boolean(this.checked);
        j16_set_detailed_preference(value);
        j16_apply_view(frm, value);
    });
}

function build_j14_material_panel(report) {
    const text = j14_escape;
    const messages = {
        MATERIAL_BALANCE_REMAINS: "Material remains outstanding",
        SUPPLY_EVIDENCE_MISMATCH: "Sent quantity differs from submitted transfer evidence",
        RETURN_EVIDENCE_MISMATCH: "Returned quantity differs from submitted return evidence",
        CONSUMPTION_EVIDENCE_MISMATCH: "Consumed quantity differs from exact receipt-row evidence",
        NEGATIVE_MATERIAL_BALANCE: "Negative material balance",
        ADJUSTMENT_ATTRIBUTION_REQUIRES_REVIEW: "Credit or settlement evidence requires review",
        SCR_RETURN_REQUIRES_REVIEW: "SCR return requires review",
        NEGATIVE_CONSUMPTION_REQUIRES_REVIEW: "Negative consumption requires review",
        NATIVE_NET_SUPPLY_MISMATCH: "Native net supply does not match sent minus returned",
        MOVEMENT_RETURN_FLAG_MISMATCH: "Return flag and warehouse direction disagree",
        INVALID_EXPLICIT_COMPONENT_LINK: "Invalid component link",
        EXPLICIT_COMPONENT_ITEM_UOM_MISMATCH: "Component link has a different item or UOM",
        AMBIGUOUS_TRANSFER_ATTRIBUTION: "Transfer cannot be assigned to one component",
        MOVEMENT_HEADER_MISMATCH: "Movement header does not match this SCO",
        CONSUMPTION_LINEAGE_MISMATCH: "Consumption row links do not match this SCO",
        SCR_HEADER_MISMATCH: "Receipt header does not match this SCO",
        UNMATCHED_MOVEMENT: "Movement has no matching component",
        UNMATCHED_CONSUMPTION: "Consumption has no matching component",
        UNSUPPORTED_MOVEMENT_DIRECTION: "Movement warehouse direction requires review",
        NONPOSITIVE_MOVEMENT_QUANTITY: "Movement quantity must be positive",
        INVALID_ADJUSTMENT_COMPONENT_LINK: "Applied credit has an invalid component link",
        ADJUSTMENT_COMPONENT_ITEM_UOM_MISMATCH: "Applied credit component or UOM disagrees with its exact link",
        ADJUSTMENT_HEADER_MISMATCH: "Applied credit does not belong to this SCO",
        DRAFT_ADJUSTMENT_REQUIRES_REVIEW: "Draft material-account evidence requires review",
        AMBIGUOUS_ADJUSTMENT_ATTRIBUTION: "Applied credit cannot be assigned to one component",
        UNMATCHED_ADJUSTMENT: "Applied credit has no matching component",
        NONPOSITIVE_ADJUSTMENT_QUANTITY: "Applied credit quantity must be positive",
        APPLIED_CREDIT_EXCEEDS_PHYSICAL_REMAINING: "Applied credit exceeds physical remaining quantity",
        NO_COMPONENTS: "No component evidence",
    };
    const pending = new Set(["MATERIAL_BALANCE_REMAINS", "ADJUSTMENT_ATTRIBUTION_REQUIRES_REVIEW",
        "SCR_RETURN_REQUIRES_REVIEW", "NEGATIVE_CONSUMPTION_REQUIRES_REVIEW", "AMBIGUOUS_TRANSFER_ATTRIBUTION",
        "DRAFT_ADJUSTMENT_REQUIRES_REVIEW", "AMBIGUOUS_ADJUSTMENT_ATTRIBUTION"]);
    const tone = codes => codes.some(code => !pending.has(code)) ? "red" : "amber";
    const codes = report.issues || [];
    const summary = report.evidence_consistent !== true ? __("Material evidence requires review")
        : report.material_balanced === true ? __("Physically reconciled")
        : report.material_accounted === true ? __("Physical balance covered by submitted credit")
        : __("Material remains unaccounted");
    const summary_tone = report.evidence_consistent === true
        && (report.material_balanced === true || report.material_accounted === true) ? "green" : tone(codes);
    const status = row => j14_badge(row.evidence_consistent !== true ? __("Review required")
        : row.material_balanced === true ? __("Physically reconciled")
        : row.material_accounted === true ? __("Accounted by credit") : __("Unaccounted balance remains"),
        row.evidence_consistent === true && (row.material_balanced === true || row.material_accounted === true)
            ? "green" : tone(row.issues || []));
    const action_tone = row => row.evidence_consistent !== true ? "red"
        : row.material_settlement_eligible === true ? "green" : "amber";
    const overall_action_tone = report.evidence_consistent !== true ? "red"
        : report.material_settlement_eligible === true ? "green" : "amber";
    let html = `<p>${j14_badge(summary, summary_tone)}</p>
        <p>${j14_badge(__("Settlement not enabled in this panel"), "neutral")}</p>
        <div style="border:1px solid #8baecb;border-radius:7px;padding:10px 12px;margin:10px 0;background:#f5f9fc">
            <strong>${text(__("What to do next"))}:</strong>
            ${j14_badge(__(report.material_next_action_label || "Review material evidence"), overall_action_tone)}
            <div style="margin-top:6px">${text(__(report.material_next_action_detail || "Review the component evidence before continuing."))}</div>
        </div>
        <p class="text-muted">${text(__(report.settlement_eligibility_scope || "Material quantities only; no settlement write, receipt completion, commercial approval, or lot closure"))}</p>
        <p class="text-muted">${text(__("Evidence covers the entire SCO. Applied credit accounts for a physical balance; it does not mean the material was consumed or returned."))}</p>`;
    if (codes.length) html += `<p>${text(codes.map(code => __(messages[code] || code)).join("; "))}</p>`;
    html += j14_table(["Component", "UOM", "Sent", "Consumed", "Returned", "Physical Remaining", "Applied Credit", "Unaccounted Remaining", "Evidence Status", "Next Material Action", "Component Return", "Commercial Treatment"],
        (report.components || []).map(row => [text(row.component_item), text(row.stock_uom), text(j14_qty(row.supplied_qty)),
            text(j14_qty(row.consumed_qty)), text(j14_qty(row.returned_qty)), text(j14_qty(row.physical_remaining_qty)),
            text(j14_qty(row.applied_credit_qty)), j14_badge(j14_qty(row.unaccounted_remaining_qty),
                Number(row.unaccounted_remaining_qty) < 0 ? "red" : Number(row.unaccounted_remaining_qty) > 0 ? "amber" : "neutral"), status(row),
            `<div>${j14_badge(__(row.material_next_action_label || "Review component evidence"), action_tone(row))}</div>
                <div class="text-muted" style="margin-top:5px">${text(__(row.material_next_action_detail || ""))}</div>`,
            j18_component_return_html(row),
            `<div>${j14_badge(__(row.commercial_treatment_label || "Commercial treatment not determined"), "neutral")}</div>
                <div class="text-muted" style="margin-top:5px">${text(__(row.component_action_detail || "No commercial document is authorised."))}</div>`]));
    html += `<details style="margin-top:14px"><summary style="cursor:pointer;display:list-item;
        width:fit-content;border:1px solid #8baecb;border-radius:6px;padding:9px 14px;
        background:#eaf3fb;color:#174c75;font-weight:600;box-shadow:0 1px 2px #00000012">
        ${text(__("View source documents and row evidence"))}</summary>`;
    html += j14_table(["Document type", "Document", "Status"], (report.sources || []).map(row => [text(__(row.doctype)),
        j14_link(row.doctype, row.name), text(__(({0: "Draft", 1: "Submitted", 2: "Cancelled"})[row.docstatus] || "Unknown"))]));
    html += j14_table(["Stock Entry", "Evidence Role", "Component", "UOM", "Stock Quantity", "From", "To", "SCO Component Row", "Material Account Entry"],
        (report.movements || []).map(row => [j14_link("Stock Entry", row.parent),
            text(row.evidence_role || __("Physical transfer or return")), text(row.item_code), text(row.stock_uom),
            text(j14_qty(row.stock_qty)), text(row.s_warehouse), text(row.t_warehouse),
            text(row.sco_rm_detail || __("Legacy matching")),
            j14_link("Processor Material Account Entry", row.processor_material_account_entry)]));
    html += j14_table(["SCR", "Component", "UOM", "Consumed", "SCR Item Row"], (report.consumptions || []).map(row => [
        j14_link("Subcontracting Receipt", row.parent), text(row.rm_item_code), text(row.stock_uom),
        text(j14_qty(row.consumed_qty)), text(row.reference_name)]));
    html += j14_table(["Material Account Entry", "Status", "Entry Type", "Component", "UOM", "Account Quantity", "SCO Component Row"],
        (report.adjustments || []).map(row => [j14_link(row.doctype, row.name),
            text(__(({0: "Draft", 1: "Submitted", 2: "Cancelled"})[row.docstatus] || "Review")),
            text(row.entry_type || row.reason || __("Other evidence")), text(row.principal_component || "—"),
            text(row.account_uom || "—"), text(row.account_qty == null ? "—" : j14_qty(row.account_qty)),
            text(row.sco_supplied_item || __("Not attributed"))]));
    html += j14_table(["Settlement Document", "Evidence Role"], (report.settlement_evidence || []).map(row => [
        j14_link(row.doctype, row.name), text(row.reason)]));
    return html + "</details>";
}


function add_credit_application_completion_button(frm) {
    if (frm.is_new() || frm.doc.docstatus === 2) {
        return;
    }

    frappe.call({
        method: [
            "subcontracting_extensions",
            "subcontracting_extensions",
            "doctype",
            "processor_lot",
            "settlement_application_engine",
            "get_credit_application_completion_status"
        ].join("."),
        args: {
            processor_lot: frm.doc.name
        },
        callback(r) {
            const status = r.message || {};
            frm.__credit_application_completion_status = status;

            if (!status.has_applications || status.is_complete) {
                return;
            }

            const label = status.broken_count
                ? __("Repair Credit Applications")
                : __("Complete Credit Applications");
            const button = frm.add_custom_button(
                label,
                () => show_credit_application_completion_dialog(
                    frm,
                    status
                )
            );

            button
                .removeClass("btn-default btn-secondary")
                .addClass(
                    status.broken_count
                        ? "btn-danger"
                        : "btn-warning"
                );
        }
    });
}


function escape_credit_application_text(value) {
    return frappe.utils.escape_html(
        String(value ?? "")
    );
}


function format_credit_application_qty(value, uom) {
    return [
        format_number(flt(value), null, 3),
        uom || ""
    ]
        .filter(Boolean)
        .map(escape_credit_application_text)
        .join(" ");
}


function credit_application_document_html(
    doctype,
    name,
    docstatus,
    is_next
) {
    const labels = {
        0: __("Draft"),
        1: __("Submitted"),
        2: __("Cancelled")
    };
    const colors = {
        0: "var(--orange-500)",
        1: "var(--green-500)",
        2: "var(--red-500)"
    };
    const status_label = name
        ? (labels[docstatus] || __("Missing"))
        : __("Missing");
    const status_color = name
        ? (colors[docstatus] || "var(--red-500)")
        : "var(--red-500)";
    const link = name
        ? frappe.utils.get_form_link(doctype, name)
        : "";
    const can_open = Boolean(
        name
        && (
            docstatus === 1
            || docstatus === 2
            || is_next
        )
    );

    return `
        <div style="
            display:flex;
            align-items:center;
            justify-content:space-between;
            gap:12px;
            padding:7px 0;
            border-top:1px solid var(--border-color);
        ">
            <div>
                <strong>${escape_credit_application_text(__(doctype))}</strong>
                ${can_open
                    ? `
                        <a
                            href="${escape_credit_application_text(link)}"
                            target="_blank"
                            rel="noopener noreferrer"
                            style="margin-left:6px;"
                        >
                            ${escape_credit_application_text(name)} ↗
                        </a>
                    `
                    : name
                    ? `
                        <span
                            class="text-muted"
                            style="margin-left:6px;"
                            title="${__("Submit the preceding document first")}"
                        >
                            ${escape_credit_application_text(name)} 🔒
                        </span>
                    `
                    : `
                        <span class="text-muted" style="margin-left:6px;">
                            ${__("Not linked")}
                        </span>
                    `}
            </div>

            <div style="white-space:nowrap;">
                <span style="color:${status_color};">●</span>
                ${escape_credit_application_text(status_label)}
                ${is_next
                    ? `<strong style="margin-left:6px;">${__("Submit next")}</strong>`
                    : ""}
            </div>
        </div>
    `;
}


function credit_application_bundles_html(status) {
    return (status.bundles || []).map(bundle => {
        const is_broken = bundle.state === "Broken";
        const heading_color = is_broken
            ? "var(--red-500)"
            : bundle.state === "Complete"
                ? "var(--green-500)"
                : "var(--orange-500)";

        return `
            <div style="
                border:1px solid var(--border-color);
                border-radius:7px;
                padding:11px 13px;
                margin-bottom:10px;
                background:var(--card-bg);
            ">
                <div style="
                    display:flex;
                    justify-content:space-between;
                    gap:12px;
                    margin-bottom:5px;
                ">
                    <strong>
                        ${__("Application Bundle {0}", [bundle.sequence])}
                    </strong>
                    <strong style="color:${heading_color};">
                        ${escape_credit_application_text(__(bundle.state))}
                    </strong>
                </div>
                <div class="text-muted" style="margin-bottom:6px;">
                    ${format_credit_application_qty(
                        bundle.account_qty,
                        bundle.account_uom
                    )}
                    · ${__("Source Credit")}
                    ${escape_credit_application_text(
                        bundle.against_entry || ""
                    )}
                </div>

                ${credit_application_document_html(
                    "Stock Entry",
                    bundle.stock_entry,
                    bundle.stock_entry_docstatus,
                    bundle.next_doctype === "Stock Entry"
                )}
                ${credit_application_document_html(
                    "Journal Entry",
                    bundle.journal_entry,
                    bundle.journal_entry_docstatus,
                    bundle.next_doctype === "Journal Entry"
                )}
                ${credit_application_document_html(
                    "Processor Material Account Entry",
                    bundle.processor_material_account_entry,
                    bundle.pma_docstatus,
                    bundle.next_doctype
                        === "Processor Material Account Entry"
                )}
            </div>
        `;
    }).join("");
}


function settlement_document_link(doctype, name) {
    if (!name) {
        return "";
    }

    return `
        <a
            href="${escape_credit_application_text(
                frappe.utils.get_form_link(doctype, name)
            )}"
            target="_blank"
            rel="noopener noreferrer"
        >
            ${escape_credit_application_text(name)} ↗
        </a>
    `;
}


function settlement_documents_html(frm, status) {
    const rows = [];

    if (frm.doc.debit_note) {
        rows.push(`
            <div class="settlement-document-row">
                <strong>${__("Primary Debit Note")}</strong>
                <span>
                    ${settlement_document_link(
                        "Purchase Invoice",
                        frm.doc.debit_note
                    )}
                    <span class="lot-health-inline-status lot-health-inline-status-good">
                        ${__("Submitted")}
                    </span>
                </span>
            </div>
        `);
    }

    (status.bundles || [])
        .filter(bundle => bundle.state === "Complete")
        .forEach(bundle => {
            rows.push(`
                <div class="settlement-document-row">
                    <strong>
                        ${__("Credit Application Bundle {0}", [
                            bundle.sequence
                        ])}
                    </strong>
                    <span>
                        ${settlement_document_link(
                            "Processor Material Account Entry",
                            bundle.processor_material_account_entry
                        )}
                        <span class="text-muted">·</span>
                        ${settlement_document_link(
                            "Stock Entry",
                            bundle.stock_entry
                        )}
                        <span class="text-muted">·</span>
                        ${settlement_document_link(
                            "Journal Entry",
                            bundle.journal_entry
                        )}
                    </span>
                </div>
            `);
        });

    return `
        <div style="
            border:1px solid var(--border-color);
            border-radius:7px;
            background:var(--card-bg);
            padding:4px 14px;
        ">
            <style>
                .settlement-document-row {
                    display:grid;
                    grid-template-columns:minmax(190px, 0.7fr) minmax(0, 2fr);
                    gap:12px;
                    padding:10px 0;
                    border-top:1px solid var(--border-color);
                }
                .settlement-document-row:first-child {
                    border-top:0;
                }
                @media (max-width: 767px) {
                    .settlement-document-row {
                        grid-template-columns:1fr;
                        gap:4px;
                    }
                }
            </style>
            ${rows.join("")}
        </div>
    `;
}


function render_settlement_documents(frm) {
    const field = frm.get_field("settlement_documents_html");
    if (!field || !field.$wrapper || !field.$wrapper.length) {
        return;
    }

    const should_show = Boolean(
        frm.doc.docstatus === 1
        && frm.doc.settlement_status === "Completed"
    );

    frm.toggle_display("generated_document_section", should_show);
    if (!should_show) {
        field.$wrapper.empty();
        return;
    }

    field.$wrapper.html(`
        <div class="text-muted" style="padding:8px 0;">
            ${__("Loading settlement documents...")}
        </div>
    `);

    frappe.call({
        method: [
            "subcontracting_extensions",
            "subcontracting_extensions",
            "doctype",
            "processor_lot",
            "settlement_application_engine",
            "get_credit_application_completion_status"
        ].join("."),
        args: {
            processor_lot: frm.doc.name
        },
        callback(r) {
            field.$wrapper.html(
                settlement_documents_html(frm, r.message || {})
            );
        },
        error() {
            field.$wrapper.html(`
                <div class="text-danger" style="padding:8px 0;">
                    ${__("Unable to load settlement documents.")}
                </div>
            `);
        }
    });
}


function show_credit_application_completion_dialog(frm, initial_status) {
    const dialog = new frappe.ui.Dialog({
        title: initial_status.broken_count
            ? __("Repair Credit Applications")
            : __("Complete Credit Applications"),
        size: "large",
        fields: [
            {
                fieldtype: "HTML",
                fieldname: "completion_html"
            }
        ],
        primary_action_label: __("Refresh Status"),
        primary_action() {
            load_status();
        }
    });
    const wrapper = dialog.fields_dict.completion_html.$wrapper;

    function render(status) {
        wrapper.html(`
            <div style="padding:4px 2px;">
                <div class="alert alert-warning" style="margin-bottom:12px;">
                    ${__(
                        "For every bundle, submit the Stock Entry first, then the Journal Entry, and finally the Processor Material Account Entry. Links open in new tabs."
                    )}
                </div>
                ${credit_application_bundles_html(status)}
            </div>
        `);
    }

    function load_status() {
        frappe.call({
            method: [
                "subcontracting_extensions",
                "subcontracting_extensions",
                "doctype",
                "processor_lot",
                "settlement_application_engine",
                "get_credit_application_completion_status"
            ].join("."),
            args: {
                processor_lot: frm.doc.name
            },
            freeze: true,
            freeze_message: __("Refreshing application status..."),
            callback(r) {
                const status = r.message || {};
                frm.__credit_application_completion_status = status;

                if (status.is_complete) {
                    dialog.hide();
                    frm.reload_doc();
                    frappe.show_alert({
                        message: __("All credit applications are complete."),
                        indicator: "green"
                    });
                    return;
                }

                render(status);
            }
        });
    }

    render(initial_status);
    dialog.show();
}


frappe.ui.form.on("Processor Lot Settlement Item", {
    form_render(frm, cdt, cdn) {
        const grid = frm.fields_dict.items?.grid;
        const grid_row = grid?.grid_rows_by_docname?.[cdn];

        if (!grid_row || !grid_row.grid_form) {
            return;
        }

        const wrapper = grid_row.grid_form.wrapper;

        // These rows are generated from the selected SCO and must not be
        // inserted, duplicated, moved or deleted manually.
        wrapper.find(".grid-delete-row").hide();
        wrapper.find(".grid-duplicate-row").hide();
        wrapper.find(".grid-insert-row-below").hide();
        wrapper.find(".grid-insert-row-above").hide();
        wrapper.find(".grid-move-row").hide();
    },

    settlement_qty(frm, cdt, cdn) {
        calculate_recovery_amount(frm, cdt, cdn);
    },

    recovery_rate(frm, cdt, cdn) {
        calculate_recovery_amount(frm, cdt, cdn);
    }
});


/**
 * Load the selected SCO's authoritative component reconciliation.
 */
function load_sco_details(frm) {
    if (frm.doc.docstatus !== 0) {
        return;
    }

    frappe.call({
        method: [
            "subcontracting_extensions",
            "subcontracting_extensions",
            "doctype",
            "processor_lot",
            "processor_lot",
            "get_sco_settlement_details"
        ].join("."),
        args: {
            subcontracting_order: frm.doc.subcontracting_order,
            settlement_name: frm.doc.name || null
        },
        freeze: true,
        freeze_message: __("Loading Subcontracting Order balances..."),
        callback(r) {
            const data = r.message;

            if (!data) {
                return;
            }

            frm.set_value("company", data.company);
            frm.set_value("purchase_order", data.purchase_order);
            frm.set_value("supplier", data.supplier);
            frm.set_value(
                "supplier_warehouse",
                data.supplier_warehouse
            );

            // These are inherited from the originating SCO / Purchase Order
            // and remain read-only throughout the settlement.
            frm.set_value("cost_center", data.cost_center);
            frm.set_value("branch", data.branch);

            // Preserve the contractual settlement policy defined on the
            // originating Purchase Order.
            frm.set_value(
                "recover_raw_material_shortage",
                data.recover_raw_material_shortage
            );

            frm.set_value(
                "recover_processing_charges_on_shortage",
                data.recover_processing_charges_on_shortage
            );

            frm.set_value(
                "settlement_basis",
                data.settlement_basis
            );

            frm.set_value(
                "settlement_policy_source",
                data.settlement_policy_source
            );

            frm.set_value(
                "settlement_remarks",
                data.settlement_remarks
            );

            frm.set_value(
                "settlement_action",
                "Recover Processor Shortage"
            );

            frm.clear_table("items");

            (data.items || []).forEach(item => {
                const row = frm.add_child("items");

                Object.assign(row, item);
            });

            frm.refresh_field("items");

            if (!(data.items || []).length) {
                frappe.msgprint({
                    title: __("Nothing to Settle"),
                    indicator: "blue",
                    message: __(
                        "No unsettled component quantity remains against this Subcontracting Order."
                    )
                });
            }
        }
    });
}


/**
 * Clear fields populated from an SCO.
 */
function clear_sco_details(frm) {
    frm.set_value("purchase_order", null);
    frm.set_value("supplier", null);
    frm.set_value("supplier_warehouse", null);
    frm.set_value("cost_center", null);
    frm.set_value("branch", null);

    frm.set_value(
        "recover_raw_material_shortage",
        0
    );

    frm.set_value(
        "recover_processing_charges_on_shortage",
        0
    );

    frm.set_value("settlement_basis", null);
    frm.set_value(
        "settlement_policy_source",
        null
    );
    frm.set_value("settlement_remarks", null);
    frm.set_value("settlement_action", null);

    frm.clear_table("items");
    frm.refresh_field("items");
}


/**
 * Recalculate the displayed recovery amount.
 *
 * The server recalculates this again during validation and submission.
 */
function calculate_recovery_amount(frm, cdt, cdn) {
    const row = locals[cdt][cdn];

    const settlement_qty = flt(row.settlement_qty);
    const recovery_rate = flt(row.recovery_rate);

    frappe.model.set_value(
        cdt,
        cdn,
        "recovery_amount",
        settlement_qty * recovery_rate
    );
}


/**
 * Prevent controlled fields and SCO-derived rows from being changed.
 */
function set_field_properties(frm) {
    const is_submitted = frm.doc.docstatus !== 0;

    frm.set_df_property(
        "subcontracting_order",
        "read_only",
        is_submitted || Boolean(frm.doc.debit_note)
    );

    frm.set_df_property(
        "company",
        "read_only",
        Boolean(frm.doc.subcontracting_order)
    );

    frm.set_df_property(
        "settlement_action",
        "read_only",
        true
    );

    const items_grid = frm.get_field("items")?.grid;

    if (!items_grid) {
        return;
    }

    // Set the restrictions on the Table field definition used by Frappe Grid.
    items_grid.df.cannot_add_rows = 1;
    items_grid.df.cannot_delete_rows = 1;

    // Remove the visible grid controls as an additional UX safeguard.
    items_grid.wrapper.find(".grid-add-row").hide();
    items_grid.wrapper.find(".grid-remove-rows").hide();
    items_grid.wrapper.find(".grid-remove-all-rows").hide();

    [
        "sco_supplied_item",
        "sco_finished_item",
        "component_item",
        "finished_item",
        "stock_uom",
        "required_qty",
        "supplied_qty",
        "consumed_qty",
        "returned_qty",
        "previously_settled_qty",
        "outstanding_qty",
        "warehouse_balance",
        "component_valuation_rate",
        "recovery_amount"
    ].forEach(fieldname => {
        items_grid.update_docfield_property(
            fieldname,
            "read_only",
            1
        );
    });

    items_grid.refresh();
}

/**
 * Derive the displayed Settlement Action from the effective policy.
 *
 * Python repeats this calculation authoritatively during validation.
 * JavaScript only provides immediate form feedback.
 */
function refresh_settlement_action(frm) {
    const recover_material = Boolean(
        frm.doc.recover_raw_material_shortage
    );

    const recover_processing = Boolean(
        frm.doc.recover_processing_charges_on_shortage
    );

    let settlement_action = "No Recovery";

    if (recover_material && recover_processing) {
        settlement_action =
            "Recover Raw Material and Processing Charges";
    } else if (recover_material) {
        settlement_action =
            "Recover Raw Material Shortage";
    } else if (recover_processing) {
        settlement_action =
            "Recover Processing Charges Only";
    }

    if (frm.doc.settlement_action !== settlement_action) {
        frm.set_value("settlement_action", settlement_action);
    }
}

/**
 * Control settlement-policy override fields.
 *
 * This is a user-interface safeguard only. The Python controller will
 * independently enforce authorization, locking and audit requirements.
 */
function apply_settlement_policy_override_properties(frm) {
    const is_system_manager =
        frappe.user.has_role("System Manager");

    const override_is_allowed = Boolean(
        is_system_manager
        && frm.doc.docstatus === 0
        && !frm.doc.debit_note
        && frm.doc.subcontracting_order
    );

    const override_is_active = Boolean(
        override_is_allowed
        && frm.doc.override_settlement_policy
    );

    frm.set_df_property(
        "override_settlement_policy",
        "read_only",
        !override_is_allowed
    );

    [
        "recover_raw_material_shortage",
        "recover_processing_charges_on_shortage",
        "settlement_basis",
        "settlement_remarks"
    ].forEach(fieldname => {
        frm.set_df_property(
            fieldname,
            "read_only",
            !override_is_active
        );
    });

    frm.set_df_property(
        "settlement_policy_override_reason",
        "read_only",
        !override_is_active
    );

    frm.set_df_property(
        "settlement_policy_override_reason",
        "reqd",
        override_is_active
    );

    /*
     * Audit fields and policy source are always controlled by the server.
     */
    [
        "settlement_policy_source",
        "overridden_by",
        "settlement_policy_overridden_on"
    ].forEach(fieldname => {
        frm.set_df_property(
            fieldname,
            "read_only",
            true
        );
    });

    frm.refresh_fields([
        "override_settlement_policy",
        "recover_raw_material_shortage",
        "recover_processing_charges_on_shortage",
        "settlement_basis",
        "settlement_remarks",
        "settlement_policy_override_reason",
        "settlement_policy_source",
        "overridden_by",
        "settlement_policy_overridden_on"
    ]);
}

/**
 * Re-hide grid controls that Frappe may redraw after row selection.
 */
function hide_items_grid_controls(frm) {
    const grid = frm.get_field("items")?.grid;

    if (!grid) {
        return;
    }

    grid.wrapper.find(".grid-add-row").hide();
    grid.wrapper.find(".grid-remove-rows").hide();
    grid.wrapper.find(".grid-remove-all-rows").hide();
}

/**
 * Add a convenient link to the generated standard Debit Note.
 */
function add_generated_document_button(frm) {
    if (!frm.doc.debit_note) {
        return;
    }

    frm.add_custom_button(
        __("Open Debit Note"),
        () => {
            frappe.set_route(
                "Form",
                "Purchase Invoice",
                frm.doc.debit_note
            );
        },
        __("View")
    );
}


/**
 * Add the Stage 1 read-only settlement-reversal preview.
 */
function add_reopen_settlement_preview_button(frm) {
    if (
        frm.doc.docstatus !== 1
        || frm.doc.settlement_status !== "Completed"
        || !frappe.user.has_role("System Manager")
    ) {
        return;
    }

    frm.add_custom_button(
        __("Reopen Settlement"),
        () => {
            load_settlement_reversal_preview(frm);
        },
        __("Actions")
    );
}


/**
 * Fetch the authoritative read-only reversal plan.
 */
function load_settlement_reversal_preview(frm) {
    frappe.call({
        method: [
            "subcontracting_extensions",
            "subcontracting_extensions",
            "doctype",
            "processor_lot",
            "processor_lot",
            "get_processor_lot_settlement_reversal_preview"
        ].join("."),
        args: {
            processor_lot: frm.doc.name
        },
        freeze: true,
        freeze_message: __(
            "Checking settlement-reversal dependencies..."
        ),
        callback(r) {
            if (!r.message) {
                frappe.msgprint({
                    title: __("Reversal Preview Unavailable"),
                    indicator: "red",
                    message: __(
                        "The server did not return a reversal plan."
                    )
                });
                return;
            }

            show_settlement_reversal_preview_dialog(
                frm,
                r.message
            );
        }
    });
}


/**
 * Show dependent reversal choices and require explicit authorization before
 * enabling the server-controlled execution action.
 */
function show_settlement_reversal_preview_dialog(frm, preview) {
    const dialog = new frappe.ui.Dialog({
        title: __("Reopen Processor Lot Settlement"),
        size: "extra-large",
        fields: [
            {
                fieldtype: "HTML",
                fieldname: "preview_header"
            },
            {
                fieldtype: "Section Break",
                label: __("Documents to Reverse")
            },
            {
                fieldtype: "Check",
                fieldname: "cancel_debit_note",
                label: __("Cancel residual Debit Note"),
                default: 0
            },
            {
                fieldtype: "Check",
                fieldname: "reverse_material_credit",
                label: __("Also reverse material-credit application"),
                default: 0,
                read_only: 1
            },
            {
                fieldtype: "HTML",
                fieldname: "dependency_tree"
            },
            {
                fieldtype: "Section Break",
                label: __("Forecast After Selected Reversal")
            },
            {
                fieldtype: "HTML",
                fieldname: "impact_preview"
            },
            {
                fieldtype: "Section Break",
                label: __("Documents Preserved")
            },
            {
                fieldtype: "HTML",
                fieldname: "preserved_documents"
            },
            {
                fieldtype: "Section Break",
                label: __("Preflight Status")
            },
            {
                fieldtype: "HTML",
                fieldname: "preflight_status"
            },
            {
                fieldtype: "Section Break",
                label: __("Authorization")
            },
            {
                fieldtype: "Small Text",
                fieldname: "reversal_reason",
                label: __("Reason for Reversal"),
                reqd: 1,
                description: __("Minimum 10 characters. This is written to the Processor Lot timeline.")
            },
            {
                fieldtype: "Data",
                fieldname: "confirmation",
                label: __("Type Processor Lot ID to Confirm"),
                reqd: 1,
                description: preview.processor_lot
            }
        ],
        primary_action_label: __("Execute Selected Reversal"),
        primary_action() {
            execute_selected_settlement_reversal(
                frm,
                dialog,
                preview
            );
        },
        secondary_action_label: __("Close"),
        secondary_action() {
            dialog.hide();
        }
    });

    const cancel_control = dialog.get_field(
        "cancel_debit_note"
    );
    const credit_control = dialog.get_field(
        "reverse_material_credit"
    );
    const reason_control = dialog.get_field("reversal_reason");
    const confirmation_control = dialog.get_field("confirmation");

    function update_dialog() {
        const cancel_debit_note = Boolean(
            dialog.get_value("cancel_debit_note")
        );
        let reverse_material_credit = Boolean(
            dialog.get_value("reverse_material_credit")
        );

        credit_control.df.read_only = !cancel_debit_note;
        credit_control.refresh();

        if (!cancel_debit_note && reverse_material_credit) {
            dialog.set_value("reverse_material_credit", 0);
            reverse_material_credit = false;
        }

        render_settlement_reversal_preview(
            dialog,
            preview,
            {
                cancel_debit_note,
                reverse_material_credit
            }
        );

        const readiness = preview.readiness || {};
        const preflight_ready = reverse_material_credit
            ? Boolean(readiness.complete_settlement)
            : Boolean(readiness.debit_note_only);
        const reason = String(
            dialog.get_value("reversal_reason") || ""
        ).trim();
        const confirmation = String(
            dialog.get_value("confirmation") || ""
        ).trim();
        dialog.get_primary_btn().prop(
            "disabled",
            !cancel_debit_note
                || !preflight_ready
                || reason.length < 10
                || confirmation !== preview.processor_lot
        );
    }

    cancel_control.df.onchange = update_dialog;
    credit_control.df.onchange = update_dialog;
    reason_control.df.onchange = update_dialog;
    confirmation_control.df.onchange = update_dialog;
    reason_control.$input.on("input", update_dialog);
    confirmation_control.$input.on("input", update_dialog);

    dialog.show();
    render_settlement_reversal_header(
        dialog,
        preview
    );
    update_dialog();
}


function execute_selected_settlement_reversal(frm, dialog, preview) {
    const reverse_material_credit = Boolean(
        dialog.get_value("reverse_material_credit")
    );
    const scope = reverse_material_credit
        ? "Complete Settlement"
        : "Debit Note Only";
    const reason = String(
        dialog.get_value("reversal_reason") || ""
    ).trim();
    const confirmation = String(
        dialog.get_value("confirmation") || ""
    ).trim();

    frappe.confirm(
        __(
            "Execute {0} reversal for Processor Lot {1}? The selected submitted documents will be cancelled in dependency order.",
            [scope, preview.processor_lot]
        ),
        () => {
            dialog.get_primary_btn().prop("disabled", true);
            frappe.call({
                method: [
                    "subcontracting_extensions",
                    "subcontracting_extensions",
                    "doctype",
                    "processor_lot",
                    "processor_lot",
                    "execute_processor_lot_settlement_reversal"
                ].join("."),
                args: {
                    processor_lot: preview.processor_lot,
                    scope,
                    reason,
                    confirmation
                },
                freeze: true,
                freeze_message: __(
                    "Reversing settlement documents in dependency order..."
                ),
                callback(r) {
                    const result = r.message;
                    if (!result) {
                        dialog.get_primary_btn().prop("disabled", false);
                        return;
                    }
                    dialog.hide();
                    frm.reload_doc().then(() => {
                        frappe.msgprint({
                            title: __("Settlement Reopened"),
                            indicator: "green",
                            message: result.message
                        });
                    });
                },
                error() {
                    dialog.get_primary_btn().prop("disabled", false);
                }
            });
        }
    );
}


function add_reopened_settlement_buttons(frm) {
    if (
        frm.doc.docstatus !== 1
        || !frappe.user.has_role("System Manager")
        || frm.doc.settlement_status !== "Reopened"
    ) {
        return;
    }

    frm.add_custom_button(
        __("Reconcile Reopened Settlement"),
        () => {
            frappe.call({
                method: [
                    "subcontracting_extensions",
                    "subcontracting_extensions",
                    "doctype",
                    "processor_lot",
                    "processor_lot",
                    "get_processor_lot_fact_summary"
                ].join("."),
                args: {
                    subcontracting_order: frm.doc.subcontracting_order,
                    processor_lot: frm.doc.name
                },
                freeze: true,
                freeze_message: __("Loading current lot facts..."),
                callback(r) {
                    if (r.message) {
                        open_reconciliation_wizard(frm, r.message);
                    }
                }
            });
        },
        __("Actions")
    );

}


function complete_reopened_settlement(frm) {
    frappe.confirm(
        __(
            "Revalidate the current settlement evidence and mark Processor Lot {0} Completed again?",
            [frm.doc.name]
        ),
        () => {
            frappe.call({
                method: [
                    "subcontracting_extensions",
                    "subcontracting_extensions",
                    "doctype",
                    "processor_lot",
                    "processor_lot",
                    "complete_reopened_processor_lot_settlement"
                ].join("."),
                args: {processor_lot: frm.doc.name},
                freeze: true,
                freeze_message: __("Revalidating settlement evidence..."),
                callback(r) {
                    if (!r.message) {
                        return;
                    }
                    frm.reload_doc().then(() => {
                        frappe.msgprint({
                            title: __("Settlement Completed"),
                            indicator: "green",
                            message: r.message.message
                        });
                    });
                }
            });
        }
    );
}


function render_settlement_reversal_header(dialog, preview) {
    const debit_note = preview.debit_note || {};
    const currency = preview.currency || "";
    const amount = format_currency(
        Math.abs(flt(debit_note.rounded_total)),
        currency
    );

    dialog.get_field("preview_header").$wrapper.html(`
        <div class="alert alert-warning" style="margin-bottom: 12px;">
            <strong>${__("Controlled reversal — submitted documents will be cancelled only after final confirmation")}</strong>
            <div style="margin-top: 4px;">
                ${__("Select a valid stopping point to review its dependency chain and restored position.")}
            </div>
        </div>
        <div class="small text-muted" style="margin-bottom: 8px;">
            ${__("Processor Lot")}: <strong>${escape_reversal_html(preview.processor_lot)}</strong>
            &nbsp;·&nbsp;
            ${__("Debit Note Value")}: <strong>${amount}</strong>
        </div>
    `);
}


function render_settlement_reversal_preview(
    dialog,
    preview,
    selection
) {
    render_settlement_reversal_dependency_tree(
        dialog,
        preview,
        selection
    );
    render_settlement_reversal_impact(
        dialog,
        preview,
        selection
    );
    render_settlement_reversal_preserved(
        dialog,
        preview
    );
    render_settlement_reversal_preflight(
        dialog,
        preview,
        selection
    );
}


function render_settlement_reversal_dependency_tree(
    dialog,
    preview,
    selection
) {
    const debit_note = preview.debit_note || {};
    const applications = preview.applications || [];
    const checked_debit_note = selection.cancel_debit_note
        ? "checked"
        : "";
    const checked_credit = selection.reverse_material_credit
        ? "checked"
        : "";
    const muted_credit = selection.cancel_debit_note
        ? ""
        : "opacity: 0.55;";

    const application_rows = applications.map(application => {
        const checked = selection.reverse_material_credit
            ? "checked"
            : "";

        return `
            <div style="margin-left: 56px; margin-top: 8px; ${muted_credit}">
                <div>
                    <input type="checkbox" disabled ${checked}>
                    <strong>${__("Cancel PMA application")}</strong>
                    ${reversal_document_link(
                        "Processor Material Account Entry",
                        application.name
                    )}
                </div>
                <div style="margin-left: 28px; margin-top: 7px;">
                    <input type="checkbox" disabled ${checked}>
                    ${__("Cancel linked Journal Entry")}
                    ${reversal_document_link(
                        "Journal Entry",
                        application.application_journal_entry
                    )}
                </div>
                <div style="margin-left: 28px; margin-top: 7px;">
                    <input type="checkbox" disabled ${checked}>
                    ${__("Cancel linked Stock Entry")}
                    ${reversal_document_link(
                        "Stock Entry",
                        application.application_stock_entry
                    )}
                </div>
            </div>
        `;
    }).join("");

    dialog.get_field("dependency_tree").$wrapper.html(`
        <div style="border: 1px solid var(--border-color); border-radius: 8px; padding: 14px;">
            <div>
                <input type="checkbox" disabled ${checked_debit_note}>
                <strong>${__("Cancel residual Debit Note")}</strong>
                ${reversal_document_link(
                    "Purchase Invoice",
                    debit_note.name
                )}
            </div>
            <div style="margin-left: 28px; margin-top: 9px; ${muted_credit}">
                <input type="checkbox" disabled ${checked_credit}>
                <strong>${__("Reverse material-credit application bundle")}</strong>
                <div class="small text-muted">
                    ${__("PMA, Journal Entry and Stock Entry form one atomic dependency group.")}
                </div>
            </div>
            ${application_rows || `
                <div class="text-muted" style="margin-left: 56px; margin-top: 8px;">
                    ${__("No active application bundle was found.")}
                </div>
            `}
        </div>
    `);
}


function render_settlement_reversal_impact(
    dialog,
    preview,
    selection
) {
    const forecast = preview.forecast || {};
    let position = forecast.current || {};
    let heading = __("Current completed position");

    if (
        selection.cancel_debit_note
        && selection.reverse_material_credit
    ) {
        position = forecast.complete_settlement || {};
        heading = __("After complete settlement reversal");
    } else if (selection.cancel_debit_note) {
        position = forecast.debit_note_only || {};
        heading = __("After Debit Note-only reversal");
    }

    const uom = preview.account_uom || "";
    const qty = value => `${format_number(flt(value), null, 3)} ${escape_reversal_html(uom)}`;

    dialog.get_field("impact_preview").$wrapper.html(`
        <div style="border: 1px solid var(--border-color); border-radius: 8px; overflow: hidden;">
            <div style="padding: 10px 12px; background: var(--subtle-fg); font-weight: 600;">
                ${heading}
            </div>
            <div style="display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; padding: 12px;">
                ${reversal_metric_card(
                    __("Net Physical Outstanding"),
                    qty(position.net_physical_outstanding_qty)
                )}
                ${reversal_metric_card(
                    __("Open Commercial Variance"),
                    qty(position.open_commercial_variance_qty)
                )}
                ${reversal_metric_card(
                    __("Source Credit Remaining"),
                    qty(position.source_credit_remaining_qty)
                )}
            </div>
        </div>
    `);
}


function render_settlement_reversal_preserved(dialog, preview) {
    const rows = (preview.preserved || []).map(row => `
        <tr>
            <td>${escape_reversal_html(row.doctype)}</td>
            <td>${reversal_document_link(row.doctype, row.name)}</td>
            <td><span class="indicator-pill green">${escape_reversal_html(row.status)}</span></td>
        </tr>
    `).join("");

    dialog.get_field("preserved_documents").$wrapper.html(`
        <div class="table-responsive">
            <table class="table table-bordered table-sm">
                <thead>
                    <tr>
                        <th>${__("Document Type")}</th>
                        <th>${__("Document")}</th>
                        <th>${__("Treatment")}</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    `);
}


function render_settlement_reversal_preflight(
    dialog,
    preview,
    selection
) {
    const readiness = preview.readiness || {};
    let ready = false;
    let blockers = [];

    if (
        selection.cancel_debit_note
        && selection.reverse_material_credit
    ) {
        ready = Boolean(readiness.complete_settlement);
        blockers = readiness.complete_settlement_blockers || [];
    } else if (selection.cancel_debit_note) {
        ready = Boolean(readiness.debit_note_only);
        blockers = readiness.debit_note_blockers || [];
    }

    if (!selection.cancel_debit_note) {
        dialog.get_field("preflight_status").$wrapper.html(`
            <div class="alert alert-info">
                ${__("Select a reversal scope to view its preflight result.")}
            </div>
        `);
        return;
    }

    if (ready) {
        dialog.get_field("preflight_status").$wrapper.html(`
            <div class="alert alert-success">
                <strong>${__("Preflight passed")}</strong>
                <div style="margin-top: 4px;">
                    ${__("This scope is structurally ready. Enter the authorization fields below to enable execution.")}
                </div>
            </div>
        `);
        return;
    }

    const blocker_items = blockers.map(blocker => `
        <li>${escape_reversal_html(blocker)}</li>
    `).join("");

    dialog.get_field("preflight_status").$wrapper.html(`
        <div class="alert alert-danger">
            <strong>${__("Preflight blocked")}</strong>
            <ul style="margin: 8px 0 0 18px;">${blocker_items}</ul>
        </div>
    `);
}


function reversal_metric_card(label, value) {
    return `
        <div style="border: 1px solid var(--border-color); border-radius: 7px; padding: 10px;">
            <div class="small text-muted">${label}</div>
            <div style="font-size: 17px; font-weight: 600; margin-top: 3px;">${value}</div>
        </div>
    `;
}


function reversal_document_link(doctype, name) {
    if (!doctype || !name) {
        return `<span class="text-muted">${__("Not available")}</span>`;
    }

    return `
        <a href="${frappe.utils.get_form_link(doctype, name)}">
            ${escape_reversal_html(name)}
        </a>
    `;
}


function escape_reversal_html(value) {
    return frappe.utils.escape_html(String(value || ""));
}


/**
 * Display the settlement status clearly on the form.
 */
function set_status_indicator(frm) {
    const status = frm.doc.settlement_status || "Draft";

    const indicators = {
        "Draft": "orange",
        "Debit Note Created": "blue",
        "Reversal In Progress": "orange",
        "Reopened": "orange",
        "Reopened - Debit Note Created": "blue",
        "Completed": "green",
        "Cancelled": "red"
    };

    frm.page.set_indicator(
        __(status),
        indicators[status] || "gray"
    );
}

/**
 * Add a read-only diagnostic button for viewing current Processor Lot facts.
 *
 * The button calls the server-side Fact Engine and displays its compact
 * summary. It does not save or modify the Processor Lot or any linked
 * ERPNext document.
 */
function add_view_lot_facts_button(frm) {
    if (!frm.doc.subcontracting_order) {
        return;
    }

    frm.add_custom_button(
        __("View Lot Facts"),
        () => {
            show_lot_facts_dialog(frm);
        },
        __("View")
    );
}


/**
 * Fetch and display the current Fact Engine summary.
 */
function show_lot_facts_dialog(frm) {
    frappe.call({
        method: [
            "subcontracting_extensions",
            "subcontracting_extensions",
            "doctype",
            "processor_lot",
            "processor_lot",
            "get_processor_lot_fact_summary"
        ].join("."),
        args: {
            subcontracting_order: frm.doc.subcontracting_order,
            processor_lot: frm.doc.name
        },
        freeze: true,
        freeze_message: __("Loading current lot facts..."),
        callback(r) {
            const facts = r.message;

            if (!facts) {
                frappe.msgprint({
                    title: __("Lot Facts Unavailable"),
                    indicator: "red",
                    message: __(
                        "The Fact Engine did not return any information."
                    )
                });
                return;
            }

            display_lot_facts_dialog(frm, facts);
        }
    });
}


/**
 * Render the Fact Engine result in a compact read-only dialog.
 */
function display_lot_facts_dialog(frm, facts) {
    const summary = facts.summary || {};
    const identity = facts.identity || {};
    const settlement_policy = facts.settlement_policy || {};
    const integrity = facts.integrity || {};

    const entrustment = summary.entrustment || {};
    const physical = summary.physical_inventory || {};
    const commercial = summary.commercial || {};
    const comparisons = summary.comparisons || {};

    const warnings = integrity.warnings || [];
    const blocking_errors =
        integrity.blocking_errors || [];

    const precision = 3;

    const format_yes_no = value => {
        return value
            ? __("Yes")
            : __("No");
    };

    const escape_text = value => {
        return frappe.utils.escape_html(
            String(value ?? "")
        );
    };

    const format_multiline_text = value => {
        return escape_text(value).replace(/\n/g, "<br>");
    };

    const format_qty = value => {
        return format_number(flt(value), null, precision);
    };

    const format_currency_value = value => {
        return format_currency(
            flt(value),
            frappe.defaults.get_default("currency")
        );
    };

    const format_signed_qty = value => {
        const number = flt(value);
        const sign = number > 0 ? "+" : "";

        return `${sign}${format_qty(number)}`;
    };

    const policy_available = Boolean(
        settlement_policy.policy_available
    );

    const policy_status_html = policy_available
        ? `
            <span class="text-success">
                ${__("Available")}
            </span>
        `
        : `
            <span class="text-warning">
                ${__("Not Defined")}
            </span>
        `;

    let integrity_html = "";

    if (!warnings.length && !blocking_errors.length) {
        integrity_html = `
            <div class="text-success">
                ${__("No integrity warnings or blocking errors.")}
            </div>
        `;
    } else {
        const blocking_html = blocking_errors.map(error => {
            return `
                <li class="text-danger">
                    ${frappe.utils.escape_html(error.message || "")}
                </li>
            `;
        }).join("");

        const warning_html = warnings.map(warning => {
            return `
                <li class="text-warning">
                    ${frappe.utils.escape_html(warning.message || "")}
                </li>
            `;
        }).join("");

        integrity_html = `
            <ul class="lot-integrity-list">
                ${blocking_html}
                ${warning_html}
            </ul>
        `;
    }

    const dialog = new frappe.ui.Dialog({
        title: __("Processor Lot Facts"),
        size: "small",
        fields: [
            {
                fieldtype: "HTML",
                fieldname: "lot_facts_html"
            }
        ],
        primary_action_label: __("Close"),
        primary_action() {
            dialog.hide();
        }
    });

    const html = `
        <div class="processor-lot-facts-compact">
            <style>
                .processor-lot-facts-compact {
                    font-size: 12px;
                    line-height: 1.3;
                }

                .processor-lot-facts-compact .lot-identity {
                    margin-bottom: 6px;
                }

                .processor-lot-facts-compact .lot-identity div {
                    margin-bottom: 2px;
                }

                .processor-lot-facts-compact hr {
                    margin: 7px 0 8px;
                }

                .processor-lot-facts-compact h5 {
                    font-size: 12px;
                    font-weight: 600;
                    margin: 8px 0 4px;
                }

                .processor-lot-facts-compact .table {
                    width: 100%;
                    margin-bottom: 7px;
                }

                .processor-lot-facts-compact .table td {
                    padding: 3px 6px;
                    vertical-align: middle;
                }

                .processor-lot-facts-compact .table td:first-child {
                    width: 68%;
                }

                .processor-lot-facts-compact .lot-policy-remarks {
                    border: 1px solid var(--border-color);
                    border-radius: 4px;
                    margin-bottom: 8px;
                    overflow: hidden;
                }

                .processor-lot-facts-compact .lot-policy-remarks-label {
                    padding: 5px 7px;
                    font-weight: 600;
                    background: var(--subtle-fg);
                    border-bottom: 1px solid var(--border-color);
                }

                .processor-lot-facts-compact .lot-policy-remarks-value {
                    padding: 7px;
                    line-height: 1.45;
                    white-space: normal;
                    overflow-wrap: anywhere;
                }

                .processor-lot-facts-compact .lot-integrity-list {
                    margin: 0;
                    padding-left: 18px;
                }

                .processor-lot-facts-compact .lot-integrity-list li {
                    margin-bottom: 2px;
                }
            </style>

            <div class="lot-identity">
                <div>
                    <strong>${__("Subcontracting Order")}:</strong>
                    ${frappe.utils.escape_html(
                        identity.subcontracting_order || ""
                    )}
                </div>

                <div>
                    <strong>${__("Purchase Order")}:</strong>
                    ${frappe.utils.escape_html(
                        identity.purchase_order || ""
                    )}
                </div>

                <div>
                    <strong>${__("Supplier")}:</strong>
                    ${frappe.utils.escape_html(
                        identity.supplier || ""
                    )}
                </div>

                <div>
                    <strong>${__("Supplier Warehouse")}:</strong>
                    ${frappe.utils.escape_html(
                        identity.supplier_warehouse || ""
                    )}
                </div>

                <div>
                    <strong>${__("SCO Status")}:</strong>
                    ${frappe.utils.escape_html(
                        identity.sco_status || ""
                    )}
                </div>
            </div>

            <hr>

            <h5>${__("Settlement Policy")}</h5>

            <table class="table table-bordered">
                <tbody>
                    <tr>
                        <td>${__("Policy Status")}</td>
                        <td>
                            ${policy_status_html}
                        </td>
                    </tr>

                    <tr>
                        <td>${__("Policy Source")}</td>
                        <td>
                            ${escape_text(
                                settlement_policy.policy_source || ""
                            )}
                        </td>
                    </tr>

                    <tr>
                        <td>${__("Purchase Order")}</td>
                        <td>
                            ${escape_text(
                                settlement_policy.purchase_order || ""
                            )}
                        </td>
                    </tr>

                    <tr>
                        <td>
                            ${__("Recover Raw Material Shortage")}
                        </td>
                        <td>
                            ${format_yes_no(
                                settlement_policy
                                    .recover_raw_material_shortage
                            )}
                        </td>
                    </tr>

                    <tr>
                        <td>
                            ${__(
                                "Recover Processing Charges on Shortage"
                            )}
                        </td>
                        <td>
                            ${format_yes_no(
                                settlement_policy
                                    .recover_processing_charges_on_shortage
                            )}
                        </td>
                    </tr>

                    <tr>
                        <td>${__("Settlement Basis")}</td>
                        <td>
                            ${escape_text(
                                settlement_policy.settlement_basis || ""
                            )}
                        </td>
                    </tr>

                </tbody>
            </table>

            <div class="lot-policy-remarks">
                <div class="lot-policy-remarks-label">
                    ${__("Settlement Remarks")}
                </div>

                <div class="lot-policy-remarks-value">
                    ${
                        format_multiline_text(
                            settlement_policy.settlement_remarks || ""
                        )
                        || "—"
                    }
                </div>
            </div>

            <h5>${__("Entrustment")}</h5>

            <table class="table table-bordered">
                <tbody>
                    <tr>
                        <td>${__("Transferred Quantity")}</td>
                        <td class="text-right">
                            ${format_qty(
                                entrustment.transferred_qty
                            )}
                        </td>
                    </tr>

                    <tr>
                        <td>${__("SCO Supplied Quantity")}</td>
                        <td class="text-right">
                            ${format_qty(
                                entrustment.sco_supplied_qty
                            )}
                        </td>
                    </tr>

                    <tr>
                        <td>${__("Transfer Value")}</td>
                        <td class="text-right">
                            ${format_currency_value(
                                entrustment.transfer_value
                            )}
                        </td>
                    </tr>
                </tbody>
            </table>

            <h5>${__("Physical / Inventory")}</h5>

            <table class="table table-bordered">
                <tbody>
                    <tr>
                        <td>${__("SCR Received Quantity")}</td>
                        <td class="text-right">
                            ${format_qty(
                                physical.scr_received_qty
                            )}
                        </td>
                    </tr>

                    <tr>
                        <td>${__("SCR Consumed Quantity")}</td>
                        <td class="text-right">
                            ${format_qty(
                                physical.scr_consumed_qty
                            )}
                        </td>
                    </tr>

                    <tr>
                        <td>${__("Components Returned")}</td>
                        <td class="text-right">
                            ${format_qty(
                                physical.components_returned_qty
                            )}
                        </td>
                    </tr>

                    <tr>
                        <td>
                            <strong>
                                ${__("Outstanding Quantity")}
                            </strong>
                        </td>
                        <td class="text-right">
                            <strong>
                                ${format_qty(
                                    physical.outstanding_qty
                                )}
                            </strong>
                        </td>
                    </tr>
                </tbody>
            </table>

            <h5>${__("Commercial")}</h5>

            <table class="table table-bordered">
                <tbody>
                    <tr>
                        <td>${__("Purchase Receipt Quantity")}</td>
                        <td class="text-right">
                            ${format_qty(
                                commercial.purchase_receipt_qty
                            )}
                        </td>
                    </tr>

                    <tr>
                        <td>${__("Purchase Invoice Quantity")}</td>
                        <td class="text-right">
                            ${format_qty(
                                commercial.purchase_invoice_qty
                            )}
                        </td>
                    </tr>

                    <tr>
                        <td>${__("Service Invoice Net Amount")}</td>
                        <td class="text-right">
                            ${format_currency_value(
                                commercial.service_invoice_net_amount
                            )}
                        </td>
                    </tr>

                    <tr>
                        <td>${__("GST Amount")}</td>
                        <td class="text-right">
                            ${format_currency_value(
                                commercial.service_invoice_tax_amount
                            )}
                        </td>
                    </tr>

                    <tr>
                        <td>${__("Service Invoice Grand Total")}</td>
                        <td class="text-right">
                            ${format_currency_value(
                                commercial.service_invoice_grand_total
                            )}
                        </td>
                    </tr>
                </tbody>
            </table>

            <h5>${__("Factual Comparisons")}</h5>

            <table class="table table-bordered">
                <tbody>
                    <tr>
                        <td>${__("Transfer vs SCO Supplied")}</td>
                        <td class="text-right">
                            ${format_signed_qty(
                                comparisons.transfer_vs_sco_supplied
                            )}
                        </td>
                    </tr>

                    <tr>
                        <td>${__("SCR Received vs SCO Received")}</td>
                        <td class="text-right">
                            ${format_signed_qty(
                                comparisons.scr_received_vs_sco_received
                            )}
                        </td>
                    </tr>

                    <tr>
                        <td>${__("SCR Consumed vs SCO Consumed")}</td>
                        <td class="text-right">
                            ${format_signed_qty(
                                comparisons.scr_consumed_vs_sco_consumed
                            )}
                        </td>
                    </tr>

                    <tr>
                        <td>${__("Invoice vs SCR Received")}</td>
                        <td class="text-right">
                            ${format_signed_qty(
                                comparisons.invoice_vs_scr_received
                            )}
                        </td>
                    </tr>

                    <tr>
                        <td>${__("Purchase Receipt vs Invoice")}</td>
                        <td class="text-right">
                            ${format_signed_qty(
                                comparisons.purchase_receipt_vs_invoice
                            )}
                        </td>
                    </tr>
                </tbody>
            </table>

            <h5>${__("Integrity")}</h5>

            ${integrity_html}
        </div>
    `;

    dialog.fields_dict.lot_facts_html.$wrapper.html(html);
    dialog.show();
}

/**
 * Render the cumulative physical receipt position recorded through
 * Processor Lot Receipts.
 *
 * Expected quantity comes from the finished-item commitment in the linked
 * Subcontracting Order.
 *
 * Accepted quantity comes only from Processor Lot Receipts.
 *
 * SCR, Purchase Receipt and Purchase Invoice quantities are intentionally
 * excluded from this panel.
 */
function render_physical_receipt_position(frm) {
    const field = frm.get_field(
        "physical_receipt_position_html"
    );

    if (!field || !field.$wrapper) {
        return;
    }

    if (frm.is_new() || !frm.doc.name) {
        field.$wrapper.html(`
            <div class="text-muted" style="padding: 8px 0;">
                ${__(
                    "Save the Processor Lot to view its physical receipt position."
                )}
            </div>
        `);
        return;
    }

    field.$wrapper.html(`
        <div class="text-muted" style="padding: 8px 0;">
            ${__("Loading physical receipt position...")}
        </div>
    `);

    frappe.call({
        method: [
            "subcontracting_extensions",
            "subcontracting_extensions",
            "doctype",
            "processor_lot",
            "processor_lot",
            "get_processor_lot_physical_position"
        ].join("."),
        args: {
            processor_lot: frm.doc.name
        },
        callback(r) {
            const position = r.message;

            if (!position) {
                field.$wrapper.html(`
                    <div class="text-danger" style="padding: 8px 0;">
                        ${__(
                            "Unable to load Physical Receipt Position."
                        )}
                    </div>
                `);
                return;
            }

            const render_position = debit_note_state => {
                field.$wrapper.html(
                    build_physical_receipt_position_html(
                        frm,
                        position,
                        debit_note_state
                    )
                );
            };

            if (!frm.doc.debit_note) {
                render_position(null);
                return;
            }

            frappe.db.get_value(
                "Purchase Invoice",
                frm.doc.debit_note,
                [
                    "docstatus",
                    "status"
                ]
            ).then(r => {
                render_position(r.message || null);
            });
        },
        error() {
            field.$wrapper.html(`
                <div class="text-danger" style="padding: 8px 0;">
                    ${__(
                        "Unable to load Physical Receipt Position."
                    )}
                </div>
            `);
        }
    });
}

/**
 * Build the Physical Receipt Position panel from server-derived facts.
 */
function build_physical_receipt_position_html(
    frm,
    position,
    debit_note_state
) {
    const expected_qty = flt(
        position.expected_qty
    );

    const accepted_qty = flt(
        position.company_accepted_qty
    );

    const physical_balance_qty = flt(
        position.physical_balance_qty
    );

    const physical_credit_applied_qty = flt(
        position.physical_credit_applied_qty
    );

    const net_physical_balance_qty = flt(
        position.net_physical_balance_qty
    );

    const debit_note_is_submitted = Boolean(
        frm.doc.debit_note
        && debit_note_state?.docstatus === 1
    );

    const stock_uom = frappe.utils.escape_html(
        position.stock_uom || ""
    );

    const processed_item = frappe.utils.escape_html(
        position.processed_item || ""
    );

    const receipt_count = Number(
        frm.doc.receipt_count || 0
    );

    const last_receipt_date = frm.doc.last_receipt_date
        ? frappe.datetime.str_to_user(
            frm.doc.last_receipt_date
        )
        : "";

    const format_display_qty = value => {
        const number = flt(value);

        const precision = Number.isInteger(number)
            ? 0
            : 3;

        return format_number(
            number,
            null,
            precision
        );
    };

    const format_qty_with_uom = value => {
        return [
            format_display_qty(value),
            stock_uom
        ].filter(Boolean).join(" ");
    };

    const last_receipt_display = last_receipt_date
        ? frappe.utils.escape_html(last_receipt_date)
        : __("No receipt recorded");

    let status_label = __("Short Received");
    let status_class = "physical-position-status-warning";
    let balance_class = "physical-position-value-warning";
    let balance_label = __("Short Received");

    const awaiting_first_receipt = Boolean(
        receipt_count === 0
        && accepted_qty <= 0
        && expected_qty > 0
    );

    if (awaiting_first_receipt) {
        status_label = __("Awaiting First Receipt");
        status_class = "physical-position-status-warning";
        balance_class = "physical-position-value-warning";
        balance_label = __("Awaiting Receipt");
    } else if (
        physical_credit_applied_qty > 0
        && net_physical_balance_qty <= 0
    ) {
        status_label = __(
            "Covered by Material Credit"
        );
        status_class = "physical-position-status-good";
        balance_class = "physical-position-value-good";
        balance_label = __("Net Physical Balance");
    } else if (
        receipt_count > 0
        && accepted_qty > 0
        && net_physical_balance_qty > 0
    ) {
        status_label = __("Receiving in Progress");
        status_class = "physical-position-status-warning";
        balance_class = "physical-position-value-warning";
        balance_label = physical_credit_applied_qty > 0
            ? __("Net Awaiting Settlement")
            : __("Awaiting Receipt");
    } else if (position.position_status === "Complete") {
        status_label = __("Complete");
        status_class = "physical-position-status-good";
        balance_class = "physical-position-value-good";
        balance_label = __("Physical Balance");
    } else if (
        position.position_status === "Excess Accepted"
    ) {
        status_label = __("Excess Accepted");
        status_class = "physical-position-status-danger";
        balance_class = "physical-position-value-danger";
        balance_label = __("Excess Accepted");
    }

    if (
        debit_note_is_submitted
        && net_physical_balance_qty > 0
    ) {
        balance_class = "physical-position-value-settled";
    }

    const receipt_word = receipt_count === 1
        ? __("receipt")
        : __("receipts");

    const physical_credit_card = physical_credit_applied_qty > 0
        ? `
            <div class="physical-position-card">
                <div class="physical-position-label">
                    ${__("Submitted Physical Credit")}
                </div>

                <div class="physical-position-value physical-position-value-good">
                    ${format_qty_with_uom(
                        physical_credit_applied_qty
                    )}
                </div>
            </div>
        `
        : "";

    return `
        <div class="processor-lot-physical-position">
            <style>
                .processor-lot-physical-position {
                    border: 1px solid var(--border-color);
                    border-radius: 8px;
                    overflow: hidden;
                    background: var(--card-bg);
                    margin: 4px 0 14px;
                }

                .processor-lot-physical-position
                .physical-position-header {
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    gap: 12px;

                    padding: 10px 14px;

                    border-bottom:
                        1px solid var(--border-color);

                    background: var(--subtle-fg);
                }

                .processor-lot-physical-position
                .physical-position-basis {
                    color: var(--text-muted);
                    font-size: 11px;
                    line-height: 1.35;
                }

                .processor-lot-physical-position
                .physical-position-status {
                    display: inline-flex;
                    align-items: center;
                    gap: 6px;

                    border: 1px solid var(--border-color);
                    border-radius: 999px;

                    padding: 4px 9px;

                    background: var(--fg-color);

                    font-size: 12px;
                    font-weight: 600;
                    white-space: nowrap;
                }

                .processor-lot-physical-position
                .physical-position-dot {
                    width: 9px;
                    height: 9px;
                    border-radius: 50%;
                    display: inline-block;
                    flex: 0 0 auto;
                }

                .processor-lot-physical-position
                .physical-position-status-good {
                    color: var(--green-700);
                }

                .processor-lot-physical-position
                .physical-position-status-good
                .physical-position-dot {
                    background: var(--green-500);
                }

                .processor-lot-physical-position
                .physical-position-status-warning {
                    color: var(--yellow-700);
                }

                .processor-lot-physical-position
                .physical-position-status-warning
                .physical-position-dot {
                    background: var(--yellow-500);
                }

                .processor-lot-physical-position
                .physical-position-status-danger {
                    color: var(--red-700);
                }

                .processor-lot-physical-position
                .physical-position-status-danger
                .physical-position-dot {
                    background: var(--red-500);
                }

                .processor-lot-physical-position
                .physical-position-content {
                    padding: 10px;
                }

                .processor-lot-physical-position
                .physical-position-item {
                    color: var(--text-muted);
                    font-size: 11px;
                    margin: 0 2px 8px;
                }

                .processor-lot-physical-position
                .physical-position-grid {
                    display: grid;
                    grid-template-columns:
                        repeat(4, minmax(0, 1fr));
                    gap: 8px;
                }

                .processor-lot-physical-position
                .physical-position-card {
                    border: 1px solid var(--border-color);
                    border-radius: 8px;

                    padding: 10px 11px;

                    background: var(--fg-color);
                    min-height: 72px;
                }

                .processor-lot-physical-position
                .physical-position-card-emphasis {
                    border-width: 2px;
                }

                .processor-lot-physical-position
                .physical-position-label {
                    color: var(--text-muted);
                    font-size: 11px;
                    margin-bottom: 4px;
                }

                .processor-lot-physical-position
                .physical-position-value {
                    font-size: 18px;
                    font-weight: 600;
                    line-height: 1.25;
                    white-space: nowrap;
                }

                .processor-lot-physical-position
                .physical-position-value-balance {
                    font-size: 20px;
                    font-weight: 700;
                }

                .processor-lot-physical-position
                .physical-position-value-good {
                    color: var(--green-700);
                }

                .processor-lot-physical-position
                .physical-position-value-warning {
                    color: var(--yellow-700);
                }

                .processor-lot-physical-position
                .physical-position-value-settled {
                    color: var(--text-color);
                }

                .processor-lot-physical-position
                .physical-position-value-danger {
                    color: var(--red-700);
                }

                @media (max-width: 991px) {
                    .processor-lot-physical-position
                    .physical-position-grid {
                        grid-template-columns:
                            repeat(2, minmax(0, 1fr));
                    }
                }

                @media (max-width: 767px) {
                    .processor-lot-physical-position
                    .physical-position-header {
                        align-items: flex-start;
                        flex-direction: column;
                        justify-content: flex-start;
                        gap: 6px;
                    }

                    .processor-lot-physical-position
                    .physical-position-grid {
                        grid-template-columns: 1fr;
                    }
                }
            </style>

            <div class="physical-position-header">
                <div class="physical-position-basis">
                    ${__(
                        "Physical position derived from {0} recorded Processor Lot {1}.",
                        [
                            receipt_count,
                            receipt_word
                        ]
                    )}
                </div>

                <div class="
                    physical-position-status
                    ${status_class}
                ">
                    <span class="physical-position-dot"></span>
                    <span>${status_label}</span>
                </div>
            </div>

            <div class="physical-position-content">
                <div class="physical-position-item">
                    ${__("Processed Item")}:
                    <strong>${processed_item || "—"}</strong>
                </div>

                <div class="physical-position-grid">
                    <div class="physical-position-card">
                        <div class="physical-position-label">
                            ${__("Expected from Processor")}
                        </div>

                        <div class="physical-position-value">
                            ${format_qty_with_uom(expected_qty)}
                        </div>
                    </div>

                    <div class="physical-position-card">
                        <div class="physical-position-label">
                            ${__("Company Accepted")}
                        </div>

                        <div class="physical-position-value">
                            ${format_qty_with_uom(accepted_qty)}
                        </div>
                    </div>

                    ${physical_credit_card}

                    <div class="
                        physical-position-card
                        physical-position-card-emphasis
                    ">
                        <div class="physical-position-label">
                            ${balance_label}
                        </div>

                        <div class="
                            physical-position-value
                            physical-position-value-balance
                            ${balance_class}
                        ">
                            ${format_qty_with_uom(
                                net_physical_balance_qty
                            )}
                        </div>
                    </div>

                    <div class="physical-position-card">
                        <div class="physical-position-label">
                            ${__("Last Physical Receipt")}
                        </div>

                        <div class="physical-position-value">
                            ${last_receipt_display}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `;
}

/**
 * Render the live Receipt Journey for all truck receipts in this lot.
 *
 * The journey is derived from linked PLR, SCR, PR and PI documents.
 * It does not save or modify any document.
 */
function render_receipt_journey(frm) {
    const field = frm.get_field("receipt_journey_html");

    if (!field || !field.$wrapper) {
        return;
    }

    if (frm.is_new() || !frm.doc.name) {
        field.$wrapper.html(`
            <div class="text-muted" style="padding: 8px 0;">
                ${__("Save the Processor Lot to view its Receipt Journey.")}
            </div>
        `);
        return;
    }

    field.$wrapper.html(`
        <div class="text-muted" style="padding: 8px 0;">
            ${__("Loading Receipt Journey...")}
        </div>
    `);

    frappe.call({
        method: [
            "subcontracting_extensions",
            "subcontracting_extensions",
            "doctype",
            "processor_lot",
            "processor_lot",
            "get_processor_lot_receipt_journey"
        ].join("."),
        args: {
            processor_lot: frm.doc.name
        },
        callback(r) {
            const journey = r.message || {};

            field.$wrapper.html(
                build_receipt_journey_html(journey)
            );

            bind_receipt_journey_links(field);
        },
        error() {
            field.$wrapper.html(`
                <div class="text-danger" style="padding: 8px 0;">
                    ${__("Unable to load Receipt Journey.")}
                </div>
            `);
        }
    });
}


/**
 * Build the Receipt Journey table from server-derived document states.
 */
function build_receipt_journey_html(journey) {
    const summary = journey.summary || {};
    const receipts = journey.receipts || [];

    if (!receipts.length) {
        return `
            <div class="receipt-journey-empty">
                ${__(
                    "No truck receipts have yet been recorded for this Processor Lot."
                )}
            </div>
        `;
    }

    const receipt_count = Number(summary.receipt_count || 0);
    const completed_count = Number(summary.completed_count || 0);
    const pending_count = Number(summary.pending_count || 0);

    const rows = receipts.map(receipt => {

        const qty = format_receipt_journey_qty(
            receipt.company_accepted_qty,
            receipt.stock_uom
        );

        const material_credit_html = (
            build_receipt_journey_material_credit(receipt)
        );

        const commercial_variance_html = (
            build_receipt_journey_commercial_variance(receipt)
        );

        const has_material_credit = (
            flt(receipt.processor_material_credit_qty) > 0
        );

        const receipt_row_class = has_material_credit
            ? "receipt-journey-material-credit-closure"
            : (
                receipt.has_commercial_qty_variance
                    ? "receipt-journey-controlled-variance"
                    : ""
            );

        return `
            <tr class="${receipt_row_class}">
                <td>
                    ${build_receipt_journey_document_link(
                        "Processor Lot Receipt",
                        receipt.processor_lot_receipt
                    )}

                    ${
                        receipt.receipt_date
                            ? `
                                <span class="receipt-journey-receipt-date">
                                    ${format_receipt_journey_date(
                                        receipt.receipt_date
                                    )}
                                </span>
                            `
                            : ""
                    }

                    ${material_credit_html}
                </td>

                <td class="receipt-journey-qty">
                    <span class="
                        receipt-journey-qty-value
                        receipt-journey-qty-${escape_receipt_journey_html(
                            receipt.journey_state || "warning"
                        )}
                    ">
                        ${qty}
                    </span>
                </td>

                <td>
                    ${build_receipt_journey_document_cell(
                        receipt.subcontracting_receipt
                    )}
                </td>

                <td>
                    ${build_receipt_journey_document_cell(
                        receipt.purchase_receipt
                    )}

                    ${commercial_variance_html}
                </td>

                <td>
                    ${build_receipt_journey_document_cell(
                        receipt.purchase_invoice
                    )}
                </td>

                <td>
                    <span class="
                        receipt-journey-stage
                        receipt-journey-stage-${escape_receipt_journey_html(
                            receipt.journey_state || "warning"
                        )}
                    ">
                        ${escape_receipt_journey_html(
                            receipt.current_stage || ""
                        )}
                    </span>
                </td>

                <td class="receipt-journey-next-action">
                    ${escape_receipt_journey_html(
                        receipt.next_action || ""
                    )}
                </td>
            </tr>
        `;
    }).join("");

    return `
        <div class="processor-lot-receipt-journey">
            <style>
                .processor-lot-receipt-journey {
                    border: 1px solid var(--border-color);
                    border-radius: 8px;
                    overflow: hidden;
                    background: var(--card-bg);
                }

                .processor-lot-receipt-journey
                .receipt-journey-summary {
                    display: flex;
                    flex-wrap: wrap;
                    gap: 18px;
                    padding: 12px 14px;
                    border-bottom: 1px solid var(--border-color);
                    background: var(--subtle-fg);
                }

                .processor-lot-receipt-journey
                .receipt-journey-summary-item {
                    display: flex;
                    gap: 6px;
                    align-items: baseline;
                }

                .processor-lot-receipt-journey
                .receipt-journey-summary-label {
                    color: var(--text-muted);
                    font-size: 12px;
                }

                .processor-lot-receipt-journey
                .receipt-journey-summary-value {
                    font-weight: 600;
                }

                .processor-lot-receipt-journey
                .receipt-journey-table-wrapper {
                    overflow-x: auto;
                }

                .processor-lot-receipt-journey table {
                    width: 100%;
                    border-collapse: collapse;
                    margin: 0;
                }

                .processor-lot-receipt-journey th,
                .processor-lot-receipt-journey td {
                    padding: 10px 12px;
                    border-bottom: 1px solid var(--border-color);
                    text-align: left;
                    vertical-align: middle;
                    white-space: nowrap;
                }

                .processor-lot-receipt-journey tbody td {
                    vertical-align: top;
                }

                .processor-lot-receipt-journey th {
                    color: var(--text-muted);
                    font-size: 12px;
                    font-weight: 600;
                    background: var(--control-bg);
                }

                .processor-lot-receipt-journey tbody tr:last-child td {
                    border-bottom: 0;
                }

                .processor-lot-receipt-journey
                tbody tr.receipt-journey-controlled-variance td:first-child {
                    box-shadow: inset 3px 0 0 var(--purple-500);
                }

                                .processor-lot-receipt-journey
                tbody tr.receipt-journey-material-credit-closure td {
                    background: var(--blue-50);
                }

                .processor-lot-receipt-journey
                tbody tr.receipt-journey-material-credit-closure td:first-child {
                    box-shadow: inset 4px 0 0 var(--blue-500);
                }

                .processor-lot-receipt-journey
                .receipt-journey-document {
                    display: inline-block;
                }

                .processor-lot-receipt-journey
                .receipt-journey-document-line {
                    display: flex;
                    align-items: center;
                    gap: 5px;
                }

                .processor-lot-receipt-journey
                .receipt-journey-document-symbol {
                    flex: 0 0 auto;
                    font-size: 11px;
                    line-height: 1;
                }

                .processor-lot-receipt-journey
                .receipt-journey-document-link {
                    color: var(--primary);
                    font-weight: 600;
                    text-decoration: underline;
                    text-underline-offset: 2px;
                }

                .processor-lot-receipt-journey
                .receipt-journey-document-link:hover {
                    color: var(--primary);
                    text-decoration-thickness: 2px;
                }

                .processor-lot-receipt-journey
                .receipt-journey-document-missing {
                    font-size: 11px;
                }

                .processor-lot-receipt-journey
                .receipt-journey-receipt-date,
                .processor-lot-receipt-journey
                .receipt-journey-document-date {
                    display: block;
                    margin-top: 2px;
                    color: var(--text-muted);
                    font-size: 10px;
                    font-weight: 400;
                    line-height: 1.2;
                }

                .processor-lot-receipt-journey
                .receipt-journey-document-date {
                    margin-left: 16px;
                }

                .processor-lot-receipt-journey
                .receipt-journey-document-status {
                    display: block;
                    margin-top: 3px;
                    margin-left: 16px;
                    font-size: 11px;
                }

                .processor-lot-receipt-journey
                .receipt-journey-material-credit {
                    display: block;
                    width: max-content;
                    max-width: 190px;
                    margin-top: 6px;
                    padding: 3px 7px;
                    border-radius: 6px;
                    font-size: 10px;
                    font-weight: 600;
                    line-height: 1.35;
                    white-space: normal;
                }

                .processor-lot-receipt-journey
                .receipt-journey-material-credit-recorded {
                    color: var(--blue-700);
                    background: var(--blue-100);
                }

                .processor-lot-receipt-journey
                .receipt-journey-material-credit-attention {
                    color: var(--orange-700);
                    background: var(--orange-100);
                }

                                .processor-lot-receipt-journey
                .receipt-journey-lot-closure {
                    display: block;
                    width: max-content;
                    margin-top: 6px;
                    padding: 2px 7px;
                    border-radius: 999px;
                    color: var(--blue-800);
                    background: var(--blue-200);
                    font-size: 9px;
                    font-weight: 700;
                    letter-spacing: 0.04em;
                    line-height: 1.35;
                }

                .processor-lot-receipt-journey
                .receipt-journey-commercial-variance {
                    display: block;
                    width: max-content;
                    max-width: 190px;
                    margin-top: 6px;
                    padding: 3px 7px;
                    border-radius: 6px;
                    color: var(--purple-700);
                    background: var(--purple-100);
                    font-size: 10px;
                    font-weight: 600;
                    line-height: 1.35;
                    white-space: normal;
                }

                .processor-lot-receipt-journey
                .receipt-journey-commercial-detail {
                    display: block;
                    margin-top: 2px;
                    font-weight: 400;
                }

                .processor-lot-receipt-journey
                .receipt-journey-state-submitted {
                    color: var(--green-600);
                }

                .processor-lot-receipt-journey
                .receipt-journey-state-draft {
                    color: var(--orange-600);
                }

                .processor-lot-receipt-journey
                .receipt-journey-state-cancelled,
                .processor-lot-receipt-journey
                .receipt-journey-state-broken_link {
                    color: var(--red-600);
                }

                .processor-lot-receipt-journey
                .receipt-journey-state-not_created {
                    color: var(--text-muted);
                }

                .processor-lot-receipt-journey
                .receipt-journey-stage {
                    display: inline-block;
                    padding: 3px 8px;
                    border-radius: 999px;
                    font-size: 11px;
                    font-weight: 600;
                }

                .processor-lot-receipt-journey
                .receipt-journey-stage-good {
                    color: var(--green-700);
                    background: var(--green-100);
                }

                .processor-lot-receipt-journey
                .receipt-journey-stage-warning {
                    color: var(--orange-700);
                    background: var(--orange-100);
                }

                .processor-lot-receipt-journey
                .receipt-journey-stage-danger {
                    color: var(--red-700);
                    background: var(--red-100);
                }

                .processor-lot-receipt-journey
                .receipt-journey-next-action {
                    white-space: normal;
                    min-width: 170px;
                }

                .processor-lot-receipt-journey
                .receipt-journey-qty {
                    text-align: right;
                }

                .processor-lot-receipt-journey
                .receipt-journey-qty-value {
                    font-weight: 600;
                }

                .processor-lot-receipt-journey
                .receipt-journey-qty-warning {
                    color: var(--orange-600);
                }

                .processor-lot-receipt-journey
                .receipt-journey-qty-good {
                    color: var(--green-600);
                }

                .processor-lot-receipt-journey
                .receipt-journey-qty-danger {
                    color: var(--red-600);
                }

                .receipt-journey-empty {
                    padding: 12px 0;
                    color: var(--text-muted);
                }
            </style>

            <div class="receipt-journey-summary">
                <div class="receipt-journey-summary-item">
                    <span class="receipt-journey-summary-label">
                        ${__("Truck Receipts")}
                    </span>
                    <span class="receipt-journey-summary-value">
                        ${receipt_count}
                    </span>
                </div>

                <div class="receipt-journey-summary-item">
                    <span class="receipt-journey-summary-label">
                        ${__("Commercially Complete")}
                    </span>
                    <span class="receipt-journey-summary-value">
                        ${completed_count}
                    </span>
                </div>

                <div class="receipt-journey-summary-item">
                    <span class="receipt-journey-summary-label">
                        ${__("Pending")}
                    </span>
                    <span class="receipt-journey-summary-value">
                        ${pending_count}
                    </span>
                </div>
            </div>

            <div class="receipt-journey-table-wrapper">
                <table>
                    <thead>
                        <tr>
                            <th>${__("Processor Lot Receipt")}</th>
                            <th>${__("Lot-Backed Qty")}</th>
                            <th>${__("Subcontracting Receipt")}</th>
                            <th>${__("Purchase Receipt")}</th>
                            <th>${__("Purchase Invoice")}</th>
                            <th>${__("Current Stage")}</th>
                            <th>${__("Next Step")}</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${rows}
                    </tbody>
                </table>
            </div>
        </div>
    `;
}


/**
 * Show a controlled Processor Material Credit on its originating PLR.
 */
function build_receipt_journey_material_credit(receipt) {
    const credit_qty = flt(
        receipt.processor_material_credit_qty
    );

    if (credit_qty <= 0) {
        return "";
    }

    const status = (
        receipt.material_credit_status
        || __("Pending")
    );

    const status_class = (
        status === "Recorded"
            ? "recorded"
            : "attention"
    );

    return `
        <span class="receipt-journey-lot-closure">
            ${__("Lot Closure")}
        </span>

        <span class="
            receipt-journey-material-credit
            receipt-journey-material-credit-${status_class}
        ">
            ${__("Material Credit")}
            ${format_receipt_journey_qty(
                credit_qty,
                receipt.stock_uom
            )}
            ·
            ${escape_receipt_journey_html(status)}
        </span>
    `;
}


/**
 * Show the deliberate difference between SCR physical quantity and the
 * supplier-invoice quantity mapped to the Purchase Receipt.
 */
function build_receipt_journey_commercial_variance(receipt) {
    if (!receipt.has_commercial_qty_variance) {
        return "";
    }

    const variance = flt(
        receipt.commercial_qty_variance
    );

    const variance_sign = variance > 0 ? "+" : "−";

    const variance_qty = format_receipt_journey_qty(
        Math.abs(variance),
        receipt.stock_uom
    );

    const commercial_qty = format_receipt_journey_qty(
        receipt.supplier_invoice_qty,
        receipt.stock_uom
    );

    const physical_qty = format_receipt_journey_qty(
        receipt.company_accepted_qty,
        receipt.stock_uom
    );

    return `
        <span class="receipt-journey-commercial-variance">
            ${__("Commercial Qty")}
            ${variance_sign}${variance_qty}

            <span class="receipt-journey-commercial-detail">
                ${commercial_qty}
                ${__("vs")}
                ${physical_qty}
            </span>
        </span>
    `;
}


/**
 * Build one linked-document cell with its current status.
 */
function build_receipt_journey_document_cell(document_state) {
    const state = document_state || {};
    const state_name = state.state || "not_created";
    const safe_state_name = escape_receipt_journey_html(state_name);
    const label = escape_receipt_journey_html(
        state.label || __("Not Created")
    );
    const posting_date_html = state.posting_date
        ? `
            <span class="receipt-journey-document-date">
                ${format_receipt_journey_date(state.posting_date)}
            </span>
        `
        : "";

    if (!state.name) {
        return `
            <span class="
                receipt-journey-document
                receipt-journey-state-${safe_state_name}
            ">
                <span class="receipt-journey-document-line">
                    <span class="receipt-journey-document-symbol">
                        ○
                    </span>

                    <span class="receipt-journey-document-missing">
                        ${__("Not Created")}
                    </span>
                </span>
            </span>
        `;
    }

    return `
        <span class="
            receipt-journey-document
            receipt-journey-state-${safe_state_name}
        ">
            <span class="receipt-journey-document-line">
                <span class="receipt-journey-document-symbol">
                    ${get_receipt_journey_status_symbol(state_name)}
                </span>

                ${build_receipt_journey_document_link(
                    state.doctype,
                    state.name
                )}
            </span>

            ${posting_date_html}

            <span class="receipt-journey-document-status">
                ${label}
            </span>
        </span>
    `;
}


/**
 * Build a clickable document link.
 */
function build_receipt_journey_document_link(doctype, name) {
    if (!doctype || !name) {
        return "—";
    }

    return `
        <a
            href="#"
            class="receipt-journey-document-link"
            data-doctype="${escape_receipt_journey_html(doctype)}"
            data-name="${escape_receipt_journey_html(name)}"
        >
            ${escape_receipt_journey_html(name)}
        </a>
    `;
}


/**
 * Route Receipt Journey links to the selected document.
 */
function bind_receipt_journey_links(field) {
    field.$wrapper
        .find(".receipt-journey-document-link")
        .off("click.processor_lot_receipt_journey")
        .on("click.processor_lot_receipt_journey", event => {
            event.preventDefault();

            const link = $(event.currentTarget);
            const doctype = link.data("doctype");
            const name = link.data("name");

            if (doctype && name) {
                frappe.set_route("Form", doctype, name);
            }
        });
}


/**
 * Return a compact marker for a linked-document state.
 */
function get_receipt_journey_status_symbol(state) {
    if (state === "submitted") {
        return "✓";
    }

    if (state === "draft") {
        return "●";
    }

    if (
        state === "cancelled"
        || state === "broken_link"
    ) {
        return "✕";
    }

    return "—";
}

/**
 * Format a document date for compact Journey display.
 */
function format_receipt_journey_date(receipt_date) {
    if (!receipt_date) {
        return "";
    }

    return escape_receipt_journey_html(
        frappe.datetime.str_to_user(
            receipt_date
        )
    );
}

/**
 * Format the company-accepted receipt quantity.
 */
function format_receipt_journey_qty(qty, uom) {
    const formatted_qty = format_number(
        flt(qty),
        null,
        3
    );

    return [
        formatted_qty,
        escape_receipt_journey_html(uom || "")
    ].filter(Boolean).join(" ");
}


/**
 * Escape values before inserting them into generated HTML.
 */
function escape_receipt_journey_html(value) {
    return frappe.utils.escape_html(
        String(value ?? "")
    );
}

/**
 * Render the read-only ERP Lot Health Panel.
 *
 * The panel consumes the existing Fact Engine output. It does not save or
 * modify the Processor Lot or any linked ERPNext document.
 */
function render_health_panel(frm) {
    const field = frm.get_field("health_panel_html");

    if (!field || !field.$wrapper) {
        return;
    }

    if (!frm.doc.subcontracting_order) {
        field.$wrapper.html(`
            <div class="text-muted" style="padding: 8px 0;">
                ${__(
                    "Select a Subcontracting Order to view the current lot health."
                )}
            </div>
        `);
        return;
    }

    field.$wrapper.html(`
        <div class="text-muted" style="padding: 8px 0;">
            ${__("Loading current lot position...")}
        </div>
    `);

    frappe.call({
        method: [
            "subcontracting_extensions",
            "subcontracting_extensions",
            "doctype",
            "processor_lot",
            "processor_lot",
            "get_processor_lot_fact_summary"
        ].join("."),
        args: {
            subcontracting_order: frm.doc.subcontracting_order,
            processor_lot: frm.doc.name
        },
        callback(r) {
            const facts = r.message;

            if (!facts) {
                field.$wrapper.html(`
                    <div class="text-danger" style="padding: 8px 0;">
                        ${__("Unable to load ERP Lot Health.")}
                    </div>
                `);
                return;
            }

            frappe.call({
                method: [
                    "subcontracting_extensions",
                    "subcontracting_extensions",
                    "doctype",
                    "processor_lot",
                    "processor_lot",
                    "get_processor_lot_lifecycle_state"
                ].join("."),
                args: {
                    processor_lot: frm.doc.name
                },
                callback(lifecycle_response) {
                    const lifecycle =
                        lifecycle_response.message || {};

                    /*
                     * Render the panel and then bind its action link.
                     *
                     * Binding must happen after the HTML has been inserted
                     * because, when a Debit Note exists, its status is fetched
                     * asynchronously.
                     */
                    const render_panel = debit_note_state => {
                        field.$wrapper.html(
                            build_health_panel_html(
                                frm,
                                facts,
                                lifecycle,
                                debit_note_state
                            )
                        );

                        field.$wrapper
                            .find(
                                '[data-action="processor-lot-next-step"]'
                            )
                            .off("click.processor_lot")
                            .on(
                                "click.processor_lot",
                                event => {
                                    event.preventDefault();

                                    const next_action = $(
                                        event.currentTarget
                                    ).data("next-action");

                                    switch (next_action) {
                                        case "transfer-material":
                                            if (
                                                !frm.doc
                                                    .subcontracting_order
                                            ) {
                                                frappe.msgprint({
                                                    title: __(
                                                        "Subcontracting Order Missing"
                                                    ),
                                                    indicator: "red",
                                                    message: __(
                                                        "No Subcontracting Order is linked to this Processor Lot."
                                                    )
                                                });
                                                return;
                                            }

                                            frappe.set_route(
                                                "Form",
                                                "Subcontracting Order",
                                                frm.doc
                                                    .subcontracting_order
                                            );
                                            return;

                                        case "receive-first-truck":
                                            frappe.new_doc(
                                                "Processor Lot Receipt",
                                                {
                                                    processor_lot:
                                                        frm.doc.name,
                                                    physical_receipt_date:
                                                        frappe.datetime
                                                            .get_today()
                                                }
                                            );
                                            return;

                                        case "receive-next-truck":
                                            frappe.new_doc(
                                                "Processor Lot Receipt",
                                                {
                                                    processor_lot:
                                                        frm.doc.name,
                                                    physical_receipt_date:
                                                        frappe.datetime
                                                            .get_today()
                                                }
                                            );
                                            return;

                                        case "reconcile":
                                            open_reconciliation_wizard(
                                                frm,
                                                facts
                                            );
                                            return;

                                        case "complete-reopened":
                                            complete_reopened_settlement(frm);
                                            return;

                                        case "view-debit-note":
                                        case "submit-debit-note":
                                            if (!frm.doc.debit_note) {
                                                frappe.msgprint({
                                                    title: __(
                                                        "Debit Note Missing"
                                                    ),
                                                    indicator: "red",
                                                    message: __(
                                                        "No Debit Note is linked to this Processor Lot."
                                                    )
                                                });
                                                return;
                                            }

                                            /*
                                             * A Draft Debit Note must be
                                             * reviewed and submitted from its
                                             * own Purchase Invoice form.
                                             */
                                            frappe.set_route(
                                                "Form",
                                                "Purchase Invoice",
                                                frm.doc.debit_note
                                            );
                                            return;

                                        case "submit-processor-lot":
                                            if (
                                                frm.doc.docstatus !== 0
                                            ) {
                                                frappe.show_alert({
                                                    message: __(
                                                        "This Processor Lot is no longer in Draft status."
                                                    ),
                                                    indicator: "blue"
                                                });
                                                return;
                                            }

                                            frappe.confirm(
                                                __(
                                                    "Submit this Processor Lot and close its reconciliation lifecycle?"
                                                ),
                                                () => {
                                                    frm.save("Submit");
                                                }
                                            );
                                            return;

                                        case "review-receipt-journey": {
                                            const journey_field = frm.get_field(
                                                "receipt_journey_html"
                                            );

                                            if (
                                                journey_field
                                                && journey_field.$wrapper
                                                && journey_field.$wrapper.length
                                            ) {
                                                const journey_top =
                                                    journey_field.$wrapper.offset()?.top;

                                                if (journey_top !== undefined) {
                                                    $("html, body").animate(
                                                        {
                                                            scrollTop:
                                                                journey_top - 120
                                                        },
                                                        250
                                                    );
                                                }
                                            }

                                            return;
                                        }

                                        default:
                                            frappe.msgprint({
                                                title: __(
                                                    "Next Step Unavailable"
                                                ),
                                                indicator: "orange",
                                                message: __(
                                                    "The requested Processor Lot action is not currently supported: {0}",
                                                    [
                                                        frappe.utils
                                                            .escape_html(
                                                                next_action
                                                                || __(
                                                                    "Unknown"
                                                                )
                                                            )
                                                    ]
                                                )
                                            });
                                    }
                                }
                            );

                        field.$wrapper
                            .find(".lot-health-document-link")
                            .off(
                                "click.processor_lot_document"
                            )
                            .on(
                                "click.processor_lot_document",
                                event => {
                                    event.preventDefault();

                                    const link = $(
                                        event.currentTarget
                                    );

                                    const doctype = link.data(
                                        "document-type"
                                    );

                                    const name = link.data(
                                        "document-name"
                                    );

                                    if (!doctype || !name) {
                                        return;
                                    }

                                    frappe.set_route(
                                        "Form",
                                        doctype,
                                        name
                                    );
                                }
                            );
                    };

                    /*
                     * No linked Debit Note: render immediately.
                     */
                    if (!frm.doc.debit_note) {
                        render_panel(null);
                        return;
                    }

                    /*
                     * Linked Debit Note: obtain its current state before
                     * rendering the appropriate wording.
                     */
                    frappe.db.get_value(
                        "Purchase Invoice",
                        frm.doc.debit_note,
                        [
                            "docstatus",
                            "status",
                            "base_rounded_total"
                        ]
                    ).then(r => {
                        render_panel(r.message || null);
                    });
                },
                error() {
                    field.$wrapper.html(`
                        <div class="text-danger" style="padding: 8px 0;">
                            ${__(
                                "Unable to load Processor Lot lifecycle."
                            )}
                        </div>
                    `);
                }
            });
        },
        error() {
            field.$wrapper.html(`
                <div class="text-danger" style="padding: 8px 0;">
                    ${__("Unable to load ERP Lot Health.")}
                </div>
            `);
        }
    });
}


/**
 * Build the ERP Lot Health Panel from Fact Engine output.
 *
 * The Fact Engine derives its position from submitted ERPNext documents
 * (Subcontracting Receipts, Purchase Receipts, Purchase Invoices, etc.)
 * and presents the current ERP workflow, integrity condition and
 * recommended next step.
 *
 * Physical receipt facts recorded through Processor Lot Receipts are
 * intentionally presented separately and are not substituted into this
 * ERP-controlled panel.
 */
function build_health_panel_html(frm, facts, lifecycle, debit_note_state) {
    const summary = facts.summary || {};
    const integrity = facts.integrity || {};
    const components = facts.components || [];

    const entrustment = summary.entrustment || {};
    const physical = summary.physical_inventory || {};
    const commercial = summary.commercial || {};

    const lifecycle_state = lifecycle?.state || "";

    const entrusted_component_qty = flt(
        lifecycle?.entrusted_component_qty
    );

    const component_uom =
        lifecycle?.component_uom || "";

    const expected_finished_qty = flt(
        lifecycle?.expected_finished_qty
    );

    const finished_uom =
        lifecycle?.finished_uom || "";

    const lifecycle_receipt_count = Number(
        lifecycle?.receipt_count || 0
    );

    const material_transfer_stock_entries = Array.isArray(
        lifecycle?.material_transfer_stock_entries
    )
        ? lifecycle.material_transfer_stock_entries.filter(Boolean)
        : [];

    const material_transfer_links =
        material_transfer_stock_entries.map(stock_entry => {
            const escaped_stock_entry =
                frappe.utils.escape_html(stock_entry);

            return `
                <a
                    href="#"
                    class="lot-health-document-link"
                    data-document-type="Stock Entry"
                    data-document-name="${escaped_stock_entry}"
                >
                    ${escaped_stock_entry}
                </a>
            `;
        });

    const material_transfer_link_list = (() => {
        if (material_transfer_links.length <= 1) {
            return material_transfer_links[0] || "";
        }

        return `${material_transfer_links
            .slice(0, -1)
            .join(", ")} ${__("and")} ${
            material_transfer_links[
                material_transfer_links.length - 1
            ]
        }`;
    })();

    const warnings = integrity.warnings || [];
    const blocking_errors = integrity.blocking_errors || [];

    /*
    * A linked Debit Note means that the material-shortage action has
    * already been recorded. This does not yet prove that every commercial
    * obligation, such as service-charge recovery, has been settled.
    */
    const debit_note = frm.doc.debit_note || "";

    const debit_note_docstatus =
        debit_note_state?.docstatus;

    const debit_note_is_draft =
        Boolean(debit_note)
        && debit_note_docstatus === 0;

    const debit_note_is_submitted =
        Boolean(debit_note)
        && debit_note_docstatus === 1;

    let variance_value_class =
        debit_note_is_submitted
            ? "lot-health-value-settled"
            : "lot-health-value-warning";

    let variance_label =
        debit_note_is_submitted
            ? __("Settled Variance")
            : __("Variance to Reconcile");

    if (!debit_note_is_submitted) {
        if (
            lifecycle_state === "material_with_processor"
        ) {
            variance_label = __(
                "Outstanding with Processor"
            );
        } else if (
            lifecycle_state === "receiving_in_progress"
        ) {
            variance_label = __(
                "Pending ERP Processing"
            );
        }
    }

    const debit_note_is_cancelled =
        Boolean(debit_note)
        && debit_note_docstatus === 2;

    const closed_sco_is_expected = Boolean(
        frm.doc.docstatus === 1
        && [
            "Completed",
            "Reopened",
            "Reopened - Debit Note Created"
        ].includes(frm.doc.settlement_status)
    );

    const actionable_warnings = warnings.filter(warning => {
        if (
            debit_note_is_submitted
            && warning.code
                === "OUTSTANDING_EXCEEDS_WAREHOUSE_BALANCE"
        ) {
            return false;
        }

        if (
            closed_sco_is_expected
            && warning.code === "SCO_CLOSED"
        ) {
            return false;
        }

        return true;
    });

    /*
     * Version 1 supports one principal component/UOM per Processor Lot.
     * Fall back to a blank value if the Fact Engine has no component row.
     */
    const stock_uom = components.length
        ? components[0].stock_uom || ""
        : "";

    /**
     * Show whole numbers without decimals.
     *
     * Examples:
     * 3000      -> 3,000
     * 2994.25   -> 2,994.250
     */
    const format_display_qty = value => {
        const number = flt(value);
        const precision = Number.isInteger(number) ? 0 : 3;

        return format_number(number, null, precision);
    };

    const format_qty_with_uom = (value, uom) => {
        const formatted_qty = format_display_qty(value);
        const escaped_uom =
            frappe.utils.escape_html(uom || "");

        return escaped_uom
            ? `${formatted_qty} ${escaped_uom}`
            : formatted_qty;
    };

    const entrusted_qty = flt(
        entrustment.sco_supplied_qty
    );

    const received_qty = flt(
        physical.scr_received_qty
    );

    const outstanding_qty = flt(
        physical.outstanding_qty
    );

    const invoice_qty = flt(
        commercial.purchase_invoice_qty
    );

    const component_credit_applied_qty = components.reduce(
        (total, component) => {
            return total + flt(
                component.credit_applied_qty
            );
        },
        0
    );

    const net_outstanding_qty = Math.max(
        outstanding_qty - component_credit_applied_qty,
        0
    );

    if (component_credit_applied_qty > 0) {
        variance_label = __("Net Physical Shortage");
        variance_value_class = net_outstanding_qty <= 0
            ? "lot-health-value-settled"
            : "lot-health-value-warning";
    }

    const physical_credit_applied_qty = flt(
        lifecycle?.physical_credit_applied_qty
    );

    const commercial_credit_applied_qty = flt(
        lifecycle?.commercial_credit_applied_qty
    );

    const advance_credit_processed_qty = flt(
        lifecycle?.advance_credit_processed_qty
    );

    const advance_credit_commercial_qty = flt(
        lifecycle?.advance_credit_commercial_qty
    );

    const gross_commercial_variance_qty = Math.max(
        invoice_qty
        - received_qty
        - advance_credit_commercial_qty,
        0
    );

    const commercial_variance_remaining_qty = Math.max(
        gross_commercial_variance_qty
        - commercial_credit_applied_qty,
        0
    );

    const processing_recovery_required = Boolean(
        frm.doc.recover_processing_charges_on_shortage
    );


    /*
    * A newly created Processor Lot may exist before any raw material has
    * actually been transferred to the supplier warehouse.
    *
    * This is a valid opening stage of the subcontracting workflow, not a
    * completed or balanced lot.
    */
    const material_not_yet_transferred = Boolean(
        entrusted_qty <= 0
        && received_qty <= 0
        && outstanding_qty <= 0
        && invoice_qty <= 0
        && !frm.doc.receipt_count
    );

    /*
    * Workflow health tells the user what stage of attention the lot requires.
    * Integrity health separately tells the user whether the facts are reliable.
    *
    * A linked material Debit Note acknowledges that the physical variance has
    * already been acted upon. It does not yet mean that all commercial terms,
    * including service-charge recovery, have been assessed.
    */
    let workflow_label = __("Ready for Closure");
    let workflow_class = "lot-health-status-good";
    let workflow_dot_class = "lot-health-dot-good";

    let integrity_label = __("Healthy");
    let integrity_class = "lot-health-status-good";

    let message_class = "lot-health-message-success";
    let message_heading = __("Lot position is balanced");
    let message_text = __(
        "No unresolved component quantity remains against this entrusted lot."
    );

    let next_action = "reconcile";
    let next_action_label = __("Review lot for closure");
    let next_action_detail = __(
        "Verify the linked documents before completing the lot."
    );

    let alternate_action = "";
    let alternate_action_label = "";
    let alternate_action_detail = "";

    let show_next_action = true;

    if (blocking_errors.length) {
        workflow_label = __("Integrity Error");
        workflow_class = "lot-health-status-danger";
        workflow_dot_class = "lot-health-dot-danger";

        integrity_label = __("Action Required");
        integrity_class = "lot-health-status-danger";

        message_class = "lot-health-message-danger";
        message_heading = __("Integrity issue detected");
        message_text = __(
            "The factual position cannot be relied upon until the blocking errors are corrected."
        );

        next_action_label = __("View Lot Facts");
        next_action_detail = __(
            "Review the detailed integrity errors before proceeding."
        );
    } else if (material_not_yet_transferred) {
        workflow_label = __("Awaiting Material Transfer");
        workflow_class = "lot-health-status-warning";
        workflow_dot_class = "lot-health-dot-warning";

        integrity_label = __("Healthy");
        integrity_class = "lot-health-status-good";

        message_class = "lot-health-message-info";
        message_heading = __("Material not yet entrusted");
        message_text = __(
            "This Processor Lot has been created from the submitted Subcontracting Order, but no raw material has yet been transferred to the processor."
        );

        next_action = "transfer-material";
        next_action_label = __("Open Subcontracting Order");
        next_action_detail = __(
            "Transfer the required raw material to the Supplier Warehouse before recording the first truck receipt."
        );
    } else if (lifecycle_state === "material_with_processor") {
        workflow_label = __("Material with Processor");
        workflow_class = "lot-health-status-warning";
        workflow_dot_class = "lot-health-dot-warning";

        integrity_label = __("Healthy");
        integrity_class = "lot-health-status-good";

        message_class = "lot-health-message-info";
        message_heading = __("Material entrusted to processor");
        message_text = material_transfer_link_list
            ? __(
                "{0} were transferred through {1}. No truck receipts have yet been recorded.",
                [
                    format_qty_with_uom(
                        entrusted_component_qty,
                        component_uom
                    ),
                    material_transfer_link_list
                ]
            )
            : __(
                "{0} have been entrusted to the processor. No truck receipts have yet been recorded.",
                [
                    format_qty_with_uom(
                        entrusted_component_qty,
                        component_uom
                    )
                ]
            );

        next_action = "receive-first-truck";
        next_action_label = __("Record First Truck Receipt");
        next_action_detail = __(
            "Create the first Processor Lot Receipt when material returns from the processor."
        );
    } else if (
        lifecycle_state === "receiving_in_progress"
        && !debit_note_is_draft
        && !debit_note_is_submitted
    ) {
        const company_accepted_qty = flt(
            lifecycle?.company_accepted_qty
        );

        const gross_physical_shortage_qty = Math.max(
            expected_finished_qty
            - company_accepted_qty,
            0
        );

        const physically_awaiting_qty = Math.max(
            gross_physical_shortage_qty
            - physical_credit_applied_qty,
            0
        );

        const receipt_word =
            lifecycle_receipt_count === 1
                ? __("receipt")
                : __("receipts");

        /*
         * Physical receipts may be ahead of their ERP document cycle.
         *
         * Once cumulative Supplier Invoice Qty catches up with cumulative
         * Company Accepted Qty, the recorded truck receipts are commercially
         * complete and the user may either receive another truck or begin
         * final reconciliation.
         */
        const erp_receipt_cycle_complete = Boolean(
            lifecycle_receipt_count > 0
            && flt(lifecycle?.pending_receipt_count) === 0
            && flt(lifecycle?.completed_receipt_count)
                === lifecycle_receipt_count
        );

        workflow_label = __("Receiving in Progress");
        workflow_class = "lot-health-status-warning";
        workflow_dot_class = "lot-health-dot-warning";

        integrity_label = __("Healthy");
        integrity_class = "lot-health-status-good";

        message_class = "lot-health-message-info";
        message_heading = __("Truck receipt recorded");

        if (physical_credit_applied_qty > 0) {
            message_heading = __(
                "Processor Material Credit applied"
            );
            message_text = __(
                "{0} were recorded through {1} truck {2}. The gross physical shortage is {3}; submitted material credit covers {4}, leaving a net physical shortage of {5}.",
                [
                    format_qty_with_uom(
                        company_accepted_qty,
                        finished_uom
                    ),
                    lifecycle_receipt_count,
                    receipt_word,
                    format_qty_with_uom(
                        gross_physical_shortage_qty,
                        finished_uom
                    ),
                    format_qty_with_uom(
                        physical_credit_applied_qty,
                        finished_uom
                    ),
                    format_qty_with_uom(
                        physically_awaiting_qty,
                        finished_uom
                    )
                ]
            );
        } else {
            message_text = __(
                "{0} have been recorded through {1} truck {2}. {3} remain to be received.",
                [
                    format_qty_with_uom(
                        company_accepted_qty,
                        finished_uom
                    ),
                    lifecycle_receipt_count,
                    receipt_word,
                    format_qty_with_uom(
                        physically_awaiting_qty,
                        finished_uom
                    )
                ]
            );
        }

        if (
            erp_receipt_cycle_complete
            && physically_awaiting_qty <= 0
        ) {
            alternate_action = "";
            alternate_action_label = "";
            alternate_action_detail = "";

            if (
                commercial_variance_remaining_qty > 0
                && processing_recovery_required
                && frm.doc.docstatus === 0
            ) {
                workflow_label = __(
                    "Commercial Reconciliation Pending"
                );
                workflow_class = "lot-health-status-warning";
                workflow_dot_class = "lot-health-dot-warning";

                message_class = "lot-health-message-warning";
                if (physical_credit_applied_qty > 0) {
                    message_heading = __(
                        "Physical shortage covered by material credit"
                    );
                    message_text = __(
                        "Submitted Processor Material Credit covers the {0} physical shortage. A commercial variance of {1} remains for final recovery.",
                        [
                            format_qty_with_uom(
                                gross_physical_shortage_qty,
                                finished_uom
                            ),
                            format_qty_with_uom(
                                commercial_variance_remaining_qty,
                                finished_uom
                            )
                        ]
                    );
                } else {
                    message_heading = __(
                        "Commercial reconciliation pending"
                    );
                    message_text = __(
                        "The physical receipt cycle is complete. A commercial variance of {0} remains for final recovery.",
                        [
                            format_qty_with_uom(
                                commercial_variance_remaining_qty,
                                finished_uom
                            )
                        ]
                    );
                }

                next_action = "reconcile";
                next_action_label = __(
                    "Begin Final Reconciliation"
                );
                next_action_detail = __(
                    "Create the residual Debit Note for the remaining commercial recovery."
                );
            } else if (
                commercial_variance_remaining_qty > 0
                && frm.doc.docstatus === 0
            ) {
                workflow_label = __("Ready for Closure");
                workflow_class = "lot-health-status-good";
                workflow_dot_class = "lot-health-dot-good";
                message_class = "lot-health-message-success";
                message_heading = frm.doc.override_settlement_policy
                    ? __("Commercial recovery waived")
                    : __("Commercial recovery not required");
                message_text = frm.doc.override_settlement_policy
                    ? __(
                        "Management has waived processing-charge recovery on the {0} commercial variance through the audited Processor Lot policy override.",
                        [
                            format_qty_with_uom(
                                commercial_variance_remaining_qty,
                                finished_uom
                            )
                        ]
                    )
                    : __(
                        "The effective settlement policy does not require processing-charge recovery on the {0} commercial variance.",
                        [
                            format_qty_with_uom(
                                commercial_variance_remaining_qty,
                                finished_uom
                            )
                        ]
                    );

                next_action = "submit-processor-lot";
                next_action_label = __("Submit Processor Lot");
                next_action_detail = __(
                    "Submit this Processor Lot to close its lifecycle."
                );
            } else if (frm.doc.docstatus === 1) {
                workflow_class = "lot-health-status-good";
                workflow_dot_class = "lot-health-dot-good";
                message_class = "lot-health-message-success";
                workflow_label = __("Lot Completed");

                message_heading = __("Processor Lot completed");
                message_text = advance_credit_processed_qty > 0
                    ? __(
                        "The ERP receipt cycle is complete, {0} of excess processed material is recorded as submitted Processor Material Credit, and this Processor Lot has been submitted.",
                        [
                            format_qty_with_uom(
                                advance_credit_processed_qty,
                                finished_uom
                            )
                        ]
                    )
                    : physical_credit_applied_qty > 0
                    ? __(
                        "The ERP receipt cycle is complete, the physical shortage is covered by submitted Processor Material Credit, and this Processor Lot has been submitted."
                    )
                    : __(
                        "All entrusted material has been received, the ERP receipt cycle is complete, and this Processor Lot has been submitted."
                    );

                next_action = "";
                next_action_label = "";
                next_action_detail = "";
                show_next_action = false;
            } else {
                workflow_class = "lot-health-status-good";
                workflow_dot_class = "lot-health-dot-good";
                message_class = "lot-health-message-success";
                workflow_label = __("Ready for Closure");

                message_heading = advance_credit_processed_qty > 0
                    ? __("Excess material credit recorded")
                    : __("Lot fully accounted for");
                message_text = advance_credit_processed_qty > 0
                    ? __(
                        "The ERP receipt cycle is complete. {0} of excess processed material is recorded as submitted Processor Material Credit, and no physical or commercial variance remains.",
                        [
                            format_qty_with_uom(
                                advance_credit_processed_qty,
                                finished_uom
                            )
                        ]
                    )
                    : physical_credit_applied_qty > 0
                    ? __(
                        "The ERP receipt cycle is complete and submitted Processor Material Credit covers the physical shortage. No net physical or commercial variance remains."
                    )
                    : __(
                        "All entrusted material has been received and the ERP receipt cycle is complete. No physical quantity remains with the processor."
                    );

                next_action = "submit-processor-lot";
                next_action_label = __("Submit Processor Lot");
                next_action_detail = __(
                    "Submit this Processor Lot to close its lifecycle."
                );
            }
        } else if (erp_receipt_cycle_complete) {
            next_action = "receive-next-truck";
            next_action_label = __("Receive Another Truck");
            next_action_detail = __(
                "Record another Processor Lot Receipt if more material is expected from the processor."
            );

            alternate_action = "reconcile";
            alternate_action_label = __(
                "Begin Final Reconciliation"
            );
            alternate_action_detail = __(
                "Use this when no further truck receipt is expected and the remaining quantity must be classified."
            );
        } else {
            next_action = "review-receipt-journey";
            next_action_label = __("Complete ERP Receipt");
            next_action_detail = __(
                "Complete the linked Subcontracting Receipt, Purchase Receipt and Purchase Invoice for the current truck."
            );
        }
        } else if (debit_note_is_submitted && outstanding_qty > 0) {
        workflow_label = __("Settlement Complete");
        workflow_class = "lot-health-status-good";
        workflow_dot_class = "lot-health-dot-good";

        integrity_label = __("Healthy");
        integrity_class = "lot-health-status-good";

        message_class = "lot-health-message-success";

        if (
            component_credit_applied_qty > 0
            && net_outstanding_qty <= 0
        ) {
            message_heading = __(
                "Material credit and commercial settlement complete"
            );
            message_text = __(
                "{0} physical shortage is covered by submitted Processor Material Credit. The residual commercial recovery has been posted and the settlement is complete. Supporting documents are listed below.",
                [
                    format_qty_with_uom(
                        outstanding_qty,
                        component_uom
                    )
                ]
            );
        } else {
            message_heading = __("Physical variance settled");
            message_text = `
                ${format_qty_with_uom(
                    net_outstanding_qty,
                    component_uom
                )}
                ${__("physical variance has been settled through Material Debit Note")}
                <a
                    href="#"
                    class="lot-health-document-link"
                    data-document-type="Purchase Invoice"
                    data-document-name="${frappe.utils.escape_html(debit_note)}"
                >
                    ${frappe.utils.escape_html(debit_note)}
                </a>
                <span class="lot-health-inline-status lot-health-inline-status-good">
                    ${__("Submitted")}
                </span>.
            `;
        }

        if (frm.doc.docstatus === 1) {
            workflow_label = __("Lot Completed");
            workflow_class = "lot-health-status-good";
            workflow_dot_class = "lot-health-dot-good";

            next_action = "";
            next_action_label = "";
            next_action_detail = "";
            show_next_action = false;
        } else {
            next_action = "submit-processor-lot";
            next_action_label = __("Submit Processor Lot");
            next_action_detail = __(
                "Review the completed reconciliation and submit this Processor Lot to close its lifecycle."
            );
        }
        } else if (debit_note_is_draft && outstanding_qty > 0) {
            workflow_label = __("Settlement Document Draft");
            workflow_class = "lot-health-status-warning";
            workflow_dot_class = "lot-health-dot-warning";

            integrity_label = __("Healthy");
            integrity_class = "lot-health-status-good";

            message_class = "lot-health-message-warning";

            if (
                component_credit_applied_qty > 0
                && net_outstanding_qty <= 0
            ) {
                message_heading = __(
                    "Residual commercial recovery prepared"
                );
                message_text = `
                    ${format_qty_with_uom(
                        outstanding_qty,
                        component_uom
                    )}
                    ${__("physical shortage is covered by submitted Processor Material Credit. The residual commercial recovery has been assigned to Debit Note")}
                    <a
                        href="#"
                        class="lot-health-document-link"
                        data-document-type="Purchase Invoice"
                        data-document-name="${frappe.utils.escape_html(debit_note)}"
                    >
                        ${frappe.utils.escape_html(debit_note)}
                    </a>
                    <span class="lot-health-inline-status lot-health-inline-status-warning">
                        ${__("Draft")}
                    </span>.
                `;
            } else {
                message_heading = __(
                    "Physical variance prepared for settlement"
                );
                message_text = `
                    ${format_qty_with_uom(
                        net_outstanding_qty,
                        component_uom
                    )}
                    ${__("physical variance has been assigned to Material Debit Note")}
                    <a
                        href="#"
                        class="lot-health-document-link"
                        data-document-type="Purchase Invoice"
                        data-document-name="${frappe.utils.escape_html(debit_note)}"
                    >
                        ${frappe.utils.escape_html(debit_note)}
                    </a>
                    <span class="lot-health-inline-status lot-health-inline-status-warning">
                        ${__("Draft")}
                    </span>.
                `;
            }

            next_action = "submit-debit-note";
            next_action_label = __("Review and Submit Debit Note");
            next_action_detail = __(
                "Verify the settlement calculation and submit the Debit Note."
            );
    } else if (actionable_warnings.length) {
        workflow_label = __("Review Required");
        workflow_class = "lot-health-status-warning";
        workflow_dot_class = "lot-health-dot-warning";

        integrity_label = __("Review Advised");
        integrity_class = "lot-health-status-warning";

        message_class = "lot-health-message-warning";
        message_heading = __("Review advised");
        message_text = __(
            "The lot facts are available, but one or more conditions require review."
        );

        next_action_label = __("View Lot Facts");
        next_action_detail = __(
            "Review the warnings before beginning reconciliation."
        );
    } else if (net_outstanding_qty > 0) {
        workflow_label = __("Reconciliation Pending");
        workflow_class = "lot-health-status-warning";
        workflow_dot_class = "lot-health-dot-warning";

        message_class = "lot-health-message-warning";
        message_heading = __(
            "{0} awaiting reconciliation",
            [
                format_qty_with_uom(
                    net_outstanding_qty,
                    component_uom
                )
            ]
        );
        message_text = __(
            "Classify this quantity according to the business reality before closing the lot."
        );

        next_action_label = __("Begin Reconciliation");
        next_action_detail = __(
            "Classify the outstanding quantity according to the business reality."
        );
    }

    if (
        frm.doc.docstatus === 1
        && [
            "Reopened",
            "Reopened - Debit Note Created"
        ].includes(frm.doc.settlement_status)
    ) {
        workflow_label = __("Settlement Reopened");
        workflow_class = "lot-health-status-warning";
        workflow_dot_class = "lot-health-dot-warning";
        message_class = "lot-health-message-warning";
        message_heading = __("Operational lot preserved; settlement is open");
        message_text = __(
            "Submitted receipts, the Processor Lot, Subcontracting Order and Purchase Order remain preserved. Only final settlement evidence is being rebuilt."
        );
        show_next_action = true;

        if (debit_note_is_draft) {
            next_action = "submit-debit-note";
            next_action_label = __("Review and Submit Debit Note");
            next_action_detail = __(
                "Submit the rebuilt residual Debit Note before completing the reopened settlement."
            );
        } else if (debit_note_is_submitted) {
            next_action = "complete-reopened";
            next_action_label = __("Complete Reopened Settlement");
            next_action_detail = __(
                "The rebuilt residual Debit Note is submitted. Revalidate the evidence and return this settlement to Completed."
            );
        } else if (
            net_outstanding_qty > 0
            || commercial_variance_remaining_qty > 0
        ) {
            next_action = "reconcile";
            next_action_label = __("Reconcile Reopened Settlement");
            next_action_detail = __(
                "Apply available material credit and prepare any residual commercial recovery."
            );
        } else {
            next_action = "complete-reopened";
            next_action_label = __("Complete Reopened Settlement");
            next_action_detail = __(
                "Revalidate the rebuilt evidence and return this settlement to Completed."
            );
        }
    }

    const issue_count = actionable_warnings.length + blocking_errors.length;

    return `
        <div class="processor-lot-health-panel">
            <style>
                .processor-lot-health-panel {
                    border: 1px solid var(--border-color);
                    border-radius: 8px;
                    padding: 12px 14px 10px;
                    margin: 4px 0 14px;
                    background: var(--card-bg);
                }

                .processor-lot-health-panel .lot-health-header {
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    gap: 12px;
                    margin-bottom: 8px;
                }

                .processor-lot-health-panel .lot-health-basis {
                    color: var(--text-muted);
                    font-size: 11px;
                    line-height: 1.35;

                    background: var(--subtle-fg);
                    border-bottom: 1px solid var(--border-color);

                    padding: 10px 14px;

                    margin: -12px -14px 10px -14px;
                }

                .processor-lot-health-panel .lot-health-status {
                    display: inline-flex;
                    align-items: center;
                    gap: 6px;
                    border: 1px solid var(--border-color);
                    border-radius: 999px;
                    padding: 4px 9px;
                    font-size: 12px;
                    font-weight: 600;
                    background: var(--fg-color);
                }

                .processor-lot-health-panel .lot-health-dot {
                    width: 9px;
                    height: 9px;
                    border-radius: 50%;
                    display: inline-block;
                    flex: 0 0 auto;
                }

                .processor-lot-health-panel .lot-health-dot-good {
                    background: var(--green-500);
                }

                .processor-lot-health-panel .lot-health-dot-warning {
                    background: var(--yellow-500);
                }

                .processor-lot-health-panel .lot-health-dot-danger {
                    background: var(--red-500);
                }

                .processor-lot-health-panel .lot-health-status-good {
                    color: var(--green-700);
                }

                .processor-lot-health-panel .lot-health-status-warning {
                    color: var(--yellow-700);
                }

                .processor-lot-health-panel .lot-health-status-danger {
                    color: var(--red-700);
                }

                .processor-lot-health-panel .lot-health-grid {
                    display: grid;
                    grid-template-columns: repeat(3, minmax(0, 1fr));
                    gap: 8px;
                    margin-bottom: 8px;
                }

                .processor-lot-health-panel .lot-health-card {
                    border: 1px solid var(--border-color);
                    border-radius: 8px;
                    padding: 10px 11px;
                    background: var(--fg-color);
                    min-height: 72px;
                }

                .processor-lot-health-panel .lot-health-card-emphasis {
                    border-width: 2px;
                }

                .processor-lot-health-panel .lot-health-label {
                    font-size: 11px;
                    color: var(--text-muted);
                    margin-bottom: 4px;
                }

                .processor-lot-health-panel .lot-health-value {
                    font-size: 19px;
                    font-weight: 600;
                    line-height: 1.2;
                    white-space: nowrap;
                }

                .processor-lot-health-panel .lot-health-value-outstanding {
                    font-size: 20px;
                    font-weight: 600;
                }

                .processor-lot-health-panel .lot-health-value-warning {
                    color: var(--yellow-700);
                }

                .processor-lot-health-panel .lot-health-value-settled {
                    color: var(--text-color);
                }

                .processor-lot-health-panel .lot-health-secondary {
                    display: grid;
                    grid-template-columns: repeat(3, minmax(0, 1fr));
                    gap: 8px;
                    margin-bottom: 9px;
                }

                .processor-lot-health-panel .lot-health-secondary-item {
                    border-top: 1px solid var(--border-color);
                    padding: 7px 2px 0;
                    font-size: 12px;
                }

                .processor-lot-health-panel .lot-health-secondary-value {
                    display: block;
                    margin-top: 2px;
                    font-weight: 600;
                }

                .processor-lot-health-panel .lot-health-message {
                    border-radius: 7px;
                    padding: 9px 11px;
                    margin-bottom: 8px;
                    font-size: 12px;
                }

                .processor-lot-health-panel .lot-health-message strong {
                    display: block;
                    margin-bottom: 2px;
                    font-size: 13px;
                }

                .processor-lot-health-panel .lot-health-message-info {
                    background: var(--blue-50);
                    border-left: 3px solid var(--blue-500);
                }

                .processor-lot-health-panel .lot-health-message-warning {
                    background: var(--yellow-50);
                    border-left: 3px solid var(--yellow-500);
                }

                .processor-lot-health-panel .lot-health-message-danger {
                    background: var(--red-50);
                    border-left: 3px solid var(--red-500);
                }

                .processor-lot-health-panel .lot-health-message-success {
                    background: var(--green-50);
                    border-left: 3px solid var(--green-500);
                }

                .processor-lot-health-panel .lot-health-next-action {
                    border-top: 1px solid var(--border-color);
                    padding-top: 9px;
                    margin-top: 2px;
                    font-size: 12px;
                }

                .processor-lot-health-panel .lot-health-next-action strong {
                    display: block;
                    margin-bottom: 4px;
                }

                .processor-lot-health-panel .lot-health-action-link {
                    display: inline-flex;
                    align-items: center;
                    gap: 5px;
                    color: var(--primary);
                    font-size: 13px;
                    font-weight: 600;
                    text-decoration: none;
                    cursor: pointer;
                }

                .processor-lot-health-panel .lot-health-action-link:hover {
                    text-decoration: underline;
                }

                .processor-lot-health-panel .lot-health-action-link:focus {
                    outline: 2px solid var(--primary);
                    outline-offset: 2px;
                    border-radius: 3px;
                }

                .processor-lot-health-panel .lot-health-document-link {
                    color: var(--primary);
                    font-weight: 600;
                    text-decoration: underline;
                    cursor: pointer;
                }

                .processor-lot-health-panel .lot-health-document-link:hover {
                    text-decoration: underline;
                }

                .processor-lot-health-panel .lot-health-inline-status {
                    display: inline-block;
                    margin-left: 6px;
                    padding: 1px 7px;
                    border-radius: 999px;
                    border: 1px solid var(--border-color);
                    font-size: 11px;
                    font-weight: 600;
                    line-height: 1.2;
                    vertical-align: middle;
                }

                .processor-lot-health-panel .lot-health-inline-status-warning {
                    color: var(--yellow-700);
                    background: var(--yellow-50);
                }

                .processor-lot-health-panel .lot-health-inline-status-good {
                    color: var(--green-700);
                    background: var(--green-50);
                }

                @media (max-width: 767px) {
                    .processor-lot-health-panel .lot-health-grid,
                    .processor-lot-health-panel .lot-health-secondary {
                        grid-template-columns: 1fr;
                    }

                    .processor-lot-health-panel .lot-health-header {
                        align-items: flex-start;
                        flex-direction: column;
                        justify-content: flex-start;
                        gap: 6px;
                    }
                }
            </style>

            <div class="lot-health-header">
                <div class="lot-health-basis">
                    ${__("ERP workflow derived from submitted ERPNext documents")}
                </div>

                <div class="lot-health-status ${workflow_class}">
                    <span class="lot-health-dot ${workflow_dot_class}"></span>
                    <span>${workflow_label}</span>
                </div>
            </div>

            <div class="lot-health-grid">
                <div class="lot-health-card">
                    <div class="lot-health-label">
                        ${__("Material Entrusted")}
                    </div>
                    <div class="lot-health-value">
                        ${format_qty_with_uom(
                            entrusted_qty,
                            component_uom
                        )}
                    </div>
                </div>

                <div class="lot-health-card">
                    <div class="lot-health-label">
                        ${__("SCR Recognised")}
                    </div>
                    <div class="lot-health-value">
                        ${format_qty_with_uom(
                            received_qty,
                            finished_uom
                        )}
                    </div>
                </div>

                <div class="lot-health-card lot-health-card-emphasis">
                    <div class="lot-health-label">
                        ${variance_label}
                    </div>
                    <div class="
                        lot-health-value
                        lot-health-value-outstanding
                        ${variance_value_class}
                    ">
                        ${format_qty_with_uom(
                            component_credit_applied_qty > 0
                                ? net_outstanding_qty
                                : outstanding_qty,
                            component_uom
                        )}
                    </div>
                </div>
            </div>

            <div class="lot-health-secondary">
                ${physical_credit_applied_qty > 0 ? `
                    <div class="lot-health-secondary-item">
                        <div class="lot-health-label">
                            ${__("Gross Physical Shortage")}
                        </div>
                        <span class="lot-health-secondary-value">
                            ${format_qty_with_uom(
                                outstanding_qty,
                                component_uom
                            )}
                        </span>
                    </div>

                    <div class="lot-health-secondary-item">
                        <div class="lot-health-label">
                            ${__("Submitted Material Credit")}
                        </div>
                        <span class="lot-health-secondary-value lot-health-value-settled">
                            ${format_qty_with_uom(
                                physical_credit_applied_qty,
                                component_uom
                            )}
                        </span>
                    </div>

                    <div class="lot-health-secondary-item">
                        <div class="lot-health-label">
                            ${__("Residual Commercial Variance Basis")}
                        </div>
                        <span class="lot-health-secondary-value">
                            ${format_qty_with_uom(
                                commercial_variance_remaining_qty,
                                finished_uom
                            )}
                        </span>
                    </div>
                ` : ""}

                <div class="lot-health-secondary-item">
                    <div class="lot-health-label">
                        ${__("Supplier Invoice Qty")}
                    </div>
                    <span class="lot-health-secondary-value">
                        ${format_qty_with_uom(
                            invoice_qty,
                            finished_uom
                        )}
                    </span>
                </div>

                <div class="lot-health-secondary-item">
                    <div class="lot-health-label">
                        ${__("Integrity")}
                    </div>
                    <span class="lot-health-secondary-value ${integrity_class}">
                        ${integrity_label}
                    </span>
                </div>

                <div class="lot-health-secondary-item">
                    <div class="lot-health-label">
                        ${__("Integrity Issues")}
                    </div>
                    <span class="lot-health-secondary-value">
                        ${issue_count}
                    </span>
                </div>
            </div>

            <div class="lot-health-message ${message_class}">
                <strong>${message_heading}</strong>
                <span>${message_text}</span>
            </div>

            ${
                show_next_action
                    ? `
                        <div class="lot-health-next-action">
                            <strong>${__("Next Step")}</strong>

                            <div style="margin-top: 8px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">

                                <a
                                    href="#"
                                    class="btn btn-primary btn-sm"
                                    data-action="processor-lot-next-step"
                                    data-next-action="${next_action}"
                                    role="button"
                                >
                                    ${next_action_label}
                                </a>

                                ${
                                    alternate_action
                                        ? `
                                            <span
                                                class="text-muted"
                                                style="font-style: italic;"
                                            >
                                                ${__("or")}
                                            </span>

                                            <a
                                                href="#"
                                                class="btn btn-default btn-sm"
                                                data-action="processor-lot-next-step"
                                                data-next-action="${alternate_action}"
                                                role="button"
                                            >
                                                ${alternate_action_label}
                                            </a>
                                        `
                                        : ""
                                }

                            </div>
                        </div>
                    `
                    : `
                        <div class="lot-health-next-action">
                            <strong>${__("Lifecycle Status")}</strong>

                            <span class="text-success">
                                ✓ ${__(
                                    "Processor Lot completed — no further action required."
                                )}
                            </span>
                        </div>
                    `
            }
        </div>
    `;
}

/**
 * Open the Processor Lot Reconciliation Wizard.
 *
 * Version 1 currently opens only a read-only shell. Allocation and decision
 * logic will be added in the next small increments.
 */
/**
 * Opens the Processor Lot Reconciliation Wizard.
 *
 * Current implementation:
 * 1. Review Facts
 * 2. Classify Business Reality
 *
 * The selected business classification is currently held only in memory.
 * Nothing is saved and no ERPNext document is created.
 */
function open_reconciliation_wizard(frm, facts) {
    const identity = facts.identity || {};
    const summary = facts.summary || {};
    const integrity = facts.integrity || {};

    const entrustment = summary.entrustment || {};
    const physical = summary.physical_inventory || {};
    const commercial = summary.commercial || {};
    const comparisons = summary.comparisons || {};

    const entrusted_qty = flt(
        entrustment.sco_supplied_qty
    );

    const received_qty = flt(
        physical.scr_received_qty
    );

    const outstanding_qty = flt(
        physical.outstanding_qty
    );

    const invoice_qty = flt(
        commercial.purchase_invoice_qty
    );

    const commercial_variance_qty = flt(
        comparisons.invoice_vs_scr_received
    );

    const integrity_issue_count =
        (integrity.warnings || []).length +
        (integrity.blocking_errors || []).length;

    const integrity_label = integrity_issue_count
        ? __("Issues Found")
        : __("Healthy");

    const integrity_color = integrity_issue_count
        ? "#d93025"
        : "green";

    const wizard_state = {
        current_step: 1,
        business_classification: "",
        recommendation: null,
        recovery: null
    };

    const dialog = new frappe.ui.Dialog({
        title: __("Processor Lot Reconciliation"),
        size: "large",
        fields: [
            {
                fieldtype: "HTML",
                fieldname: "reconciliation_wizard_html"
            }
        ]
    });

    const wrapper =
        dialog.fields_dict.reconciliation_wizard_html.$wrapper;

    /**
     * Returns the wizard progress header.
     *
     * Completed steps are shown with green ticks.
     * Current step is shown in blue.
     * Future steps are shown in grey.
     */
    function get_progress_html(active_step) {

        function step_html(step_no, label) {

            if (step_no < active_step) {
                return `
                    <span style="
                        color:#2e7d32;
                        font-weight:600;
                    ">
                        ✔ ${label}
                    </span>
                `;
            }

            if (step_no === active_step) {
                return `
                    <span style="
                        color:#0d6efd;
                        font-weight:600;
                    ">
                        ${["①", "②", "③", "④"][step_no - 1]} ${label}
                    </span>
                `;
            }

            return `
                <span style="
                    color:#999;
                    font-weight:600;
                ">
                    ${["①", "②", "③", "④"][step_no - 1]} ${label}
                </span>
            `;
        }

        return `
            <div style="
                display:flex;
                gap:18px;
                margin-bottom:20px;
                font-size:13px;
                flex-wrap:wrap;
            ">

                ${step_html(1, __("Review Facts"))}

                ${step_html(
                    2,
                    wizard_state.business_classification
                        === "commercial_variance_only"
                        ? __("Physical Position Complete")
                        : __("Confirm Physical Reality")
                )}

                ${step_html(3, __("Review Recommendation"))}

                ${step_html(4, __("Review Settlement Calculation"))}

            </div>
        `;
    }
    /**
     * Displays Step 1 — Review Facts.
     */
    function render_step_1() {
        wizard_state.current_step = 1;

        wrapper.html(`
            <div style="padding:8px 0;">

                ${get_progress_html(1)}

                <div style="
                    border:1px solid #d9d9d9;
                    border-radius:6px;
                    padding:15px;
                    background:#fafafa;
                ">

                    <div style="
                        font-size:16px;
                        font-weight:600;
                        margin-bottom:15px;
                    ">
                        ${__("Step 1 — Review Facts")}
                    </div>

                    <table
                        class="table table-bordered"
                        style="margin-bottom:0;"
                    >
                        <tbody>

                            <tr>
                                <td>
                                    <strong>
                                        ${__("Subcontracting Order")}
                                    </strong>
                                </td>
                                <td>
                                    ${frappe.utils.escape_html(
                                        identity.subcontracting_order || ""
                                    )}
                                </td>
                            </tr>

                            <tr>
                                <td>
                                    <strong>
                                        ${__("Entrusted Qty")}
                                    </strong>
                                </td>
                                <td>
                                    ${format_number(
                                        entrusted_qty,
                                        null,
                                        3
                                    )}
                                </td>
                            </tr>

                            <tr>
                                <td>
                                    <strong>
                                        ${__("Company Accepted Qty")}
                                    </strong>
                                </td>
                                <td>
                                    ${format_number(
                                        received_qty,
                                        null,
                                        3
                                    )}
                                </td>
                            </tr>

                            <tr>
                                <td>
                                    <strong>
                                        ${__("Outstanding Qty")}
                                    </strong>
                                </td>
                                <td>
                                    <strong style="color:#d35400;">
                                        ${format_number(
                                            outstanding_qty,
                                            null,
                                            3
                                        )}
                                    </strong>
                                </td>
                            </tr>

                            <tr>
                                <td>
                                    <strong>
                                        ${__("Supplier Invoice Qty")}
                                    </strong>
                                </td>
                                <td>
                                    ${format_number(
                                        invoice_qty,
                                        null,
                                        3
                                    )}
                                </td>
                            </tr>

                            <tr>
                                <td>
                                    <strong>
                                        ${__("Integrity")}
                                    </strong>
                                </td>
                                <td style="color:${integrity_color};">
                                    ${integrity_label}
                                </td>
                            </tr>

                        </tbody>
                    </table>

                    <div style="
                        margin-top:15px;
                        color:#666;
                    ">
                        ${__(
                            "Review the facts before continuing with reconciliation."
                        )}
                    </div>

                </div>

            </div>
        `);

        dialog.set_primary_action(
            __("Continue"),
            () => {
                if (
                    outstanding_qty <= 0.000001
                    && commercial_variance_qty > 0.000001
                ) {
                    wizard_state.business_classification =
                        "commercial_variance_only";
                    wizard_state.recommendation = null;
                    wizard_state.recovery = null;
                    render_step_3();
                    return;
                }

                render_step_2();
            }
        );

        dialog.set_secondary_action_label("");
    }

    /**
     * Display Step 2 — Confirm Physical Reality.
     *
     * This step asks the user to confirm what physically happened to the
     * outstanding component quantity.
     *
     * It does not ask the user to decide the commercial settlement. The
     * originating Purchase Order settlement policy is applied separately by
     * the Recommendation Engine.
     *
     * The selected classification remains only in memory during this wizard
     * session. Nothing is saved and no ERPNext document is created here.
     */
    function render_step_2() {
        wizard_state.current_step = 2;

        const selected_value =
            wizard_state.business_classification;

        wrapper.html(`
            <div style="padding:8px 0;">

                ${get_progress_html(2)}

                <div style="
                    border:1px solid #d9d9d9;
                    border-radius:6px;
                    padding:15px;
                    background:#fafafa;
                ">

                    <div style="
                        font-size:16px;
                        font-weight:600;
                        margin-bottom:8px;
                    ">
                        ${__(
                            "Step 2 — Confirm Physical Reality"
                        )}
                    </div>

                    <div style="
                        color:#666;
                        margin-bottom:12px;
                        line-height:1.45;
                    ">
                        ${__(
                            "What physically happened to the outstanding material?"
                        )}
                    </div>

                    <div style="
                        border-left:3px solid var(--blue-500);
                        border-radius:5px;
                        padding:9px 11px;
                        margin-bottom:15px;
                        background:var(--blue-50);
                        font-size:12px;
                        line-height:1.45;
                    ">
                        ${__(
                            "Select only the physical reality. The system will apply the Purchase Order settlement policy separately when preparing its recommendation."
                        )}
                    </div>

                    <div style="
                        border:1px solid #ead4bb;
                        border-radius:6px;
                        padding:12px;
                        margin-bottom:16px;
                        background:#fff8ef;
                    ">
                        <div style="
                            font-size:12px;
                            color:#777;
                            margin-bottom:3px;
                        ">
                            ${__("Outstanding Quantity")}
                        </div>

                        <div style="
                            font-size:20px;
                            font-weight:600;
                            color:#d35400;
                        ">
                            ${format_number(
                                outstanding_qty,
                                null,
                                3
                            )}
                        </div>
                    </div>

                    <div
                        class="processor-lot-classification-options"
                    >
                        <style>
                            .processor-lot-classification-option {
                                display: block;
                                border: 1px solid var(--border-color);
                                border-radius: 6px;
                                padding: 12px;
                                margin-bottom: 10px;
                                background: var(--card-bg);
                                cursor: pointer;
                                transition:
                                    border-color 0.15s ease,
                                    background-color 0.15s ease,
                                    box-shadow 0.15s ease;
                            }

                            .processor-lot-classification-option:hover {
                                border-color: var(--blue-300);
                                background: var(--blue-50);
                            }

                            .processor-lot-classification-option-selected {
                                border-color: var(--blue-500);
                                background: var(--blue-50);
                                box-shadow: 0 0 0 1px var(--blue-500);
                            }

                            .processor-lot-classification-option-last {
                                margin-bottom: 0;
                            }
                        </style>

                        <label
                            class="
                                processor-lot-classification-option
                                ${
                                    selected_value === "recoverable_shortage"
                                        ? "processor-lot-classification-option-selected"
                                        : ""
                                }
                            "
                        >
                            <div style="
                                display:flex;
                                align-items:flex-start;
                                gap:10px;
                            ">
                                <input
                                    type="radio"
                                    name="processor_lot_classification"
                                    value="recoverable_shortage"
                                    ${
                                        selected_value ===
                                        "recoverable_shortage"
                                            ? "checked"
                                            : ""
                                    }
                                    style="margin-top:4px;"
                                >

                                <div>
                                    <div style="
                                        font-weight:600;
                                        margin-bottom:3px;
                                    ">
                                        ${__(
                                            "Material Not Returned by Processor"
                                        )}
                                    </div>

                                    <div style="
                                        color:#666;
                                        font-size:12px;
                                        line-height:1.4;
                                    ">
                                        ${__(
                                            "The outstanding material has neither been received back nor recorded as returned and remains attributable to the processor."
                                        )}
                                    </div>
                                </div>
                            </div>
                        </label>

                        <label
                            class="
                                processor-lot-classification-option
                                ${
                                    selected_value === "accepted_process_loss"
                                        ? "processor-lot-classification-option-selected"
                                        : ""
                                }
                            "
                        >
                            <div style="
                                display:flex;
                                align-items:flex-start;
                                gap:10px;
                            ">
                                <input
                                    type="radio"
                                    name="processor_lot_classification"
                                    value="accepted_process_loss"
                                    ${
                                        selected_value ===
                                        "accepted_process_loss"
                                            ? "checked"
                                            : ""
                                    }
                                    style="margin-top:4px;"
                                >

                                <div>
                                    <div style="
                                        font-weight:600;
                                        margin-bottom:3px;
                                    ">
                                        ${__(
                                            "Consumed as Accepted Process Loss"
                                        )}
                                    </div>

                                    <div style="
                                        color:#666;
                                        font-size:12px;
                                        line-height:1.4;
                                    ">
                                        ${__(
                                            "The outstanding quantity was consumed during normal processing and is being treated as accepted process loss."
                                        )}
                                    </div>
                                </div>
                            </div>
                        </label>

                        <label
                            class="
                                processor-lot-classification-option
                                ${
                                    selected_value === "components_returned"
                                        ? "processor-lot-classification-option-selected"
                                        : ""
                                }
                            "
                        >
                            <div style="
                                display:flex;
                                align-items:flex-start;
                                gap:10px;
                            ">
                                <input
                                    type="radio"
                                    name="processor_lot_classification"
                                    value="components_returned"
                                    ${
                                        selected_value ===
                                        "components_returned"
                                            ? "checked"
                                            : ""
                                    }
                                    style="margin-top:4px;"
                                >

                                <div>
                                    <div style="
                                        font-weight:600;
                                        margin-bottom:3px;
                                    ">
                                        ${__(
                                            "Material Physically Returned"
                                        )}
                                    </div>

                                    <div style="
                                        color:#666;
                                        font-size:12px;
                                        line-height:1.4;
                                    ">
                                        ${__(
                                            "The outstanding material has been physically returned by the processor and the corresponding ERPNext return transaction must be verified."
                                        )}
                                    </div>
                                </div>
                            </div>
                        </label>

                        <label
                            class="
                                processor-lot-classification-option
                                processor-lot-classification-option-last
                                ${
                                    selected_value === "pending_investigation"
                                        ? "processor-lot-classification-option-selected"
                                        : ""
                                }
                            "
                        >
                            <div style="
                                display:flex;
                                align-items:flex-start;
                                gap:10px;
                            ">
                                <input
                                    type="radio"
                                    name="processor_lot_classification"
                                    value="pending_investigation"
                                    ${
                                        selected_value ===
                                        "pending_investigation"
                                            ? "checked"
                                            : ""
                                    }
                                    style="margin-top:4px;"
                                >

                                <div>
                                    <div style="
                                        font-weight:600;
                                        margin-bottom:3px;
                                    ">
                                        ${__(
                                            "Physical Position Not Yet Confirmed"
                                        )}
                                    </div>

                                    <div style="
                                        color:#666;
                                        font-size:12px;
                                        line-height:1.4;
                                    ">
                                        ${__(
                                            "The available facts are incomplete and the physical position requires investigation before settlement can proceed."
                                        )}
                                    </div>
                                </div>
                            </div>
                        </label>

                    </div>

                </div>

            </div>
        `);

        wrapper
            .find(
                'input[name="processor_lot_classification"]'
            )
            .off("change.processor_lot")
            .on("change.processor_lot", function () {
                const selected_value = $(this).val();

                wrapper
                    .find(".processor-lot-classification-option")
                    .removeClass(
                        "processor-lot-classification-option-selected"
                    );

                $(this)
                    .closest(".processor-lot-classification-option")
                    .addClass(
                        "processor-lot-classification-option-selected"
                    );

                /*
                * A changed physical classification invalidates any
                * Recommendation or Recovery Report previously obtained during
                * this wizard session.
                */
                if (
                    wizard_state.business_classification
                    !== selected_value
                ) {
                    wizard_state.recommendation = null;
                    wizard_state.recovery = null;
                }

                wizard_state.business_classification =
                    selected_value;
            });

        dialog.set_secondary_action_label(__("Back"));

        dialog.set_secondary_action(() => {
            render_step_1();
        });

        dialog.set_primary_action(
            __("Continue"),
            () => {
                const selected =
                    wrapper
                        .find(
                            'input[name="processor_lot_classification"]:checked'
                        )
                        .val() || "";

                if (!selected) {
                    frappe.msgprint({
                        title: __("Physical Reality Required"),
                        indicator: "orange",
                        message: __(
                            "Please confirm what physically happened to the outstanding material."
                        )
                    });

                    return;
                }

                wizard_state.business_classification =
                    selected;

                render_step_3();
            }
        );
    }

    function return_to_classification_step() {
        if (
            wizard_state.business_classification
            === "commercial_variance_only"
        ) {
            render_step_1();
            return;
        }

        render_step_2();
    }

/**
 * Display Step 3 — Review Recommendation.
 *
 * The recommendation is obtained from the server-side Recommendation
 * Engine. JavaScript only presents the returned decision report.
 *
 * Nothing is saved and no ERPNext document is created.
 */
function render_step_3() {
    wizard_state.current_step = 3;

    if (!wizard_state.business_classification) {
        frappe.msgprint({
            title: __("Classification Required"),
            indicator: "orange",
            message: __(
                "Please select a business classification before reviewing the recommendation."
            )
        });

        render_step_2();
        return;
    }

    /*
     * Show a loading state inside the wizard while the Recommendation
     * Engine evaluates the current facts, classification and PO policy.
     */
    wrapper.html(`
        <div style="padding:8px 0;">
            ${get_progress_html(3)}

            <div style="
                border:1px solid var(--border-color);
                border-radius:6px;
                padding:20px;
                background:var(--subtle-fg);
                text-align:center;
            ">
                <div style="
                    font-size:15px;
                    font-weight:600;
                    margin-bottom:6px;
                ">
                    ${__("Preparing Recommendation")}
                </div>

                <div class="text-muted">
                    ${__(
                        "Evaluating the current lot facts, business classification and Purchase Order settlement policy..."
                    )}
                </div>
            </div>
        </div>
    `);

    dialog.set_primary_action(__("Please Wait"), () => {});
    dialog.get_primary_btn().prop("disabled", true);

    dialog.set_secondary_action_label(__("Back"));

    dialog.set_secondary_action(() => {
        return_to_classification_step();
    });

    frappe.call({
        method: [
            "subcontracting_extensions",
            "subcontracting_extensions",
            "doctype",
            "processor_lot",
            "processor_lot",
            "get_processor_lot_recommendation"
        ].join("."),
        args: {
            subcontracting_order:
                frm.doc.subcontracting_order,

            processor_lot: frm.doc.name,

            business_classification:
                wizard_state.business_classification
        },
        callback(r) {
            const recommendation = r.message;

            if (!recommendation) {
                frappe.msgprint({
                    title: __("Recommendation Unavailable"),
                    indicator: "red",
                    message: __(
                        "The Recommendation Engine did not return a result."
                    )
                });

                return_to_classification_step();
                return;
            }

            display_recommendation(recommendation);
        },
        error() {
            return_to_classification_step();
        }
    });
}


/**
 * Present the Recommendation Engine result.
 *
 * The business reasoning is supplied entirely by the server-side
 * Recommendation Engine. JavaScript only formats and displays it.
 *
 * Nothing is saved and no ERPNext document is created.
 */
function display_recommendation(recommendation) {
    wizard_state.recommendation = recommendation;

    const verdict = recommendation.verdict || {};
    const confidence = recommendation.confidence || {};
    const reasoning = recommendation.reasoning || [];

    const status =
        verdict.status
        || recommendation.recommendation_status
        || "";

    const next_step =
        verdict.next_step
        || recommendation.recommended_next_step
        || "";

    const confidence_level =
        confidence.level || "";

    const confidence_score =
        flt(confidence.score);

    const confidence_message =
        confidence.message || "";

    const outstanding_qty =
        flt(recommendation.outstanding_qty);

    const outstanding_uom =
        recommendation.outstanding_uom || "";

    const classification_label =
        verdict.classification_label
        || recommendation.classification_label
        || "";

    const escape_text = value => {
        return frappe.utils.escape_html(
            String(value ?? "")
        );
    };

    const get_physical_reality_label = classification => {
        const labels = {
            accepted_process_loss:
                __("Consumed as Accepted Process Loss"),

            recoverable_shortage:
                __("Material Not Returned by Processor"),

            components_returned:
                __("Material Physically Returned"),

            pending_investigation:
                __("Physical Position Not Yet Confirmed"),

            commercial_variance_only:
                __("Commercial Variance Only")
        };

        return labels[classification]
            || classification_label
            || classification
            || "";
    };

    const physical_reality_label =
        get_physical_reality_label(
            verdict.classification
            || recommendation.classification
            || ""
        );

    const formatted_outstanding_qty = [
        format_number(outstanding_qty, null, 3),
        escape_text(outstanding_uom)
    ].filter(Boolean).join(" ");

    /**
     * Render the ordered explanation trail returned by the
     * Recommendation Engine.
     */
    const reasoning_html = reasoning.length
        ? reasoning
            .slice()
            .sort((a, b) => {
                return flt(a.sequence) - flt(b.sequence);
            })
            .map(item => {
                const message = escape_text(
                    item.message || ""
                );

                return `
                    <div style="
                        display:flex;
                        align-items:flex-start;
                        gap:9px;
                        padding:8px 0;
                        border-bottom:
                            1px solid var(--border-color);
                    ">
                        <span style="
                            color:var(--green-600);
                            font-weight:700;
                            line-height:1.4;
                            flex:0 0 auto;
                        ">
                            ✓
                        </span>

                        <span style="
                            line-height:1.45;
                        ">
                            ${message}
                        </span>
                    </div>
                `;
            })
            .join("")
        : `
            <div class="text-muted">
                ${__(
                    "No detailed reasoning was returned by the Recommendation Engine."
                )}
            </div>
        `;

    wrapper.html(`
        <div style="padding:8px 0;">

            ${get_progress_html(3)}

            <div style="
                border:1px solid var(--border-color);
                border-radius:7px;
                padding:15px;
                background:var(--subtle-fg);
            ">

                <div style="
                    font-size:16px;
                    font-weight:600;
                    margin-bottom:15px;
                ">
                    ${__("Step 3 — Review Recommendation")}
                </div>

                <div style="
                    display:grid;
                    grid-template-columns:
                        repeat(auto-fit, minmax(210px, 1fr));
                    gap:10px;
                    margin-bottom:12px;
                ">
                    <div style="
                        border:1px solid var(--border-color);
                        border-radius:6px;
                        padding:11px;
                        background:var(--card-bg);
                    ">
                        <div class="text-muted" style="
                            font-size:12px;
                            margin-bottom:3px;
                        ">
                            ${__("Outstanding Quantity")}
                        </div>

                        <div style="
                            font-size:19px;
                            font-weight:600;
                        ">
                            ${formatted_outstanding_qty}
                        </div>
                    </div>

                    <div style="
                        border:1px solid var(--border-color);
                        border-radius:6px;
                        padding:11px;
                        background:var(--card-bg);
                    ">
                        <div class="text-muted" style="
                            font-size:12px;
                            margin-bottom:3px;
                        ">
                            ${__("Confirmed Physical Reality")}
                        </div>

                        <div style="
                            font-size:16px;
                            font-weight:600;
                        ">
                            ${escape_text(physical_reality_label)}
                        </div>
                    </div>
                </div>

                <div style="
                    border-left:4px solid var(--primary);
                    border-radius:5px;
                    padding:13px 14px;
                    margin-bottom:12px;
                    background:var(--card-bg);
                ">
                    <div class="text-muted" style="
                        font-size:12px;
                        margin-bottom:4px;
                    ">
                        ${__("Recommendation Status")}
                    </div>

                    <div style="
                        font-size:18px;
                        font-weight:700;
                    ">
                        ${escape_text(status)}
                    </div>
                </div>

                <div style="
                    border:1px solid var(--border-color);
                    border-radius:6px;
                    padding:13px;
                    margin-bottom:12px;
                    background:var(--card-bg);
                ">
                    <div style="
                        font-weight:600;
                        margin-bottom:4px;
                    ">
                        ${__(
                            "How This Recommendation Was Derived"
                        )}
                    </div>

                    <div style="
                        margin-top:4px;
                    ">
                        ${reasoning_html}
                    </div>
                </div>

                <div style="
                    border:1px solid var(--border-color);
                    border-radius:6px;
                    padding:13px;
                    margin-bottom:12px;
                    background:var(--card-bg);
                ">
                    <div style="
                        display:flex;
                        align-items:baseline;
                        justify-content:space-between;
                        gap:10px;
                        margin-bottom:5px;
                    ">
                        <strong>${__("Confidence")}</strong>

                        <span style="font-weight:600;">
                            ${escape_text(confidence_level)}
                            ${
                                confidence_score
                                    ? ` (${format_number(
                                        confidence_score,
                                        null,
                                        0
                                    )}%)`
                                    : ""
                            }
                        </span>
                    </div>

                    <div class="text-muted" style="line-height:1.45;">
                        ${escape_text(confidence_message)}
                    </div>
                </div>

                <div style="
                    border:1px solid var(--border-color);
                    border-radius:6px;
                    padding:13px;
                    background:var(--card-bg);
                ">
                    <div class="text-muted" style="
                        font-size:12px;
                        margin-bottom:4px;
                    ">
                        ${__("What Should I Do Next?")}
                    </div>

                    <div style="
                        font-size:17px;
                        font-weight:700;
                    ">
                        ➜ ${escape_text(next_step)}
                    </div>
                </div>

            </div>
        </div>
    `);

    /*
     * Remove the final divider from the reasoning list so the last
     * explanation line finishes cleanly.
     */
    wrapper
        .find(
            '[style*="border-bottom:"]'
        )
        .last()
        .css("border-bottom", "0");

    dialog.set_secondary_action_label(__("Back"));

    dialog.set_secondary_action(() => {
        return_to_classification_step();
    });

    dialog.get_primary_btn().prop("disabled", false);

    dialog.set_primary_action(
        __("Continue"),
        () => {
            render_step_4();
        }
    );
}

/**
 * Display Step 4 — Review Settlement.
 *
 * The Recovery Calculator runs on the server using current lot facts and
 * the Recommendation Engine result. JavaScript only presents the returned
 * commercial recovery report.
 *
 * Nothing is saved and no ERPNext document is created by this step.
 */
function render_step_4() {
    wizard_state.current_step = 4;

    if (!wizard_state.business_classification) {
        frappe.msgprint({
            title: __("Classification Required"),
            indicator: "orange",
            message: __(
                "Please select a business classification before reviewing the settlement."
            )
        });

        render_step_2();
        return;
    }

    /*
     * Reuse the already-fetched recovery report when the user returns to
     * this step using Back and Continue.
     */
    if (wizard_state.recovery) {
        display_recovery(wizard_state.recovery);
        return;
    }

    wrapper.html(`
        <div style="padding:8px 0;">
            ${get_progress_html(4)}

            <div style="
                border:1px solid var(--border-color);
                border-radius:6px;
                padding:20px;
                background:var(--subtle-fg);
                text-align:center;
            ">
                <div style="
                    font-size:15px;
                    font-weight:600;
                    margin-bottom:6px;
                ">
                    ${__("Calculating Recovery")}
                </div>

                <div class="text-muted">
                    ${__(
                        "Deriving the recovery quantity, raw-material value and processing-charge recovery from authoritative ERPNext records..."
                    )}
                </div>
            </div>
        </div>
    `);

    dialog.set_primary_action(
        __("Please Wait"),
        () => {}
    );

    dialog.get_primary_btn().prop("disabled", true);

    dialog.set_secondary_action_label(__("Back"));

    dialog.set_secondary_action(() => {
        if (wizard_state.recommendation) {
            display_recommendation(
                wizard_state.recommendation
            );
            return;
        }

        render_step_3();
    });

    frappe.call({
        method: [
            "subcontracting_extensions",
            "subcontracting_extensions",
            "doctype",
            "processor_lot",
            "processor_lot",
            "get_processor_lot_recovery"
        ].join("."),
        args: {
            subcontracting_order:
                frm.doc.subcontracting_order,

            processor_lot: frm.doc.name,

            business_classification:
                wizard_state.business_classification
        },
        callback(r) {
            const recovery = r.message;

            if (!recovery) {
                frappe.msgprint({
                    title: __("Recovery Unavailable"),
                    indicator: "red",
                    message: __(
                        "The Recovery Calculator did not return a result."
                    )
                });

                if (wizard_state.recommendation) {
                    display_recommendation(
                        wizard_state.recommendation
                    );
                    return;
                }

                render_step_3();
                return;
            }

            wizard_state.recovery = recovery;
            display_recovery(recovery);
        },
        error() {
            if (wizard_state.recommendation) {
                display_recommendation(
                    wizard_state.recommendation
                );
                return;
            }

            render_step_3();
        }
    });
}


/**
 * Present the server-derived Recovery Calculator report.
 *
 * Rates, quantities and amounts are supplied by the server. JavaScript does
 * not recalculate or override any recovery value.
 */
function display_recovery(recovery) {
    wizard_state.current_step = 4;
    wizard_state.recovery = recovery;

    const quantity = recovery.quantity || {};
    const raw_material = recovery.raw_material || {};
    const processing_charges =
        recovery.processing_charges || {};
    const totals = recovery.totals || {};
    const integrity = recovery.integrity || {};
    const reasoning = recovery.reasoning || [];
    const settlement_netting =
        recovery.settlement_netting || {};
    const material_credit =
        settlement_netting.material_credit || {};
    const net_recovery = settlement_netting.net || {};
    const proposed_applications =
        material_credit.applications || [];
    const existing_applications =
        material_credit.existing_applications || [];
    const credit_is_fully_applied = Boolean(
        existing_applications.length
        && flt(net_recovery.physical_shortage_qty) === 0
        && flt(net_recovery.commercial_variance_qty) === 0
        && flt(net_recovery.total_recovery) === 0
    );
    const residual_after_applied_credit = Boolean(
        existing_applications.length
        && flt(net_recovery.total_recovery) > 0
    );

    const physical_classification =
        wizard_state.business_classification || "";

    const recovery_is_recommended =
        flt(totals.total_recovery) > 0;

    let quantity_heading =
        __("Outstanding Quantity Reviewed");

    let reviewed_quantity = flt(
        quantity.shortage_qty
    );

    let reviewed_uom = quantity.shortage_uom;

    if (
        physical_classification === "recoverable_shortage"
        && recovery_is_recommended
    ) {
        quantity_heading =
            __("Outstanding Quantity to Recover");
    } else if (
        physical_classification === "commercial_variance_only"
    ) {
        quantity_heading =
            __("Commercial Variance Reviewed");
        reviewed_quantity = flt(
            wizard_state.recommendation
                ?.evidence
                ?.facts
                ?.commercial_variance_qty
            || processing_charges.quantity
        );
        reviewed_uom = processing_charges.uom;
    }

    const calculation_heading =
        recovery_is_recommended
            ? __("How This Recovery Was Calculated")
            : __("How This Settlement Was Derived");
    const warnings = integrity.warnings || [];
    const blocking_errors =
        integrity.blocking_errors || [];

    const commercial_waiver_is_relevant = Boolean(
        physical_classification === "commercial_variance_only"
        && recovery_is_recommended
        && blocking_errors.length === 0
        && frm.doc.docstatus === 0
        && !frm.doc.debit_note
    );

    const commercial_waiver_is_allowed = Boolean(
        commercial_waiver_is_relevant
        && frappe.user.has_role("System Manager")
    );

    const commercial_waiver_html = commercial_waiver_is_relevant
        ? `
            <div style="
                border:1px solid var(--orange-300);
                border-left:4px solid var(--orange-500);
                border-radius:8px;
                padding:14px 15px;
                margin:14px 0;
                background:var(--orange-50);
            ">
                <div style="
                    font-size:14px;
                    font-weight:700;
                    margin-bottom:6px;
                ">
                    ${__("Management Decision")}
                </div>

                <div style="line-height:1.5;">
                    ${commercial_waiver_is_allowed
                        ? __(
                            "The effective policy recommends creating the Debit Note shown below. Management may instead choose not to recover this minor amount."
                        )
                        : __(
                            "The effective policy recommends creating the Debit Note shown below. Only a System Manager may approve an alternative waiver."
                        )}
                </div>

                ${commercial_waiver_is_allowed ? `
                    <div style="
                        border-top:1px solid var(--orange-300);
                        margin-top:12px;
                        padding-top:12px;
                    ">
                        <div style="
                            color:var(--orange-700);
                            font-size:11px;
                            font-weight:700;
                            letter-spacing:0.04em;
                            margin-bottom:8px;
                            text-transform:uppercase;
                        ">
                            ${__("Alternative to Debit Note")}
                        </div>

                        <button
                            type="button"
                            class="btn btn-warning btn-sm"
                            data-action="waive-commercial-recovery"
                            style="
                                font-weight:700;
                                min-height:34px;
                                padding:7px 14px;
                            "
                        >
                            ${__("Waive Recommended Recovery")}
                        </button>

                        <div style="
                            color:var(--text-muted);
                            font-size:11px;
                            line-height:1.45;
                            margin-top:7px;
                        ">
                            ${__("A mandatory reason and the approving user will be recorded for audit.")}
                        </div>
                    </div>
                ` : ""}
            </div>
        `
        : "";

    const currency =
        recovery.currency
        || frappe.defaults.get_default("currency")
        || "";

    const escape_text = value => {
        return frappe.utils.escape_html(
            String(value ?? "")
        );
    };

    const format_qty = (value, uom) => {
        const number = flt(value);
        const precision = Number.isInteger(number)
            ? 0
            : 3;

        return [
            format_number(number, null, precision),
            escape_text(uom || "")
        ].filter(Boolean).join(" ");
    };

    const format_money = value => {
        return format_currency(
            flt(value),
            currency
        );
    };

    const get_source_document_label = documents => {
        return (documents || []).length === 1
            ? __("Source Document")
            : __("Source Documents");
    };

    const build_document_links = (
        doctype,
        documents
    ) => {
        const names = documents || [];

        if (!names.length) {
            return `
                <span class="text-muted">—</span>
            `;
        }

        return names.map(name => {
            return `
                <a
                    href="#"
                    class="processor-lot-recovery-document-link"
                    data-doctype="${escape_text(doctype)}"
                    data-name="${escape_text(name)}"
                    style="
                        display:inline-block;
                        margin-right:10px;
                        margin-bottom:3px;
                    "
                >
                    ${escape_text(name)}
                </a>
            `;
        }).join("");
    };

    const raw_material_documents =
        build_document_links(
            raw_material.source_document_type,
            raw_material.source_documents
        );

    const processing_documents =
        build_document_links(
            processing_charges.source_document_type,
            processing_charges.source_documents
        );

    const existing_application_links =
        existing_applications.length
            ? existing_applications.map(application => {
                const links = [
                    [
                        "Processor Material Account Entry",
                        application.name
                    ],
                    [
                        "Stock Entry",
                        application.application_stock_entry
                    ],
                    [
                        "Journal Entry",
                        application.application_journal_entry
                    ]
                ].filter(row => row[1]);

                return `
                    <div style="margin-top:5px;">
                        ${links.map(row => `
                            <a
                                href="#"
                                class="processor-lot-recovery-document-link"
                                data-doctype="${escape_text(row[0])}"
                                data-name="${escape_text(row[1])}"
                                style="
                                    display:inline-block;
                                    margin-right:10px;
                                    margin-bottom:3px;
                                "
                            >
                                ${escape_text(row[1])}
                            </a>
                        `).join("")}
                    </div>
                `;
            }).join("")
            : `<span class="text-muted">—</span>`;

    const material_credit_html = settlement_netting.plan_version
        ? `
            <div style="
                border:2px solid ${
                    credit_is_fully_applied
                        ? "var(--green-500)"
                        : "var(--blue-400)"
                };
                border-radius:7px;
                padding:13px 14px;
                margin-bottom:12px;
                background:${
                    credit_is_fully_applied
                        ? "var(--green-50)"
                        : "var(--blue-50)"
                };
            ">
                <div style="
                    font-size:15px;
                    font-weight:700;
                    margin-bottom:10px;
                ">
                    ${__("Processor Material Credit Netting")}
                </div>

                <div style="
                    display:grid;
                    grid-template-columns:repeat(4, minmax(0, 1fr));
                    gap:10px;
                    margin-bottom:10px;
                ">
                    <div>
                        <div class="text-muted" style="font-size:12px;">
                            ${__("Already Applied")}
                        </div>
                        <strong>
                            ${format_qty(
                                material_credit.already_applied_qty,
                                quantity.shortage_uom
                            )}
                        </strong>
                    </div>
                    <div>
                        <div class="text-muted" style="font-size:12px;">
                            ${__("Unapplied Source Credit Balance")}
                        </div>
                        <strong>
                            ${format_qty(
                                material_credit.available_qty,
                                quantity.shortage_uom
                            )}
                        </strong>
                    </div>
                    <div>
                        <div class="text-muted" style="font-size:12px;">
                            ${__("Residual Processing Basis")}
                        </div>
                        <strong>
                            ${format_qty(
                                net_recovery.commercial_variance_qty,
                                quantity.shortage_uom
                            )}
                        </strong>
                    </div>
                    <div>
                        <div class="text-muted" style="font-size:12px;">
                            ${__("Net Recovery")}
                        </div>
                        <strong>
                            ${format_money(net_recovery.total_recovery)}
                        </strong>
                    </div>
                </div>

                <div style="font-size:12px;">
                    <strong>${__("Settlement Documents")}:</strong>
                    ${existing_application_links}
                </div>

                ${credit_is_fully_applied ? `
                    <div style="
                        margin-top:10px;
                        padding-top:9px;
                        border-top:1px solid var(--border-color);
                        color:var(--green-700);
                        font-weight:600;
                    ">
                        ${__(
                            "The gross shortage and commercial variance are fully covered by submitted Processor Material Credit. No Debit Note is required."
                        )}
                    </div>
                ` : ""}
            </div>
        `
        : "";

    const reasoning_html = reasoning.length
        ? reasoning
            .slice()
            .sort((a, b) => {
                return flt(a.sequence) - flt(b.sequence);
            })
            .map(item => {
                return `
                    <div style="
                        display:flex;
                        align-items:flex-start;
                        gap:9px;
                        padding:7px 0;
                        border-bottom:
                            1px solid var(--border-color);
                    ">
                        <span style="
                            color:var(--green-600);
                            font-weight:700;
                            flex:0 0 auto;
                        ">
                            ✓
                        </span>

                        <span style="line-height:1.45;">
                            ${escape_text(item.message || "")}
                        </span>
                    </div>
                `;
            })
            .join("")
        : `
            <div class="text-muted">
                ${__(
                    "No detailed recovery reasoning was returned."
                )}
            </div>
        `;

    const blocking_errors_html =
        blocking_errors.length
            ? `
                <div style="
                    border-left:4px solid var(--red-500);
                    border-radius:5px;
                    padding:12px 13px;
                    margin-bottom:12px;
                    background:var(--red-50);
                ">
                    <strong>
                        ${__("Settlement Cannot Proceed")}
                    </strong>

                    <ul style="
                        margin:7px 0 0;
                        padding-left:18px;
                    ">
                        ${blocking_errors.map(error => {
                            return `
                                <li>
                                    ${escape_text(
                                        error.message || ""
                                    )}
                                </li>
                            `;
                        }).join("")}
                    </ul>
                </div>
            `
            : "";

    const warnings_html = warnings.length
        ? `
            <div style="
                border-left:4px solid var(--yellow-500);
                border-radius:5px;
                padding:12px 13px;
                margin-bottom:12px;
                background:var(--yellow-50);
            ">
                <strong>
                    ${__("Review Warnings")}
                </strong>

                <ul style="
                    margin:7px 0 0;
                    padding-left:18px;
                ">
                    ${warnings.map(warning => {
                        return `
                            <li>
                                ${escape_text(
                                    warning.message || ""
                                )}
                            </li>
                        `;
                    }).join("")}
                </ul>
            </div>
        `
        : "";

    wrapper.html(`
        <div style="padding:8px 0;">

            ${get_progress_html(4)}

            <div style="
                border:1px solid var(--border-color);
                border-radius:7px;
                padding:15px;
                background:var(--subtle-fg);
            ">

                <div style="
                    font-size:16px;
                    font-weight:600;
                    margin-bottom:15px;
                ">
                    ${__("Step 4 — Review Settlement Calculation")}
                </div>

                ${blocking_errors_html}
                ${warnings_html}

                <div style="
                    border:1px solid #ead4bb;
                    border-radius:7px;
                    padding:12px 14px;
                    margin-bottom:12px;
                    background:#fff8ef;
                ">
                    <div class="text-muted" style="
                        font-size:12px;
                        margin-bottom:3px;
                    ">
                        ${quantity_heading}
                    </div>

                    <div style="
                        font-size:21px;
                        font-weight:700;
                        color:#d35400;
                    ">
                        ${format_qty(
                            reviewed_quantity,
                            reviewed_uom
                        )}
                    </div>
                </div>

                <div style="
                    border:1px solid var(--border-color);
                    border-radius:7px;
                    padding:13px 14px;
                    margin-bottom:12px;
                    background:var(--card-bg);
                ">
                    <div style="
                        display:flex;
                        align-items:flex-start;
                        justify-content:space-between;
                        gap:15px;
                        margin-bottom:8px;
                    ">
                        <div>
                            <div style="
                                font-size:15px;
                                font-weight:700;
                                margin-bottom:3px;
                            ">
                                ${__("Raw Material Recovery")}
                            </div>

                            <div class="text-muted">
                                ${escape_text(
                                    raw_material.calculation || ""
                                )}
                            </div>
                        </div>

                        <div style="
                            font-size:20px;
                            font-weight:700;
                            white-space:nowrap;
                        ">
                            ${format_money(
                                raw_material.amount
                            )}
                        </div>
                    </div>

                    <div style="
                        border-top:
                            1px solid var(--border-color);
                        padding-top:8px;
                        margin-top:8px;
                        font-size:12px;
                    ">
                        <div style="margin-bottom:5px;">
                            <strong>
                                ${__("Rate Source")}:
                            </strong>

                            ${escape_text(
                                raw_material.rate_source || ""
                            )}
                        </div>

                        <div>
                            <strong>
                                ${get_source_document_label(
                                    raw_material.source_documents
                                )}:
                            </strong>

                            <div style="margin-top:3px;">
                                ${raw_material_documents}
                            </div>
                        </div>
                    </div>
                </div>

                <div style="
                    border:1px solid var(--border-color);
                    border-radius:7px;
                    padding:13px 14px;
                    margin-bottom:12px;
                    background:var(--card-bg);
                ">
                    <div style="
                        display:flex;
                        align-items:flex-start;
                        justify-content:space-between;
                        gap:15px;
                        margin-bottom:8px;
                    ">
                        <div>
                            <div style="
                                font-size:15px;
                                font-weight:700;
                                margin-bottom:3px;
                            ">
                                ${__("Processing Charge Recovery")}
                            </div>

                            <div class="text-muted">
                                ${escape_text(
                                    processing_charges.calculation
                                    || ""
                                )}
                            </div>
                        </div>

                        <div style="
                            font-size:20px;
                            font-weight:700;
                            white-space:nowrap;
                        ">
                            ${format_money(
                                processing_charges.amount
                            )}
                        </div>
                    </div>

                    <div style="
                        border-top:
                            1px solid var(--border-color);
                        padding-top:8px;
                        margin-top:8px;
                        font-size:12px;
                    ">
                        <div style="margin-bottom:5px;">
                            <strong>
                                ${__("Rate Source")}:
                            </strong>

                            ${escape_text(
                                processing_charges.rate_source
                                || ""
                            )}
                        </div>

                        <div>
                            <strong>
                                ${get_source_document_label(
                                    processing_charges.source_documents
                                )}:
                            </strong>

                            <div style="margin-top:3px;">
                                ${processing_documents}
                            </div>
                        </div>
                    </div>
                </div>

                <div style="
                    display:flex;
                    align-items:center;
                    justify-content:space-between;
                    gap:15px;
                    border:2px solid var(--green-500);
                    border-radius:7px;
                    padding:14px;
                    margin-bottom:12px;
                    background:var(--green-50);
                ">
                    <div style="
                        font-size:15px;
                        font-weight:700;
                    ">
                        ${settlement_netting.plan_version
                            ? __("Gross Recommended Recovery")
                            : __("Total Recommended Recovery")}
                    </div>

                    <div style="
                        font-size:23px;
                        font-weight:800;
                        white-space:nowrap;
                    ">
                        ${format_money(
                            totals.total_recovery
                        )}
                    </div>
                </div>

                ${material_credit_html}

                ${commercial_waiver_html}

                <details style="
                    border:1px solid var(--border-color);
                    border-radius:7px;
                    padding:12px 13px;
                    background:var(--card-bg);
                ">
                    <summary style="
                        cursor:pointer;
                        font-weight:600;
                    ">
                        ${calculation_heading}
                    </summary>

                    <div style="margin-top:8px;">
                        ${reasoning_html}
                    </div>
                </details>

            </div>
        </div>
    `);

    wrapper
        .find(".processor-lot-recovery-document-link")
        .off("click.processor_lot_recovery")
        .on("click.processor_lot_recovery", event => {
            event.preventDefault();

            const link = $(event.currentTarget);
            const doctype = link.data("doctype");
            const name = link.data("name");

            if (doctype && name) {
                frappe.set_route(
                    "Form",
                    doctype,
                    name
                );
            }
        });

    wrapper
        .find('[data-action="waive-commercial-recovery"]')
        .off("click.processor_lot_waiver")
        .on("click.processor_lot_waiver", event => {
            event.preventDefault();
            waive_commercial_recovery();
        });

    dialog.set_secondary_action_label(__("Back"));

    dialog.set_secondary_action(() => {
        if (wizard_state.recommendation) {
            display_recommendation(
                wizard_state.recommendation
            );
            return;
        }

        render_step_3();
    });

    if (credit_is_fully_applied) {
        dialog.set_primary_action(
            __("Close Review"),
            () => {
                dialog.hide();
                frm.reload_doc();
            }
        );
    } else if (proposed_applications.length) {
        dialog.set_primary_action(
            __("Create Draft Credit Application"),
            () => {
                frappe.confirm(
                    __(
                        "Create the linked draft Stock Entry, Journal Entry and Processor Material Account Entry for this material-credit application?"
                    ),
                    () => create_draft_credit_application()
                );
            }
        );
    } else if (residual_after_applied_credit) {
        dialog.set_primary_action(
            __("Create Draft Residual Debit Note"),
            () => {
                create_draft_debit_note();
            }
        );
    } else {
        dialog.set_primary_action(
            __("Create Draft Debit Note"),
            () => {
                create_draft_debit_note();
            }
        );
    }

    dialog.get_primary_btn().prop(
        "disabled",
        blocking_errors.length > 0
    );

    function waive_commercial_recovery() {
        if (!frappe.user.has_role("System Manager")) {
            frappe.msgprint({
                title: __("System Manager Required"),
                indicator: "orange",
                message: __(
                    "Only a System Manager may waive the recommended recovery."
                )
            });
            return;
        }

        if (frm.is_dirty()) {
            frappe.msgprint({
                title: __("Unsaved Changes"),
                indicator: "orange",
                message: __(
                    "Save or discard the existing Processor Lot changes before recording the waiver."
                )
            });
            return;
        }

        frappe.prompt(
            [
                {
                    fieldname: "waiver_reason",
                    fieldtype: "Small Text",
                    label: __("Waiver Reason"),
                    reqd: 1
                }
            ],
            values => {
                const reason = String(
                    values.waiver_reason || ""
                ).trim();

                if (!reason) {
                    return;
                }

                frappe.confirm(
                    __(
                        "Waive the recommended processing-charge recovery and record this reason on the Processor Lot?"
                    ),
                    () => {
                        Promise.resolve(
                            frm.set_value(
                                "override_settlement_policy",
                                1
                            )
                        )
                            .then(() => frm.set_value(
                                "recover_processing_charges_on_shortage",
                                0
                            ))
                            .then(() => frm.set_value(
                                "settlement_policy_override_reason",
                                reason
                            ))
                            .then(() => frm.save())
                            .then(() => {
                                dialog.hide();
                                frappe.show_alert({
                                    message: __(
                                        "Commercial recovery waiver recorded."
                                    ),
                                    indicator: "green"
                                });
                            });
                    }
                );
            },
            __("Waive Recommended Recovery"),
            __("Review Waiver")
        );
    }

    function create_draft_credit_application() {
        if (frm.is_new() || !frm.doc.name || frm.is_dirty()) {
            frappe.msgprint({
                title: __("Save Processor Lot"),
                indicator: "orange",
                message: __(
                    "Save the Processor Lot before creating credit-application documents."
                )
            });
            return;
        }

        const primary_button = dialog.get_primary_btn();
        primary_button.prop("disabled", true);

        frappe.call({
            method: [
                "subcontracting_extensions",
                "subcontracting_extensions",
                "doctype",
                "processor_lot",
                "settlement_application_engine",
                "create_credit_application_documents"
            ].join("."),
            args: {
                processor_lot: frm.doc.name,
                business_classification:
                    wizard_state.business_classification
            },
            freeze: true,
            freeze_message: __(
                "Creating Draft Processor Material Credit application documents..."
            ),
            callback(r) {
                const result = r.message || {};
                const applications = result.applications || [];

                if (!applications.length) {
                    primary_button.prop("disabled", false);
                    frappe.msgprint({
                        title: __("Credit Application Not Created"),
                        indicator: "red",
                        message: __(
                            "The settlement application engine did not return any created documents."
                        )
                    });
                    return;
                }

                dialog.hide();
                frm.reload_doc().then(() => {
                    const created_status = {
                        bundles: applications.map(
                            (application, index) => ({
                                sequence: index + 1,
                                processor_material_account_entry:
                                    application.processor_material_account_entry,
                                pma_docstatus: 0,
                                stock_entry: application.stock_entry,
                                stock_entry_docstatus: 0,
                                journal_entry: application.journal_entry,
                                journal_entry_docstatus: 0,
                                against_entry: application.against_entry,
                                account_qty: application.account_qty,
                                commercial_qty: application.commercial_qty,
                                account_uom: application.account_uom,
                                state: "Pending",
                                next_doctype: "Stock Entry"
                            })
                        )
                    };
                    frappe.msgprint({
                        title: __("Draft Credit Application Created"),
                        indicator: "green",
                        message: `
                            <p>
                                ${__(
                                    "Submit each bundle in this order: Stock Entry, Journal Entry, then Processor Material Account Entry. Links open in new tabs."
                                )}
                            </p>
                            ${credit_application_bundles_html(
                                created_status
                            )}
                        `
                    });
                });
            },
            error() {
                primary_button.prop("disabled", false);
            }
        });
    }

    /**
     * Create the draft Debit Note through the server-side Settlement Engine.
     *
     * The browser sends only the saved Processor Lot identity and the selected
     * business classification. Quantities, rates and amounts are recalculated
     * server-side immediately before document creation.
     */
    function create_draft_debit_note() {
        if (frm.is_new() || !frm.doc.name) {
            frappe.msgprint({
                title: __("Save Processor Lot"),
                indicator: "orange",
                message: __(
                    "Save the Processor Lot before creating its Draft Debit Note."
                )
            });
            return;
        }

        if (frm.is_dirty()) {
            frappe.msgprint({
                title: __("Unsaved Changes"),
                indicator: "orange",
                message: __(
                    "Save the Processor Lot before creating its Draft Debit Note."
                )
            });
            return;
        }

        if (frm.doc.docstatus === 2) {
            frappe.msgprint({
                title: __("Processor Lot Cancelled"),
                indicator: "red",
                message: __(
                    "A cancelled Processor Lot cannot create a Debit Note."
                )
            });
            return;
        }

        if (frm.doc.debit_note) {
            frappe.msgprint({
                title: __("Debit Note Already Created"),
                indicator: "blue",
                message: __(
                    "Debit Note {0} is already linked to this Processor Lot.",
                    [frappe.utils.escape_html(frm.doc.debit_note)]
                )
            });
            return;
        }

        if (!wizard_state.business_classification) {
            frappe.msgprint({
                title: __("Classification Required"),
                indicator: "orange",
                message: __(
                    "Select the business classification before creating the Debit Note."
                )
            });
            return;
        }

        const primary_button = dialog.get_primary_btn();

        primary_button.prop("disabled", true);

        frappe.call({
            method: [
                "subcontracting_extensions",
                "subcontracting_extensions",
                "doctype",
                "processor_lot",
                "processor_lot",
                "create_processor_lot_debit_note"
            ].join("."),
            args: {
                processor_lot: frm.doc.name,
                business_classification:
                    wizard_state.business_classification
            },
            freeze: true,
            freeze_message: __(
                "Recalculating settlement and creating Draft Debit Note..."
            ),
            callback(r) {
                const result = r.message;

                if (!result || !result.name) {
                    primary_button.prop("disabled", false);

                    frappe.msgprint({
                        title: __("Debit Note Not Created"),
                        indicator: "red",
                        message: __(
                            "The Settlement Engine did not return a created Purchase Invoice."
                        )
                    });

                    return;
                }

                dialog.hide();

                frm.reload_doc().then(() => {
                    /*
                    * Explicitly redraw the Processor Lot guidance after the Settlement
                    * Engine has linked the Draft Debit Note and updated settlement status.
                    *
                    * reload_doc normally triggers a form refresh, but these HTML panels
                    * are asynchronous and should not remain on their pre-settlement state.
                    */
                    set_status_indicator(frm);
                    render_receipt_journey(frm);
                    render_physical_receipt_position(frm);
                    render_health_panel(frm);

                    frappe.msgprint({
                        title: __("Draft Debit Note Created"),
                        indicator: "green",
                        message: result.message || __(
                            "Draft Debit Note {0} has been created for review.",
                            [
                                frappe.utils.get_form_link(
                                    result.doctype
                                        || "Purchase Invoice",
                                    result.name,
                                    true
                                )
                            ]
                        ),
                        primary_action: {
                            label: __("Open Debit Note"),
                            action() {
                                frappe.set_route(
                                    "Form",
                                    result.doctype
                                        || "Purchase Invoice",
                                    result.name
                                );
                            }
                        }
                    });
                });
            },
            error() {
                primary_button.prop("disabled", false);
            }
        });
    }
}

dialog.show();
render_step_1();
}

/**
 * Open a new Processor Lot Receipt for one arriving truck.
 *
 * The receipt is opened as an unsaved document so an abandoned action does
 * not leave behind an empty PLR. The Processor Lot link allows the PLR to
 * fetch its controlled SCO, supplier, company and warehouse information.
 */
function add_receive_truck_button(frm) {
    const receipt_blocked_statuses = [
        "Debit Note Created",
        "Reversal In Progress",
        "Reopened",
        "Reopened - Debit Note Created",
        "Completed",
        "Cancelled"
    ];

    if (
        frm.is_new()
        || frm.doc.docstatus === 2
        || receipt_blocked_statuses.includes(frm.doc.settlement_status)
    ) {
        return;
    }

    frm.add_custom_button(
        __("Receive Truck"),
        () => {
            frappe.new_doc(
                "Processor Lot Receipt",
                {
                    processor_lot: frm.doc.name,
                    physical_receipt_date: frappe.datetime.get_today()
                }
            );
        },
        __("Create")
    );

    frm.change_custom_button_type(
        __("Receive Truck"),
        __("Create"),
        "primary"
    );
}

/**
 * Highlight Settlement Policy Source when the effective policy
 * does not originate from the Purchase Order.
 *
 * Read-only Frappe fields are displayed through .control-value rather
 * than .control-input, so the visible read-only value is styled here.
 */
function highlight_settlement_policy_source(frm) {
    const field = frm.get_field(
        "settlement_policy_source"
    );

    if (!field || !field.$wrapper) {
        return;
    }

    const is_deviation = Boolean(
        frm.doc.settlement_policy_source
        && frm.doc.settlement_policy_source !== "Purchase Order"
    );

    const value_area = field.$wrapper.find(
        ".control-value"
    );

    const label_area = field.$wrapper.find(
        ".label-area"
    );

    if (is_deviation) {
        value_area.css({
            "background-color": "#fff3f3",
            "border": "1px solid #dc3545",
            "border-radius": "6px",
            "padding": "6px 10px",
            "color": "#b02a37",
            "font-weight": "600"
        });

        label_area.css({
            "color": "#b02a37",
            "font-weight": "600"
        });
    } else {
        value_area.css({
            "background-color": "",
            "border": "",
            "border-radius": "",
            "padding": "",
            "color": "",
            "font-weight": ""
        });

        label_area.css({
            "color": "",
            "font-weight": ""
        });
    }
}
