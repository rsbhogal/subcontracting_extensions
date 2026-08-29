"""Create and validate Accounts required by Processor Material Credit."""

from __future__ import annotations

import frappe
from frappe import _


COMPANY = "Bhogals Private Limited"
PARENT_ACCOUNT_NAME = "Other Payables"

REQUIRED_ACCOUNTS = (
	"Processor Material Credit Liability",
	"Accrued Subcontracting Charges",
)


def ensure_processor_material_accounts(*args, **kwargs) -> None:
	"""Ensure the two Processor Material Credit liability Accounts exist.

	This function is deliberately idempotent. It is safe to call after every
	installation and migration. Existing correct Accounts are retained; missing
	Accounts are created; structurally incorrect Accounts stop the operation
	with an actionable message instead of being modified silently.
	"""
	if not frappe.db.exists("Company", COMPANY):
		# A genuinely new site may install the app before its Company exists.
		# The next migrate after Company creation will run this function again.
		return

	parent_account = _get_parent_account()

	for account_name in REQUIRED_ACCOUNTS:
		_ensure_leaf_account(
			account_name=account_name,
			parent_account=parent_account,
		)


def _get_parent_account() -> str:
	"""Return the company's Other Payables group Account."""
	parent_account = frappe.db.get_value(
		"Account",
		{
			"company": COMPANY,
			"account_name": PARENT_ACCOUNT_NAME,
			"is_group": 1,
			"disabled": 0,
		},
		"name",
	)

	if parent_account:
		return parent_account

	frappe.throw(
		_(
			"Subcontracting Extensions requires an enabled group Account named {0} "
			"for Company {1} before Processor Material Credit Accounts "
			"can be configured."
		).format(
			frappe.bold(PARENT_ACCOUNT_NAME),
			frappe.bold(COMPANY),
		),
		title=_("Processor Material Account Setup Failed"),
	)


def _ensure_leaf_account(
	account_name: str,
	parent_account: str,
) -> str:
	"""Create one required leaf Account or validate its existing definition."""
	existing = frappe.db.get_value(
		"Account",
		{
			"company": COMPANY,
			"account_name": account_name,
		},
		[
			"name",
			"parent_account",
			"root_type",
			"account_type",
			"is_group",
			"disabled",
		],
		as_dict=True,
	)

	if existing:
		_validate_existing_account(
			account=existing,
			parent_account=parent_account,
		)
		return existing.name

	account = frappe.get_doc(
		{
			"doctype": "Account",
			"account_name": account_name,
			"company": COMPANY,
			"parent_account": parent_account,
			"is_group": 0,
			"account_type": "",
		}
	)
	account.insert(ignore_permissions=True)

	_validate_existing_account(
		account=frappe.db.get_value(
			"Account",
			account.name,
			[
				"name",
				"parent_account",
				"root_type",
				"account_type",
				"is_group",
				"disabled",
			],
			as_dict=True,
		),
		parent_account=parent_account,
	)

	return account.name


def _validate_existing_account(
	account,
	parent_account: str,
) -> None:
	"""Reject a conflicting Account instead of rewriting accounting masters."""
	problems = []

	if account.parent_account != parent_account:
		problems.append(
			_("Parent Account must be {0}").format(
				frappe.bold(parent_account)
			)
		)

	if account.root_type != "Liability":
		problems.append(
			_("Root Type must be Liability")
		)

	if account.account_type:
		problems.append(
			_("Account Type must be blank")
		)

	if account.is_group:
		problems.append(
			_("the Account must be a ledger Account, not a group")
		)

	if account.disabled:
		problems.append(
			_("the Account must be enabled")
		)

	if not problems:
		return

	frappe.throw(
		_(
			"Account {0} conflicts with the Processor Material Credit "
			"setup: {1}. Correct the Account manually and run migrate "
			"again."
		).format(
			frappe.bold(account.name),
			"; ".join(problems),
		),
		title=_("Processor Material Account Setup Conflict"),
	)