from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from crm.api import assistant

READS = ("setup_status", "my_day", "sales_health", "find_records")
WRITES = (
	"publish_web_form",
	"add_fields",
	"show_fields",
	"assign_new_leads",
	"set_currency",
	"add_automation",
	"send_email",
)


class TestAssistantFunctions(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_skills_can_call_every_function(self):
		for name in READS + WRITES:
			self.assertIn(getattr(assistant, name), frappe.whitelisted, name)

	def test_reads_go_through_frappe_call(self):
		lead = frappe.get_doc(
			{"doctype": "CRM Lead", "first_name": "Nina", "organization": "Corvid Labs Test"}
		).insert(ignore_permissions=True)
		found = frappe.call("crm.api.assistant.find_records", text="Corvid Labs Test")
		self.assertEqual([r["record"] for r in found], [lead.name])
		self.assertIn("tasks", frappe.call("crm.api.assistant.my_day"))
		self.assertIn("open_pipeline", frappe.call("crm.api.assistant.sales_health"))
		self.assertIn("deal_stages", frappe.call("crm.api.assistant.setup_status"))

	def test_note_change_refreshes_its_timeline(self):
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": "Kabir"}).insert(ignore_permissions=True)
		with patch("crm.api.activities.frappe.publish_realtime") as publish:
			frappe.get_doc(
				{
					"doctype": "FCRM Note",
					"title": "Call with Kabir",
					"reference_doctype": "CRM Lead",
					"reference_docname": lead.name,
				}
			).insert(ignore_permissions=True)
		publish.assert_any_call(
			"crm_activity_update",
			{"doctype": "CRM Lead", "name": lead.name},
			doctype="CRM Lead",
			docname=lead.name,
			after_commit=True,
		)
