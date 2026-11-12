"""Sales Invoice controller override.

ERPNext's ``AccountsController.calculate_taxes_and_totals`` instantiates
``erpnext.controllers.taxes_and_totals.calculate_taxes_and_totals`` (verified in
``accounts_controller.py``).  We override that single method on Sales Invoice so that the
*whole* standard pipeline (discounts, taxes, rounding, advances, outstanding, GL, ...) runs
on bundle-aware item amounts rather than patching values afterwards.
"""

from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice
from erpnext.controllers.taxes_and_totals import calculate_taxes_and_totals
from frappe.utils import flt

from bundle_weight_calculation.bundle import get_row_weight, resolve_bundle_rows


class BundleTaxesAndTotals(calculate_taxes_and_totals):
	def calculate_item_values(self):
		# `discount_amount_applied` means item amounts were already derived (and distributed
		# discounts applied) - the parent skips recomputing, so must we, otherwise we would
		# multiply an already multiplied amount.
		already_applied = self.discount_amount_applied
		super().calculate_item_values()

		if already_applied or self.doc.get("is_consolidated"):
			return

		for item in self.doc.items:
			weight = get_row_weight(item)
			if not weight:
				continue
			# parent just set amount = rate * qty (from scratch), so this is idempotent
			item.amount = flt(item.amount * weight, item.precision("amount"))
			item.net_amount = item.amount
			self._set_in_company_currency(item, ["amount", "net_amount"])

	def _calculate(self):
		super()._calculate()
		self.fix_bundle_net_rate()

	def fix_bundle_net_rate(self):
		"""Rate is per KG; keep net_rate per KG too (parent derives it as net_amount / qty)."""
		if self.doc.get("is_consolidated"):
			return
		for item in self.doc.items:
			weight = get_row_weight(item)
			if not weight or not flt(item.qty):
				continue
			item.net_rate = flt(item.net_amount / (item.qty * weight), item.precision("net_rate"))
			self._set_in_company_currency(item, ["net_rate"])


class BundleSalesInvoice(SalesInvoice):
	def calculate_taxes_and_totals(self):
		resolve_bundle_rows(self)
		BundleTaxesAndTotals(self)
