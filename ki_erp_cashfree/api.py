import json

import frappe
from erpnext.accounts.doctype.payment_request.payment_request import (
	make_payment_request as erpnext_make_payment_request,
)
from frappe import _

from ki_erp_cashfree import const
from ki_erp_cashfree.client import CashfreeError, verify_signature
from ki_erp_cashfree.processing import (
	as_administrator,
	find_integration_request,
	get_settings,
	sync_integration_request,
)

WEBSHOP_PAYABLE_DOCTYPES = ("Sales Order", "Sales Invoice")
MAKE_PAYMENT_REQUEST = "erpnext.accounts.doctype.payment_request.payment_request.make_payment_request"


@frappe.whitelist(allow_guest=True, methods=["POST"])
def webhook():
	"""Receive a Cashfree payment notification.

	The signature is checked against the secret key; the payment status itself is then read from
	the Cashfree API, the notification body is only used to know which order changed.
	"""
	raw_body = frappe.request.get_data()
	headers = frappe.request.headers
	settings = get_settings()
	error = verify_signature(
		settings.get_password("secret_key", raise_exception=False),
		headers.get("x-webhook-timestamp"),
		raw_body,
		headers.get("x-webhook-signature"),
	)
	if error:
		# Not an Error Log: anyone can call this public URL, which would flood it.
		frappe.logger("ki_erp_cashfree").warning(f"Cashfree webhook rejected: {error}")
		frappe.local.response.http_status_code = 401
		return {"status": "rejected"}

	try:
		data = json.loads(raw_body or b"{}")
	except ValueError:
		frappe.local.response.http_status_code = 400
		return {"status": "invalid"}

	if data.get("type") not in const.PAYMENT_WEBHOOK_EVENTS:
		return {"status": "ignored"}

	order_id = ((data.get("data") or {}).get("order") or {}).get("order_id")
	integration_request = find_integration_request(order_id) if order_id else None
	if not integration_request:
		return {"status": "ignored"}

	try:
		result = sync_integration_request(integration_request)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title=f"Cashfree webhook: could not process order {order_id}")
		# A non-2xx answer makes Cashfree retry the notification later.
		frappe.local.response.http_status_code = 500
		return {"status": "error"}
	return {"status": result["status"]}


@frappe.whitelist(methods=["POST"])
def check_payment_status(payment_request):
	"""Read the outcome of the Cashfree payments of a Payment Request (button on the form)."""
	frappe.has_permission("Payment Request", "write", payment_request, throw=True)
	names = frappe.get_all(
		"Integration Request",
		filters={
			"integration_request_service": const.SERVICE_NAME,
			"reference_doctype": "Payment Request",
			"reference_docname": payment_request,
			"request_id": ["is", "set"],
		},
		pluck="name",
		order_by="creation desc",
	)
	if not names:
		return _("The customer hasn't opened the Cashfree checkout yet.")

	statuses = []
	for name in names:
		try:
			statuses.append(sync_integration_request(frappe.get_doc("Integration Request", name))["status"])
		except CashfreeError as e:
			frappe.throw(_("Could not check the payment with Cashfree: {0}").format(e))

	if "Completed" in statuses:
		return _("The payment is complete.")
	if "Pending" in statuses:
		return _("The payment is still pending at Cashfree.")
	return _("No successful payment yet: the customer's attempts failed or were cancelled.")


@frappe.whitelist()
def make_payment_request(**args):
	"""Stands in for ERPNext's `make_payment_request` (see `override_whitelisted_methods`).

	Webshop's Pay button calls it with `order_type="Shopping Cart"`: the customer then pays with
	Cashfree, whatever the Webshop checkout uses otherwise (e.g. Cash on Delivery). ERPNext v16 only
	lets users who can create Payment Requests call it, which shuts out website customers, so their
	access to the order is checked here instead. Every other call goes on unchanged.
	"""
	args = frappe._dict(args)
	if args.order_type != "Shopping Cart" or not frappe.db.get_single_value(
		"Cashfree Settings", "webshop_pay_button"
	):
		return get_next_make_payment_request()(**args)

	if args.dt not in WEBSHOP_PAYABLE_DOCTYPES or not isinstance(args.dn, str):
		frappe.throw(_("Online payment is only available for orders and invoices."))
	doc = frappe.get_doc(args.dt, args.dn)
	if not (frappe.has_website_permission(doc) or frappe.has_permission(doc.doctype, "read", doc)):
		raise frappe.PermissionError
	if doc.docstatus != 1:
		frappe.throw(_("{0} {1} is not submitted.").format(_(doc.doctype), doc.name))

	gateway_account = frappe.db.get_value(
		"Payment Gateway Account", {"payment_gateway": const.SERVICE_NAME, "currency": doc.currency}
	)
	if not gateway_account:
		frappe.throw(_("Online payment is not available in {0}.").format(doc.currency))

	with as_administrator():
		# Sets the redirect to the Cashfree checkout page
		return erpnext_make_payment_request(
			dt=doc.doctype,
			dn=doc.name,
			order_type="Shopping Cart",
			payment_gateway_account=gateway_account,
			submit_doc=1,
			loyalty_points=args.loyalty_points,
			return_doc=args.return_doc,
		)


def get_next_make_payment_request():
	"""Return the `make_payment_request` that applies when Cashfree doesn't handle the call: another
	app's override (e.g. another gateway's Pay button) if there is one, else ERPNext's own."""
	ours = f"{__name__}.make_payment_request"
	overrides = frappe.get_hooks("override_whitelisted_methods", {}).get(MAKE_PAYMENT_REQUEST, [])
	others = [method for method in overrides if method != ours]
	return frappe.get_attr(others[-1]) if others else erpnext_make_payment_request
