"""Bundle-item resolution and validation helpers.

Amount for a bundle item row is ``Qty x Rate x Bundle Weight (KG)``.  The Item master
is the source of truth (``custom_is_bundle`` / ``custom_bundle_weight_kg``).  The resolved
values are snapshotted on the Sales Invoice Item row (same fieldnames) so the saved
document is self-describing and the client-side calculator has them available.
"""

import frappe
from frappe import _
from frappe.utils import flt

ITEM_FLAG = "custom_is_bundle"
ITEM_WEIGHT = "custom_bundle_weight_kg"

logger = frappe.logger("bundle_weight_calculation", allow_site=True, file_count=5)


def get_item_bundle_info(item_code):
	"""Return (is_bundle, weight) from the Item master (cached; cache clears on Item save)."""
	if not item_code:
		return 0, 0.0
	values = frappe.get_cached_value("Item", item_code, [ITEM_FLAG, ITEM_WEIGHT], as_dict=True)
	if not values:
		return 0, 0.0
	return (1 if values.get(ITEM_FLAG) else 0), flt(values.get(ITEM_WEIGHT))


def _get_return_source_info(row):
	"""For return/credit-note rows, reuse the weight the original invoice was billed with."""
	source = row.get("sales_invoice_item")
	if not source:
		return None
	values = frappe.db.get_value(
		"Sales Invoice Item", source, ["item_code", ITEM_FLAG, ITEM_WEIGHT], as_dict=True
	)
	if not values or values.item_code != row.item_code or not values.get(ITEM_FLAG):
		return None
	return 1, flt(values.get(ITEM_WEIGHT))


def resolve_bundle_rows(doc):
	"""Refresh the bundle snapshot on every item row and validate it.

	Never trusts values supplied by the client / API payload: they are always overwritten
	from the Item master (or, for returns, from the original invoice row).
	"""
	for row in doc.get("items") or []:
		info = None
		if doc.get("is_return"):
			info = _get_return_source_info(row)
		if info is None:
			info = get_item_bundle_info(row.item_code)
		is_bundle, weight = info

		if is_bundle and weight <= 0:
			frappe.throw(
				_(
					"Row #{0}: Item {1} is marked as a Bundle item but its Bundle Weight (KG) is "
					"missing, zero or negative ({2}). Please set a positive Bundle Weight (KG) in the Item master."
				).format(row.idx, frappe.bold(row.item_code), flt(weight)),
				title=_("Invalid Bundle Weight"),
			)

		row.set(ITEM_FLAG, is_bundle)
		row.set(ITEM_WEIGHT, weight if is_bundle else 0)


def get_row_weight(row):
	"""Weight multiplier for a (resolved) row; 1 means "no bundle multiplication"."""
	if row.get(ITEM_FLAG) and flt(row.get(ITEM_WEIGHT)) > 0:
		return flt(row.get(ITEM_WEIGHT))
	return 0


def before_validate(doc, method=None):
	"""doc_event: resolve + validate bundle data as early as possible."""
	try:
		resolve_bundle_rows(doc)
	except frappe.ValidationError:
		raise
	except Exception:
		logger.exception(f"Bundle resolution failed for {doc.doctype} {doc.name}")
		raise


def before_submit(doc, method=None):
	"""doc_event: guard that saved amounts still follow the bundle formula.

	Catches drafts altered through direct DB writes or tooling that bypassed ``validate``.
	"""
	for row in doc.get("items") or []:
		weight = get_row_weight(row)
		if not weight:
			continue
		qty = flt(row.qty)
		if not qty and not doc.get("is_return"):
			continue
		expected = flt(row.rate * qty * weight, row.precision("amount")) if qty else None
		if expected is not None and abs(flt(row.amount) - expected) > 0.01:
			logger.error(
				f"{doc.doctype} {doc.name} row {row.idx}: amount {row.amount} != expected {expected}"
			)
			frappe.throw(
				_(
					"Row #{0}: Amount {1} does not match Qty x Rate x Bundle Weight ({2}). "
					"Please save the invoice again to recalculate."
				).format(row.idx, row.amount, expected)
			)
