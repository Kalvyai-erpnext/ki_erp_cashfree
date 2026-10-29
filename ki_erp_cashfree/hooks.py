app_name = "ki_erp_cashfree"
app_title = "Cashfree Payments for ERPNext"
app_publisher = "kalvyai"
app_description = "Cashfree payment gateway for ERPNext: UPI, cards, net banking and wallets"
app_email = "kalvyai.admin@gmail.com"
app_license = "mit"

required_apps = ["frappe/erpnext", "frappe/payments"]

doctype_js = {"Payment Request": "public/js/payment_request.js"}

# The Webshop's Pay button: payment with Cashfree, open to website customers
override_whitelisted_methods = {
	"erpnext.accounts.doctype.payment_request.payment_request.make_payment_request": (
		"ki_erp_cashfree.api.make_payment_request"
	)
}

scheduler_events = {
	"hourly": ["ki_erp_cashfree.processing.sync_pending_payments"],
}
