# Copyright (c) 2026, kalvyai and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import call_hook_method, get_url
from payments.utils import create_payment_gateway

from ki_erp_cashfree import const
from ki_erp_cashfree.client import CashfreeClient, CashfreeError, normalize_phone


class CashfreeSettings(Document):
	supported_currencies = const.SUPPORTED_CURRENCIES

	def onload(self):
		self.webhook_url = self.get_webhook_url()

	def validate(self):
		create_payment_gateway(const.SERVICE_NAME)
		call_hook_method("payment_gateway_enabled", gateway=const.SERVICE_NAME)
		self.webhook_url = self.get_webhook_url()
		if self.default_phone and not normalize_phone(self.default_phone):
			frappe.throw(
				_(
					"The fallback mobile must be a 10-digit Indian mobile number or start with + and the country code."
				)
			)
		if not self.flags.ignore_mandatory:
			self.validate_credentials()

	def validate_credentials(self):
		try:
			self.get_client().check_credentials()
		except CashfreeError as e:
			frappe.throw(_("Cashfree rejected the App ID or Secret Key: {0}").format(e))

	@frappe.whitelist()
	def test_connection(self):
		frappe.only_for(("System Manager", "Accounts Manager"))
		self.validate_credentials()
		return _("Connected to Cashfree ({0}).").format(_(self.environment))

	def get_client(self):
		return CashfreeClient(
			self.app_id,
			self.get_password("secret_key", raise_exception=False),
			self.environment,
		)

	def get_webhook_url(self):
		return get_url("/api/method/ki_erp_cashfree.api.webhook")

	# === Payment gateway controller, called by ERPNext's Payment Request === #

	def validate_transaction_currency(self, currency):
		if currency not in self.supported_currencies:
			frappe.throw(
				_(
					"Please select another payment method. Cashfree does not support transactions in currency '{0}'"
				).format(currency)
			)

	def get_payment_url(self, **kwargs):
		"""Return the payment link; the Cashfree order is created when the customer opens it."""
		# ERPNext asks again for the link of a Payment Request (e.g. to redirect the Webshop customer,
		# after committing the Payment Request): the same link is returned.
		name = frappe.db.get_value(
			"Integration Request",
			{
				"integration_request_service": const.SERVICE_NAME,
				"reference_doctype": kwargs.get("reference_doctype"),
				"reference_docname": kwargs.get("reference_docname"),
				"status": ["in", ["Queued", "Failed"]],
			},
		)
		if name:
			return get_url(f"/cashfree_checkout?token={name}")

		# Not `create_request_log`: it commits, which would break the caller's transaction.
		integration_request = frappe.get_doc(
			{
				"doctype": "Integration Request",
				"integration_request_service": const.SERVICE_NAME,
				"data": json.dumps(kwargs, default=str),
				"reference_doctype": kwargs.get("reference_doctype"),
				"reference_docname": kwargs.get("reference_docname"),
			}
		).insert(ignore_permissions=True)
		return get_url(f"/cashfree_checkout?token={integration_request.name}")
