# Cashfree Payment Gateway (PG) constants.
# See https://www.cashfree.com/docs/api-reference/payments/latest/overview

SERVICE_NAME = "Cashfree"

API_VERSION = "2025-01-01"

API_URLS = {
	"Sandbox": "https://sandbox.cashfree.com/pg/",
	"Production": "https://api.cashfree.com/pg/",
}

JS_SDK_URL = "https://sdk.cashfree.com/js/v3/cashfree.js"

REQUEST_TIMEOUT = 30

# Cashfree order identifiers accept alphanumerics, "_" and "-" only.
ID_MAX_LENGTH = 45

# Phone numbers accepted by Cashfree: a 10-digit Indian mobile number, or any number in the
# international format (+ country code and number, E.164 allows up to 15 digits).
INDIAN_MOBILE_PATTERN = r"[6-9]\d{9}"
INTERNATIONAL_PHONE_LENGTH = (8, 15)

# Currencies Cashfree can accept (international payments must be enabled on the merchant account
# for anything other than INR). Settlements are always made in INR.
SUPPORTED_CURRENCIES = (
	"AED", "AUD", "BDT", "BHD", "CAD", "CHF", "CNY", "CZK", "DKK", "EUR", "GBP", "HKD", "IDR",
	"ILS", "INR", "JPY", "KES", "KRW", "KWD", "LKR", "MUR", "MXN", "MYR", "NOK", "NPR", "NZD",
	"OMR", "PHP", "PLN", "QAR", "SAR", "SEK", "SGD", "THB", "TRY", "TWD", "USD", "VND", "ZAR",
)  # fmt: skip

# Cashfree payment statuses, grouped by their outcome.
SUCCESS_STATUSES = ("SUCCESS",)
FAILED_STATUSES = ("FAILED", "USER_DROPPED", "CANCELLED", "VOID")

# Cashfree order statuses for which the order can no longer be paid.
CLOSED_ORDER_STATUSES = ("EXPIRED", "TERMINATED", "TERMINATION_REQUESTED")

PAYMENT_WEBHOOK_EVENTS = (
	"PAYMENT_SUCCESS_WEBHOOK",
	"PAYMENT_FAILED_WEBHOOK",
	"PAYMENT_USER_DROPPED_WEBHOOK",
)

# Webhooks older than this are rejected to prevent replays (seconds).
WEBHOOK_MAX_AGE = 24 * 3600

# Unpaid attempts younger than this are re-checked with Cashfree by the hourly job (hours).
SYNC_WINDOW_HOURS = 72
