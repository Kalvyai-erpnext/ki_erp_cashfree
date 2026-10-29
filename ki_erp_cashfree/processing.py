"""Payment flow: create the Cashfree order, read its outcome and mark the document as paid.

One Integration Request is created per payment link (see `CashfreeSettings.get_payment_url`). It
holds the Cashfree order of that link in `request_id`, so that the return page, the webhook and
the hourly job can all find it. The outcome of a payment is always read from the Cashfree API,
never from the browser or the webhook body.
"""

import json
from contextlib import contextmanager
from datetime import timedelta
from urllib.parse import urlencode

import frappe
from frappe import _
from frappe.utils import flt, get_url, now_datetime

from ki_erp_cashfree import const
from ki_erp_cashfree.client import CashfreeError, normalize_phone, pick_payment, sanitize_id


def commit():
	# The payment outcome must survive errors later in the request; tests roll back instead.
	if not frappe.in_test:
		frappe.db.commit()


def get_settings():
	return frappe.get_single("Cashfree Settings")


def get_integration_request(token):
	"""Return the Integration Request of a payment link, or None if the token is unknown."""
	if not token or not frappe.db.exists(
		"Integration Request", {"name": token, "integration_request_service": const.SERVICE_NAME}
	):
		return None
	return frappe.get_doc("Integration Request", token)


def find_integration_request(order_id):
	name = frappe.db.get_value(
		"Integration Request",
		{"integration_request_service": const.SERVICE_NAME, "request_id": order_id},
	)
	return frappe.get_doc("Integration Request", name) if name else None


def get_success_url(data, redirect_to=None):
	url = "/payment-success?" + urlencode(
		{"doctype": data.get("reference_doctype"), "docname": data.get("reference_docname")}
	)
	if redirect_to:
		url += "&" + urlencode({"redirect_to": redirect_to})
	return url


# === CHECKOUT === #


def prepare_checkout(integration_request):
	"""Return what the checkout page needs to open the Cashfree checkout.

	The Cashfree order is created when the customer opens the payment link, not when the link is
	created, so that links sent by email don't expire. An order that is still active is reused.

	:return: A dict with a `status` of "ready" (with the payment session), "paid" (with a
	         `redirect`) or "error" (with a `message`).
	"""
	ir = integration_request
	data = json.loads(ir.data)
	if ir.status == "Cancelled":
		return {"status": "error", "message": _("This payment link has expired.")}
	if ir.status == "Completed":
		return {"status": "paid", "redirect": get_success_url(data)}

	reference = frappe.get_doc(data["reference_doctype"], data["reference_docname"])
	if reference.doctype == "Payment Request":
		if reference.status == "Paid" or flt(reference.outstanding_amount) <= 0:
			return {"status": "paid", "redirect": get_success_url(data)}
		if reference.docstatus != 1 or reference.status == "Cancelled":
			return {"status": "error", "message": _("This payment request is no longer valid.")}

	settings = get_settings()
	client = settings.get_client()

	order = None
	if ir.request_id:
		order = get_order_of_current_keys(client, ir.request_id) or {}
		order_status = (order.get("order_status") or "").upper()
		if order_status == "PAID":
			result = sync_integration_request(ir)
			if result["status"] == "Completed":
				return {"status": "paid", "redirect": result["redirect"]}
		if order_status != "ACTIVE":
			order = None

	if not order:
		order = create_order(ir, data, reference, settings, client)

	return {
		"status": "ready",
		"payment_session_id": order["payment_session_id"],
		"mode": client.mode,
		"sdk_url": const.JS_SDK_URL,
		"amount": flt(order.get("order_amount")),
		"currency": order.get("order_currency"),
		"title": data.get("title"),
		"description": data.get("description"),
	}


def get_order_of_current_keys(client, order_id):
	"""Return the Cashfree order, or None when it was created with other keys.

	That is the case of links opened before the keys changed, e.g. from Sandbox to Production.
	"""
	try:
		return client.get_order(order_id)
	except CashfreeError as e:
		if e.status_code == 404:
			return None
		raise


def create_order(ir, data, reference, settings, client):
	order_id = sanitize_id(data["reference_docname"], frappe.generate_hash(length=6))
	amount = flt(data.get("amount"), 2)
	if reference.doctype == "Payment Request":
		amount = flt(reference.outstanding_amount, 2)

	order_meta = {"return_url": get_url(f"/cashfree_return?order_id={order_id}")}
	webhook_url = settings.get_webhook_url()
	if webhook_url.startswith("https://"):  # Cashfree only calls secure notification URLs.
		order_meta["notify_url"] = webhook_url

	payload = {
		"order_id": order_id,
		"order_amount": amount,
		"order_currency": data.get("currency") or "INR",
		"customer_details": {
			"customer_id": sanitize_id(get_customer_id(data, reference)),
			"customer_name": (data.get("payer_name") or "")[:100],
			"customer_email": data.get("payer_email") or "",
			"customer_phone": get_customer_phone(reference, settings),
		},
		"order_meta": order_meta,
		"order_note": (data.get("description") or data["reference_docname"])[:200],
		"order_tags": {"erpnext_reference": data["reference_docname"][:255]},
	}
	order = client.create_order(payload)
	if not order.get("payment_session_id"):
		raise CashfreeError(_("Cashfree did not return a payment session."))

	data.update({"cashfree_order_id": order_id, "order_amount": amount})
	ir.db_set({"request_id": order_id, "status": "Queued", "data": json.dumps(data)})
	commit()
	return order


def get_customer_id(data, reference):
	party = reference.get("party") or reference.get("customer")
	return f"erpnext_{party or data.get('payer_email') or 'guest'}"


def get_customer_phone(reference, settings):
	"""Return the customer's phone number, falling back on the number set in Cashfree Settings.

	Cashfree rejects orders without a valid phone number.
	"""
	candidates = [reference.get("phone_number")]
	if reference.doctype == "Payment Request" and reference.reference_doctype and reference.reference_name:
		meta = frappe.get_meta(reference.reference_doctype)
		fields = [f for f in ("contact_mobile", "contact_phone") if meta.has_field(f)]
		if fields:
			candidates += frappe.db.get_value(
				reference.reference_doctype, reference.reference_name, fields, as_dict=True
			).values()
	party = reference.get("party") if reference.get("party_type") == "Customer" else None
	if party:
		candidates.append(frappe.db.get_value("Customer", party, "mobile_no"))
	candidates.append(settings.default_phone)

	for candidate in candidates:
		phone = normalize_phone(candidate)
		if phone:
			return phone
	raise CashfreeError(
		_(
			"A valid mobile number is required to pay with Cashfree. Please contact us to add your "
			"mobile number and try again."
		)
	)


# === PAYMENT OUTCOME === #


def lock_integration_request(name):
	"""Lock the row so that the return page and the webhooks can't both record the payment.

	:return: The current status of the Integration Request.
	"""
	try:
		return frappe.db.get_value("Integration Request", name, "status", for_update=True)
	except frappe.QueryDeadlockError:
		# With snapshot isolation (default since MariaDB 11.8), the lock is refused when another
		# request changed the row after this transaction first read it. Nothing has been written
		# yet: start a new transaction, which waits for the other one and reads its outcome.
		frappe.db.rollback()
		return frappe.db.get_value("Integration Request", name, "status", for_update=True)


def sync_integration_request(ir):
	"""Read the outcome of the Cashfree order of a payment link and record it.

	Safe to call several times and concurrently (return page, webhook and hourly job).

	:return: A dict with the `status` ("Completed", "Failed" or "Pending") and, when completed, the
	         `redirect` URL for the customer.
	"""
	status = lock_integration_request(ir.name)
	ir.reload()
	data = json.loads(ir.data)
	if status == "Completed":
		return {"status": "Completed", "redirect": get_success_url(data)}
	if not ir.request_id:
		return {"status": "Pending"}

	try:
		payments = get_settings().get_client().get_order_payments(ir.request_id)
	except CashfreeError as e:
		if e.status_code == 404:  # order created with other keys: nothing to record
			return {"status": "Pending"}
		raise
	payment = pick_payment(payments)
	payment_status = ((payment or {}).get("payment_status") or "").upper()

	if payment_status in const.SUCCESS_STATUSES:
		error = validate_payment_amount(payment, data)
		if error:
			ir.db_set({"status": "Failed", "error": json.dumps({"message": error, "payment": payment})})
			frappe.log_error(title="Cashfree payment amount mismatch", message=error)
			commit()
			return {"status": "Failed"}
		redirect_to = complete_payment(ir, data, payment)
		commit()
		return {"status": "Completed", "redirect": get_success_url(data, redirect_to)}

	if payment_status in const.FAILED_STATUSES:
		error = payment.get("payment_message") or (payment.get("error_details") or {}).get(
			"error_description"
		)
		ir.db_set({"status": "Failed", "error": json.dumps({"status": payment_status, "message": error})})
		commit()
		return {"status": "Failed", "message": error}

	return {"status": "Pending"}


def validate_payment_amount(payment, data):
	expected = flt(data.get("order_amount"), 2)
	received = flt(payment.get("payment_amount"), 2)
	currency = payment.get("payment_currency") or data.get("currency")
	if received != expected or currency != (data.get("currency") or "INR"):
		return _("Cashfree order {0}: received {1} {2}, expected {3} {4}.").format(
			data.get("cashfree_order_id"), received, currency, expected, data.get("currency")
		)
	return None


def complete_payment(ir, data, payment):
	"""Record a successful payment and mark the referenced document as paid.

	:return: The URL the referenced document wants the customer redirected to, if any.
	"""
	ir.db_set({"status": "Completed", "output": json.dumps(payment, default=str)})
	reference = frappe.get_doc(data["reference_doctype"], data["reference_docname"])

	redirect_to = None
	frappe.db.savepoint("cashfree_authorized")
	try:
		frappe.flags.data = data
		# As Administrator: on ERPNext v16, Webshop's `on_payment_authorized` fails for website
		# users (they can't read the receivable account) and does nothing for guests.
		with as_administrator():
			redirect_to = reference.run_method("on_payment_authorized", "Completed")
	except Exception:
		frappe.db.rollback(save_point="cashfree_authorized")
		frappe.log_error(title="Cashfree: on_payment_authorized failed")

	# ERPNext's Payment Request has no `on_payment_authorized` of its own (only Webshop adds one),
	# so the Payment Entry is created here when needed.
	if reference.doctype == "Payment Request":
		reference.reload()
		if reference.status != "Paid" and flt(reference.outstanding_amount) > 0:
			with as_administrator():
				reference.set_as_paid()

		reference.add_comment(
			"Info",
			_("Paid with Cashfree: order {0}, payment {1} ({2}).").format(
				data.get("cashfree_order_id"),
				payment.get("cf_payment_id"),
				payment.get("payment_group") or _("unknown method"),
			),
		)
	return redirect_to


@contextmanager
def as_administrator():
	user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		yield
	finally:
		frappe.set_user(user)


def sync_pending_payments():
	"""Hourly job: re-check recent unpaid orders, in case a webhook was missed (or can't arrive)."""
	if not frappe.db.get_single_value("Cashfree Settings", "app_id"):
		return
	since = now_datetime() - timedelta(hours=const.SYNC_WINDOW_HOURS)
	names = frappe.get_all(
		"Integration Request",
		filters={
			"integration_request_service": const.SERVICE_NAME,
			"request_id": ["is", "set"],
			"status": ["in", ["Queued", "Failed"]],
			"creation": [">=", since],
		},
		pluck="name",
	)
	for name in names:
		try:
			sync_integration_request(frappe.get_doc("Integration Request", name))
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Cashfree: could not check payment {name}")
