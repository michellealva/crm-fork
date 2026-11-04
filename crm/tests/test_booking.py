from datetime import UTC, datetime, timedelta

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.api.booking import (
	SlotUnavailableError,
	book,
	cancel_booking,
	get_slots,
	reschedule_booking,
	to_iso,
	to_system,
)

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

	def book_as_guest(self, start, email="visitor@example.com", page=None, values=None):
		frappe.set_user("Guest")
		try:
			return book(
				(page or self.page).route,
				to_iso(start),
				values={"first_name": "Vera", "last_name": "Visitor", "email": email, **(values or {})},
				timezone="Europe/Paris",
			)
		finally:
			frappe.set_user("Administrator")

	def add_fields(self, page=None):
		"""Company (required) and Team size on the page's own form."""
		form = frappe.get_doc("Web Form", (page or self.page).web_form)
		form.append(
			"web_form_fields",
			{"fieldname": "organization", "fieldtype": "Data", "label": "Company", "reqd": 1},
		)
		form.append(
			"web_form_fields",
			{
				"fieldname": "no_of_employees",
				"fieldtype": "Select",
				"label": "Team size",
				"options": "1-10\n11-50\n51-200\n201-500\n501-1000\n1000+",
			},
		)
		form.save(ignore_permissions=True)

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
		self.assertEqual(slots, [to_iso(self.at(9, m)) for m in (0, 30, 60, 90, 120, 150)])

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
		self.assertEqual(
			frappe.db.get_value("CRM Booking", {"token": result["token"]}, "reference_docname"), lead.name
		)

	def test_round_robin_and_taken_slot(self):
		page = self.make_page(HOSTS)
		first = self.book_as_guest(self.at(9), "one@example.com", page)
		second = self.book_as_guest(self.at(9), "two@example.com", page)
		hosts = {frappe.db.get_value("CRM Booking", {"token": r["token"]}, "host") for r in (first, second)}
		self.assertEqual(hosts, set(HOSTS))
		self.assertNotIn(to_iso(self.at(9)), self.slots_on_monday(page))
		# its own error, so the page sends the visitor back to the calendar
		with self.assertRaises(SlotUnavailableError):
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

	def test_hours_must_not_overlap(self):
		self.page.append(
			"availability", {"workday": "Monday", "start_time": "11:30:00", "end_time": "14:00:00"}
		)
		with self.assertRaises(frappe.ValidationError):
			self.page.save()

	def test_hours_must_end_after_they_start(self):
		self.page.availability[0].end_time = "08:00:00"
		with self.assertRaises(frappe.ValidationError):
			self.page.save()

	def test_each_page_gets_its_own_form_with_a_required_email(self):
		form = frappe.get_doc("Web Form", self.page.web_form)
		self.assertEqual(form.doc_type, "CRM Lead")
		self.assertEqual(next(f.reqd for f in form.web_form_fields if f.fieldname == "email"), 1)

	def test_a_deal_page_creates_a_deal(self):
		page = self.make_page([HOSTS[0]], document_type="CRM Deal")
		result = self.book_as_guest(self.at(9), email="buyer@example.com", page=page)
		booking = frappe.get_doc("CRM Booking", {"token": result["token"]})
		self.assertEqual(booking.reference_doctype, "CRM Deal")
		self.assertEqual(frappe.db.get_value("CRM Deal", booking.reference_docname, "deal_owner"), HOSTS[0])

	def test_form_answers_go_to_a_new_lead(self):
		self.add_fields()
		result = self.book_as_guest(self.at(9), values={"organization": "Acme", "no_of_employees": "11-50"})
		booking = frappe.get_doc("CRM Booking", {"token": result["token"]})
		lead = frappe.get_doc("CRM Lead", booking.reference_docname)
		self.assertEqual((lead.organization, lead.no_of_employees), ("Acme", "11-50"))
		self.assertEqual(lead.first_name, "Vera")
		self.assertEqual(frappe.parse_json(booking.answers), {"Company": "Acme", "Team size": "11-50"})

	def test_form_answers_only_fill_empty_fields_of_an_existing_lead(self):
		self.add_fields()
		lead = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "Known",
				"email": "known@example.com",
				"organization": "Old Co",
			}
		).insert(ignore_permissions=True)
		self.book_as_guest(
			self.at(9),
			email="known@example.com",
			values={"organization": "New Co", "no_of_employees": "1-10"},
		)
		lead.reload()
		self.assertEqual((lead.organization, lead.no_of_employees), ("Old Co", "1-10"))

	def test_required_form_fields_and_options_are_enforced(self):
		self.add_fields()
		with self.assertRaises(frappe.ValidationError):
			self.book_as_guest(self.at(9), values={"no_of_employees": "11-50"})
		with self.assertRaises(frappe.ValidationError):
			self.book_as_guest(self.at(9), values={"organization": "Acme", "no_of_employees": "lots"})

	def test_host_gets_a_booking_notification_not_a_guest_assignment(self):
		result = self.book_as_guest(self.at(9))
		booking = frappe.get_doc("CRM Booking", {"token": result["token"]})
		notifications = frappe.get_all(
			"CRM Notification", filters={"to_user": HOSTS[0]}, fields=["type", "message", "reference_name"]
		)
		self.assertEqual([n.type for n in notifications], ["Booking"])
		self.assertEqual(notifications[0].reference_name, booking.reference_docname)
		self.assertIn("Vera Visitor booked Intro call", notifications[0].message)

	def test_deleting_a_page_without_bookings_removes_its_form(self):
		from crm.api.booking import list_pages

		form = self.page.web_form
		self.assertEqual(next(p for p in list_pages() if p.name == self.page.name).bookings, 0)
		frappe.delete_doc("CRM Booking Page", self.page.name)
		self.assertFalse(frappe.db.exists("Web Form", form))

	def test_a_page_with_bookings_is_kept(self):
		from crm.api.booking import list_pages

		self.book_as_guest(self.at(9))
		self.assertEqual(next(p for p in list_pages() if p.name == self.page.name).bookings, 1)
		with self.assertRaises(frappe.LinkExistsError):
			frappe.delete_doc("CRM Booking Page", self.page.name)

	# Slots taken and freed, whoever makes the change

	def booking_for(self, result):
		return frappe.get_doc("CRM Booking", {"token": result["token"]})

	def test_a_booked_slot_cannot_be_booked_again(self):
		self.book_as_guest(self.at(9), "first@example.com")
		self.assertNotIn(to_iso(self.at(9)), self.slots_on_monday())
		with self.assertRaises(SlotUnavailableError):
			self.book_as_guest(self.at(9), "second@example.com")

	def test_team_slot_frees_when_one_of_two_bookings_is_cancelled(self):
		page = self.make_page(HOSTS)
		first = self.book_as_guest(self.at(9), "one@example.com", page)
		self.book_as_guest(self.at(9), "two@example.com", page)
		self.assertNotIn(to_iso(self.at(9)), self.slots_on_monday(page))
		frappe.set_user("Guest")
		cancel_booking(first["token"])
		frappe.set_user("Administrator")
		self.assertIn(to_iso(self.at(9)), self.slots_on_monday(page))

	def test_host_cancelling_the_event_cancels_the_booking(self):
		booking = self.booking_for(self.book_as_guest(self.at(9)))
		event = frappe.get_doc("Event", booking.event)
		event.status = "Cancelled"
		event.save(ignore_permissions=True)
		booking.reload()
		self.assertEqual(booking.status, "Cancelled")
		self.assertIn(to_iso(self.at(9)), self.slots_on_monday())
		self.assertTrue(
			frappe.db.exists(
				"Email Queue Recipient", {"recipient": booking.guest_email, "parenttype": "Email Queue"}
			)
		)

	def test_host_deleting_the_event_cancels_the_booking(self):
		booking = self.booking_for(self.book_as_guest(self.at(9)))
		frappe.delete_doc("Event", booking.event, ignore_permissions=True)
		booking.reload()
		self.assertEqual((booking.status, booking.event), ("Cancelled", None))
		self.assertIn(to_iso(self.at(9)), self.slots_on_monday())

	def test_host_moving_the_event_moves_the_booking(self):
		booking = self.booking_for(self.book_as_guest(self.at(9)))
		event = frappe.get_doc("Event", booking.event)
		event.starts_on, event.ends_on = to_system(self.at(11)), to_system(self.at(11, 30))
		event.save(ignore_permissions=True)
		booking.reload()
		self.assertEqual(frappe.utils.get_datetime(booking.starts_on), to_system(self.at(11)))
		slots = self.slots_on_monday()
		self.assertIn(to_iso(self.at(9)), slots)
		self.assertNotIn(to_iso(self.at(11)), slots)

	def test_cancelling_and_rebooking_the_same_slot(self):
		first = self.book_as_guest(self.at(9))
		frappe.set_user("Guest")
		cancel_booking(first["token"])
		frappe.set_user("Administrator")
		second = self.book_as_guest(self.at(9), "again@example.com")
		self.assertEqual(self.booking_for(second).status, "Confirmed")
		self.assertNotIn(to_iso(self.at(9)), self.slots_on_monday())

	def test_teammates_see_a_booked_meeting_on_the_lead_but_not_private_ones(self):
		from crm.api.event import get_event, get_event_list

		manager = "booking-manager@example.com"
		if not frappe.db.exists("User", manager):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": manager,
					"first_name": "Manager",
					"roles": [{"role": "Sales Manager"}],
				}
			).insert(ignore_permissions=True)
		booking = self.booking_for(self.book_as_guest(self.at(9)))
		private = self.add_event(HOSTS[0], self.at(11), self.at(11, 30))
		on_lead = {"reference_doctype": "CRM Lead", "reference_docname": booking.reference_docname}
		try:
			# a manager can read the lead, so sees its meeting, but not the host's own
			frappe.set_user(manager)
			self.assertEqual([e.name for e in get_event_list("Event", ["name"], on_lead)], [booking.event])
			self.assertEqual(get_event(booking.event)["name"], booking.event)
			self.assertNotIn(
				private.name, [e.name for e in get_event_list("Event", ["name"], {"owner": HOSTS[0]})]
			)
			with self.assertRaises(frappe.PermissionError):
				get_event(private.name)
			# a sales user outside the host's team can't read the lead, so sees neither
			frappe.set_user(HOSTS[1])
			self.assertEqual(get_event_list("Event", ["name"], on_lead), [])
		finally:
			frappe.set_user("Administrator")

	def test_meeting_title_can_be_configured(self):
		booking = self.booking_for(self.book_as_guest(self.at(9)))
		self.assertEqual(
			frappe.db.get_value("Event", booking.event, "subject"), "Intro call with Vera Visitor"
		)

		self.page.event_title = "Demo · {guest} · {page} · {unknown}"
		self.page.save()
		booking = self.booking_for(self.book_as_guest(self.at(10), "second@example.com"))
		self.assertEqual(
			frappe.db.get_value("Event", booking.event, "subject"),
			"Demo · Vera Visitor · Intro call · {unknown}",
		)
