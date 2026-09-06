/* J14 DOM-string contract, links, small residuals, permissions and async races. */
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const path = require("path");
const storage = new Map();
const context = {__: s => s, localStorage: {getItem: key => storage.get(key) || null,
    setItem: (key, value) => storage.set(key, value)}, frappe: {session: {user: "user@example.com"}, ui: {form: {on() {}}}, utils: {
    escape_html: s => String(s).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;")
}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.resolve(__dirname,
    "../../subcontracting_extensions/doctype/processor_lot/processor_lot.js"), "utf8"), context);
function form() {
    const fields = new Map();
    const get = key => {
        if (!fields.has(key)) fields.set(key, {df: {hidden: 0}, $wrapper: {html(value) {this.markup = value;}}});
        return fields.get(key);
    };
    return {doc: {name: "LOT", subcontracting_order: "SCO"}, is_new: () => false,
        get_field: get, set_df_property(key, field, value) {get(key).df[field] = value;}};
}
const data = () => ({enabled: true, processor_lot: "LOT", subcontracting_order: "SCO", material_balanced: true,
    material_accounted: true,
    material_settlement_eligible: true, material_next_action: "REVIEW_RECEIPT_AND_INVOICING",
    material_next_action_label: "Review receipt and invoicing journey",
    material_next_action_detail: "Material quantities are accounted for. Confirm receipt evidence.",
    settlement_eligibility_scope: "Material quantities only; no settlement write",
    evidence_consistent: true, settlement_enabled: false, issues: [],
    components: [{component_item: "Wire <unsafe>", stock_uom: "Kg", supplied_qty: 500, consumed_qty: 500,
        returned_qty: 0, remaining_qty: 0, physical_remaining_qty: 0, applied_credit_qty: 0,
        unaccounted_remaining_qty: 0, material_balanced: true, material_accounted: true, evidence_consistent: true,
        material_settlement_eligible: true, material_next_action_label: "No further material action",
        material_next_action_detail: "Physically reconciled.", issues: []},
        {component_item: "Blank", stock_uom: "Units", supplied_qty: 100, consumed_qty: 100,
        returned_qty: 0, remaining_qty: 0, physical_remaining_qty: 0, applied_credit_qty: 0,
        unaccounted_remaining_qty: 0, material_balanced: true, material_accounted: true, evidence_consistent: true,
        material_settlement_eligible: true, material_next_action_label: "No further material action",
        material_next_action_detail: "Physically reconciled.", issues: []}],
    sources: [{doctype: "Stock Entry", name: 'STE/a?"<>', docstatus: 1}],
    movements: [{parent: "STE", evidence_role: "Physical transfer or return", item_code: "Wire", stock_uom: "Kg", stock_qty: 500,
        s_warehouse: "Factory", t_warehouse: "Processor", sco_rm_detail: "RM"}],
    consumptions: [{parent: "SCR", rm_item_code: "Wire", stock_uom: "Kg", consumed_qty: 500, reference_name: "SCR-ROW"}],
    adjustments: [], settlement_evidence: []});
(async () => {
    const frm = form();
    let report = data();
    context.frappe.call = async () => ({message: report});
    await context.render_j14_material_panel(frm);
    const html = () => frm.get_field("material_reconciliation_html").$wrapper.markup;
    const hidden = () => frm.get_field("material_reconciliation_section").df.hidden;
    assert.strictEqual(hidden(), 1);
    assert.strictEqual(frm.get_field("operational_guidance_section").df.hidden, 0);
    assert(frm.get_field("operational_guidance_html").$wrapper.markup.includes("Show detailed audit evidence"));
    context.j16_apply_view(frm, false);
    assert.strictEqual(frm.get_field("settlement_policy_override_section").df.hidden, 1);
    context.j16_apply_view(frm, true);
    assert.strictEqual(hidden(), 0);
    context.j16_apply_view(frm, false);
    assert.strictEqual(hidden(), 1);
    context.j16_set_detailed_preference(true);
    assert.strictEqual(context.j16_detailed_preference(), true);
    context.j16_set_detailed_preference(false);
    const excess = context.j16_guidance_state(frm, data(), {enabled: true, journey_complete: false,
        items: [{subcontracting_order_item: "FG-A", stock_uom: "Kg", excess_reserved_qty: 201,
            issues: ["EXCESS_CAPACITY_COMMITMENT"]}],
        journeys: [{subcontracting_order_item: "FG-A", processor_lot_receipt: 'PLR/a?"<>', scr_verified: false}]});
    assert(excess.title.includes("Review draft receipt reservation"));
    assert(excess.detail.includes("201.000 Kg"));
    assert(excess.link.includes("/app/processor-lot-receipt/PLR%2Fa%3F%22%3C%3E"));
    frm.doc.docstatus = 1;
    frm.doc.settlement_status = "Completed";
    const historical = context.j16_guidance_state(frm, data(), {enabled: true, journey_complete: false});
    assert(historical.title.includes("No current material action required"));
    frm.doc.docstatus = 0;
    frm.doc.settlement_status = "Draft";
    assert(html().includes("Physically reconciled"));
    assert(html().includes("What to do next"));
    assert(html().includes("Review receipt and invoicing journey"));
    assert(html().includes("Next Material Action"));
    assert(html().includes('data-status-tone="green"'));
    assert(html().includes("500.000") && html().includes("100.000") && !html().includes("600.000"));
    assert(html().includes("Wire &lt;unsafe&gt;") && !html().includes("Wire <unsafe>"));
    assert(html().includes("/app/stock-entry/STE%2Fa%3F%22%3C%3E"));
    assert(html().includes("/app/subcontracting-receipt/SCR"));
    assert(html().includes("Settlement not enabled"));
    report.material_balanced = false;
    report.issues = ["MATERIAL_BALANCE_REMAINS"];
    report.material_accounted = false;
    report.material_settlement_eligible = false;
    report.material_next_action_label = "Account for remaining material";
    report.material_next_action_detail = "Complete the indicated component actions.";
    Object.assign(report.components[0], {material_balanced: false, material_accounted: false,
        remaining_qty: 0.000001, physical_remaining_qty: 0.000001,
        unaccounted_remaining_qty: 0.000001, material_settlement_eligible: false,
        material_next_action_label: "Account for remaining material",
        material_next_action_detail: "Record return, credit, or recovery.", issues: report.issues});
    await context.render_j14_material_panel(frm);
    assert(html().includes("0.000001"));
    assert(html().includes('data-status-tone="amber"'));
    assert.strictEqual(context.j14_qty(500), "500.000");
    assert.strictEqual(context.j14_qty(-0.000001), "-0.000001");
    report = data();
    report.material_balanced = false;
    Object.assign(report.components[0], {consumed_qty: 490, remaining_qty: 10, physical_remaining_qty: 10,
        applied_credit_qty: 10, unaccounted_remaining_qty: 0, material_balanced: false, material_accounted: true});
    report.adjustments = [{doctype: "Processor Material Account Entry", name: 'PMA/a?"<>', docstatus: 1,
        entry_type: "Credit Applied", principal_component: "Wire <unsafe>", account_uom: "Kg",
        account_qty: 10, sco_supplied_item: "RM-A"}];
    report.movements.push({parent: "APP-SE", evidence_role: "Material credit application",
        processor_material_account_entry: 'PMA/a?"<>', item_code: "Wire", stock_uom: "Kg", stock_qty: 10,
        s_warehouse: "Processor", t_warehouse: null, sco_rm_detail: null});
    report.settlement_evidence = [{doctype: "Purchase Invoice", name: "DN-1", reason: "Debit Note"}];
    await context.render_j14_material_panel(frm);
    assert(html().includes("Physical balance covered by submitted credit"));
    assert(html().includes("Accounted by credit"));
    assert(html().includes("Physical Remaining") && html().includes("Applied Credit") && html().includes("Unaccounted Remaining"));
    assert(html().includes("/app/processor-material-account-entry/PMA%2Fa%3F%22%3C%3E"));
    assert(html().includes("Wire &lt;unsafe&gt;") && !html().includes("Wire <unsafe>"));
    assert(html().includes("RM-A"));
    assert(html().includes("Material credit application"));
    assert(html().includes("/app/stock-entry/APP-SE"));
    assert(html().includes("/app/purchase-invoice/DN-1"));
    report.evidence_consistent = false;
    report.issues = ["NEGATIVE_MATERIAL_BALANCE", "<unknown>"];
    await context.render_j14_material_panel(frm);
    assert(html().includes('data-status-tone="red"'));
    assert(html().includes("&lt;unknown&gt;"));
    context.frappe.call = async () => {throw Error("SECRET-DOCUMENT");};
    await context.render_j14_material_panel(frm);
    assert(html().includes("Material evidence unavailable"));
    assert(!html().includes("SECRET") && !html().includes("500.000"));
    context.frappe.call = async () => ({message: {enabled: false}});
    await context.render_j14_material_panel(frm);
    assert.strictEqual(hidden(), 1);
    assert.strictEqual(html(), "");
    let releases = [];
    context.frappe.call = () => new Promise(resolve => releases.push(resolve));
    const old = context.render_j14_material_panel(frm);
    const recent = context.render_j14_material_panel(frm);
    releases[1]({message: data()});
    await recent;
    const rendered = html();
    releases[0]({message: {...data(), issues: ["OLD"]}});
    await old;
    assert.strictEqual(html(), rendered);
    releases = [];
    const changed = context.render_j14_material_panel(frm);
    frm.doc.subcontracting_order = "OTHER";
    releases[0]({message: data()});
    await changed;
    assert.strictEqual(html(), "");
    frm.doc.subcontracting_order = "SCO";
    context.frappe.call = async () => ({message: {...data(), processor_lot: "OTHER"}});
    await context.render_j14_material_panel(frm);
    assert(html().includes("Material evidence unavailable"));
    frm.is_new = () => true;
    context.frappe.call = () => {throw Error("Should not call for new document");};
    await context.render_j14_material_panel(frm);
    assert.strictEqual(hidden(), 1);
    console.log("J16B simple/detailed view, exact guidance, evidence links, escaping, permissions and stale responses: PASS");
})().catch(error => {console.error(error); process.exitCode = 1;});
