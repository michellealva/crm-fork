from datetime import UTC, datetime, timedelta

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.api.booking import book, cancel_booking, get_slots, reschedule_booking, to_iso, to_system

HOSTS = ["booking-host-a@example.com", "booking-host-b@example.com"]


def next_monday() -> datetime:
	"""A Monday at least two days out, as UTC midnight."""
	today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
	day = today + timedelta(days=2)
	return day + timedelta(days=(7 - day.weekday()) % 7)


class TestBooking(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		if not frappe.db.exists("Email Account", {"default_outgoing": 1}):
			frappe.get_doc(
				{
					"doctype": "Email Account",
					"email_account_name": "_Test Booking",
					"email_id": "booking@example.com",
					"enable_outgoing": 1,
					"default_outgoing": 1,
					"no_smtp_authentication": 1,
					"smtp_server": "localhost",
				}
			).insert(ignore_permissions=True)
		for email in HOSTS:
			if not frappe.db.exists("User", email):
				frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": email.split("@")[0].title(),
						"roles": [{"role": "Sales User"}],
					}
				).insert(ignore_permissions=True)
		self.monday = next_monday()
		self.page = self.make_page([HOSTS[0]])

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def make_page(self, hosts, **kw):
		doc = {
			"doctype": "CRM Booking Page",
			"title": "Intro call",
			"route": frappe.generate_hash(length=8).lower(),
			"duration": 30,
			"timezone": "UTC",
			"min_notice": 0,
			"max_days_ahead": 30,
			"location_type": "Phone call",
			"hosts": [{"user": h} for h in hosts],
			"availability": [
				{"workday": d, "start_time": "09:00:00", "end_time": "12:00:00"}
				for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
			],
		}
		doc.update(kw)
		return frappe.get_doc(doc).insert(ignore_permissions=True)

	def at(self, hour, minute=0, days=0):
		return self.monday + timedelta(days=days, hours=hour, minutes=minute)

	def slots_on_monday(self, page=None, token=None):
		frappe.set_user("Guest")
		try:
			return get_slots((page or self.page).route, to_iso(self.monday), to_iso(self.at(24)), token)
		finally:
			frappe.set_user("Administrator")

	def book_as_guest(self, start, email="visitor@example.com", page=None):
		frappe.set_user("Guest")
		try:
			return book((page or self.page).route, to_iso(start), "Vera Visitor", email, timezone="Europe/Paris")
		finally:
			frappe.set_user("Administrator")

	def add_event(self, owner, start, end, **kw):
		event = frappe.get_doc(
			{
				"doctype": "Event",
				"subject": "Busy",
				"event_type": "Private",
				"starts_on": to_system(start),
				"ends_on": to_system(end),
				**kw,
			}
		).insert(ignore_permissions=True)
		event.db_set("owner", owner)
		return event

	def test_slots_follow_weekly_hours(self):
		slots = self.slots_on_monday()
		self.assertEqual(
			slots, [to_iso(self.at(9, m)) for m in (0, 30, 60, 90, 120, 150)]
		)

	def test_events_and_buffer_block_slots(self):
		self.add_event(HOSTS[0], self.at(10), self.at(10, 30))
		self.assertNotIn(to_iso(self.at(10)), self.slots_on_monday())

		self.page.buffer = 15
		self.page.save()
		slots = self.slots_on_monday()
		for blocked in (self.at(9, 30), self.at(10), self.at(10, 30)):
			self.assertNotIn(to_iso(blocked), slots)
		self.assertIn(to_iso(self.at(9)), slots)

	def test_weekly_repeating_event_blocks_slots(self):
		week_before = self.at(11, days=-7)
		self.add_event(
			HOSTS[0],
			week_before,
			week_before + timedelta(minutes=30),
			repeat_this_event=1,
			repeat_on="Weekly",
			monday=1,
		)
		self.assertNotIn(to_iso(self.at(11)), self.slots_on_monday())

	def test_holidays_close_the_day(self):
		holiday_list = frappe.get_doc(
			{
				"doctype": "CRM Holiday List",
				"holiday_list_name": frappe.generate_hash(length=8),
				"from_date": self.monday.date() - timedelta(days=1),
				"to_date": self.monday.date() + timedelta(days=1),
				"holidays": [{"date": self.monday.date(), "description": "Off"}],
			}
		).insert(ignore_permissions=True)
		self.page.holiday_list = holiday_list.name
		self.page.save()
		self.assertEqual(self.slots_on_monday(), [])

	def test_booking_creates_event_and_lead_for_host(self):
		result = self.book_as_guest(self.at(9))
		booking = frappe.get_doc("CRM Booking", {"token": result["token"]})
		self.assertEqual(booking.host, HOSTS[0])
		self.assertEqual(booking.reference_doctype, "CRM Lead")

		lead = frappe.get_doc("CRM Lead", booking.reference_docname)
		self.assertEqual(lead.email, "visitor@example.com")
		self.assertEqual(lead.lead_owner, HOSTS[0])

		event = frappe.get_doc("Event", booking.event)
		self.assertEqual(event.owner, HOSTS[0])
		self.assertEqual(event.reference_docname, lead.name)
		self.assertEqual(event.event_participants[0].email, "visitor@example.com")
		self.assertEqual(frappe.utils.get_datetime(event.starts_on), to_system(self.at(9)))

		self.assertNotIn(to_iso(self.at(9)), self.slots_on_monday())

	def test_existing_lead_is_reused(self):
		lead = frappe.get_doc(
			{"doctype": "CRM Lead", "first_name": "Known", "email": "known@example.com"}
		).insert(ignore_permissions=True)
		result = self.book_as_guest(self.at(9), email="Known@Example.com")
		self.assertEqual(frappe.db.get_value("CRM Booking", {"token": result["token"]}, "reference_docname"), lead.name)

	def test_round_robin_and_taken_slot(self):
		page = self.make_page(HOSTS)
		first = self.book_as_guest(self.at(9), "one@example.com", page)
		second = self.book_as_guest(self.at(9), "two@example.com", page)
		hosts = {
			frappe.db.get_value("CRM Booking", {"token": r["token"]}, "host") for r in (first, second)
		}
		self.assertEqual(hosts, set(HOSTS))
		self.assertNotIn(to_iso(self.at(9)), self.slots_on_monday(page))
		with self.assertRaises(frappe.ValidationError):
			self.book_as_guest(self.at(9), "three@example.com", page)

	def test_reschedule_moves_event_and_frees_old_slot(self):
		result = self.book_as_guest(self.at(9))
		# the meeting's own time doesn't block an overlapping new time
		self.assertIn(to_iso(self.at(9)), self.slots_on_monday(token=result["token"]))

		frappe.set_user("Guest")
		reschedule_booking(result["token"], to_iso(self.at(11)))
		frappe.set_user("Administrator")

		booking = frappe.get_doc("CRM Booking", {"token": result["token"]})
		self.assertEqual(frappe.db.get_value("Event", booking.event, "starts_on"), to_system(self.at(11)))
		slots = self.slots_on_monday()
		self.assertIn(to_iso(self.at(9)), slots)
		self.assertNotIn(to_iso(self.at(11)), slots)

	def test_cancel_frees_the_slot(self):
		result = self.book_as_guest(self.at(9))
		frappe.set_user("Guest")
		cancel_booking(result["token"], "Something came up")
		frappe.set_user("Administrator")

		booking = frappe.get_doc("CRM Booking", {"token": result["token"]})
		self.assertEqual(booking.status, "Cancelled")
		self.assertEqual(frappe.db.get_value("Event", booking.event, "status"), "Cancelled")
		self.assertIn(to_iso(self.at(9)), self.slots_on_monday())
		with self.assertRaises(frappe.ValidationError):
			frappe.set_user("Guest")
			cancel_booking(result["token"])

	def test_disabled_page_is_hidden_from_guests(self):
		self.page.enabled = 0
		self.page.save()
		with self.assertRaises(frappe.DoesNotExistError):
			self.slots_on_monday()
