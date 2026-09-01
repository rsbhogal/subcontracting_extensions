/* Node-only: no Frappe site, database, fixtures or network. */
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const handlers = {};
let response = {enabled: true, targets: {"ROUTE-A": "TARGET-A", "ROUTE-B": "TARGET-B"}};
const writes = [];
global.__ = value => value;
global.flt = value => Number(value || 0);
global.frappe = {
    ui: {form: {on(doctype, events) { handlers[doctype] = events; }}},
    call: async () => ({message: response}),
    model: {set_value: async (doctype, name, field, value) => writes.push({doctype, name, field, value})},
    utils: {escape_html: String},
};
global.add_subcontracting_workspace_button = () => {};
vm.runInThisContext(fs.readFileSync(__dirname + "/purchase_order.js", "utf8"));

function form() {
    const items = [
        {doctype: "Purchase Order Item", name: "ROW-A", custom_processing_route: "ROUTE-A", warehouse: "HEADER"},
        {doctype: "Purchase Order Item", name: "ROW-B", custom_processing_route: "ROUTE-B", warehouse: "HEADER"},
    ];
    return {doc: {is_subcontracted: 1, company: "COMPANY", items},
        refresh_field() {}, get_field() { return null; }};
}

(async () => {
    const frm = form();
    await apply_v2_route_target_warehouses(frm);
    assert.deepStrictEqual(writes.map(row => [row.name, row.value]), [["ROW-A", "TARGET-A"], ["ROW-B", "TARGET-B"]]);

    writes.splice(0); response = {enabled: false, targets: {"ROUTE-A": "TARGET-A"}};
    await apply_v2_route_target_warehouses(form());
    assert.strictEqual(writes.length, 0);

    let release;
    frappe.call = () => new Promise(resolve => { release = resolve; });
    const changed = form();
    const pending = apply_v2_route_target_warehouses(changed);
    changed.doc.items[0].custom_processing_route = "OTHER";
    release({message: {enabled: true, targets: {"ROUTE-A": "TARGET-A"}}});
    await pending;
    assert.strictEqual(writes.length, 0);

    assert.strictEqual(typeof handlers["Purchase Order"].set_warehouse, "function");
    assert.strictEqual(typeof handlers["Purchase Order Item"].custom_processing_route, "function");
    console.log("PO route target assignment, V1 gating, stale-response and event checks: PASS");
})().catch(error => { console.error(error); process.exitCode = 1; });
