# Bundle Weight Calculation

ERPNext / Frappe v15 app. For Sales Invoice items whose Item master has **Is Bundle** (`custom_is_bundle`) checked:

    Amount = Qty x Rate x Bundle Weight (KG)

`Qty` stays the number of bundles, `Rate` is the price per KG. Qty 2, Rate 50, Weight 25 gives 2,500.
Non-bundle items are untouched.

Verified against ERPNext 15.108.3 / Frappe 15.108.0.

## How it works (and why)

ERPNext computes totals in `erpnext/controllers/taxes_and_totals.py::calculate_taxes_and_totals`
(`calculate_item_values` sets `amount = rate * qty`, then taxes, discount distribution, rounding,
advances and outstanding all derive from `net_amount`). `AccountsController.calculate_taxes_and_totals`
instantiates that class for every save and for return mapping.

| Piece | File | Role |
|---|---|---|
| `override_doctype_class` Sales Invoice | `overrides/sales_invoice.py` | `BundleSalesInvoice.calculate_taxes_and_totals` resolves bundle data, then runs `BundleTaxesAndTotals`, a subclass of the stock calculator. No core file is changed. |
| `BundleTaxesAndTotals.calculate_item_values` | same | Calls the parent (amount = rate x qty, from scratch), then multiplies by weight. Skipped when the parent skips (`discount_amount_applied`), so a value is never multiplied twice. |
| `fix_bundle_net_rate` | same | The parent sets `net_rate = net_amount / qty` after a discount; we keep it per KG. |
| `before_validate` doc_event | `bundle.py` | Refreshes the row snapshot from the Item master and validates the weight (missing, zero or negative raises a clear error with row and item). Client or API supplied weight/flag values are ignored. |
| `before_submit` doc_event | `bundle.py` | Guard: blocks submission if a bundle row's amount no longer equals Qty x Rate x Weight (for example a direct DB edit). |
| `public/js/sales_invoice.js` | | Wraps ERPNext's browser calculator (`erpnext.taxes_and_totals.prototype`) so the form shows the same numbers as the server, and re-reads the Item on every item selection. |
| `install.py` | | Creates the Item fields only if missing, plus two read-only snapshot fields on **Sales Invoice Item**. |

Because the bundle amount is the item's `amount`/`net_amount`, taxes, additional discounts, GL entries
(`base_net_amount`), outstanding and e-invoice values all follow it. Stock ledger uses Qty (bundles) x
stock UOM conversion, unchanged.

### Behaviour notes

* **Weight source**: Item master on each save of a draft (so re-selecting an item or editing a draft picks up a changed weight). Submitted invoices are never recalculated, so later master changes do not alter them.
* **Returns / Credit Notes**: rows linked to the original invoice row (`sales_invoice_item`) reuse the weight the original was billed with, even if the master weight changed since.
* **Missing rate**: treated as 0 (amount 0), as in standard ERPNext. Note ERPNext refills a 0 rate from `price_list_rate` when one exists.
* **Item-level discount** applies to the rate (per KG); **additional discount** is distributed on the bundle amounts.
* **Amendments**: a new draft is recalculated like any draft.
* Scope is Sales Invoice only. Sales Order / Delivery Note / POS Invoice are not changed; billing percentage on a Sales Order fed by a bundle invoice will therefore not match by amount.
* `Amount != Qty x Rate` for bundle rows by design; print formats that show Qty, Rate and Amount may want a Weight column (`custom_bundle_weight_kg` on the item row).
* The two Item fields were not present on `dev.local` when this app was installed, so `install.py` created them (`Is Bundle`, `Bundle Weight (KG)`). If they already exist on another site they are left as is.

## Installation (site `dev.local`)

```bash
cd ~/frappe-bench
bench get-app /home/sai/frappe-bench/apps/bundle_weight_calculation   # only needed on another bench
# (already created here with: bench new-app bundle_weight_calculation)
bench --site dev.local install-app bundle_weight_calculation
bench build --app bundle_weight_calculation
bench --site dev.local migrate
bench restart            # or: sudo supervisorctl restart all   (in dev: stop/start `bench start`)
```

### Verify hooks and scripts are loaded

```bash
bench --site dev.local console
>>> import frappe
>>> frappe.get_hooks("override_doctype_class")["Sales Invoice"]
['bundle_weight_calculation.overrides.sales_invoice.BundleSalesInvoice']
>>> frappe.get_hooks("doc_events")["Sales Invoice"]["before_submit"]   # contains bundle_weight_calculation.bundle.before_submit
>>> frappe.get_hooks("doctype_js")["Sales Invoice"]                      # contains public/js/sales_invoice.js
>>> from frappe.model.base_document import get_controller
>>> get_controller("Sales Invoice")
<class 'bundle_weight_calculation.overrides.sales_invoice.BundleSalesInvoice'>
```

In the browser: hard-reload (Ctrl+Shift+R), open a Sales Invoice, and in the console check
`erpnext.taxes_and_totals.prototype.__bundle_weight_patched === true`.

## Tests

```bash
bench --site dev.local set-config allow_tests true
bench --site dev.local run-tests --app bundle_weight_calculation \
      --module bundle_weight_calculation.tests.test_bundle_weight
bench --site dev.local set-config allow_tests false
```

The tests run against `_Test Company` and roll back. They cover: the 5-row amount matrix, idempotent
recalculation, rate/qty/item changes, master weight change on re-selection, ignored client-supplied
values, mixed rows, item discount %, additional discount + tax (net rate stays per KG), invalid
(0 / negative) weights, missing rate, save/submit/reopen/GL balance, post-submit master change,
amendment, stock ledger quantity, tamper guard, API-created invoice and Sales Return.

## Manual UI check

1. Item master: tick **Is Bundle**, set **Bundle Weight (KG)** = 25.
2. New Sales Invoice, add the item, Qty 1, Rate 50 -> Amount 1,250. Qty 2 -> 2,500. Rate 60 -> 3,000.
3. Add a normal item next to it: Amount = Qty x Rate. Save, submit, reopen: amounts unchanged.
