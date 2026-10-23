# Copyright (c) 2026, yash joshi and contributors
# For license information, please see license.txt

"""Purchase Order schedule: split a Purchase Order Item into dated receipts.

Mirrors ERPNext's Sales Order "Delivery Schedule" (Delivery Schedule Item), but for
Purchase Order, implemented through hooks (no core override):

- get_purchase_schedule / create_purchase_schedule: whitelisted, used by the Add Schedule dialog
  on a saved draft or submitted Purchase Order.
- validate_scheduled_qty: Purchase Order `validate` / `before_update_after_submit` (Update Items)
  — scheduled qty must not exceed item qty.
- delete_removed_schedule_items: Purchase Order `on_submit` / `on_update_after_submit`
  — drop rows of removed items.
- delete_all_schedule_items: Purchase Order `on_cancel` / `on_trash`.
"""

import frappe
from frappe import _
from frappe.query_builder.functions import Sum
from frappe.utils import flt, getdate, parse_json

SCHEDULE_DOCTYPE = "Purchase Receipt Schedule"


@frappe.whitelist()
def get_purchase_schedule(purchase_order: str, purchase_order_item: str) -> list[dict]:
	"""Return schedule rows of one Purchase Order Item, earliest first."""
	frappe.get_doc("Purchase Order", purchase_order).check_permission("read")

	return frappe.get_all(
		SCHEDULE_DOCTYPE,
		filters={"purchase_order": purchase_order, "purchase_order_item": purchase_order_item},
		fields=["name", "schedule_date", "qty"],
		order_by="schedule_date asc",
	)


@frappe.whitelist(methods=["POST"])
def create_purchase_schedule(purchase_order: str, purchase_order_item: str, schedules) -> None:
	"""Replace the schedule of one Purchase Order Item (draft or submitted) with `schedules`.

	`schedules` is a list of {"schedule_date", "qty", "name" (optional, existing row)}.
	Sets the item's Required By date (allow on submit) to the earliest scheduled date
	and saves the PO (a normal save for drafts, update-after-submit once submitted).
	"""
	if isinstance(schedules, str):
		schedules = parse_json(schedules)

	po = frappe.get_doc("Purchase Order", purchase_order)
	po.check_permission("write")

	if po.docstatus == 2:
		frappe.throw(_("Schedule cannot be changed on a cancelled Purchase Order."))

	item = next((d for d in po.items if d.name == purchase_order_item), None)
	if not item:
		frappe.throw(
			_("Row {0} does not belong to Purchase Order {1}.").format(purchase_order_item, po.name)
		)

	rows = _clean_schedule_rows(po, item, schedules)
	names = [_save_schedule_row(po, item, row) for row in rows]

	delete_schedule_items(po.name, purchase_order_item=item.name, ignore_names=names)

	item.schedule_date = min(row.schedule_date for row in rows)
	po.save()


def _clean_schedule_rows(po, item, schedules) -> list[frappe._dict]:
	"""Validate input rows; return them with parsed dates and rounded qty."""
	if not schedules:
		frappe.throw(_("Please enter at least one Required By date and quantity."))

	precision = item.precision("qty")
	transaction_date = getdate(po.transaction_date)
	rows, total_qty = [], 0.0

	for idx, raw in enumerate(schedules, start=1):
		row = frappe._dict(raw)

		if not row.schedule_date:
			frappe.throw(_("Schedule row {0}: Required By date is mandatory.").format(idx))

		row.schedule_date = getdate(row.schedule_date)
		if row.schedule_date < transaction_date:
			frappe.throw(
				_("Schedule row {0}: Required By date cannot be before the Transaction Date.").format(idx)
			)

		row.qty = flt(row.qty, precision)
		if row.qty <= 0:
			frappe.throw(_("Schedule row {0}: Please enter a valid quantity.").format(idx))

		total_qty += row.qty
		rows.append(row)

	if flt(total_qty, precision) > flt(item.qty, precision):
		frappe.throw(
			_("Row #{0}: Total scheduled quantity {1} cannot be greater than the item quantity {2}.").format(
				item.idx, flt(total_qty, precision), item.qty
			)
		)

	return rows


def _save_schedule_row(po, item, row) -> str:
	"""Insert or update one Purchase Receipt Schedule; return its name."""
	conversion_factor = flt(item.conversion_factor) or 1.0
	data = {
		"schedule_date": row.schedule_date,
		"qty": row.qty,
		"uom": item.uom,
		"stock_uom": item.stock_uom,
		"item_code": item.item_code,
		"conversion_factor": conversion_factor,
		"warehouse": item.warehouse,
		"purchase_order": po.name,
		"purchase_order_item": item.name,
		"stock_qty": row.qty * conversion_factor,
	}

	if row.name and frappe.db.exists(SCHEDULE_DOCTYPE, row.name):
		doc = frappe.get_doc(SCHEDULE_DOCTYPE, row.name)
		# never let a caller re-point a row that belongs to another PO item
		if (doc.purchase_order, doc.purchase_order_item) != (po.name, item.name):
			frappe.throw(_("Schedule {0} does not belong to this Purchase Order item.").format(row.name))
	else:
		doc = frappe.new_doc(SCHEDULE_DOCTYPE)

	doc.update(data)
	# access is already checked against the Purchase Order (write)
	doc.save(ignore_permissions=True)
	return doc.name


def delete_schedule_items(purchase_order: str, purchase_order_item=None, ignore_names=None) -> None:
	"""Delete schedule rows of a Purchase Order, optionally for one item / except some names."""
	doctype = frappe.qb.DocType(SCHEDULE_DOCTYPE)
	query = frappe.qb.from_(doctype).delete().where(doctype.purchase_order == purchase_order)

	if purchase_order_item:
		query = query.where(doctype.purchase_order_item == purchase_order_item)

	if ignore_names:
		query = query.where(doctype.name.notin(ignore_names))

	query.run()


def _get_scheduled_qty_map(purchase_order: str) -> dict[str, float]:
	"""{purchase_order_item: total scheduled qty} for a Purchase Order."""
	doctype = frappe.qb.DocType(SCHEDULE_DOCTYPE)
	rows = (
		frappe.qb.from_(doctype)
		.select(doctype.purchase_order_item, Sum(doctype.qty).as_("qty"))
		.where(doctype.purchase_order == purchase_order)
		.groupby(doctype.purchase_order_item)
	).run(as_dict=True)

	return {row.purchase_order_item: flt(row.qty) for row in rows}


# --- Purchase Order doc_events --------------------------------------------------------


def validate_scheduled_qty(doc, method=None) -> None:
	"""Block saving a PO whose item qty was reduced below its scheduled qty."""
	if doc.is_new():
		return

	scheduled = _get_scheduled_qty_map(doc.name)
	if not scheduled:
		return

	for item in doc.items:
		precision = item.precision("qty")
		scheduled_qty = flt(scheduled.get(item.name), precision)
		if scheduled_qty > flt(item.qty, precision):
			frappe.throw(
				_(
					"Row #{0}: Quantity {1} is less than the scheduled quantity {2}. Update the schedule first."
				).format(item.idx, item.qty, scheduled_qty)
			)


def delete_removed_schedule_items(doc, method=None) -> None:
	"""On submit, delete schedule rows whose Purchase Order Item no longer exists."""
	item_names = [d.name for d in doc.items]
	doctype = frappe.qb.DocType(SCHEDULE_DOCTYPE)
	query = frappe.qb.from_(doctype).delete().where(doctype.purchase_order == doc.name)

	if item_names:
		query = query.where(doctype.purchase_order_item.notin(item_names))

	query.run()


def delete_all_schedule_items(doc, method=None) -> None:
	"""On cancel / delete, remove every schedule row of the Purchase Order."""
	delete_schedule_items(doc.name)
