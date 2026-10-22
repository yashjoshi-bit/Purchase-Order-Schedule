# Copyright (c) 2026, yash joshi and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class PurchaseReceiptSchedule(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		conversion_factor: DF.Float
		item_code: DF.Link | None
		purchase_order: DF.Link | None
		purchase_order_item: DF.Data | None
		qty: DF.Float
		schedule_date: DF.Date | None
		stock_qty: DF.Float
		stock_uom: DF.Link | None
		uom: DF.Link | None
		warehouse: DF.Link | None
	# end: auto-generated types

	pass
