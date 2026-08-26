// Copyright (c) 2026, R S Bhogal and contributors
// For license information, please see license.txt

function add_subcontracting_workspace_button(frm) {
	const button = frm.add_custom_button(
		__("Subcontracting"),
		() => {
			frappe.set_route("subcontracting");
		}
	);

	button
		.removeClass("btn-default btn-secondary")
		.addClass("btn-primary");

	if (frm.page.custom_actions) {
		button.prependTo(frm.page.custom_actions);
	}
}

[
	"Subcontracting Order",
	"Processor Lot",
	"Processor Lot Receipt",
	"Processor Material Account Entry",
	"Subcontracting Receipt",
].forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			add_subcontracting_workspace_button(frm);
		},
	});
});


// Open the Subcontracting Quick Guide in a new browser tab.
const SUBCONTRACTING_QUICK_GUIDE_URL =
	"/assets/subcontracting_extensions/docs/subcontracting_quick_guide.pdf";

document.addEventListener(
	"click",
	(event) => {
		const quick_guide = event.target.closest(
			'.shortcut-widget-box[aria-label="Subcontracting Quick Guide"]'
		);

		if (!quick_guide) {
			return;
		}

		event.preventDefault();
		event.stopPropagation();
		event.stopImmediatePropagation();

		window.open(
			SUBCONTRACTING_QUICK_GUIDE_URL,
			"_blank",
			"noopener"
		);
	},
	true
);
