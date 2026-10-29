from unittest.mock import patch

import frappe
from erpnext.accounts.doctype.payment_request.payment_request import make_payment_request
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from ki_erp_cashfree import const
from ki_erp_cashfree.client import CashfreeClient, CashfreeError
from ki_erp_cashfree.processing import prepare_checkout

# Demo data of the development site
COMPANY = "Kalvy Demo"
CUSTOMER = "Rahul Sharma"
ITEM = "TSHIRT-001"
WAREHOUSE = "Stores - KD"
SECRET_KEY = "TEST_SECRET"


class FakeCashfree:
	"""Stands in for the Cashfree API: remembers created orders and returns preset payments."""

	def __init__(self):
		self.orders = {}
		self.payments = []

	def create_order(self, client, payload):
		order = {**payload, "order_status": "ACTIVE", "payment_session_id": f"session_{payload['order_id']}"}
		self.orders[payload["order_id"]] = order
		return order

	def get_order(self, client, order_id):
		self.check_order_exists(order_id)
		return self.orders[order_id]

	def get_order_payments(self, client, order_id):
		self.check_order_exists(order_id)
		return self.payments

	def check_order_exists(self, order_id):
		if order_id not in self.orders:
			raise CashfreeError("Order Reference Id does not exist", code="order_not_found", status_code=404)


class CashfreeTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		settings = frappe.get_single("Cashfree Settings")
		settings.update(
			{"environment": "Sandbox", "app_id": "TEST_APP", "secret_key": SECRET_KEY, "default_phone": ""}
		)
		settings.flags.ignore_mandatory = True  # skips the credential check with Cashfree
		settings.save()
		cls.gateway_account = frappe.db.get_value(
			"Payment Gateway Account", {"payment_gateway": const.SERVICE_NAME, "currency": "INR"}
		)

	def setUp(self):
		self.fake = FakeCashfree()
		for method in ("create_order", "get_order", "get_order_payments"):
			patcher = patch.object(
				CashfreeClient, method, autospec=True, side_effect=getattr(self.fake, method)
			)
			patcher.start()
			self.addCleanup(patcher.stop)

	def make_sales_order(self, rate=500):
		so = frappe.get_doc(
			{
				"doctype": "Sales Order",
				"company": COMPANY,
				"customer": CUSTOMER,
				"transaction_date": nowdate(),
				"delivery_date": add_days(nowdate(), 7),
				"items": [{"item_code": ITEM, "qty": 1, "rate": rate, "warehouse": WAREHOUSE}],
			}
		).insert()
		so.submit()
		return so

	def make_payment_request(self, rate=500, dt="Sales Order", dn=None):
		if not dn:
			dn = self.make_sales_order(rate).name
		return make_payment_request(
			dt=dt,
			dn=dn,
			payment_gateway_account=self.gateway_account,
			mute_email=1,
			submit_doc=1,
			return_doc=1,
		)

	def open_checkout(self, pr):
		token = pr.payment_url.split("token=")[1]
		ir = frappe.get_doc("Integration Request", token)
		checkout = prepare_checkout(ir)
		return ir.reload(), checkout

	def pay(self, pr, status="SUCCESS", amount=None, **extra):
		"""Make Cashfree report a payment attempt on the order of `pr`."""
		self.fake.payments = [
			{
				"cf_payment_id": len(self.fake.payments) + 100,
				"payment_status": status,
				"payment_amount": pr.grand_total if amount is None else amount,
				"payment_currency": "INR",
				"payment_group": "upi",
				**extra,
			}
		]

	def assertPaid(self, pr, payment_entries=1):
		self.assertEqual(frappe.db.get_value("Payment Request", pr.name, "status"), "Paid")
		self.assertEqual(
			frappe.db.count("Payment Entry", {"reference_no": pr.name, "docstatus": 1}), payment_entries
		)

	def assertNotPaid(self, pr):
		self.assertNotEqual(frappe.db.get_value("Payment Request", pr.name, "status"), "Paid")
		self.assertEqual(frappe.db.count("Payment Entry", {"reference_no": pr.name}), 0)
