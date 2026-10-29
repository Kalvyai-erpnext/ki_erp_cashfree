"""Small Cashfree PG REST client.

The official Cashfree Python SDK pins dependencies that clash with Frappe, so the REST API is
called directly with `requests`.
"""

import base64
import hashlib
import hmac
import re
import time

import frappe
import requests
from frappe import _

from ki_erp_cashfree import const


class CashfreeError(Exception):
	def __init__(self, message, code=None, status_code=None):
		super().__init__(message)
		self.code = code
		self.status_code = status_code


class CashfreeClient:
	def __init__(self, app_id, secret_key, environment="Sandbox"):
		self.app_id = app_id or ""
		self.secret_key = secret_key or ""
		self.environment = environment if environment in const.API_URLS else "Sandbox"

	@property
	def mode(self):
		"""Mode expected by the Cashfree JS SDK."""
		return "production" if self.environment == "Production" else "sandbox"

	def request(self, method, endpoint, payload=None, idempotency_key=None):
		headers = {
			"x-client-id": self.app_id,
			"x-client-secret": self.secret_key,
			"x-api-version": const.API_VERSION,
			"Accept": "application/json",
		}
		if idempotency_key:
			headers["x-idempotency-key"] = idempotency_key
		url = f"{const.API_URLS[self.environment]}{endpoint}"
		try:
			response = requests.request(
				method, url, json=payload, headers=headers, timeout=const.REQUEST_TIMEOUT
			)
		except requests.RequestException as e:
			raise CashfreeError(_("Could not reach Cashfree: {0}").format(e))

		try:
			content = response.json()
		except ValueError:
			content = {}
		if not response.ok:
			message = (content.get("message") if isinstance(content, dict) else None) or response.reason
			code = content.get("code") if isinstance(content, dict) else None
			raise CashfreeError(
				f"{message} ({code})" if code else message, code=code, status_code=response.status_code
			)
		return content

	def create_order(self, payload):
		return self.request("POST", "orders", payload, idempotency_key=payload["order_id"])

	def get_order(self, order_id):
		return self.request("GET", f"orders/{order_id}")

	def get_order_payments(self, order_id):
		response = self.request("GET", f"orders/{order_id}/payments")
		return response if isinstance(response, list) else response.get("data", [])

	def check_credentials(self):
		"""Fetch an order that doesn't exist: Cashfree answers 404 when the credentials are valid."""
		try:
			self.get_order(f"erpnext-connection-test-{frappe.generate_hash(length=8)}")
		except CashfreeError as e:
			if e.status_code == 404:
				return
			raise


def compute_signature(secret_key, timestamp, raw_body):
	"""Return the webhook signature as documented by Cashfree.

	The signature is the base64 encoding of the HMAC-SHA256 of the timestamp concatenated with the
	raw request body, signed with the secret key.
	"""
	if isinstance(raw_body, str):
		raw_body = raw_body.encode()
	message = (timestamp or "").encode() + (raw_body or b"")
	digest = hmac.new(secret_key.encode(), msg=message, digestmod=hashlib.sha256).digest()
	return base64.b64encode(digest).decode()


def verify_signature(secret_key, timestamp, raw_body, signature, now=None):
	"""Return None if the webhook is authentic, otherwise the reason it was rejected."""
	if not signature or not timestamp:
		return _("The signature headers are missing.")
	try:
		timestamp_seconds = int(timestamp) / 1000 if len(timestamp) > 10 else int(timestamp)
	except ValueError:
		return _("The webhook timestamp is invalid.")
	if abs((now or time.time()) - timestamp_seconds) > const.WEBHOOK_MAX_AGE:
		return _("The webhook timestamp is too old.")
	if not secret_key or not hmac.compare_digest(
		signature, compute_signature(secret_key, timestamp, raw_body)
	):
		return _("The signature does not match the secret key.")
	return None


def sanitize_id(value, suffix=""):
	"""Turn an ERPNext name into a valid Cashfree identifier; the suffix is never truncated."""
	cleaned = "".join(c if c.isalnum() or c in "-_" else "_" for c in value or "")
	cleaned = cleaned.strip("_-") or "erpnext"
	suffix = f"-{suffix}" if suffix else ""
	return f"{cleaned[: const.ID_MAX_LENGTH - len(suffix)]}{suffix}"


def normalize_phone(phone):
	"""Return the phone number in a format Cashfree accepts, or an empty string if invalid.

	Cashfree accepts a 10-digit Indian mobile number or a number in the international format.
	Numbers without a country code are treated as Indian numbers.
	"""
	if not phone:
		return ""
	has_plus = phone.strip().startswith("+")
	digits = re.sub(r"\D", "", phone)
	min_length, max_length = const.INTERNATIONAL_PHONE_LENGTH
	if not has_plus:
		digits = digits.lstrip("0")  # Trunk prefix, as in 09876543210.
		if re.fullmatch(const.INDIAN_MOBILE_PATTERN, digits):
			return digits
		if not (len(digits) == 12 and digits.startswith("91")):
			return ""
	if digits.startswith("91"):  # Indian numbers must be mobile numbers.
		return f"+{digits}" if re.fullmatch(const.INDIAN_MOBILE_PATTERN, digits[2:]) else ""
	return f"+{digits}" if min_length <= len(digits) <= max_length else ""


def pick_payment(payments):
	"""Return the most relevant payment attempt of an order: a successful one, else the latest."""
	if not payments:
		return None
	successful = [p for p in payments if (p.get("payment_status") or "").upper() in const.SUCCESS_STATUSES]
	if successful:
		return successful[0]
	return max(
		payments,
		key=lambda p: (
			p.get("payment_time") or p.get("payment_completion_time") or "",
			str(p.get("cf_payment_id") or ""),
		),
	)
