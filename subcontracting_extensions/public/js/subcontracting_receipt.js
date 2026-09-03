// Copyright (c) 2026, R S Bhogal and contributors
// For license information, please see license.txt

frappe.ui.form.on("Subcontracting Receipt", {
    refresh(frm) {
        hide_processor_first_purchase_receipt_action(frm);
    },

    before_submit(frm) {
        return confirm_original_document_reference(frm);
    }
});


async function hide_processor_first_purchase_receipt_action(frm) {
    if (
        frm.doc.docstatus !== 1
        || !frm.doc.custom_processor_lot_receipt
    ) {
        return;
    }

    const response = await frappe.call({
        method:
            "subcontracting_extensions.overrides.subcontracting_receipt." +
            "get_processor_first_draft_pr_mode",
        args: {source_name: frm.doc.name},
    });
    const mode = response && response.message;
    if (
        !mode
        || !mode.checkpoint
        || mode.draft_pr_enabled
    ) {
        return;
    }

    frm.remove_custom_button(__("Purchase Receipt"), __("Create"));
    frm.remove_custom_button(__("Purchase Receipt"));
}


/**
 * Warn before submitting a Subcontracting Receipt without an
 * Original Document Reference.
 *
 * ERPNext currently allows submission without such a reference.
 * The operator therefore receives a deliberate choice instead of
 * being hard-blocked.
 */
function confirm_original_document_reference(frm) {
    const references =
        frm.doc.doc_references || [];

    if (references.length) {
        return Promise.resolve();
    }

    return new Promise((resolve, reject) => {
        const dialog = new frappe.ui.Dialog({
            title: __("Original Document Reference Missing"),
            fields: [
                {
                    fieldtype: "HTML",
                    fieldname: "message",
                    options: `
                        <div>
                            <p>
                                ${__(
                                    "This Subcontracting Receipt has no Original Document Reference."
                                )}
                            </p>

                            <p>
                                ${__(
                                    "For subcontracting receipts, the original material transfer Stock Entry can normally be fetched using <strong>Fetch Original Document Reference</strong>."
                                )}
                            </p>

                            <p style="margin-bottom:0;">
                                <strong>
                                    ${__(
                                        "Do you want to submit this receipt without that reference?"
                                    )}
                                </strong>
                            </p>
                        </div>
                    `
                }
            ],

            primary_action_label:
                __("Submit Anyway"),

            primary_action() {
                dialog.hide();
                resolve();
            },

            secondary_action_label:
                __("Go Back"),

            secondary_action() {
                frappe.validated = false;
                dialog.hide();

                reject(
                    new Error(
                        "Original Document Reference confirmation cancelled."
                    )
                );
            }
        });

        dialog.show();
    });
}
