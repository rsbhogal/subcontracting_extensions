/* J14 DOM-string contract, links, small residuals, permissions and async races. */
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const path = require("path");
const context = {__: s => s, frappe: {ui: {form: {on() {}}}, utils: {
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
    evidence_consistent: true, settlement_enabled: false, issues: [],
    components: [{component_item: "Wire <unsafe>", stock_uom: "Kg", supplied_qty: 500, consumed_qty: 500,
        returned_qty: 0, remaining_qty: 0, material_balanced: true, evidence_consistent: true, issues: []},
        {component_item: "Blank", stock_uom: "Units", supplied_qty: 100, consumed_qty: 100,
        returned_qty: 0, remaining_qty: 0, material_balanced: true, evidence_consistent: true, issues: []}],
    sources: [{doctype: "Stock Entry", name: 'STE/a?"<>', docstatus: 1}],
    movements: [{parent: "STE", item_code: "Wire", stock_uom: "Kg", stock_qty: 500,
        s_warehouse: "Factory", t_warehouse: "Processor", sco_rm_detail: "RM"}],
    consumptions: [{parent: "SCR", rm_item_code: "Wire", stock_uom: "Kg", consumed_qty: 500, reference_name: "SCR-ROW"}]});
(async () => {
    const frm = form();
    let report = data();
    context.frappe.call = async () => ({message: report});
    await context.render_j14_material_panel(frm);
    const html = () => frm.get_field("material_reconciliation_html").$wrapper.markup;
    const hidden = () => frm.get_field("material_reconciliation_section").df.hidden;
    assert.strictEqual(hidden(), 0);
    assert(html().includes("Material quantities reconciled"));
    assert(html().includes('data-status-tone="green"'));
    assert(html().includes("500.000") && html().includes("100.000") && !html().includes("600.000"));
    assert(html().includes("Wire &lt;unsafe&gt;") && !html().includes("Wire <unsafe>"));
    assert(html().includes("/app/stock-entry/STE%2Fa%3F%22%3C%3E"));
    assert(html().includes("/app/subcontracting-receipt/SCR"));
    assert(html().includes("Settlement not enabled"));
    report.material_balanced = false;
    report.issues = ["MATERIAL_BALANCE_REMAINS"];
    Object.assign(report.components[0], {material_balanced: false, remaining_qty: 0.000001, issues: report.issues});
    await context.render_j14_material_panel(frm);
    assert(html().includes("0.000001"));
    assert(html().includes('data-status-tone="amber"'));
    assert.strictEqual(context.j14_qty(500), "500.000");
    assert.strictEqual(context.j14_qty(-0.000001), "-0.000001");
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
    console.log("J14 material statuses, links, precision, escaping, permissions and stale responses: PASS");
})().catch(error => {console.error(error); process.exitCode = 1;});
