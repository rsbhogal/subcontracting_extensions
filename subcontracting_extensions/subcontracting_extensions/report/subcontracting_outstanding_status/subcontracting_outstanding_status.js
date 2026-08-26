// Copyright (c) 2026, R.S. Bhogal and contributors
// For license information, please see license.txt

frappe.query_reports["Subcontracting Outstanding Status"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "supplier",
			label: __("Supplier"),
			fieldtype: "Link",
			options: "Supplier",
		},
		{
			fieldname: "processed_item",
			label: __("Finished Item"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			fieldname: "position",
			label: __("Position"),
			fieldtype: "Select",
			options: "Outstanding\nAll\nComplete",
			default: "Outstanding",
		},
		{
			fieldname: "as_on_date",
			label: __("Ageing As On"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		if (!data) {
			return value;
		}

		if (column.fieldname === "overdue_days" && data.overdue_days > 0) {
			return `<span class="text-danger"><strong>${value}</strong></span>`;
		}

		if (column.fieldname === "outstanding_finished_qty") {
			const css_class = data.outstanding_finished_qty > 0
				? "text-warning"
				: "text-success";

			return `<span class="${css_class}"><strong>${value}</strong></span>`;
		}

		if (
			column.fieldname === "settled_variance_qty"
			&& data.settled_variance_qty > 0
		) {
			return `<span class="text-muted"><strong>${value}</strong></span>`;
		}

				if (
			column.fieldname === "material_credit_applied_qty"
			&& data.material_credit_applied_qty > 0
		) {
			return `<span class="text-info"><strong>${value}</strong></span>`;
		}

		if (
			column.fieldname === "debit_note_qty"
			&& data.debit_note_qty > 0
		) {
			return `<span class="text-muted"><strong>${value}</strong></span>`;
		}

		if (column.fieldname === "operational_status") {
			const css_class = {
				"Overdue": "text-danger",
				"Pipeline Attention": "text-danger",
				"Partly Received": "text-warning",
				"Shortfall Under Settlement": "text-warning",
				"Awaiting First Receipt": "text-warning",
				"Material Not Transferred": "text-muted",
				"Receipt Complete": "text-success",
				"Completed": "text-success",
			}[data.operational_status] || "";

			return css_class
				? `<span class="${css_class}"><strong>${value}</strong></span>`
				: value;
		}

		return value;
	},
};