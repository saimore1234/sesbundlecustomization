// Bundle Weight Calculation - Sales Invoice client script.
// Amount = Qty x Rate x Bundle Weight (KG) for Item.custom_is_bundle items.
//
// ERPNext's browser-side totals engine (erpnext.taxes_and_totals) computes
// item.amount = rate * qty. We wrap its calculate_item_values / _calculate_taxes_and_totals
// so the bundle amount flows through taxes, discounts and grand total exactly as on the
// server (see overrides/sales_invoice.py). The server recalculates on every save and is
// the source of truth.

(function () {
	const proto = erpnext.taxes_and_totals && erpnext.taxes_and_totals.prototype;
	if (!proto || proto.__bundle_weight_patched) return;
	proto.__bundle_weight_patched = true;

	const bundle_weight = (item) =>
		cint(item.custom_is_bundle) && flt(item.custom_bundle_weight_kg) > 0
			? flt(item.custom_bundle_weight_kg)
			: 0;

	const orig_calculate_item_values = proto.calculate_item_values;
	proto.calculate_item_values = function () {
		const already_applied = this.discount_amount_applied;
		orig_calculate_item_values.apply(this, arguments);
		if (already_applied || this.frm.doc.doctype !== "Sales Invoice") return;

		for (const item of this.frm.doc.items || []) {
			const weight = bundle_weight(item);
			if (!weight) continue;
			// the original just reset amount = rate * qty, so this never compounds
			item.net_amount = item.amount = flt(item.amount * weight, precision("amount", item));
			this.set_in_company_currency(item, ["amount", "net_amount"]);
		}
	};

	const orig_calc = proto._calculate_taxes_and_totals;
	proto._calculate_taxes_and_totals = function () {
		orig_calc.apply(this, arguments);
		if (this.frm.doc.doctype !== "Sales Invoice") return;
		// keep net_rate per KG (discount distribution sets it to net_amount / qty)
		for (const item of this.frm.doc.items || []) {
			const weight = bundle_weight(item);
			if (!weight || !flt(item.qty)) continue;
			item.net_rate = flt(item.net_amount / (item.qty * weight), precision("net_rate", item));
			this.set_in_company_currency(item, ["net_rate"]);
		}
	};
})();

frappe.ui.form.on("Sales Invoice Item", {
	item_code(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.item_code) {
			frappe.model.set_value(cdt, cdn, { custom_is_bundle: 0, custom_bundle_weight_kg: 0 });
			return;
		}
		// Re-read the Item master every time the item is (re)selected.
		frappe.db
			.get_value("Item", row.item_code, ["custom_is_bundle", "custom_bundle_weight_kg"])
			.then(({ message }) => {
				const is_bundle = cint(message && message.custom_is_bundle);
				const weight = flt(message && message.custom_bundle_weight_kg);
				if (is_bundle && weight <= 0) {
					frappe.msgprint({
						title: __("Invalid Bundle Weight"),
						indicator: "red",
						message: __(
							"Item {0} is marked as a Bundle item but its Bundle Weight (KG) is missing, zero or negative. Set it in the Item master.",
							[row.item_code.bold()]
						),
					});
				}
				// set_value on child rows without triggering a full pricing refetch
				row.custom_is_bundle = is_bundle;
				row.custom_bundle_weight_kg = is_bundle ? weight : 0;
				frm.refresh_field("items");
				frm.cscript.calculate_taxes_and_totals();
			});
	},

	// rate / qty / discount edits already call calculate_taxes_and_totals() in ERPNext,
	// which now runs through the wrapped calculators above.
});
