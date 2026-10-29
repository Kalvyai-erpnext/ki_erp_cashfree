// Copyright (c) 2026, kalvyai and contributors
// For license information, please see license.txt

frappe.ui.form.on("Cashfree Settings", {
	refresh(frm) {
		if (frm.doc.app_id && !frm.is_dirty()) {
			frm.add_custom_button(__("Test Connection"), () => {
				frm.call("test_connection").then((r) => {
					frappe.show_alert({ message: r.message, indicator: "green" });
				});
			});
		}
		if (frm.doc.webhook_url) {
			frm.add_custom_button(__("Copy Webhook URL"), () => {
				frappe.utils.copy_to_clipboard(frm.doc.webhook_url);
			});
		}
	},
});
