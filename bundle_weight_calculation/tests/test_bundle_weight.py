import frappe
from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

CUSTOMER_NAME = "_BW Customer"
COMPANY_KW = dict(
	company="_Test Company",
	customer=None,  # resolved in setUpClass
	warehouse="_Test Warehouse - _TC",
	debit_to="Debtors - _TC",
	income_account="Sales - _TC",
	expense_account="Cost of Goods Sold - _TC",
	cost_center="Main - _TC",
)


def make_item(code, is_bundle=0, weight=0, is_stock=0):
	if frappe.db.exists("Item", code):
		item = frappe.get_doc("Item", code)
	else:
		item = frappe.new_doc("Item")
		item.item_code = code
		item.item_name = code
		item.item_group = "All Item Groups"
		item.stock_uom = "Nos"
	# India Compliance requires an HSN code on sales items
	if frappe.db.exists("DocType", "GST HSN Code"):
		if not frappe.db.exists("GST HSN Code", "99999999"):
			frappe.get_doc({"doctype": "GST HSN Code", "hsn_code": "99999999"}).insert(ignore_if_duplicate=True)
		item.gst_hsn_code = "99999999"
	item.is_stock_item = is_stock
	item.custom_is_bundle = is_bundle
	item.custom_bundle_weight_kg = weight
	item.flags.ignore_mandatory = True
	item.save()
	return item


def make_invoice(item_code, qty, rate, save=True, **kw):
	args = dict(COMPANY_KW, item_code=item_code, qty=qty, rate=rate, do_not_save=1, **kw)
	si = create_sales_invoice(**args)
	if save:
		si.save()
	return si


class TestBundleWeightCalculation(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		existing = frappe.db.get_value("Customer", {"customer_name": CUSTOMER_NAME})
		if existing:
			COMPANY_KW["customer"] = existing
		else:
			doc = frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": CUSTOMER_NAME,
					"customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}, "name"),
					"territory": frappe.db.get_value("Territory", {"is_group": 0}, "name"),
				}
			).insert()
			COMPANY_KW["customer"] = doc.name
		make_item("_BW Normal", 0, 0)
		make_item("_BW Steel 25", 1, 25)
		make_item("_BW Steel 10", 1, 10)

	# ---- amounts -------------------------------------------------------------------
	def test_amount_matrix(self):
		cases = [
			("_BW Normal", 1, 100, 100),
			("_BW Steel 25", 1, 50, 1250),
			("_BW Steel 25", 2, 50, 2500),
			("_BW Steel 10", 3, 100, 3000),
			("_BW Normal", 2, 100, 200),
		]
		for item, qty, rate, expected in cases:
			si = make_invoice(item, qty, rate)
			row = si.items[0]
			self.assertEqual(flt(row.amount), expected, f"{item} q{qty} r{rate}")
			self.assertEqual(flt(row.net_amount), expected)
			self.assertEqual(flt(row.base_amount), expected)
			self.assertEqual(flt(row.qty), qty)  # qty is never overwritten
			self.assertEqual(flt(row.rate), rate)
			self.assertEqual(flt(si.net_total), expected)
			self.assertEqual(flt(si.grand_total), expected)

	def test_recalculation_is_idempotent(self):
		si = make_invoice("_BW Steel 25", 2, 50)
		for _ in range(3):
			si.calculate_taxes_and_totals()
		self.assertEqual(flt(si.items[0].amount), 2500)
		si.save()
		si.reload()
		si.save()
		self.assertEqual(flt(si.items[0].amount), 2500)
		self.assertEqual(flt(si.grand_total), 2500)

	def test_rate_and_qty_change(self):
		si = make_invoice("_BW Steel 25", 1, 50)
		si.items[0].rate = 60
		si.save()
		self.assertEqual(flt(si.items[0].amount), 1500)
		si.items[0].qty = 4
		si.save()
		self.assertEqual(flt(si.items[0].amount), 6000)

	def test_item_change_and_master_weight_change(self):
		si = make_invoice("_BW Steel 25", 1, 50)
		si.items[0].item_code = "_BW Normal"
		si.save()
		self.assertEqual(flt(si.items[0].amount), 50)
		self.assertFalse(si.items[0].custom_is_bundle)
		self.assertEqual(flt(si.items[0].custom_bundle_weight_kg), 0)

		item = make_item("_BW Steel 25", 1, 20)
		try:
			si.items[0].item_code = "_BW Steel 25"
			si.save()
			self.assertEqual(flt(si.items[0].amount), 1000)
			self.assertEqual(flt(si.items[0].custom_bundle_weight_kg), 20)
		finally:
			make_item(item.name, 1, 25)

	def test_client_supplied_weight_is_ignored(self):
		"""API payloads cannot bypass the formula by sending their own weight/flag."""
		si = make_invoice("_BW Steel 25", 1, 50, save=False)
		si.items[0].custom_bundle_weight_kg = 1
		si.items[0].amount = 1
		si.save()
		self.assertEqual(flt(si.items[0].amount), 1250)

		si = make_invoice("_BW Normal", 1, 50, save=False)
		si.items[0].custom_is_bundle = 1
		si.items[0].custom_bundle_weight_kg = 99
		si.save()
		self.assertEqual(flt(si.items[0].amount), 50)

	# ---- mixed / multiple rows -------------------------------------------------------
	def test_mixed_rows(self):
		si = make_invoice("_BW Steel 25", 2, 50, save=False)
		si.append("items", dict(si.items[0].as_dict(), name=None, idx=None, item_code="_BW Normal", qty=3, rate=10))
		si.append("items", dict(si.items[0].as_dict(), name=None, idx=None, item_code="_BW Steel 10", qty=1, rate=5))
		si.save()
		self.assertEqual([flt(r.amount) for r in si.items], [2500, 30, 50])
		self.assertEqual(flt(si.net_total), 2580)

	# ---- discounts & taxes -----------------------------------------------------------
	def test_item_discount_percentage(self):
		si = make_invoice("_BW Steel 25", 2, 50, save=False)
		row = si.items[0]
		row.price_list_rate = 100
		row.discount_percentage = 50  # rate -> 50
		si.save()
		self.assertEqual(flt(row.rate), 50)
		self.assertEqual(flt(row.amount), 2500)

	def test_additional_discount_amount_and_tax(self):
		si = make_invoice("_BW Steel 25", 2, 50, save=False)  # 2500
		si.apply_discount_on = "Net Total"
		si.discount_amount = 500
		si.append(
			"taxes",
			{
				"charge_type": "On Net Total",
				"account_head": "_Test Account VAT - _TC",
				"cost_center": "Main - _TC",
				"description": "VAT",
				"rate": 10,
			},
		)
		si.save()
		row = si.items[0]
		self.assertEqual(flt(row.amount), 2500)
		self.assertEqual(flt(row.net_amount), 2000)
		self.assertEqual(flt(row.net_rate), 40)  # per KG, not per bundle
		self.assertEqual(flt(si.net_total), 2000)
		self.assertEqual(flt(si.total_taxes_and_charges), 200)
		self.assertEqual(flt(si.grand_total), 2200)
		# repeated saves must not drift
		si.save()
		self.assertEqual(flt(si.grand_total), 2200)

	def test_tax_without_discount(self):
		si = make_invoice("_BW Steel 25", 1, 50, save=False)
		si.append(
			"taxes",
			{
				"charge_type": "On Net Total",
				"account_head": "_Test Account VAT - _TC",
				"cost_center": "Main - _TC",
				"description": "VAT",
				"rate": 18,
			},
		)
		si.save()
		self.assertEqual(flt(si.total_taxes_and_charges), 225)
		self.assertEqual(flt(si.grand_total), 1475)

	# ---- validation -----------------------------------------------------------------
	def test_invalid_weights_are_rejected(self):
		for bad in (0, -5, None):
			item = make_item("_BW Bad", 1, 0)
			# bypass the mandatory_depends_on / validation to simulate legacy data
			frappe.db.set_value("Item", item.name, "custom_bundle_weight_kg", bad or 0)
			frappe.db.set_value("Item", item.name, "custom_bundle_weight_kg", bad if bad else 0)
			frappe.clear_document_cache("Item", item.name)
			si = make_invoice("_BW Bad", 1, 50, save=False)
			with self.assertRaises(frappe.ValidationError) as ctx:
				si.save()
			self.assertIn("Bundle Weight", str(ctx.exception))

	def test_missing_rate_gives_zero_amount(self):
		si = make_invoice("_BW Steel 25", 1, 50)
		# (ERPNext refills a zero rate from price_list_rate, so clear that too)
		si.items[0].price_list_rate = 0
		si.items[0].rate = 0
		si.save()
		self.assertEqual(flt(si.items[0].amount), 0)
		si.items[0].rate = None
		si.save()
		self.assertEqual(flt(si.items[0].amount), 0)

	# ---- lifecycle -----------------------------------------------------------------
	def test_submit_reopen_and_gl(self):
		si = make_invoice("_BW Steel 25", 2, 50)
		si.submit()
		si = frappe.get_doc("Sales Invoice", si.name)
		self.assertEqual(flt(si.items[0].amount), 2500)
		self.assertEqual(flt(si.grand_total), 2500)
		gl = frappe.db.sql(
			"select sum(debit), sum(credit) from `tabGL Entry` where voucher_no=%s and is_cancelled=0",
			si.name,
		)[0]
		self.assertEqual(flt(gl[0]), 2500)
		self.assertEqual(flt(gl[1]), 2500)
		self.assertEqual(flt(si.outstanding_amount), 2500)

		# a master change after submission does not touch the submitted invoice
		make_item("_BW Steel 25", 1, 40)
		try:
			si = frappe.get_doc("Sales Invoice", si.name)
			si.reload()
			self.assertEqual(flt(si.items[0].amount), 2500)
		finally:
			make_item("_BW Steel 25", 1, 25)

		# amendment recalculates in a new draft
		si.cancel()
		amended = frappe.copy_doc(frappe.get_doc("Sales Invoice", si.name))
		amended.docstatus = 0
		amended.amended_from = si.name
		amended.save()
		self.assertEqual(flt(amended.items[0].amount), 2500)

	def test_stock_ledger_uses_bundle_count(self):
		item = make_item("_BW Steel Stock", 1, 25, is_stock=1)
		make_stock_entry(item_code=item.name, target="_Test Warehouse - _TC", qty=10, rate=100, company="_Test Company")
		si = make_invoice(item.name, 3, 50, update_stock=1)
		si.submit()
		sle_qty = frappe.db.get_value(
			"Stock Ledger Entry", {"voucher_no": si.name, "is_cancelled": 0}, "actual_qty"
		)
		self.assertEqual(flt(sle_qty), -3)
		self.assertEqual(flt(si.items[0].amount), 3750)

	def test_before_submit_guard_detects_tampering(self):
		si = make_invoice("_BW Steel 25", 2, 50)
		frappe.db.set_value("Sales Invoice Item", si.items[0].name, "amount", 100)
		si = frappe.get_doc("Sales Invoice", si.name)
		from bundle_weight_calculation.bundle import before_submit

		with self.assertRaises(frappe.ValidationError):
			before_submit(si)

	def test_api_created_invoice(self):
		doc = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"company": "_Test Company",
				"customer": COMPANY_KW["customer"],
				"debit_to": "Debtors - _TC",
				"currency": "INR",
				"items": [
					{
						"item_code": "_BW Steel 25",
						"qty": 2,
						"rate": 50,
						"income_account": "Sales - _TC",
						"cost_center": "Main - _TC",
					}
				],
			}
		).insert()
		self.assertEqual(flt(doc.items[0].amount), 2500)
		doc.submit()
		self.assertEqual(flt(doc.grand_total), 2500)

	# ---- returns -------------------------------------------------------------------
	def test_sales_return(self):
		si = make_invoice("_BW Steel 25", 3, 50)
		si.submit()
		# master weight changes afterwards: the return must follow the original invoice
		make_item("_BW Steel 25", 1, 40)
		try:
			ret = make_sales_return(si.name)
			ret.items[0].qty = -1
			ret.save()
			self.assertEqual(flt(ret.items[0].custom_bundle_weight_kg), 25)
			self.assertEqual(flt(ret.items[0].amount), -1250)
			self.assertEqual(flt(ret.grand_total), -1250)
			ret.submit()
			self.assertEqual(flt(ret.outstanding_amount), -1250)
			debit = frappe.db.sql(
				"select sum(debit), sum(credit) from `tabGL Entry` where voucher_no=%s", ret.name
			)[0]
			self.assertEqual((flt(debit[0]), flt(debit[1])), (1250, 1250))
		finally:
			make_item("_BW Steel 25", 1, 25)

	def test_non_bundle_untouched(self):
		si = make_invoice("_BW Normal", 7, 13.5)
		self.assertEqual(flt(si.items[0].amount), flt(7 * 13.5))
		self.assertEqual(flt(si.items[0].net_rate), 13.5)
