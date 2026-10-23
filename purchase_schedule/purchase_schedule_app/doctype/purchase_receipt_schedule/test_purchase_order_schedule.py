# Copyright (c) 2026, yash joshi and Contributors
# See license.txt

import json

import frappe
from erpnext.controllers.accounts_controller import update_child_qty_rate
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, nowdate

from purchase_schedule.purchase_schedule_app.purchase_order_schedule import (
	SCHEDULE_DOCTYPE,
	create_purchase_schedule,
	get_purchase_schedule,
)


class TestPurchaseOrderSchedule(IntegrationTestCase):
	"""Purchase Order schedule: create/update/validate + PO lifecycle cleanup.

	Master data defaults to ERPNext's standard test records; override the class
	attributes to run against other data.
	"""

	COMPANY = "_Test Company"
	SUPPLIER = "_Test Supplier"
	ITEM = "_Test Item"
	WAREHOUSE = "_Test Warehouse - _TC"

	def make_po(self, qty=10, items=1, submit=True):
		po = frappe.new_doc("Purchase Order")
		po.company = self.COMPANY
		po.supplier = self.SUPPLIER
		po.transaction_date = nowdate()
		po.schedule_date = add_days(nowdate(), 1)
		for _ in range(items):
			po.append(
				"items",
				{
					"item_code": self.ITEM,
					"warehouse": self.WAREHOUSE,
					"qty": qty,
					"rate": 100,
					"schedule_date": add_days(nowdate(), 1),
				},
			)
		po.set_missing_values()
		po.insert()
		if submit:
			po.submit()
		return po

	def schedule(self, po, item, rows):
		create_purchase_schedule(po.name, item.name, rows)
		po.reload()
		return get_purchase_schedule(po.name, item.name)

	def count_rows(self, po):
		return frappe.db.count(SCHEDULE_DOCTYPE, {"purchase_order": po.name})

	def test_create_schedule_sets_earliest_date_and_stock_qty(self):
		po = self.make_po(qty=10)
		item = po.items[0]
		rows = self.schedule(
			po,
			item,
			[
				{"schedule_date": add_days(nowdate(), 20), "qty": 4},
				{"schedule_date": add_days(nowdate(), 5), "qty": 3},
				{"schedule_date": add_days(nowdate(), 10), "qty": 3},
			],
		)

		self.assertEqual(len(rows), 3)
		self.assertEqual([getdate(r.schedule_date) for r in rows], sorted(getdate(r.schedule_date) for r in rows))
		self.assertEqual(getdate(po.items[0].schedule_date), getdate(add_days(nowdate(), 5)))

		saved = frappe.get_doc(SCHEDULE_DOCTYPE, rows[0].name)
		self.assertEqual(saved.item_code, item.item_code)
		self.assertEqual(saved.stock_qty, saved.qty * (item.conversion_factor or 1))

	def test_resave_keeps_sent_rows_and_deletes_the_rest(self):
		po = self.make_po(qty=10)
		item = po.items[0]
		first = self.schedule(
			po,
			item,
			[
				{"schedule_date": add_days(nowdate(), 5), "qty": 5},
				{"schedule_date": add_days(nowdate(), 10), "qty": 5},
			],
		)
		kept = first[0].name

		second = self.schedule(
			po,
			po.items[0],
			[
				{"schedule_date": add_days(nowdate(), 6), "qty": 6, "name": kept},
				{"schedule_date": add_days(nowdate(), 12), "qty": 2},
			],
		)

		self.assertEqual(len(second), 2)
		self.assertIn(kept, [r.name for r in second])
		self.assertFalse(frappe.db.exists(SCHEDULE_DOCTYPE, first[1].name))

	def test_invalid_rows_are_rejected(self):
		po = self.make_po(qty=10)
		item = po.items[0]
		cases = {
			"over item qty": [{"schedule_date": add_days(nowdate(), 5), "qty": 11}],
			"zero qty": [{"schedule_date": add_days(nowdate(), 5), "qty": 0}],
			"missing date": [{"qty": 1}],
			"before transaction date": [{"schedule_date": add_days(nowdate(), -1), "qty": 1}],
			"empty": [],
		}
		for label, rows in cases.items():
			with self.subTest(label), self.assertRaises(frappe.ValidationError):
				create_purchase_schedule(po.name, item.name, rows)

		self.assertEqual(self.count_rows(po), 0)

	def test_cannot_reuse_row_of_another_item(self):
		po = self.make_po(qty=10, items=2)
		other = self.schedule(po, po.items[1], [{"schedule_date": add_days(nowdate(), 5), "qty": 2}])

		with self.assertRaises(frappe.ValidationError):
			create_purchase_schedule(
				po.name,
				po.items[0].name,
				[{"schedule_date": add_days(nowdate(), 5), "qty": 2, "name": other[0].name}],
			)

	def test_item_not_in_po_is_rejected(self):
		po = self.make_po()
		with self.assertRaises(frappe.ValidationError):
			create_purchase_schedule(po.name, "not-a-row", [{"schedule_date": nowdate(), "qty": 1}])

	def update_items(self, po, rows):
		"""Run ERPNext's "Update Items" on a submitted PO."""
		update_child_qty_rate("Purchase Order", json.dumps(rows), po.name)
		po.reload()

	def item_row(self, item, qty=None):
		return {
			"docname": item.name,
			"item_code": item.item_code,
			"qty": qty if qty is not None else item.qty,
			"rate": item.rate,
			"conversion_factor": item.conversion_factor,
			"schedule_date": str(item.schedule_date),
		}

	def test_draft_po_is_rejected(self):
		po = self.make_po(qty=10, submit=False)
		with self.assertRaises(frappe.ValidationError):
			create_purchase_schedule(
				po.name, po.items[0].name, [{"schedule_date": add_days(nowdate(), 5), "qty": 1}]
			)
		self.assertEqual(self.count_rows(po), 0)

	def test_update_items_cannot_reduce_qty_below_schedule(self):
		po = self.make_po(qty=10)
		self.schedule(po, po.items[0], [{"schedule_date": add_days(nowdate(), 5), "qty": 8}])

		with self.assertRaises(frappe.ValidationError):
			self.update_items(po, [self.item_row(po.items[0], qty=5)])

	def test_update_items_removing_item_drops_its_schedule(self):
		po = self.make_po(qty=10, items=2)
		self.schedule(po, po.items[0], [{"schedule_date": add_days(nowdate(), 5), "qty": 4}])
		self.schedule(po, po.items[1], [{"schedule_date": add_days(nowdate(), 5), "qty": 4}])
		removed = po.items[1].name

		self.update_items(po, [self.item_row(po.items[0])])

		self.assertEqual(self.count_rows(po), 1)
		self.assertFalse(
			frappe.db.exists(SCHEDULE_DOCTYPE, {"purchase_order": po.name, "purchase_order_item": removed})
		)

	def test_cancel_deletes_all_rows(self):
		po = self.make_po(qty=10)
		self.schedule(po, po.items[0], [{"schedule_date": add_days(nowdate(), 5), "qty": 4}])
		po.cancel()

		self.assertEqual(self.count_rows(po), 0)

	def test_requires_write_permission_on_po(self):
		po = self.make_po(qty=10)
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				create_purchase_schedule(
					po.name, po.items[0].name, [{"schedule_date": add_days(nowdate(), 5), "qty": 1}]
				)
			with self.assertRaises(frappe.PermissionError):
				get_purchase_schedule(po.name, po.items[0].name)
		finally:
			frappe.set_user("Administrator")
