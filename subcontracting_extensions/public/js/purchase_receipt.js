// Copyright (c) 2026, RydWel and contributors
// For license information, please see license.txt

frappe.ui.form.on("Purchase Receipt", {
	refresh(frm) {
		toggle_purchase_receipt_workspace_button(frm);
	},

	subcontracting_receipt(frm) {
		toggle_purchase_receipt_workspace_button(frm);
	},

	is_subcontracted(frm) {
		toggle_purchase_receipt_workspace_button(frm);
	},
});

/**
 * Show the workspace button only when this Purchase Receipt
 * belongs to the subcontracting flow.
 */
function toggle_purchase_receipt_workspace_button(frm) {
	frm.remove_custom_button(__("Subcontracting"));

	if (frm.doc.subcontracting_receipt || frm.doc.is_subcontracted) {
		add_subcontracting_workspace_button(frm);
	}
}