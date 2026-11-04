# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_time


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
		self.validate_hours()
		self.ensure_form()
		hosts = [h.user for h in self.hosts]
		if len(hosts) != len(set(hosts)):
			frappe.throw(_("A host is listed more than once"))

	def validate_hours(self):
		by_day = {}
		for row in self.availability:
			start, end = get_time(row.start_time), get_time(row.end_time)
			if start >= end:
				frappe.throw(_("{0}: end time must be after the start time").format(_(row.workday)))
			by_day.setdefault(row.workday, []).append((start, end))
		for day, ranges in by_day.items():
			ranges.sort()
			for (_start, prev_end), (next_start, _end) in zip(ranges, ranges[1:]):
				if next_start < prev_end:
					frappe.throw(_("{0}: hours overlap").format(_(day)))

	def ensure_form(self):
		"""Each page owns a CRM web form for its document type, created with the page.
		Bookings need an email, so the form always asks for one."""
		from crm.api.form import _save_form

		if not self.web_form:
			# Web Forms are named after their title, so make it unique; it's never shown
			key = frappe.generate_hash(length=10).lower()
			self.web_form = _save_form(
				None,
				{
					"title": f"{self.title} (booking {key})",
					"route": f"crm-booking-{key}",
					"document_type": self.document_type or "CRM Lead",
					"published": 0,
				},
			)["name"]
		form = frappe.get_doc("Web Form", self.web_form)
		if form.doc_type != self.document_type:
			frappe.throw(_("This page's form creates a {0}").format(_(form.doc_type)))
		email = next((f for f in form.web_form_fields if f.fieldname == "email"), None)
		if not email:
			frappe.throw(_("The form needs an Email field, so people can be sent their booking"))
		if not email.reqd:
			email.db_set("reqd", 1)

	def after_delete(self):
		# the page's own form goes with it (bookings block deleting a page that has any)
		if self.web_form and frappe.db.exists("Web Form", self.web_form):
			frappe.delete_doc("Web Form", self.web_form, ignore_permissions=True, force=True)
