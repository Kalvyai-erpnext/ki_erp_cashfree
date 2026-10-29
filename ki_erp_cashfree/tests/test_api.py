import json
import time
from unittest.mock import patch

import frappe
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from ki_erp_cashfree import api
from ki_erp_cashfree.client import compute_signature
from ki_erp_cashfree.tests.utils import SECRET_KEY, CashfreeTestCase


class TestCashfreeWebhook(CashfreeTestCase):
	def call_webhook(self, data, secret=SECRET_KEY, timestamp=None, signature=None):
		body = json.dumps(data).encode()
		timestamp = timestamp or str(int(time.time() * 1000))
		if signature is None:
			signature = compute_signature(secret, timestamp, body)
		environ = EnvironBuilder(
			method="POST",
			data=body,
			content_type="application/json",
			headers={"x-webhook-timestamp": timestamp, "x-webhook-signature": signature},
		).get_environ()
		previous_request = getattr(frappe.local, "request", None)
		frappe.local.request = Request(environ)
		frappe.local.response = frappe._dict()
		try:
			result = api.webhook()
		finally:
			frappe.local.request = previous_request
		return result, frappe.local.response.get("http_status_code", 200)

	def payment_event(self, order_id, event="PAYMENT_SUCCESS_WEBHOOK"):
		return {"type": event, "data": {"order": {"order_id": order_id}, "payment": {}}}

	def test_signed_webhook_completes_payment(self):
		pr = self.make_payment_request()
		ir, _checkout = self.open_checkout(pr)
		self.pay(pr)

		result, status = self.call_webhook(self.payment_event(ir.request_id))

		self.assertEqual((result, status), ({"status": "Completed"}, 200))
		self.assertPaid(pr)

		# Cashfree retries the notification: nothing more happens
		self.assertEqual(self.call_webhook(self.payment_event(ir.request_id))[0], {"status": "Completed"})
		self.assertPaid(pr, payment_entries=1)

	def test_webhook_body_is_not_trusted(self):
		# A genuine notification of success while Cashfree's API says the payment failed
		pr = self.make_payment_request()
		ir, _checkout = self.open_checkout(pr)
		self.pay(pr, status="FAILED")

		result, _status = self.call_webhook(self.payment_event(ir.request_id))

		self.assertEqual(result, {"status": "Failed"})
		self.assertNotPaid(pr)

	def test_rejected_webhooks(self):
		pr = self.make_payment_request()
		ir, _checkout = self.open_checkout(pr)
		self.pay(pr)
		event = self.payment_event(ir.request_id)
		old = str(int((time.time() - 2 * 86400) * 1000))

		for kwargs in (
			{"secret": "wrong-secret"},
			{"signature": ""},
			{"signature": "forged"},
			{"timestamp": old, "signature": compute_signature(SECRET_KEY, old, json.dumps(event).encode())},
		):
			self.assertEqual(self.call_webhook(event, **kwargs), ({"status": "rejected"}, 401), kwargs)
		self.assertNotPaid(pr)

	def test_ignored_webhooks(self):
		self.assertEqual(self.call_webhook({"type": "REFUND_STATUS_WEBHOOK"})[0], {"status": "ignored"})
		self.assertEqual(self.call_webhook(self.payment_event("unknown-order"))[0], {"status": "ignored"})

	def test_failure_asks_cashfree_to_retry(self):
		pr = self.make_payment_request()
		ir, _checkout = self.open_checkout(pr)
		with (
			patch("ki_erp_cashfree.api.sync_integration_request", side_effect=Exception("database down")),
			patch("ki_erp_cashfree.api.frappe.log_error"),
			patch("ki_erp_cashfree.api.frappe.db.rollback"),
		):
			self.assertEqual(self.call_webhook(self.payment_event(ir.request_id)), ({"status": "error"}, 500))


class TestWebshopPayButton(CashfreeTestCase):
	WEBSITE_USER = "rahul@example.test"  # contact of the demo customer

	def click_pay(self, so, user=WEBSITE_USER):
		frappe.local.response = frappe._dict()
		frappe.set_user(user)
		try:
			api.make_payment_request(dt=so.doctype, dn=so.name, submit_doc=1, order_type="Shopping Cart")
		finally:
			frappe.set_user("Administrator")
		return frappe.local.response

	def test_customer_pays_with_cashfree(self):
		frappe.db.set_single_value(
			"Webshop Settings", "payment_gateway_account", "Cash on Delivery - INR - KD"
		)
		so = self.make_sales_order()

		response = self.click_pay(so)

		self.assertEqual(response.type, "redirect")
		self.assertIn("/cashfree_checkout?token=", response.location)
		pr = frappe.get_doc("Payment Request", {"reference_name": so.name, "docstatus": 1})
		self.assertEqual(pr.payment_gateway, "Cashfree")
		self.assertEqual(pr.grand_total, so.grand_total)
		self.assertTrue(pr.make_sales_invoice)

	def test_order_of_another_customer(self):
		other = frappe.get_doc(
			{"doctype": "Customer", "customer_name": "Cashfree Other Customer", "customer_type": "Individual"}
		).insert()
		so = self.make_sales_order()
		so.db_set("customer", other.name)

		with self.assertRaises(frappe.PermissionError):
			self.click_pay(so)
		self.assertFalse(frappe.db.exists("Payment Request", {"reference_name": so.name}))

	def test_other_calls_go_on_unchanged(self):
		with patch("ki_erp_cashfree.api.get_next_make_payment_request") as get_next:
			api.make_payment_request(dt="Sales Order", dn="SO-1", payment_gateway_account="Any")
		get_next.return_value.assert_called_once_with(
			dt="Sales Order", dn="SO-1", payment_gateway_account="Any"
		)

	def test_pay_button_can_be_left_to_another_gateway(self):
		frappe.db.set_single_value("Cashfree Settings", "webshop_pay_button", 0)
		try:
			with patch("ki_erp_cashfree.api.get_next_make_payment_request") as get_next:
				api.make_payment_request(dt="Sales Order", dn="SO-1", order_type="Shopping Cart")
			get_next.return_value.assert_called_once()
		finally:
			frappe.db.set_single_value("Cashfree Settings", "webshop_pay_button", 1)

	def test_next_override(self):
		other = "other_app.api.make_payment_request"
		hooks = {api.MAKE_PAYMENT_REQUEST: [other, "ki_erp_cashfree.api.make_payment_request"]}
		with (
			patch("frappe.get_hooks", return_value=hooks),
			patch("frappe.get_attr", side_effect=lambda method: method),
		):
			self.assertEqual(api.get_next_make_payment_request(), other)
		with patch("frappe.get_hooks", return_value={}):
			self.assertIs(api.get_next_make_payment_request(), api.erpnext_make_payment_request)


class TestCheckPaymentStatus(CashfreeTestCase):
	def test_check_status(self):
		pr = self.make_payment_request()
		self.assertIn("hasn't opened", api.check_payment_status(pr.name))

		self.open_checkout(pr)
		self.pay(pr, status="PENDING")
		self.assertIn("pending", api.check_payment_status(pr.name))

		self.pay(pr, status="FAILED")
		self.assertIn("No successful payment", api.check_payment_status(pr.name))

		self.pay(pr)
		self.assertIn("complete", api.check_payment_status(pr.name))
		self.assertPaid(pr)

	def test_check_status_needs_permission(self):
		pr = self.make_payment_request()
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				api.check_payment_status(pr.name)
		finally:
			frappe.set_user("Administrator")
