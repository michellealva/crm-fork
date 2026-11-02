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
	"connect_email",
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

	def test_only_reads_run_without_a_card(self):
		# Flow's run_action skips the approval card for a whitelisted function marked read_only
		for name in READS:
			self.assertTrue(getattr(getattr(assistant, name), "read_only", False), name)
		for name in WRITES:
			self.assertFalse(getattr(getattr(assistant, name), "read_only", False), name)

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

	def test_task_delete_refreshes_its_timeline(self):
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": "Ira"}).insert(ignore_permissions=True)
		task = frappe.get_doc(
			{
				"doctype": "CRM Task",
				"title": "Call Ira",
				"reference_doctype": "CRM Lead",
				"reference_docname": lead.name,
			}
		).insert(ignore_permissions=True)
		with patch("crm.api.activities.frappe.publish_realtime") as publish:
			task.delete(ignore_permissions=True)
		publish.assert_any_call(
			"crm_activity_update",
			{"doctype": "CRM Lead", "name": lead.name},
			doctype="CRM Lead",
			docname=lead.name,
			after_commit=True,
		)

	def test_connect_email_waits_for_the_password(self):
		email = "sales.assistant.test@gmail.com"
		first = assistant.connect_email("GMail", email)
		self.assertEqual(first, {"email_account": email, "connected": False, "needs_password": True})
		account = frappe.get_doc("Email Account", email)
		self.assertFalse(account.enable_incoming or account.enable_outgoing)
		self.assertTrue(assistant.connect_email("GMail", email)["needs_password"])

		# what request_secret's password box does
		account.password = "app-password"
		account.save()
		self.assertEqual(assistant.connect_email("GMail", email), {"email_account": email, "connected": True})
		account.reload()
		self.assertTrue(account.enable_incoming and account.enable_outgoing and account.default_outgoing)
