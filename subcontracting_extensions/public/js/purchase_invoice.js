// Copyright (c) 2026, RydWel and contributors
// For license information, please see license.txt

frappe.ui.form.on("Purchase Invoice", {
	refresh(frm) {
		toggle_purchase_invoice_workspace_button(frm);
	},

	is_subcontracted(frm) {
		toggle_purchase_invoice_workspace_button(frm);
	},

	custom_processor_lot_settlement(frm) {
		toggle_purchase_invoice_workspace_button(frm);
	},
});

/**
 * Show the workspace button for:
 * 1. Normal subcontracting Purchase Invoices.
 * 2. Processor Lot settlement Debit Notes.
 */
function toggle_purchase_invoice_workspace_button(frm) {
	frm.remove_custom_button(__("Subcontracting"));

	if (
		frm.doc.is_subcontracted ||
		frm.doc.custom_processor_lot_settlement
	) {
		add_subcontracting_workspace_button(frm);
	}
}