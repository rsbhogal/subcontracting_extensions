/* Run with Node. No Frappe site, database, fixtures or network calls. */
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

class Wrapper {
    constructor() { this.handlers = new Map(); this.values = new Map(); }
    html(value) { this.markup = value; return this; }
    on(events, selector, callback) { this.handlers.set(selector, callback); return this; }
    off() { return this; }
    find(selector) { return {text: value => this.values.set(selector, value)}; }
    input(index, field, value) {
        this.handlers.get("[data-reading][data-field]").call({dataset: {reading: String(index), field}, value});
    }
}
let dialog;
global.__ = value => value;
global.document = {addEventListener() {}};
global.frappe = {
    provide(path) { path.split(".").reduce((obj, key) => obj[key] ||= {}, global); },
    utils: {escape_html: value => String(value).replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")},
    throw(message) { throw new Error(message); },
    confirm(message, yes) { yes(); },
    msgprint() {},
    ui: {Dialog: class {
        constructor(options) {
            this.options = options; this.fields_dict = {readings: {$wrapper: new Wrapper()}};
            this.values = Object.fromEntries(options.fields.map(field => [field.fieldname, field.default]));
            dialog = this;
        }
        get_value(field) { return this.values[field]; }
        show() { this.visible = true; }
        hide() { this.visible = false; }
    }},
};
vm.runInThisContext(fs.readFileSync(__dirname + "/processor_receipt_entry_preview.js", "utf8"));
const api = frappe.subcontracting_entry_preview;
const items = ["A", "B"].map(key => ({item_key: key, processed_item: "Item " + key,
    measurement_method: "Weight", measurement_basis: "Truck Differential Weight", stock_uom: "Kg"}));
const row = (scale, key, time, extra = {}) => ({
    weighment_stage: key ? "After Unloading" : "Arrival Loaded", receipt_item_key: key || null,
    measurement_uom: "Kg", scale_weight: scale, adjustment_qty: 0,
    weighment_date: "2026-08-31", weighment_time: time, ...extra,
});
const base = [row(5100, null, "10:00:00", {name: "W1", slip_number: "SLIP-1", weighbridge: "Bridge 1"}),
    row(5000, "A", "10:15:00", {name: "W2", slip_number: "SLIP-2", weighbridge: "Bridge 2", remarks: "Keep me"})];
let count = 0;
function test(label, fn) { fn(); count++; console.log("PASS:", label); }

test("single differential and source immutability", () => {
    const before = JSON.stringify(base);
    const result = api.prepare_weighments(base, items);
    assert.strictEqual(result[0].accepted_qty, 0);
    assert.strictEqual(result[1].accepted_qty, 100);
    assert.strictEqual(JSON.stringify(base), before);
});
test("multiple items and repeated item readings", () => {
    const result = api.prepare_weighments([
        row(8000, null, "10:00"), row(6500, "A", "10:10"), row(5400, "B", "10:20"), row(5000, "A", "10:30"),
    ], items);
    assert.deepStrictEqual(result.map(r => r.accepted_qty), [0, 1500, 1100, 400]);
});
test("three-decimal subtraction avoids binary float residue", () => {
    const result = api.prepare_weighments([row("1.003", null, "10:00"), row("1.001", "A", "10:01")], items);
    assert.strictEqual(result[1].accepted_qty, 0.002);
});
test("explained signed adjustment", () => {
    const result = api.prepare_weighments([base[0], {...base[1], adjustment_qty: -1.125, adjustment_reason: "Test"}], items);
    assert.strictEqual(result[1].accepted_qty, 98.875);
});
for (const [label, change, message] of [
    ["increasing weight", {scale_weight: 5101}, /exceed/],
    ["unexplained adjustment", {adjustment_qty: 1}, /Explain/],
    ["negative acceptance", {adjustment_qty: -101, adjustment_reason: "Test"}, /negative/],
    ["wrong item", {receipt_item_key: "UNKNOWN"}, /Select/],
    ["mixed UOM", {measurement_uom: "Units"}, /same Scale UOM/],
    ["earlier time", {weighment_time: "09:59"}, /chronological/],
    ["invalid date", {weighment_date: "2026-02-30"}, /valid date/],
    ["invalid time", {weighment_time: "24:01"}, /24-hour/],
    ["excess input precision", {scale_weight: "5000.0001"}, /three decimals/],
    ["nonfinite weight", {scale_weight: Infinity}, /three decimals/],
    ["duplicate arrival", {weighment_stage: "Arrival Loaded"}, /one Arrival/],
]) test(label, () => assert.throws(() => api.prepare_weighments([base[0], {...base[1], ...change}], items), message));
test("Count items cannot receive truck differentials", () => {
    assert.throws(() => api.prepare_weighments(base, [{...items[0], measurement_method: "Count"}]), /Select/);
});
test("item UOM must match scale", () => {
    assert.throws(() => api.prepare_weighments(base, [{...items[0], stock_uom: "Units"}]), /Stock UOM/);
});
test("arrival-only is a valid partial sequence, not permission to save", () => {
    assert.strictEqual(api.prepare_weighments([base[0]], items).length, 1);
});
test("later date is valid", () => {
    assert.strictEqual(api.prepare_weighments([base[0], {...base[1], weighment_date: "2026-09-01", weighment_time: "00:01"}], items)[1].accepted_qty, 100);
});
test("200-reading limit", () => {
    assert.throws(() => api.prepare_weighments(Array(201).fill(base[0]), items), /200/);
});

function form(readings = base) {
    const frm = {
        doc: {name: "PLR-TEST", modified: "2026-08-31 11:00:00", docstatus: 0,
            receipt_items: items.map(item => ({...item, company_accepted_qty: 100, supplier_invoice_qty: 101})),
            item_weighments: JSON.parse(JSON.stringify(readings)), lot_allocations: [], physical_receipt_date: "2026-08-31"},
        __j2_state: {enabled: true, busy: false, reviewed: "prior-review"},
        is_new: () => false,
        fields_dict: {processor_first_draft_html: {$wrapper: new Wrapper()}},
        dirty() { this.was_dirty = true; },
        add_child(field) { const child = {name: "new-" + this.doc[field].length, __islocal: 1}; this.doc[field].push(child); return child; },
    };
    return frm;
}
test("cancel leaves all form values and review unchanged", () => {
    const frm = form(); const before = JSON.stringify(frm.doc);
    api.edit_weighments(frm);
    dialog.fields_dict.readings.$wrapper.input(1, "scale_weight", "4990.000");
    dialog.hide();
    assert.strictEqual(JSON.stringify(frm.doc), before);
    assert.strictEqual(frm.__j2_state.reviewed, "prior-review");
});
test("apply preserves row objects, names, timestamps, slips and remarks", () => {
    const frm = form(); const original = frm.doc.item_weighments[1];
    api.edit_weighments(frm);
    const wrapper = dialog.fields_dict.readings.$wrapper;
    wrapper.input(1, "scale_weight", "5001.000");
    assert.strictEqual(wrapper.values.get('[data-previous="1"]'), "5100.000");
    assert.strictEqual(wrapper.values.get('[data-accepted="1"]'), "99.000");
    dialog.options.primary_action();
    assert.strictEqual(frm.doc.item_weighments[1], original);
    assert.strictEqual(original.name, "W2");
    assert.strictEqual(original.slip_number, "SLIP-2");
    assert.strictEqual(original.weighment_time, "10:15:00");
    assert.strictEqual(original.weighbridge, "Bridge 2");
    assert.strictEqual(original.remarks, "Keep me");
    assert.strictEqual(original.accepted_qty, 99);
    assert.strictEqual(frm.__j2_state.reviewed, null);
    assert.strictEqual(frm.was_dirty, true);
    assert.strictEqual(frm.doc.receipt_items[0].company_accepted_qty, 100);
});
test("append uses defaults only for the new reading", () => {
    const frm = form(); api.edit_weighments(frm);
    const wrapper = dialog.fields_dict.readings.$wrapper;
    dialog.values.new_bridge = "New Bridge";
    wrapper.handlers.get("[data-add-reading]")();
    wrapper.input(2, "receipt_item_key", "B");
    wrapper.input(2, "scale_weight", "4990.000");
    wrapper.input(2, "weighment_time", "10:30:00");
    dialog.options.primary_action();
    assert.strictEqual(frm.doc.item_weighments.length, 3);
    assert.strictEqual(frm.doc.item_weighments[0].weighbridge, "Bridge 1");
    assert.strictEqual(frm.doc.item_weighments[1].weighbridge, "Bridge 2");
    assert.strictEqual(frm.doc.item_weighments[2].weighbridge, "New Bridge");
    assert.strictEqual(frm.doc.item_weighments[2].accepted_qty, 10);
});
test("remove recomputes the next pair and preserves its identity", () => {
    const frm = form([...base, row(4990, "B", "10:30", {name: "W3"})]); api.edit_weighments(frm);
    dialog.fields_dict.readings.$wrapper.handlers.get("[data-remove-reading]").call({dataset: {removeReading: "1"}});
    dialog.options.primary_action();
    assert.deepStrictEqual(frm.doc.item_weighments.map(r => r.name), ["W1", "W3"]);
    assert.strictEqual(frm.doc.item_weighments[1].accepted_qty, 110);
    assert.strictEqual(frm.doc.item_weighments[1].idx, 2);
});
test("invalid Apply never partly mutates the form", () => {
    const frm = form(); const before = JSON.stringify(frm.doc); api.edit_weighments(frm);
    dialog.fields_dict.readings.$wrapper.input(1, "scale_weight", "6000.000");
    assert.throws(() => dialog.options.primary_action(), /exceed/);
    assert.strictEqual(JSON.stringify(frm.doc), before);
});
test("stale dialog cannot overwrite changed receipt", () => {
    const frm = form(); api.edit_weighments(frm); frm.doc.vehicle_no = "CHANGED";
    assert.throws(() => dialog.options.primary_action(), /receipt changed/);
});
test("new dialog supports an arrival/unloading pair in one Apply", () => {
    const frm = form([]); api.edit_weighments(frm);
    const wrapper = dialog.fields_dict.readings.$wrapper;
    wrapper.input(0, "scale_weight", "5100.000");
    wrapper.input(1, "scale_weight", "5000.000");
    wrapper.input(1, "receipt_item_key", "A");
    dialog.options.primary_action();
    assert.strictEqual(frm.doc.item_weighments.length, 2);
    assert.strictEqual(frm.doc.item_weighments[1].accepted_qty, 100);
});
test("clear requires Apply and invalidates FIFO review", () => {
    const frm = form(); api.edit_weighments(frm);
    dialog.fields_dict.readings.$wrapper.handlers.get("[data-clear-readings]")();
    assert.strictEqual(frm.doc.item_weighments.length, 2);
    dialog.options.primary_action();
    assert.deepStrictEqual(frm.doc.item_weighments, []);
    assert.strictEqual(frm.__j2_state.reviewed, null);
});
test("first appended reading after clearing is Arrival Loaded", () => {
    const frm = form(); api.edit_weighments(frm);
    const wrapper = dialog.fields_dict.readings.$wrapper;
    wrapper.handlers.get("[data-clear-readings]")();
    wrapper.handlers.get("[data-add-reading]")();
    wrapper.input(0, "scale_weight", "5100.000");
    dialog.options.primary_action();
    assert.strictEqual(frm.doc.item_weighments[0].weighment_stage, "Arrival Loaded");
});
console.log(`Combined weighment checks: ${count} PASS`);
