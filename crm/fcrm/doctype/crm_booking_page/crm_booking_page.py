# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import frappe
from frappe import _
from frappe.model.document import Document


class CRMBookingPage(Document):
	def validate(self):
		self.route = re.sub(r"[^a-z0-9-]+", "-", (self.route or self.title).strip().lower()).strip("-")
		if not self.route:
			frappe.throw(_("Route is required"))
		if self.duration <= 0:
			frappe.throw(_("Duration must be more than zero"))
		if self.timezone:
			try:
				ZoneInfo(self.timezone)
			except (ZoneInfoNotFoundError, ValueError):
				frappe.throw(_("{0} is not a valid timezone").format(self.timezone))
		for row in self.availability:
			if row.start_time >= row.end_time:
				frappe.throw(_("Row {0}: end time must be after start time").format(row.idx))
		hosts = [h.user for h in self.hosts]
		if len(hosts) != len(set(hosts)):
			frappe.throw(_("A host is listed more than once"))
