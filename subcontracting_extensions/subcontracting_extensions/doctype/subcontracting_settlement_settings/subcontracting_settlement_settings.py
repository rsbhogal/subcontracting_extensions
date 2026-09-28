# Copyright (c) 2026, R.S. Bhogal and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from subcontracting_extensions.settlement_method_policy import (
	SettlementMethodPolicyError,
	normalize_method_rows,
)
from subcontracting_extensions.settlement_action_authority import (
	SettlementActionAuthorityError,
	normalize_action_rows,
)


class SubcontractingSettlementSettings(Document):
	def before_validate(self):
		"""Restore method metadata and validate action roles without reseeding."""
		try:
			rows = normalize_method_rows(self.get("allowed_settlement_methods"))
		except SettlementMethodPolicyError as error:
			frappe.throw(_(str(error)), title=_("Invalid Settlement Method Settings"))

		existing = {
			row.method_code: row
			for row in (self.get("allowed_settlement_methods") or [])
		}
		ordered = []
		for index, values in enumerate(rows, start=1):
			row = existing.get(values["method_code"])
			if row is None:
				row = self.append("allowed_settlement_methods", {})
			for fieldname, value in values.items():
				row.set(fieldname, value)
			row.idx = index
			ordered.append(row)
		self.set("allowed_settlement_methods", ordered)

		try:
			action_rows = normalize_action_rows(
				self.get("controlled_action_roles"),
				role_exists=lambda role: frappe.db.exists("Role", role),
			)
			existing_actions = {
				(row.action_code, row.role): row
				for row in (self.get("controlled_action_roles") or [])
			}
			ordered_actions = []
			for index, values in enumerate(action_rows, start=1):
				row = existing_actions[(values["action_code"], values["role"])]
				for fieldname, value in values.items():
					row.set(fieldname, value)
				row.idx = index
				ordered_actions.append(row)
			self.set("controlled_action_roles", ordered_actions)
		except SettlementActionAuthorityError as error:
			frappe.throw(_(str(error)), title=_("Invalid Settlement Action Authorities"))

		# These safety requirements are intentionally not administrator-disableable.
		self.require_recovery_customer_for_sales_invoice = 1
		self.require_resolved_excess_ownership = 1
