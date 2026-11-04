# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from crm.permissions.org_hierarchy import (
	get_deal_permission_query_conditions,
	get_lead_permission_query_conditions,
)


class CRMBooking(Document):
	def validate(self):
		if self.is_new() or self.has_value_changed("starts_on"):
			from crm.api.booking import reminder_already_due

			# a new time gets its own reminder, unless it's already too close
			self.reminder_sent = int(reminder_already_due(self))


def sees_all_bookings(user: str) -> bool:
	return user == "Administrator" or bool({"System Manager", "Sales Manager"} & set(frappe.get_roles(user)))


def get_permission_query_conditions(user=None):
	"""A Sales User sees the bookings they host, and those on leads and deals they can see."""
	user = user or frappe.session.user
	if sees_all_bookings(user):
		return ""
	lead = get_lead_permission_query_conditions(user) or "1=1"
	deal = get_deal_permission_query_conditions(user) or "1=1"
	return f"""(`tabCRM Booking`.host = {frappe.db.escape(user)}
		or (`tabCRM Booking`.reference_doctype = 'CRM Lead' and `tabCRM Booking`.reference_docname in
			(select `tabCRM Lead`.name from `tabCRM Lead` where {lead}))
		or (`tabCRM Booking`.reference_doctype = 'CRM Deal' and `tabCRM Booking`.reference_docname in
			(select `tabCRM Deal`.name from `tabCRM Deal` where {deal})))"""


def has_permission(doc, ptype, user=None):
	user = user or frappe.session.user
	if sees_all_bookings(user) or doc.host == user:
		return True
	if ptype != "read" or not doc.reference_docname:
		return False
	return frappe.has_permission(doc.reference_doctype, "read", doc.reference_docname, user=user)
