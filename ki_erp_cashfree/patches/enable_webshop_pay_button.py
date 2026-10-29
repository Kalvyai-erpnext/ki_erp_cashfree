import frappe


def execute():
	"""Keep the Webshop Pay button on Cashfree for sites installed before the setting existed."""
	if not frappe.db.exists("Singles", {"doctype": "Cashfree Settings", "field": "webshop_pay_button"}):
		frappe.db.set_single_value("Cashfree Settings", "webshop_pay_button", 1)
