const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const handlers = {};
let draftPrEnabled = false;
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
    async call() {
        return {
            message: {
                checkpoint: true,
                draft_pr_enabled: draftPrEnabled,
            },
        };
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

    draftPrEnabled = true;
    const enabledRemoved = [];
    await handlers["Subcontracting Receipt"].refresh({
        doc: {
            name: "MAT-SCR-V2",
            docstatus: 1,
            custom_processor_lot_receipt: "PLR-V2",
        },
        remove_custom_button(label, group) {
            enabledRemoved.push([label, group]);
        },
    });
    await new Promise(resolve => setImmediate(resolve));
    assert.deepStrictEqual(enabledRemoved, []);

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
        "J7 submitted SCR Draft Purchase Receipt action gate: PASS"
    );
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
