/* J19A3 read-only commercial panel: exact rows, safety, escaping and async races. */
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const context = {__: value => value, localStorage: {getItem: () => null, setItem() {}},
    frappe: {session: {user: "user@example.com"}, ui: {form: {on() {}}}, utils: {
        escape_html: value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;").replaceAll('"', "&quot;")
    }}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.resolve(__dirname,
    "../../subcontracting_extensions/doctype/processor_lot/processor_lot.js"), "utf8"), context);

function form() {
    const fields = new Map();
    const get = key => {
        if (!fields.has(key)) fields.set(key, {df: {hidden: 0}, $wrapper: {
            html(value) { if (value !== undefined) this.markup = value; return this.markup; },
            find() { return {off() {return this;}, on() {return this;}}; },
        }});
        return fields.get(key);
    };
    return {doc: {name: "LOT", subcontracting_order: "SCO", docstatus: 0, settlement_status: "Draft"},
        is_new: () => false, get_field: get,
        set_df_property(key, field, value) {get(key).df[field] = value;}};
}

function material() {
    return {enabled: true, processor_lot: "LOT", subcontracting_order: "SCO",
        evidence_consistent: true, material_settlement_eligible: true,
        commercial_policy_status: "DEFERRED", components: [
            {sco_supplied_item: "RM-KG", component_item: "Wire <unsafe>", stock_uom: "Kg",
                physical_remaining_qty: 0, applied_credit_qty: 0, unaccounted_remaining_qty: 0,
                evidence_consistent: true, material_settlement_eligible: true,
                material_next_action_label: "No further material action",
                component_return_label: "No component return required", component_return_detail: "None",
                draft_return_reserved_qty: 0, return_qty_available_to_prepare: 0},
            {sco_supplied_item: "RM-UNIT", component_item: "Blank", stock_uom: "Units",
                physical_remaining_qty: 0, applied_credit_qty: 0, unaccounted_remaining_qty: 0,
                evidence_consistent: true, material_settlement_eligible: true,
                material_next_action_label: "No further material action",
                component_return_label: "No component return required", component_return_detail: "None",
                draft_return_reserved_qty: 0, return_qty_available_to_prepare: 0},
        ], issues: [], movements: [], consumptions: [], adjustments: [], settlement_evidence: []};
}

function commercial() {
    return {enabled: true, processor_lot: "LOT", subcontracting_order: "SCO",
        commercial_decision_code: "COMMERCIAL_REVIEW_COMPLETE_NO_RECOVERY",
        commercial_review_permitted: true, commercial_document_authorized: false,
        commercial_document_creation_enabled: false, lot_closure_authorized: false, policy_issues: [],
        settlement_policy: {policy_source: "Purchase Order", policy_ready: true,
            shortage_settlement_method: "PENDING_INVESTIGATION",
            shortage_settlement_method_label: "Pending Investigation",
            excess_settlement_method: "PENDING_OWNERSHIP_INVESTIGATION",
            excess_settlement_method_label: "Pending Ownership Investigation",
            recovery_customer: "Shiv <unsafe>", recovery_customer_ready: true},
        components: [
            {sco_supplied_item: "RM-KG", component_item: "Wire <unsafe>", stock_uom: "Kg",
                physical_remaining_qty: 0, applied_credit_qty: 0, unaccounted_remaining_qty: 0,
                commercial_decision_code: "NO_RAW_MATERIAL_RECOVERY", commercial_review_permitted: true,
                persisted_classification: {classification: "PROCESSOR_RESPONSIBLE",
                    selected_treatment_method: "COMMERCIAL_WAIVER", classification_revision: 2,
                    last_decision_by: "auditor<unsafe>", last_decision_at: "2026-09-09 10:00:00",
                    decision_events: [{reason: "Approved <reason>"}]}},
            {sco_supplied_item: "RM-UNIT", component_item: "Blank", stock_uom: "Units",
                physical_remaining_qty: 0, applied_credit_qty: 0, unaccounted_remaining_qty: 0,
                commercial_decision_code: "RAW_MATERIAL_CREDIT_ACCOUNTED", commercial_review_permitted: true},
        ], finished_items: [
            {sco_finished_item: "FG-KG", finished_item: "Drawn <unsafe>", stock_uom: "Kg",
                company_accepted_qty: 500, supplier_invoice_qty: 500, commercial_variance_qty: 0,
                commercial_decision_code: "NO_PROCESSING_RECOVERY", commercial_review_permitted: true,
                matched_invoice_rows: [{purchase_invoice: 'PI/a?"<>', purchase_invoice_item: 'ROW<1>'}]},
            {sco_finished_item: "FG-UNIT", finished_item: "Cup", stock_uom: "Units",
                company_accepted_qty: 100, supplier_invoice_qty: 100, commercial_variance_qty: 0,
                commercial_decision_code: "NO_PROCESSING_RECOVERY", commercial_review_permitted: true,
                matched_invoice_rows: [{purchase_invoice: "PI-2", purchase_invoice_item: "ROW-2"}]},
        ], legacy_evidence: []};
}

(async () => {
    const report = commercial();
    let html = context.build_j19_commercial_panel(report);
    assert(html.includes("Component commercial preview"));
    assert(html.includes("Read-only preview. No commercial document or lot closure is authorised."));
    assert(html.includes("Policy ready") && html.includes("Pending Investigation"));
    assert(html.includes("Pending Ownership Investigation"));
    assert(html.includes("Shiv &lt;unsafe&gt;") && !html.includes("Shiv <unsafe>"));
    assert(html.includes("500.000") && html.includes("100.000") && !html.includes("600.000"));
    assert(html.includes("Drawn &lt;unsafe&gt;") && !html.includes("Drawn <unsafe>"));
    assert(html.includes("/app/purchase-invoice/PI%2Fa%3F%22%3C%3E"));
    assert(html.includes("ROW&lt;1&gt;") && !html.includes("ROW<1>"));
    assert(html.includes("Persisted Classification") && html.includes("PROCESSOR_RESPONSIBLE"));
    assert(html.includes("COMMERCIAL_WAIVER") && html.includes("auditor&lt;unsafe&gt;"));
    assert(html.includes("Approved &lt;reason&gt;") && !html.includes("Approved <reason>"));
    assert(!html.includes("Create Debit Note") && !html.includes("<button"));

    report.policy_issues = ["PROCESSOR_LOT_POLICY_DIFFERS_FROM_PURCHASE_ORDER"];
    report.legacy_evidence = [{doctype: "Processor Material Account Entry", name: 'PMA/a?"<>',
        reason: "Missing exact SCO supplied row <unsafe>"}];
    html = context.build_j19_commercial_panel(report);
    assert(html.includes("Settlement policy requires review"));
    assert(html.includes("/app/processor-material-account-entry/PMA%2Fa%3F%22%3C%3E"));
    assert(html.includes("&lt;unsafe&gt;") && !html.includes("<unsafe>"));

    const frm = form();
    context.frappe.call = async options => ({message: options.method.endsWith("get_material_panel")
        ? material() : commercial()});
    await context.render_j14_material_panel(frm);
    const guidance = frm.get_field("operational_guidance_html").$wrapper.markup;
    assert(guidance.includes("Component commercial preview"));
    assert(guidance.includes("No raw-material recovery"));
    assert.strictEqual(frm.__j19_commercial_report.commercial_reader_version, undefined);

    context.frappe.call = async options => ({message: options.method.endsWith("get_material_panel")
        ? material() : {enabled: false}});
    await context.render_j14_material_panel(frm);
    assert.strictEqual(frm.__j19_commercial_report, null);
    assert(!frm.get_field("operational_guidance_html").$wrapper.markup.includes("Component commercial preview"));

    context.frappe.call = async options => {
        if (options.method.endsWith("get_material_panel")) return {message: material()};
        throw Error("SECRET-COMMERCIAL-DOCUMENT");
    };
    await context.render_j14_material_panel(frm);
    html = frm.get_field("operational_guidance_html").$wrapper.markup;
    assert(html.includes("Commercial evidence unavailable"));
    assert(!html.includes("SECRET-COMMERCIAL-DOCUMENT"));

    context.frappe.call = async () => ({message: {...commercial(), processor_lot: "OTHER"}});
    await context.load_j19_commercial_preview(frm, () => true, "LOT", "SCO");
    assert.strictEqual(frm.__j19_commercial_report.enabled, true);
    assert.strictEqual(frm.__j19_commercial_report.error, true);

    let release;
    context.frappe.call = () => new Promise(resolve => {release = resolve;});
    let current = true;
    const pending = context.load_j19_commercial_preview(frm, () => current, "LOT", "SCO");
    current = false;
    release({message: commercial()});
    await pending;
    assert.strictEqual(frm.__j19_commercial_report, null);

    console.log("J19A3 read-only commercial panel, mixed UOM, escaping, permissions and stale responses: PASS");
})().catch(error => {console.error(error); process.exitCode = 1;});
