app_name = "subcontracting_extensions"
app_title = "Subcontracting Extensions"
app_publisher = "R.S. Bhogal"
app_description = "Bhogals subcontracting workflow extensions for ERPNext"
app_email = "rsbhogal@bhogal.com"
app_license = "mit"

required_apps = ["erpnext"]

after_install = [
        (
                "subcontracting_extensions.setup."
                "processor_material_accounts."
                "ensure_processor_material_accounts"
        ),
        (
                "subcontracting_extensions.setup.ownership."
                "ensure_subcontracting_report_ownership"
        ),
]

after_migrate = [
        (
                "subcontracting_extensions.setup."
                "processor_material_accounts."
                "ensure_processor_material_accounts"
        ),
        (
                "subcontracting_extensions.setup.ownership."
                "ensure_subcontracting_report_ownership"
        ),
]

fixtures = [
	{
		"dt": "Workspace",
		"filters": [["name", "=", "Subcontracting"]],
	},
	{
		"dt": "Custom Field",
		"filters": [
			[
				"name",
				"in",
				[
					"Purchase Invoice-custom_processor_lot_settlement",
					"Subcontracting Receipt-custom_processor_lot_references",
					"Subcontracting Receipt-custom_processor_lot",
					"Subcontracting Receipt-custom_column_break_2r0a0",
					"Subcontracting Receipt-custom_processor_lot_receipt",
					"Purchase Order-custom_processor_settlement_policy",
					"Purchase Order-custom_recover_raw_material_shortage",
					"Purchase Order-custom_recover_processing_charges_on_shortage",
					"Purchase Order-custom_column_break_quhan",
					"Purchase Order-custom_settlement_basis",
					"Purchase Order-custom_settlement_remarks",
					"Purchase Order Item-custom_processing_route",
				],
			]
		],
	},
]

doctype_js = {
	"Purchase Order": "public/js/purchase_order.js",
	"Purchase Receipt": "public/js/purchase_receipt.js",
	"Purchase Invoice": "public/js/purchase_invoice.js",
	"Subcontracting Receipt": "public/js/subcontracting_receipt.js",
}

app_include_js = [
	"/assets/subcontracting_extensions/js/subcontracting_workspace_link.js",
]

doc_events = {
	"Subcontracting Order": {
		"before_validate": (
			"subcontracting_extensions.scripts.subcontracting_order."
			"apply_processing_routes"
		),
		"validate": (
			"subcontracting_extensions.scripts.subcontracting_order."
			"apply_processing_route_warehouses"
		),
		"on_submit": (
			"subcontracting_extensions.scripts.subcontracting_order."
			"ensure_processor_lot"
		),
	},
	"Purchase Order": {
		"before_insert": (
			"subcontracting_extensions.scripts.purchase_order_naming."
			"set_po_date_series_field"
		),
		"validate": "subcontracting_extensions.scripts.purchase_order.validate",
	},
	"Purchase Invoice": {
		"autoname": (
			"subcontracting_extensions.scripts.purchase_document_naming."
			"set_posting_date_name"
		),
		"validate": (
			"subcontracting_extensions.subcontracting_extensions.doctype."
			"processor_lot_receipt.processor_lot_receipt."
			"validate_purchase_invoice_supplier_identity"
		),
		"after_insert": (
			"subcontracting_extensions.subcontracting_extensions.doctype."
			"processor_lot_receipt.processor_lot_receipt."
			"link_purchase_invoice"
		),
		"on_update": (
			"subcontracting_extensions.subcontracting_extensions.doctype."
			"processor_lot_receipt.processor_lot_receipt."
			"link_purchase_invoice"
		),
		"on_submit": (
			"subcontracting_extensions.subcontracting_extensions.doctype."
			"processor_lot_receipt.processor_lot_receipt."
			"link_purchase_invoice"
		),
		"on_cancel": (
			"subcontracting_extensions.subcontracting_extensions.doctype."
			"processor_lot_receipt.processor_lot_receipt."
			"unlink_purchase_invoice"
		),
		"on_trash": [
			(
				"subcontracting_extensions.subcontracting_extensions.doctype."
				"processor_lot_receipt.processor_lot_receipt."
				"unlink_purchase_invoice"
			),
			(
				"subcontracting_extensions.subcontracting_extensions.doctype."
				"processor_lot.processor_lot."
				"unlink_processor_lot_settlement_debit_note"
			),
		],
	},
	"Purchase Receipt": {
		"autoname": (
			"subcontracting_extensions.scripts.purchase_document_naming."
			"set_posting_date_name"
		),
		"after_insert": (
			"subcontracting_extensions.subcontracting_extensions.doctype."
			"processor_lot_receipt.processor_lot_receipt."
			"link_purchase_receipt"
		),
		"on_update": (
			"subcontracting_extensions.subcontracting_extensions.doctype."
			"processor_lot_receipt.processor_lot_receipt."
			"link_purchase_receipt"
		),
		"on_submit": (
			"subcontracting_extensions.subcontracting_extensions.doctype."
			"processor_lot_receipt.processor_lot_receipt."
			"link_purchase_receipt"
		),
		"on_cancel": (
			"subcontracting_extensions.subcontracting_extensions.doctype."
			"processor_lot_receipt.processor_lot_receipt."
			"unlink_purchase_receipt"
		),
		"on_trash": (
			"subcontracting_extensions.subcontracting_extensions.doctype."
			"processor_lot_receipt.processor_lot_receipt."
			"unlink_purchase_receipt"
		),
	},
	"Stock Entry": {
		"validate": [
			(
				"subcontracting_extensions.subcontracting_extensions.doctype."
				"processor_lot_receipt.processor_lot_receipt."
				"validate_material_credit_stock_entry"
			),
			(
				"subcontracting_extensions.subcontracting_extensions.doctype."
				"processor_lot.settlement_application_engine."
				"validate_credit_application_stock_entry"
			),
			(
				"subcontracting_extensions.subcontracting_extensions.doctype."
				"processor_lot.settlement_application_engine."
				"prepare_credit_application_stock_entry_submit"
			),
		],
		"before_submit": [
			(
				"subcontracting_extensions.subcontracting_extensions.doctype."
				"processor_lot_receipt.processor_lot_receipt."
				"validate_material_credit_stock_entry"
			),
			(
				"subcontracting_extensions.subcontracting_extensions.doctype."
				"processor_lot.settlement_application_engine."
				"validate_credit_application_stock_entry"
			),
		],
		"before_cancel": (
			"subcontracting_extensions.subcontracting_extensions.doctype."
			"processor_lot.settlement_application_engine."
			"prevent_credit_application_document_cancel"
		),
		"on_submit": (
			"subcontracting_extensions.subcontracting_extensions.doctype."
			"processor_lot.settlement_application_engine."
			"restore_credit_application_stock_entry_sco_status"
		),
		"on_cancel": [
			(
				"subcontracting_extensions.subcontracting_extensions.doctype."
				"processor_lot_receipt.processor_lot_receipt."
				"unlink_material_credit_stock_entry"
			),
			(
				"subcontracting_extensions.subcontracting_extensions.doctype."
				"processor_lot.settlement_application_engine."
				"unlink_credit_application_document"
			),
		],
		"on_trash": [
			(
				"subcontracting_extensions.subcontracting_extensions.doctype."
				"processor_lot_receipt.processor_lot_receipt."
				"unlink_material_credit_stock_entry"
			),
			(
				"subcontracting_extensions.subcontracting_extensions.doctype."
				"processor_lot.settlement_application_engine."
				"unlink_credit_application_document"
			),
		],
	},
	"Journal Entry": {
		"validate": (
			"subcontracting_extensions.subcontracting_extensions.doctype."
			"processor_lot.settlement_application_engine."
			"validate_credit_application_journal_entry"
		),
		"before_submit": (
			"subcontracting_extensions.subcontracting_extensions.doctype."
			"processor_lot.settlement_application_engine."
			"validate_credit_application_journal_entry"
		),
		"before_cancel": (
			"subcontracting_extensions.subcontracting_extensions.doctype."
			"processor_lot.settlement_application_engine."
			"prevent_credit_application_document_cancel"
		),
		"on_cancel": (
			"subcontracting_extensions.subcontracting_extensions.doctype."
			"processor_lot.settlement_application_engine."
			"unlink_credit_application_document"
		),
		"on_trash": (
			"subcontracting_extensions.subcontracting_extensions.doctype."
			"processor_lot.settlement_application_engine."
			"unlink_credit_application_document"
		),
	},
	"Subcontracting Receipt": {
		"before_insert": (
			"subcontracting_extensions.scripts.subcontracting_receipt."
			"before_insert"
		),
		"validate": (
			"subcontracting_extensions.scripts.subcontracting_receipt.validate"
		),
		"after_insert": (
			"subcontracting_extensions.scripts.subcontracting_receipt."
			"after_insert"
		),
		"on_trash": (
			"subcontracting_extensions.scripts.subcontracting_receipt.on_trash"
		),
	},
}

override_whitelisted_methods = {
	(
		"erpnext.controllers.subcontracting_controller."
		"make_rm_stock_entry"
	): (
		"subcontracting_extensions.overrides.subcontracting_order."
		"make_rm_stock_entry"
	),
	(
		"erpnext.subcontracting.doctype.subcontracting_receipt."
		"subcontracting_receipt.make_purchase_receipt"
	): (
		"subcontracting_extensions.overrides.subcontracting_receipt."
		"make_purchase_receipt"
	),
	(
		"erpnext.stock.doctype.purchase_receipt."
		"purchase_receipt.make_purchase_invoice"
	): (
		"subcontracting_extensions.overrides.purchase_receipt."
		"make_purchase_invoice"
	),
}


# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "subcontracting_extensions",
# 		"logo": "/assets/subcontracting_extensions/logo.png",
# 		"title": "Subcontracting Extensions",
# 		"route": "/subcontracting_extensions",
# 		"has_permission": "subcontracting_extensions.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/subcontracting_extensions/css/subcontracting_extensions.css"
# app_include_js = "/assets/subcontracting_extensions/js/subcontracting_extensions.js"

# include js, css files in header of web template
# web_include_css = "/assets/subcontracting_extensions/css/subcontracting_extensions.css"
# web_include_js = "/assets/subcontracting_extensions/js/subcontracting_extensions.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "subcontracting_extensions/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "subcontracting_extensions/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "subcontracting_extensions.utils.jinja_methods",
# 	"filters": "subcontracting_extensions.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "subcontracting_extensions.install.before_install"
# after_install = "subcontracting_extensions.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "subcontracting_extensions.uninstall.before_uninstall"
# after_uninstall = "subcontracting_extensions.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "subcontracting_extensions.utils.before_app_install"
# after_app_install = "subcontracting_extensions.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "subcontracting_extensions.utils.before_app_uninstall"
# after_app_uninstall = "subcontracting_extensions.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "subcontracting_extensions.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"subcontracting_extensions.tasks.all"
# 	],
# 	"daily": [
# 		"subcontracting_extensions.tasks.daily"
# 	],
# 	"hourly": [
# 		"subcontracting_extensions.tasks.hourly"
# 	],
# 	"weekly": [
# 		"subcontracting_extensions.tasks.weekly"
# 	],
# 	"monthly": [
# 		"subcontracting_extensions.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "subcontracting_extensions.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "subcontracting_extensions.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "subcontracting_extensions.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["subcontracting_extensions.utils.before_request"]
# after_request = ["subcontracting_extensions.utils.after_request"]

# Job Events
# ----------
# before_job = ["subcontracting_extensions.utils.before_job"]
# after_job = ["subcontracting_extensions.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"subcontracting_extensions.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

