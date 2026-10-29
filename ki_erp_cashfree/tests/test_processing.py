import json
from unittest.mock import patch

import frappe
from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice
from frappe.utils import flt

from ki_erp_cashfree.client import CashfreeError
from ki_erp_cashfree.processing import (
	find_integration_request,
	get_customer_phone,
	prepare_checkout,
	sync_integration_request,
	sync_pending_payments,
)
from ki_erp_cashfree.tests.utils import CashfreeTestCase


class TestCashfreeCheckout(CashfreeTestCase):
	def test_gateway_account_created(self):
		self.assertTrue(self.gateway_account)
		pr = self.make_payment_request()
		self.assertIn("/cashfree_checkout?token=", pr.payment_url)
		# Asking again gives the same link
		self.assertEqual(pr.get_payment_url(), pr.payment_url)

	def test_checkout_creates_order(self):
		pr = self.make_payment_request(rate=750)
		ir, checkout = self.open_checkout(pr)

		self.assertEqual(checkout["status"], "ready")
		self.assertEqual(checkout["mode"], "sandbox")
		self.assertEqual(ir.status, "Queued")
		order = self.fake.orders[ir.request_id]
		self.assertEqual(order["order_amount"], flt(pr.grand_total, 2))
		self.assertEqual(order["order_currency"], "INR")
		self.assertEqual(order["customer_details"]["customer_phone"], "+919876543210")
		self.assertEqual(order["customer_details"]["customer_id"], "erpnext_Rahul_Sharma")
		self.assertIn(f"/cashfree_return?order_id={ir.request_id}", order["order_meta"]["return_url"])
		if frappe.utils.get_url().startswith("https://"):
			self.assertTrue(
				order["order_meta"]["notify_url"].endswith("/api/method/ki_erp_cashfree.api.webhook")
			)
		else:
			self.assertNotIn("notify_url", order["order_meta"])  # Cashfree can't call an http:// site

		# Opening the link again reuses the active order
		self.assertEqual(prepare_checkout(ir)["payment_session_id"], checkout["payment_session_id"])
		self.assertEqual(len(self.fake.orders), 1)

	def test_closed_order_is_replaced(self):
		pr = self.make_payment_request()
		ir, _checkout = self.open_checkout(pr)
		first_order = ir.request_id
		self.fake.orders[first_order]["order_status"] = "EXPIRED"

		checkout = prepare_checkout(ir)

		self.assertEqual(checkout["status"], "ready")
		self.assertNotEqual(ir.reload().request_id, first_order)
		self.assertEqual(len(self.fake.orders), 2)

	def test_order_of_other_keys(self):
		# A link opened before the Cashfree keys changed (e.g. from Sandbox to Production)
		pr = self.make_payment_request()
		ir, _checkout = self.open_checkout(pr)
		old_order = ir.request_id
		del self.fake.orders[old_order]  # the fake now answers 404
		self.pay(pr)

		self.assertEqual(sync_integration_request(ir)["status"], "Pending")
		checkout = prepare_checkout(ir)

		self.assertEqual(checkout["status"], "ready")
		self.assertNotEqual(ir.reload().request_id, old_order)

	def test_cancelled_payment_request(self):
		pr = self.make_payment_request()
		pr.cancel()
		_ir, checkout = self.open_checkout(pr)
		self.assertEqual(checkout["status"], "error")
		self.assertEqual(self.fake.orders, {})

	def test_paid_order_found_when_link_reopened(self):
		# The customer paid but neither the return page nor the webhook arrived
		pr = self.make_payment_request()
		ir, _checkout = self.open_checkout(pr)
		self.fake.orders[ir.request_id]["order_status"] = "PAID"
		self.pay(pr)

		checkout = prepare_checkout(ir)

		self.assertEqual(checkout["status"], "paid")
		self.assertPaid(pr)

	def test_unsupported_currency(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_single("Cashfree Settings").validate_transaction_currency("XYZ")


class TestCashfreePaymentOutcome(CashfreeTestCase):
	def test_successful_payment_marks_request_paid(self):
		pr = self.make_payment_request()
		ir, _checkout = self.open_checkout(pr)
		self.pay(pr)

		result = sync_integration_request(find_integration_request(ir.request_id))

		self.assertEqual(result["status"], "Completed")
		self.assertIn("/payment-success?", result["redirect"])
		self.assertPaid(pr)
		self.assertEqual(frappe.db.get_value("Integration Request", ir.name, "status"), "Completed")
		self.assertEqual(json.loads(ir.reload().output)["cf_payment_id"], 100)
		self.assertTrue(
			frappe.db.exists(
				"Comment", {"reference_name": pr.name, "content": ["like", "%Paid with Cashfree%"]}
			)
		)

		# Return page and webhook both arriving: no second Payment Entry
		self.assertEqual(sync_integration_request(ir)["status"], "Completed")
		self.assertPaid(pr, payment_entries=1)
		# A paid link redirects to the success page
		self.assertEqual(prepare_checkout(ir.reload())["status"], "paid")

	def test_payment_confirmed_by_webhook_as_guest(self):
		pr = self.make_payment_request()
		ir, _checkout = self.open_checkout(pr)
		self.pay(pr)

		frappe.set_user("Guest")
		try:
			result = sync_integration_request(ir)
		finally:
			frappe.set_user("Administrator")

		self.assertEqual(result["status"], "Completed")
		self.assertPaid(pr)

	def test_refused_lock_is_retried(self):
		# Cashfree sends each webhook in two versions at once, often with the customer's return:
		# MariaDB can refuse the lock of the one that comes second
		pr = self.make_payment_request()
		ir, _checkout = self.open_checkout(pr)
		self.pay(pr)
		db_class = type(frappe.local.db)
		get_value = db_class.get_value
		refused = []

		def refuse_first_lock(db, *args, **kwargs):
			if kwargs.get("for_update") and not refused:
				refused.append(args)
				raise frappe.QueryDeadlockError
			return get_value(db, *args, **kwargs)

		with (
			patch.object(db_class, "get_value", autospec=True, side_effect=refuse_first_lock),
			patch.object(db_class, "rollback", autospec=True) as rollback,
		):
			self.assertEqual(sync_integration_request(ir)["status"], "Completed")

		self.assertTrue(refused)
		rollback.assert_called_once()
		self.assertPaid(pr)

	def test_invoice_payment(self):
		so = self.make_sales_order(rate=640)
		invoice = make_sales_invoice(so.name)
		invoice.insert()
		invoice.submit()
		pr = self.make_payment_request(dt="Sales Invoice", dn=invoice.name)
		ir, _checkout = self.open_checkout(pr)
		self.pay(pr)

		self.assertEqual(sync_integration_request(ir)["status"], "Completed")

		self.assertPaid(pr)
		self.assertEqual(frappe.db.get_value("Sales Invoice", invoice.name, "outstanding_amount"), 0)

	def test_failed_payment(self):
		pr = self.make_payment_request()
		ir, _checkout = self.open_checkout(pr)
		self.pay(pr, status="FAILED", payment_message="Declined")

		result = sync_integration_request(ir)

		self.assertEqual(result["status"], "Failed")
		self.assertEqual(result["message"], "Declined")
		self.assertEqual(ir.reload().status, "Failed")
		self.assertNotPaid(pr)

		# The customer retries on the same order and pays
		self.pay(pr)
		self.assertEqual(sync_integration_request(ir)["status"], "Completed")
		self.assertPaid(pr)

	def test_user_dropped(self):
		pr = self.make_payment_request()
		ir, _checkout = self.open_checkout(pr)
		self.pay(pr, status="USER_DROPPED")
		self.assertEqual(sync_integration_request(ir)["status"], "Failed")
		self.assertNotPaid(pr)

	def test_pending_payment(self):
		pr = self.make_payment_request()
		ir, _checkout = self.open_checkout(pr)
		self.pay(pr, status="PENDING")
		self.assertEqual(sync_integration_request(ir)["status"], "Pending")
		self.assertEqual(ir.reload().status, "Queued")
		self.assertNotPaid(pr)

	def test_no_payment_attempt_yet(self):
		pr = self.make_payment_request()
		ir, _checkout = self.open_checkout(pr)
		self.assertEqual(sync_integration_request(ir)["status"], "Pending")

	def test_amount_mismatch_is_not_marked_paid(self):
		pr = self.make_payment_request()
		ir, _checkout = self.open_checkout(pr)
		self.pay(pr, amount=1)

		with patch("ki_erp_cashfree.processing.frappe.log_error") as log_error:
			self.assertEqual(sync_integration_request(ir)["status"], "Failed")

		log_error.assert_called_once()
		self.assertNotPaid(pr)
		self.assertIn("expected", json.loads(ir.reload().error)["message"])

	def test_currency_mismatch_is_not_marked_paid(self):
		pr = self.make_payment_request()
		ir, _checkout = self.open_checkout(pr)
		self.pay(pr, payment_currency="USD")

		with patch("ki_erp_cashfree.processing.frappe.log_error"):
			self.assertEqual(sync_integration_request(ir)["status"], "Failed")
		self.assertNotPaid(pr)

	def test_hourly_job_records_missed_payments(self):
		paid = self.make_payment_request()
		paid_ir, _checkout = self.open_checkout(paid)
		unpaid = self.make_payment_request(rate=600)
		self.open_checkout(unpaid)

		# Cashfree reports the same payment list for every order in this fake: only `paid` matches
		self.pay(paid)
		with patch("ki_erp_cashfree.processing.frappe.log_error"):
			sync_pending_payments()

		self.assertPaid(paid)
		self.assertEqual(paid_ir.reload().status, "Completed")
		self.assertNotPaid(unpaid)  # its amount differs, so it isn't accepted


class TestCustomerPhone(CashfreeTestCase):
	def reference(self, **values):
		return frappe._dict(doctype="Payment Request", **values)

	def test_phone_from_payment_request(self):
		settings = frappe._dict(default_phone="9000000000")
		self.assertEqual(
			get_customer_phone(self.reference(phone_number="98765 43210"), settings), "9876543210"
		)

	def test_phone_from_customer(self):
		settings = frappe._dict(default_phone="")
		reference = self.reference(party_type="Customer", party="Rahul Sharma")
		self.assertEqual(get_customer_phone(reference, settings), "+919876543210")

	def test_invalid_phone_falls_back_on_settings(self):
		settings = frappe._dict(default_phone="+91 90000 00000")
		self.assertEqual(get_customer_phone(self.reference(phone_number="12345"), settings), "+919000000000")

	def test_no_phone(self):
		with self.assertRaises(CashfreeError):
			get_customer_phone(self.reference(), frappe._dict(default_phone=""))
