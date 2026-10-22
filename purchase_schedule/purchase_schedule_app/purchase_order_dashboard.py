# Copyright (c) 2026, yash joshi and contributors
# For license information, please see license.txt

from frappe import _


def get_data(data):
	"""Add a "Schedule" group (Purchase Receipt Schedule) to the Purchase Order connections tab."""
	data.setdefault("transactions", []).append(
		{"label": _("Schedule"), "items": ["Purchase Receipt Schedule"]}
	)
	return data
