const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const handlers = {};
global.__ = value => value;
global.frappe = {
    ui: {
        form: {
            on(doctype, events) {
                handlers[doctype] = events;
            },
        },
        Dialog: function () {},
    },
    db: {
        async get_value() {
            return {
                message: {
                    receipt_structure_version: "V2 Itemized",
                    processor_first_draft_only: 1,
                },
            };
        },
    },
};

const source = fs.readFileSync(
    __dirname + "/subcontracting_receipt.js",
    "utf8"
);
vm.runInThisContext(source, {filename: "subcontracting_receipt.js"});

(async () => {
    const removed = [];
    const frm = {
        doc: {
            docstatus: 1,
            custom_processor_lot_receipt: "PLR-V2",
        },
        remove_custom_button(label, group) {
            removed.push([label, group]);
        },
    };

    await handlers["Subcontracting Receipt"].refresh(frm);
    await new Promise(resolve => setImmediate(resolve));

    assert.deepStrictEqual(removed, [
        ["Purchase Receipt", "Create"],
        ["Purchase Receipt", undefined],
    ]);

    const draft = {
        doc: {
            docstatus: 0,
            custom_processor_lot_receipt: "PLR-V2",
        },
        remove_custom_button() {
            throw new Error("Draft must not remove submitted actions");
        },
    };
    await handlers["Subcontracting Receipt"].refresh(draft);

    console.log(
        "J6 submitted SCR Purchase Receipt action gate: PASS"
    );
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
