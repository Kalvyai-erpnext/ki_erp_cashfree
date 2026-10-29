from frappe.tests import UnitTestCase

from ki_erp_cashfree.client import (
	compute_signature,
	normalize_phone,
	pick_payment,
	sanitize_id,
	verify_signature,
)


class TestCashfreeClient(UnitTestCase):
	def test_compute_signature_matches_cashfree_formula(self):
		# base64(HMAC-SHA256(secret, timestamp + raw body))
		self.assertEqual(
			compute_signature("secret", "1700000000", b'{"a":1}'),
			"86FWgFUUlefScwLE0Z5MvtxlDxn6W0A3PADL6lKmRAY=",
		)

	def test_verify_signature(self):
		body = b'{"type":"PAYMENT_SUCCESS_WEBHOOK"}'
		timestamp = "1700000000000"  # milliseconds, as sent by Cashfree
		signature = compute_signature("secret", timestamp, body)
		now = 1700000000 + 60

		self.assertIsNone(verify_signature("secret", timestamp, body, signature, now=now))
		self.assertIsNotNone(verify_signature("other", timestamp, body, signature, now=now))
		self.assertIsNotNone(verify_signature("secret", timestamp, body + b" ", signature, now=now))
		self.assertIsNotNone(verify_signature("secret", timestamp, body, None, now=now))
		self.assertIsNotNone(verify_signature("secret", "abc", body, signature, now=now))
		# replayed a week later
		self.assertIsNotNone(verify_signature("secret", timestamp, body, signature, now=now + 7 * 86400))

	def test_sanitize_id(self):
		self.assertEqual(sanitize_id("ACC-PRQ-2026-00001", "ab12cd"), "ACC-PRQ-2026-00001-ab12cd")
		self.assertEqual(sanitize_id("SO/001 #2"), "SO_001__2")
		self.assertEqual(sanitize_id(""), "erpnext")
		long_id = sanitize_id("X" * 100, "suffix")
		self.assertEqual(len(long_id), 45)
		self.assertTrue(long_id.endswith("-suffix"))

	def test_normalize_phone(self):
		cases = {
			"9876543210": "9876543210",
			"09876543210": "9876543210",
			"+91-9876543210": "+919876543210",
			"919876543210": "+919876543210",
			"+91 1234567890": "",  # not a mobile number
			"+1 415 555 2671": "+14155552671",
			"12345": "",
			"": "",
			None: "",
		}
		for phone, expected in cases.items():
			self.assertEqual(normalize_phone(phone), expected, phone)

	def test_pick_payment(self):
		self.assertIsNone(pick_payment([]))
		failed = {"cf_payment_id": 1, "payment_status": "FAILED", "payment_time": "2026-10-23T10:00:00"}
		success = {"cf_payment_id": 2, "payment_status": "SUCCESS", "payment_time": "2026-10-23T09:00:00"}
		dropped = {
			"cf_payment_id": 3,
			"payment_status": "USER_DROPPED",
			"payment_time": "2026-10-23T11:00:00",
		}
		self.assertEqual(pick_payment([failed, success, dropped]), success)
		self.assertEqual(pick_payment([failed, dropped]), dropped)
