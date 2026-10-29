import frappe
from frappe import _

from ki_erp_cashfree.client import CashfreeError
from ki_erp_cashfree.processing import find_integration_request, sync_integration_request

no_cache = 1


def get_context(context):
	"""Page Cashfree sends the customer back to; the outcome is read from the Cashfree API."""
	context.no_cache = 1
	context.show_sidebar = False

	order_id = frappe.form_dict.get("order_id")
	integration_request = find_integration_request(order_id) if order_id else None
	if not integration_request:
		context.update(status="error", message=_("This payment could not be found."))
		return

	try:
		result = sync_integration_request(integration_request)
	except CashfreeError:
		frappe.log_error(title=f"Cashfree: could not check order {order_id}")
		result = {"status": "Pending"}

	if result["status"] == "Completed":
		frappe.local.flags.redirect_location = result["redirect"]
		raise frappe.Redirect

	context.status = result["status"]
	context.message = result.get("message")
	context.retry_url = f"/cashfree_checkout?token={integration_request.name}"
