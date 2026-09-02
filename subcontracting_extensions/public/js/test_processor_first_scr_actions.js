const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(
    __dirname + "/processor_receipt_entry_preview.js",
    "utf8"
);

const mapped = [];
const routes = [];
const messages = [];
let workspaceCalls = 0;

global.__ = value => value;
global.document = {
    addEventListener() {},
};
global.add_subcontracting_workspace_button = () => {
    workspaceCalls += 1;
};
global.frappe = {
    provide(path) {
        const parts = path.split(".");
        let target = global;
        for (const part of parts) {
            target[part] = target[part] || {};
            target = target[part];
        }
    },
    utils: {
        escape_html: value => value,
    },
    model: {
        open_mapped_doc: args => mapped.push(args),
    },
    set_route: (...args) => routes.push(args),
    msgprint: message => messages.push(message),
};

vm.runInThisContext(source, {
    filename: "processor_receipt_entry_preview.js",
});

const actions = frappe.subcontracting_entry_preview;
assert.strictEqual(
    typeof actions.setup_draft_page_actions,
    "function"
);

function form({dirty = false, receipt = null, isNew = false} = {}) {
    const buttons = [];
    return {
        doc: {
            name: "PLR-V2",
            subcontracting_receipt: receipt,
        },
        buttons,
        clear_custom_buttons() {
            buttons.splice(0);
        },
        add_custom_button(label, handler, group) {
            buttons.push({label, handler, group});
        },
        is_new: () => isNew,
        is_dirty: () => dirty,
    };
}

const clean = form();
actions.setup_draft_page_actions(clean, {
    draft_scr_enabled: true,
});
assert.strictEqual(workspaceCalls, 1);
assert.strictEqual(clean.buttons.length, 1);
assert.strictEqual(clean.buttons[0].label, "Subcontracting Receipt");
assert.strictEqual(clean.buttons[0].group, "Create");
clean.buttons[0].handler();
assert.strictEqual(mapped.length, 1);
assert.ok(mapped[0].method.endsWith("make_subcontracting_receipt"));

const dirty = form({dirty: true});
actions.setup_draft_page_actions(dirty, {
    draft_scr_enabled: true,
});
dirty.buttons[0].handler();
assert.strictEqual(mapped.length, 1);
assert.strictEqual(messages.length, 1);

const linked = form({receipt: "MAT-SCR-V2"});
actions.setup_draft_page_actions(linked, {
    draft_scr_enabled: true,
});
assert.strictEqual(linked.buttons[0].group, "View");
linked.buttons[0].handler();
assert.deepStrictEqual(
    routes[0],
    ["Form", "Subcontracting Receipt", "MAT-SCR-V2"]
);

const disabled = form();
actions.setup_draft_page_actions(disabled, {
    draft_scr_enabled: false,
});
assert.strictEqual(disabled.buttons.length, 0);

console.log(
    "J5 workspace, Draft SCR creation, dirty guard and View action checks: PASS"
);
