/* Node-only checks: no site, database, fixtures or network. */
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const path = require("path");
let result;
const context = {
    __: value => value,
    frappe: {
        ui: {form: {on() {}}},
        utils: {escape_html: value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;")},
        call: async request => ({message: request.method.includes("receipt_completion") ? {enabled: false} : result}),
    },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.resolve(__dirname, "../../subcontracting_extensions/doctype/processor_lot/processor_lot.js"), "utf8"), context);
// Grid cosmetics are not relevant to these panel/contract checks.
vm.runInContext("hide_items_grid_controls = () => {};", context);
function form() {
    const fields = new Map();
    const get = key => {
        if (!fields.has(key)) fields.set(key, {df: {hidden: 0}, $wrapper: {html(value) { this.markup = value; }}});
        return fields.get(key);
    };
    return {doc: {name: "LOT", subcontracting_order: "SCO"}, is_new: () => false,
        get_field: get, set_df_property(key, field, value) { get(key).df[field] = value; }};
}
(async () => {
    const frm = form();
    result = {enabled: true, is_multi_item: true, truck_count: 1, items: [
        {processed_item: "A <unsafe>", stock_uom: "Kg", receipt_count: 1, ordered_qty: 100,
            accepted_qty: 10, invoice_qty: 11, invoice_vs_accepted_qty: 1, plr_balance_qty: 90,
            native_received_qty: 0, unposted_accepted_qty: 10, credit_applied_qty: 0, available_qty: 90},
        {processed_item: "B", stock_uom: "Units", receipt_count: 1, ordered_qty: 50,
            accepted_qty: 3, invoice_qty: 3, invoice_vs_accepted_qty: 0, plr_balance_qty: 47,
            native_received_qty: 0, unposted_accepted_qty: 3, credit_applied_qty: 0, available_qty: 47},
    ], journeys: []};
    assert.strictEqual(await context.render_multi_item_receipt_checkpoint(frm), true);
    const physical = frm.get_field("physical_receipt_position_html").$wrapper.markup;
    assert(physical.includes("Receipts for item"));
    assert(physical.includes("<strong>1</strong>"));
    assert(physical.includes("10.000"));
    assert(physical.includes("A &lt;unsafe&gt;"));
    assert(!physical.includes("A <unsafe>"));
    assert.strictEqual(frm.get_field("receipt_stock_uom").df.hidden, 1);
    assert(frm.get_field("health_panel_html").$wrapper.markup.includes("90.000"));
    assert(frm.get_field("receipt_journey_html").$wrapper.markup.includes("No recorded receipt items"));
    result = {enabled: true, is_multi_item: false};
    assert.strictEqual(await context.render_multi_item_receipt_checkpoint(frm), false);
    assert.strictEqual(frm.get_field("receipt_stock_uom").df.hidden, 0);
    result = {enabled: false};
    assert.strictEqual(await context.render_multi_item_receipt_checkpoint(frm), false);
    let release;
    context.frappe.call = () => new Promise(resolve => { release = resolve; });
    const pending = context.render_multi_item_receipt_checkpoint(frm);
    frm.doc.name = "OTHER-LOT";
    release({message: {enabled: true, is_multi_item: true}});
    assert.strictEqual(await pending, true);
    console.log("J4 panel counts, precision, escaping, legacy restoration and stale-response checks: PASS");
})().catch(error => { console.error(error); process.exitCode = 1; });
