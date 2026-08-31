/* Static/runtime smoke test. Browser rendering remains a manual checkpoint. */
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

global.__ = value => value;
global.document = {addEventListener() {}};
global.frappe = {
    provide(path) { path.split(".").reduce((object, key) => object[key] ||= {}, global); },
    utils: {escape_html: value => String(value)},
    defaults: {get_user_default: () => "COMPANY"},
    ui: {Dialog: class {}},
    call: async () => ({message: {draft_entry_enabled: true}}),
    throw(message) { throw new Error(message); },
};
vm.runInThisContext(fs.readFileSync(__dirname + "/processor_receipt_entry_preview.js", "utf8"));

const api = frappe.subcontracting_entry_preview;
assert.strictEqual(api.draft_numeric("12.123456", undefined), 12.123456);
assert.strictEqual(api.draft_numeric("12.123", 12.123456), 12.123456);
assert.strictEqual(api.draft_numeric("-0.125", undefined, 3, true), -0.125);
assert.throws(() => api.draft_numeric("1.1234567", undefined), /at most 6/);
assert.throws(() => api.draft_numeric("-1", undefined), /non-negative/);

const frm = {
    is_new: () => false,
    doc: {
        name: "PLR-J2", modified: "2026-08-31 01:02:03.000001",
        company: "COMPANY", supplier: "SUPPLIER", supplier_warehouse: "WAREHOUSE",
        physical_receipt_date: "2026-08-31", vehicle_no: "PB00TEST",
        receipt_items: [{name: "ROW-1", item_key: "ITEM-001", processed_item: "ITEM-A",
            company_accepted_qty: 10.123456, supplier_invoice_qty: 10}],
        item_weighments: [{name: "new-row", __islocal: 1, weighment_stage: "Arrival Loaded"}],
    },
};
const payload = api.draft_payload(frm);
assert.strictEqual(payload.receipt_items[0].company_accepted_qty, 10.123456);
assert.strictEqual(payload.receipt_items[0].name, "ROW-1");
assert.ok(!Object.hasOwn(payload.item_weighments[0], "name"));
assert.ok(!Object.hasOwn(payload.receipt_items[0], "lot_backed_qty"));

(async () => {
    let markup = "";
    let dirty = false;
    const wrapper = {
        html(value) { markup = value; return this; },
        off() { return this; }, on() { return this; },
    };
    const fields = [
        {fieldname: "processor_first_draft_html", hidden: 1, read_only: 0, label: "Draft"},
        {fieldname: "company", hidden: 0, read_only: 1, label: "Company"},
    ];
    frm.doc.item_weighments = [];
    Object.assign(frm, {
        meta: {fields}, fields_dict: {processor_first_draft_html: {$wrapper: wrapper}},
        toggle_display(name, visible) { fields.find(field => field.fieldname === name).hidden = visible ? 0 : 1; },
        set_df_property(name, property, value) { fields.find(field => field.fieldname === name)[property] = value; },
        is_dirty: () => dirty,
        clear_custom_buttons() {}, set_intro() {},
        disable_save() { this.save_disabled = true; },
        enable_save() { this.save_disabled = false; },
    });
    await api.render_draft(frm);
    assert.strictEqual(frm.save_disabled, false);
    assert.strictEqual(fields[0].hidden, 0);
    assert.strictEqual(fields[1].hidden, 1);
    assert.ok(markup.includes("10.123"));
    assert.ok(!markup.includes("10.123456"));
    api.validate_draft(frm);
    frm.doc.vehicle_no = "CHANGED"; dirty = true;
    assert.throws(() => api.validate_draft(frm), /Review FIFO/);
    frappe.call = async () => ({message: {draft_entry_enabled: false}});
    await api.render_draft(frm);
    assert.strictEqual(frm.save_disabled, true);
    assert.throws(() => api.validate_draft(frm), /not available/);
    api.restore_draft(frm);
    assert.strictEqual(fields[0].hidden, 1);
    assert.strictEqual(fields[1].hidden, 0);
    assert.strictEqual(fields[1].read_only, 1);
    assert.strictEqual(frm.__j2_state, undefined);
    console.log("J2 JS precision, payload, review-gating and form-restoration checks: PASS");
})().catch(error => { console.error(error); process.exitCode = 1; });
