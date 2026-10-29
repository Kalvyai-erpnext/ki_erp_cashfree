frappe.ui.form.on("Payment Request", {
	refresh(frm) {
		if (
			frm.doc.payment_gateway === "Cashfree" &&
			frm.doc.docstatus === 1 &&
			!["Paid", "Cancelled"].includes(frm.doc.status)
		) {
			frm.add_custom_button(__("Check Status with Cashfree"), () => {
				frappe
					.call({
						method: "ki_erp_cashfree.api.check_payment_status",
						args: { payment_request: frm.doc.name },
						freeze: true,
					})
					.then((r) => {
						frappe.show_alert({ message: r.message, indicator: "blue" });
						frm.reload_doc();
					});
			});
		}
	},
});
