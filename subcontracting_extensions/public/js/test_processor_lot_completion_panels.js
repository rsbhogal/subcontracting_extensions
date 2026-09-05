/* J12: Node-only panel, permission-failure and stale-response checks. */
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
vm.runInContext("hide_items_grid_controls = () => {};", context);
function form() {
    const fields = new Map();
    const get = key => {
        if (!fields.has(key)) fields.set(key, {df: {hidden: 0}, $wrapper: {html(value) {this.markup = value;}}});
        return fields.get(key);
    };
    return {doc: {name: "LOT", subcontracting_order: "SCO"}, is_new: () => false,
        get_field: get, set_df_property(key, field, value) {get(key).df[field] = value;}};
}
const data = () => ({enabled: true, is_multi_item: true, journey_complete: true, truck_count: 1,
    items: [{processed_item: "A <unsafe>", stock_uom: "Kg", ordered_qty: 500,
        native_received_qty: 500, reserved_accepted_qty: 0, available_qty: 0,
        submitted_scr_qty: 500, submitted_pr_qty: 500, submitted_pi_qty: 500,
        journey_complete: true, issues: []},
    {processed_item: "B", stock_uom: "Units", ordered_qty: 100,
        native_received_qty: 100, available_qty: 0, reserved_accepted_qty: 0, submitted_scr_qty: 100, submitted_pr_qty: 100,
        submitted_pi_qty: 100, journey_complete: true, issues: []}],
    journeys: [{processor_lot_receipt: "PLR", processed_item: "A", stock_uom: "Kg",
        allocated_accepted_qty: 500, allocated_invoice_qty: 500, subcontracting_receipt: "SCR",
        purchase_receipt: "PR", purchase_invoice: "PI", scr_verified: true, pr_verified: true, pi_verified: true, issues: []}]});
(async () => {
    let report = data();
    const frm = form();
    context.frappe.call = async request => ({message: request.method.includes("receipt_completion")
        ? report : {enabled: true, is_multi_item: true}});
    await context.render_multi_item_receipt_checkpoint(frm);
    const html = field => frm.get_field(field).$wrapper.markup;
    assert(html("health_panel_html").includes("complete—not settlement approval"));
    assert(html("physical_receipt_position_html").includes("Ordered quantities received in ERP"));
    assert(html("physical_receipt_position_html").includes("A &lt;unsafe&gt;"));
    assert(!html("physical_receipt_position_html").includes("A <unsafe>"));
    assert(html("health_panel_html").includes("500.000"));
    assert(html("health_panel_html").includes("100.000"));
    assert(!html("health_panel_html").includes("600.000"));
    assert(html("receipt_journey_html").replace(/<[^>]*>/g, "").includes("PI / Verified"));
    assert(html("receipt_journey_html").includes('/app/purchase-invoice/PI'));
    assert(html("receipt_journey_html").includes('/app/processor-lot-receipt/PLR'));
    assert(html("health_panel_html").includes('data-status-tone="green"'));
    assert(html("health_panel_html").includes('Receipt Journey Complete'));
    report.journey_complete = false;
    report.items[0].journey_complete = false;
    report.items[0].available_qty = -201;
    report.items[0].reserved_accepted_qty = 201;
    report.items[0].issues = ["EXCESS_CAPACITY_COMMITMENT", "<unknown>"];
    report.journeys[0].pi_verified = false;
    await context.render_multi_item_receipt_checkpoint(frm);
    assert(html("physical_receipt_position_html").includes("-201.000"));
    assert(html("health_panel_html").includes("Receipt commitments exceed remaining capacity"));
    assert(html("health_panel_html").includes("&lt;unknown&gt;"));
    assert(html("receipt_journey_html").replace(/<[^>]*>/g, "").includes("PI / Not verified"));
    context.frappe.call = async request => {
        if (request.method.includes("receipt_completion")) throw Error("Permission denied");
        return {message: {enabled: true, is_multi_item: true}};
    };
    await context.render_multi_item_receipt_checkpoint(frm);
    for (const field of ["physical_receipt_position_html", "health_panel_html", "receipt_journey_html"]) {
        assert(html(field).includes("could not be verified"));
        assert(!html(field).includes("PI / Verified"));
    }
    let calls = 0;
    context.frappe.call = async () => {calls++; return {message: {enabled: true, is_multi_item: false}};};
    assert.strictEqual(await context.render_multi_item_receipt_checkpoint(frm), false);
    assert.strictEqual(calls, 1);
    context.frappe.call = async request => ({message: request.method.includes("receipt_completion")
        ? report : {enabled: true, is_multi_item: false, use_item_panels: true}});
    assert.strictEqual(await context.render_multi_item_receipt_checkpoint(frm), true);
    assert(html("health_panel_html").includes("Receipt commitments exceed remaining capacity"));
    assert(!html("physical_receipt_position_html").includes("Awaiting Receipt"));
    let release;
    context.frappe.call = async request => request.method.includes("receipt_completion")
        ? new Promise(resolve => {release = resolve;}) : {message: {enabled: true, is_multi_item: true}};
    const pending = context.render_multi_item_receipt_checkpoint(frm);
    await new Promise(setImmediate);
    const before = html("health_panel_html");
    frm.doc.name = "OTHER";
    release({message: data()});
    await pending;
    assert.strictEqual(html("health_panel_html"), before);
    console.log("J12 completion, mixed UOM, escaping, warnings, permissions, legacy and stale-response checks: PASS");
})().catch(error => {console.error(error); process.exitCode = 1;});
