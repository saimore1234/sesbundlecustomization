import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def after_install():
	setup_custom_fields()


def after_migrate():
	setup_custom_fields()


def setup_custom_fields():
	"""Idempotent. Item fields are only created when missing (never duplicated)."""
	fields = {}

	item_fields = []
	if not frappe.db.exists("Custom Field", {"dt": "Item", "fieldname": "custom_is_bundle"}) and not (
		frappe.get_meta("Item").has_field("custom_is_bundle")
	):
		item_fields.append(
			dict(
				fieldname="custom_is_bundle",
				label="Is Bundle",
				fieldtype="Check",
				insert_after="is_stock_item",
				description="Sales Invoice amount = Qty x Rate x Bundle Weight (KG)",
			)
		)
	if not frappe.get_meta("Item").has_field("custom_bundle_weight_kg"):
		item_fields.append(
			dict(
				fieldname="custom_bundle_weight_kg",
				label="Bundle Weight (KG)",
				fieldtype="Float",
				insert_after="custom_is_bundle",
				depends_on="custom_is_bundle",
				mandatory_depends_on="custom_is_bundle",
			)
		)
	if item_fields:
		fields["Item"] = item_fields

	# Row-level snapshot (read-only) used by the calculator and the client script.
	fields["Sales Invoice Item"] = [
		dict(
			fieldname="custom_is_bundle",
			label="Is Bundle",
			fieldtype="Check",
			insert_after="item_name",
			read_only=1,
			no_copy=0,
			print_hide=1,
			hidden=1,
		),
		dict(
			fieldname="custom_bundle_weight_kg",
			label="Bundle Weight (KG)",
			fieldtype="Float",
			insert_after="custom_is_bundle",
			read_only=1,
			print_hide=1,
			depends_on="custom_is_bundle",
			in_list_view=0,
		),
	]

	create_custom_fields(fields, update=False)
	frappe.clear_cache(doctype="Item")
	frappe.clear_cache(doctype="Sales Invoice Item")
