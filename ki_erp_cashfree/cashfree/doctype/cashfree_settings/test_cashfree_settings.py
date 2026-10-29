# Copyright (c) 2026, kalvyai and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from ki_erp_cashfree.client import CashfreeClient, CashfreeError


class TestCashfreeSettings(IntegrationTestCase):
	def get_settings(self, **values):
		settings = frappe.get_single("Cashfree Settings")
		settings.update(
			{"environment": "Sandbox", "app_id": "TEST_APP", "secret_key": "TEST_SECRET", **values}
		)
		return settings

	@patch.object(CashfreeClient, "check_credentials")
	def test_save_creates_gateway(self, check_credentials):
		settings = self.get_settings()
		settings.save()

		check_credentials.assert_called_once()
		self.assertTrue(frappe.db.exists("Payment Gateway", "Cashfree"))
		self.assertTrue(frappe.db.exists("Payment Gateway Account", {"payment_gateway": "Cashfree"}))
		self.assertTrue(settings.webhook_url.endswith("/api/method/ki_erp_cashfree.api.webhook"))

	@patch.object(
		CashfreeClient,
		"check_credentials",
		side_effect=CashfreeError("authentication Failed", code="authentication_error", status_code=401),
	)
	def test_wrong_credentials_are_rejected(self, _check_credentials):
		with self.assertRaises(frappe.ValidationError):
			self.get_settings(secret_key="wrong").save()

	@patch.object(CashfreeClient, "check_credentials")
	def test_fallback_mobile_must_be_valid(self, _check_credentials):
		with self.assertRaises(frappe.ValidationError):
			self.get_settings(default_phone="12345").save()
		self.get_settings(default_phone="+91 98765 43210").save()

	def test_client_uses_environment(self):
		client = self.get_settings(environment="Production").get_client()
		self.assertEqual(client.mode, "production")
		self.assertEqual(self.get_settings().get_client().mode, "sandbox")
