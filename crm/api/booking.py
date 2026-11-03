# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Public appointment booking.

A CRM Booking Page holds a host's (or a team's) weekly hours. Visitors on
/crm-book/<route> see the open slots, which are those hours minus each host's
busy time from CRM Events and, when connected, their Google Calendar. A booking
creates an Event owned by the host and links it to the visitor's lead or deal,
creating a lead when none exists.

Times cross the API as UTC ISO strings. Frappe stores datetimes naive in the
system timezone, so everything is converted at the edges.
"""

import secrets
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import frappe
from frappe import _
from frappe.query_builder.functions import Max
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, get_datetime, get_system_timezone, get_time, get_url, validate_email_address

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# a visitor browses a month at a time; anything wider is not a calendar view
MAX_RANGE = timedelta(days=42)
GOOGLE_BUSY_CACHE_SECONDS = 5 * 60


@frappe.whitelist(allow_guest=True)
def get_slots(route: str, start: str, end: str, token: str | None = None) -> list[str]:
	"""Open slot start times between `start` and `end`, as UTC ISO strings.

	With a booking `token` the slots are the booking host's, for rescheduling."""
	page = get_page(route)
	start_utc, end_utc = parse_utc(start), parse_utc(end)
	if end_utc <= start_utc or end_utc - start_utc > MAX_RANGE:
		frappe.throw(_("Pick a range of up to six weeks"))
	booking = get_booking_by_token(token) if token else None
	hosts = [booking.host] if booking else page_hosts(page)
	return [to_iso(s) for s in available_slots(page, start_utc, end_utc, hosts, booking)]


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=10, seconds=60 * 60)
def book(
	route: str,
	start: str,
	full_name: str,
	email: str,
	phone: str | None = None,
	answers: dict | str | None = None,
	timezone: str | None = None,
) -> dict:
	page = get_page(route)
	full_name = (full_name or "").strip()
	email = (email or "").strip().lower()
	if not full_name:
		frappe.throw(_("Please enter your name"))
	if not validate_email_address(email):
		frappe.throw(_("Please enter a valid email address"))
	answers = clean_answers(page, frappe.parse_json(answers) or {})

	start_utc = parse_utc(start)
	end_utc = start_utc + timedelta(minutes=page.duration)
	hosts = page_hosts(page)
	lock_hosts(hosts)
	free_hosts = available_slots(page, start_utc, end_utc, hosts).get(start_utc)
	if not free_hosts:
		frappe.throw(_("That time was just taken. Please pick another one."), title=_("Slot unavailable"))
	host = pick_host(page, free_hosts)

	reference_doctype, reference_docname = link_record(page, full_name, email, phone, host)
	booking = frappe.get_doc(
		{
			"doctype": "CRM Booking",
			"booking_page": page.name,
			"host": host,
			"starts_on": to_system(start_utc),
			"ends_on": to_system(end_utc),
			"guest_timezone": valid_timezone(timezone),
			"guest_name": full_name,
			"guest_email": email,
			"guest_phone": phone,
			"answers": answers,
			"reference_doctype": reference_doctype,
			"reference_docname": reference_docname,
			"token": secrets.token_urlsafe(24),
		}
	).insert(ignore_permissions=True)

	event = create_event(page, booking)
	booking.db_set("event", event.name, update_modified=False)
	send_booking_emails(page, booking, event, "confirmed")
	return public_booking(booking, page, event)


@frappe.whitelist(allow_guest=True)
def get_booking(token: str) -> dict:
	booking = get_booking_by_token(token, confirmed_only=False)
	page = frappe.get_doc("CRM Booking Page", booking.booking_page)
	event = frappe.get_doc("Event", booking.event) if booking.event else None
	return public_booking(booking, page, event)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=60 * 60)
def cancel_booking(token: str, reason: str | None = None) -> dict:
	booking = get_booking_by_token(token)
	ensure_upcoming(booking)
	booking.status = "Cancelled"
	booking.cancel_reason = (reason or "").strip()[:500]
	booking.save(ignore_permissions=True)

	page = frappe.get_doc("CRM Booking Page", booking.booking_page)
	event = None
	if booking.event and frappe.db.exists("Event", booking.event):
		event = frappe.get_doc("Event", booking.event)
		event.status = "Cancelled"
		event.save(ignore_permissions=True)
		cancel_google_event(event)
	send_booking_emails(page, booking, event, "cancelled")
	return public_booking(booking, page, event)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=60 * 60)
def reschedule_booking(token: str, start: str) -> dict:
	booking = get_booking_by_token(token)
	ensure_upcoming(booking)
	page = get_page(frappe.db.get_value("CRM Booking Page", booking.booking_page, "route"))

	start_utc = parse_utc(start)
	end_utc = start_utc + timedelta(minutes=page.duration)
	lock_hosts([booking.host])
	if not available_slots(page, start_utc, end_utc, [booking.host], booking).get(start_utc):
		frappe.throw(_("That time was just taken. Please pick another one."), title=_("Slot unavailable"))

	booking.starts_on = to_system(start_utc)
	booking.ends_on = to_system(end_utc)
	booking.save(ignore_permissions=True)

	event = None
	if booking.event and frappe.db.exists("Event", booking.event):
		event = frappe.get_doc("Event", booking.event)
		event.starts_on = booking.starts_on
		event.ends_on = booking.ends_on
		# saving pushes the new time to Google, which emails the attendees
		event.save(ignore_permissions=True)
	send_booking_emails(page, booking, event, "rescheduled")
	return public_booking(booking, page, event)


# Pages and bookings


def get_page(route: str):
	"""An enabled page by route. People who can edit pages may also open disabled ones, to preview."""
	filters = {"route": route}
	if not (frappe.session.user != "Guest" and frappe.has_permission("CRM Booking Page", "write")):
		filters["enabled"] = 1
	name = frappe.db.get_value("CRM Booking Page", filters)
	if not name:
		raise frappe.DoesNotExistError(_("This booking page does not exist"))
	return frappe.get_doc("CRM Booking Page", name)


def page_hosts(page) -> list[str]:
	users = [h.user for h in page.hosts]
	enabled = set(frappe.get_all("User", filters={"name": ["in", users], "enabled": 1}, pluck="name"))
	return [u for u in users if u in enabled]


def public_page(page) -> dict:
	"""What a visitor may see of a page."""
	hosts = page_hosts(page)
	return {
		"title": page.title,
		"route": page.route,
		"description": page.description or "",
		"duration": page.duration,
		"location_type": page.location_type,
		"location": page.location if page.location_type == "In person" else "",
		"timezone": page_timezone(page),
		"max_days_ahead": cint(page.max_days_ahead) or 30,
		"questions": [
			{
				"name": q.name,
				"label": q.label,
				"fieldtype": q.fieldtype,
				"options": [o.strip() for o in (q.options or "").split("\n") if o.strip()],
				"reqd": cint(q.reqd),
			}
			for q in page.questions
		],
		"hosts": [
			{"full_name": u.full_name, "image": u.user_image}
			for u in frappe.get_all("User", filters={"name": ["in", hosts]}, fields=["full_name", "user_image"])
		],
		"confirmation_message": page.confirmation_message or "",
	}


def public_booking(booking, page, event=None) -> dict:
	return {
		"token": booking.token,
		"status": booking.status,
		"start": to_iso(to_utc(booking.starts_on)),
		"end": to_iso(to_utc(booking.ends_on)),
		"guest_name": booking.guest_name,
		"guest_email": booking.guest_email,
		"host": frappe.db.get_value("User", booking.host, "full_name"),
		"meeting_link": meeting_link(page, event),
		"page": {"title": page.title, "route": page.route, "duration": page.duration},
		"upcoming": to_utc(booking.starts_on) > datetime.now(UTC),
	}


def get_booking_by_token(token: str | None, confirmed_only: bool = True):
	name = token and frappe.db.get_value("CRM Booking", {"token": token})
	if not name:
		raise frappe.DoesNotExistError(_("This booking does not exist"))
	booking = frappe.get_doc("CRM Booking", name)
	if confirmed_only and booking.status != "Confirmed":
		frappe.throw(_("This booking was cancelled"))
	return booking


def ensure_upcoming(booking):
	if to_utc(booking.starts_on) <= datetime.now(UTC):
		frappe.throw(_("This meeting has already started"))


def clean_answers(page, answers: dict) -> dict:
	"""Keep answers to the page's own questions only, keyed by question label."""
	out = {}
	for q in page.questions:
		value = str(answers.get(q.name) or "").strip()[:2000]
		if q.reqd and not value:
			frappe.throw(_("Please answer: {0}").format(q.label))
		if value and q.fieldtype == "Options":
			options = [o.strip() for o in (q.options or "").split("\n") if o.strip()]
			if value not in options:
				frappe.throw(_("Please pick an option for: {0}").format(q.label))
		if value:
			out[q.label] = value
	return out


def lock_hosts(hosts: list[str]):
	"""Serialise bookings per host so two visitors can't take the same slot."""
	frappe.db.sql(
		"select name from `tabUser` where name in %(hosts)s order by name for update",
		{"hosts": tuple(hosts) or ("",)},
	)


def pick_host(page, free_hosts: list[str]) -> str:
	"""Round robin: the free host who was booked from this page longest ago."""
	if len(free_hosts) == 1:
		return free_hosts[0]
	Booking = frappe.qb.DocType("CRM Booking")
	rows = (
		frappe.qb.from_(Booking)
		.select(Booking.host, Max(Booking.creation).as_("last"))
		.where((Booking.booking_page == page.name) & Booking.host.isin(free_hosts))
		.groupby(Booking.host)
		.run(as_dict=True)
	)
	last_booked = {row.host: row.last for row in rows}
	order = {h.user: h.idx for h in page.hosts}
	return min(free_hosts, key=lambda h: (last_booked.get(h) or datetime.min, order.get(h, 0)))


def link_record(page, full_name, email, phone, host) -> tuple[str | None, str | None]:
	"""The open lead or deal this visitor belongs to, or a new lead for the host."""
	lead = frappe.db.get_value("CRM Lead", {"email": email, "converted": 0}, "name", order_by="modified desc")
	if lead:
		return "CRM Lead", lead

	contact = frappe.db.get_value("Contact Email", {"email_id": email, "parenttype": "Contact"}, "parent")
	if contact:
		deal = frappe.db.get_value(
			"CRM Contacts", {"contact": contact, "parenttype": "CRM Deal"}, "parent", order_by="modified desc"
		)
		if deal:
			return "CRM Deal", deal

	if not page.create_lead:
		return None, None

	first_name, _sep, last_name = full_name.partition(" ")
	lead = frappe.get_doc(
		{
			"doctype": "CRM Lead",
			"first_name": first_name,
			"last_name": last_name,
			"email": email,
			"mobile_no": phone,
			"lead_owner": host,
			"source": page.lead_source,
		}
	).insert(ignore_permissions=True)
	return "CRM Lead", lead.name


# Slots


def available_slots(page, start_utc, end_utc, hosts, booking=None) -> dict[datetime, list[str]]:
	"""Slot start → hosts free for it, for slots starting in [start_utc, end_utc).

	`booking` is one being rescheduled; its own meeting doesn't count as busy."""
	now = datetime.now(UTC)
	start_utc = max(start_utc, now + timedelta(hours=cint(page.min_notice)))
	end_utc = min(end_utc, now + timedelta(days=cint(page.max_days_ahead) or 30))
	if start_utc >= end_utc or not hosts:
		return {}
	slots = list(candidate_slots(page, start_utc, end_utc))
	if not slots:
		return {}

	duration = timedelta(minutes=page.duration)
	buffer = timedelta(minutes=cint(page.buffer))
	busy = {h: busy_times(h, slots[0] - buffer, slots[-1] + duration + buffer, booking) for h in hosts}

	out = {}
	for slot in slots:
		before, after = slot - buffer, slot + duration + buffer
		free = [h for h in hosts if not any(b_start < after and b_end > before for b_start, b_end in busy[h])]
		if free:
			out[slot] = free
	return out


def candidate_slots(page, start_utc, end_utc):
	"""Slot starts from the page's weekly hours, minus holidays, in [start_utc, end_utc)."""
	tz = ZoneInfo(page_timezone(page))
	duration = timedelta(minutes=page.duration)
	step = timedelta(minutes=cint(page.slot_interval) or page.duration)
	holidays = holiday_dates(page)
	hours = {}
	for row in page.availability:
		hours.setdefault(row.workday, []).append((get_time(row.start_time), get_time(row.end_time)))

	day = start_utc.astimezone(tz).date()
	last_day = end_utc.astimezone(tz).date()
	while day <= last_day:
		if day not in holidays:
			for open_at, close_at in hours.get(WEEKDAYS[day.weekday()], []):
				slot = datetime.combine(day, open_at, tzinfo=tz)
				close = datetime.combine(day, close_at, tzinfo=tz)
				while slot + duration <= close:
					slot_utc = slot.astimezone(UTC)
					if start_utc <= slot_utc < end_utc:
						yield slot_utc
					slot += step
		day += timedelta(days=1)


def holiday_dates(page) -> set:
	if not page.holiday_list:
		return set()
	return set(
		frappe.get_all(
			"CRM Holiday",
			filters={"parent": page.holiday_list, "parenttype": "CRM Holiday List"},
			pluck="date",
		)
	)


def busy_times(host, start_utc, end_utc, booking=None) -> list[tuple[datetime, datetime]]:
	"""The host's busy intervals (UTC) overlapping [start_utc, end_utc)."""
	busy = event_busy_times(host, start_utc, end_utc, booking and booking.event)
	google = google_busy_times(host, start_utc, end_utc)
	if booking:
		# the booking's own event as pulled back from Google
		own = (to_utc(booking.starts_on), to_utc(booking.ends_on))
		google = [b for b in google if b != own]
	return busy + google


def event_busy_times(host, start_utc, end_utc, exclude_event=None) -> list[tuple[datetime, datetime]]:
	"""Open, timed CRM Events the host owns or attends. All-day events don't block time."""
	emails = {host, frappe.db.get_value("User", host, "email")} - {None}
	start, end = to_system(start_utc), to_system(end_utc)
	events = frappe.db.sql(
		"""
		select distinct e.name, e.starts_on, e.ends_on, e.repeat_this_event, e.repeat_on, e.repeat_till,
			e.monday, e.tuesday, e.wednesday, e.thursday, e.friday, e.saturday, e.sunday
		from `tabEvent` e
		left join `tabEvent Participants` p on p.parent = e.name and p.parenttype = 'Event'
		where e.status = 'Open' and e.all_day = 0 and e.name != %(exclude)s
			and (e.owner = %(host)s or p.email in %(emails)s)
			and e.starts_on < %(end)s
			and (
				coalesce(e.ends_on, e.starts_on) > %(start)s
				or (e.repeat_this_event = 1 and coalesce(e.repeat_till, '3000-01-01') >= date(%(start)s))
			)
		""",
		{"host": host, "emails": tuple(emails), "start": start, "end": end, "exclude": exclude_event or ""},
		as_dict=True,
	)
	busy = []
	for e in events:
		for occ_start, occ_end in occurrences(e, start, end):
			busy.append((to_utc(occ_start), to_utc(occ_end)))
	return busy


def occurrences(event, start, end):
	"""(start, end) of each occurrence of a possibly repeating event within [start, end)."""
	length = (event.ends_on - event.starts_on) if event.ends_on else timedelta(0)
	if not event.repeat_this_event:
		yield event.starts_on, event.starts_on + length
		return

	first = event.starts_on.date()
	day = max(first, (start - length).date())
	last = end.date()
	if event.repeat_till:
		last = min(last, event.repeat_till)
	weekdays = [WEEKDAYS[i].lower() for i in range(7)]
	any_weekday = any(event.get(d) for d in weekdays)
	while day <= last:
		months = (day.year - first.year) * 12 + day.month - first.month
		repeats = {
			"Daily": True,
			"Weekly": event.get(weekdays[day.weekday()]) if any_weekday else day.weekday() == first.weekday(),
			"Monthly": day.day == first.day,
			"Quarterly": day.day == first.day and months % 3 == 0,
			"Half Yearly": day.day == first.day and months % 6 == 0,
			"Yearly": day.day == first.day and day.month == first.month,
		}.get(event.repeat_on)
		if repeats:
			occ_start = datetime.combine(day, event.starts_on.time())
			yield occ_start, occ_start + length
		day += timedelta(days=1)


def google_busy_times(host, start_utc, end_utc) -> list[tuple[datetime, datetime]]:
	"""Busy blocks from the host's Google Calendar, when they've connected one.

	Asks Google for whole UTC days so nearby requests share a cache entry."""
	calendar = frappe.db.get_value("Google Calendar", {"user": host, "enable": 1}, "name")
	if not calendar:
		return []
	day_start = start_utc.replace(hour=0, minute=0, second=0, microsecond=0)
	day_end = end_utc.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
	key = f"crm_booking_google_busy:{calendar}:{day_start.date()}:{day_end.date()}"
	blocks = frappe.cache.get_value(key)
	if blocks is None:
		blocks = fetch_google_busy(calendar, day_start, day_end)
		frappe.cache.set_value(key, blocks, expires_in_sec=GOOGLE_BUSY_CACHE_SECONDS)
	return [(parse_utc(s), parse_utc(e)) for s, e in blocks]


def fetch_google_busy(calendar, start_utc, end_utc) -> list[tuple[str, str]]:
	from frappe.integrations.doctype.google_calendar.google_calendar import get_google_calendar_object

	try:
		service, account = get_google_calendar_object(calendar)
		# the host's own meetings live in "primary"; Frappe may sync into a calendar of its own
		ids = {"primary", account.google_calendar_id} - {None, ""}
		result = (
			service.freebusy()
			.query(body={"timeMin": to_iso(start_utc), "timeMax": to_iso(end_utc), "items": [{"id": i} for i in ids]})
			.execute()
		)
	except Exception:
		frappe.log_error(title=f"CRM Booking: Google free/busy failed for {calendar}")
		return []
	return [(b["start"], b["end"]) for cal in result.get("calendars", {}).values() for b in cal.get("busy", [])]


# Events and emails


def create_event(page, booking):
	def build():
		event = frappe.new_doc("Event")
		event.update(
			{
				"subject": _("{0} with {1}").format(page.title, booking.guest_name),
				"event_category": "Meeting",
				"event_type": "Private",
				"starts_on": booking.starts_on,
				"ends_on": booking.ends_on,
				"description": event_description(booking),
				"location": page.location if page.location_type in ("In person", "Custom link") else "",
			}
		)
		if booking.reference_doctype:
			event.reference_doctype = booking.reference_doctype
			event.reference_docname = booking.reference_docname
			event.append(
				"event_participants",
				{
					"reference_doctype": booking.reference_doctype,
					"reference_docname": booking.reference_docname,
					"email": booking.guest_email,
				},
			)
		return event

	event = build()
	calendar = frappe.db.get_value(
		"Google Calendar", {"user": booking.host, "enable": 1, "push_to_google_calendar": 1}, "name"
	)
	if calendar:
		event.sync_with_google_calendar = 1
		event.google_calendar = calendar
		event.add_video_conferencing = int(page.location_type == "Google Meet")
		frappe.db.savepoint("crm_booking_event")
		try:
			event.insert(ignore_permissions=True)
		except Exception:
			# keep the booking even if Google is unreachable; it just won't sync
			frappe.db.rollback(save_point="crm_booking_event")
			frappe.log_error(title=f"CRM Booking: Google sync failed for {booking.name}")
			event = build()
			event.insert(ignore_permissions=True)
	else:
		event.insert(ignore_permissions=True)

	# insert always stamps the session user (Guest) as owner; the host owns the meeting
	event.db_set("owner", booking.host, update_modified=False)
	event.reload()
	return event


def event_description(booking) -> str:
	lines = [f"<p>{frappe.utils.escape_html(booking.guest_name)} &lt;{booking.guest_email}&gt;</p>"]
	if booking.guest_phone:
		lines.append(f"<p>{frappe.utils.escape_html(booking.guest_phone)}</p>")
	for question, answer in (frappe.parse_json(booking.answers) or {}).items():
		lines.append(
			f"<p><strong>{frappe.utils.escape_html(question)}</strong><br>{frappe.utils.escape_html(answer)}</p>"
		)
	return "".join(lines)


def cancel_google_event(event):
	if not (event.sync_with_google_calendar and event.google_calendar_event_id):
		return
	from frappe.integrations.doctype.google_calendar.google_calendar import delete_event_from_google_calendar

	try:
		delete_event_from_google_calendar(event)
	except Exception:
		frappe.log_error(title=f"CRM Booking: Google cancel failed for {event.name}")


def meeting_link(page, event) -> str:
	if page.location_type == "Google Meet":
		return (event and event.google_meet_link) or ""
	if page.location_type == "Custom link":
		return page.location or ""
	return ""


def send_booking_emails(page, booking, event, action: str):
	"""Tell the guest and the host a booking was confirmed, rescheduled or cancelled."""
	manage_url = get_url(f"/crm-book/{page.route}?booking={booking.token}")
	link = meeting_link(page, event)
	invited_by_google = bool(event and event.google_calendar_event_id and booking.reference_doctype)
	subjects = {
		"confirmed": _("Confirmed: {0}"),
		"rescheduled": _("Rescheduled: {0}"),
		"cancelled": _("Cancelled: {0}"),
	}
	host_name, host_email, host_tz = frappe.db.get_value(
		"User", booking.host, ["full_name", "email", "time_zone"]
	)

	guest_when = format_when(booking, booking.guest_timezone or page_timezone(page))
	attachments = []
	if action != "cancelled" and not invited_by_google:
		attachments.append({"fname": "invite.ics", "fcontent": ics(page, booking, host_name, host_email, link)})
	frappe.sendmail(
		recipients=[booking.guest_email],
		reply_to=host_email,
		subject=subjects[action].format(page.title) + f" · {guest_when}",
		template="crm_booking",
		args={
			"action": action,
			"title": page.title,
			"when": guest_when,
			"with_name": host_name,
			"location": location_text(page, link),
			"manage_url": manage_url if action != "cancelled" else "",
			"message": page.confirmation_message if action == "confirmed" else "",
			"reason": booking.cancel_reason if action == "cancelled" else "",
		},
		attachments=attachments,
		reference_doctype="CRM Booking",
		reference_name=booking.name,
	)

	host_when = format_when(booking, host_tz or get_system_timezone())
	record_url = ""
	if booking.reference_doctype:
		route = "leads" if booking.reference_doctype == "CRM Lead" else "deals"
		record_url = get_url(f"/crm/{route}/{booking.reference_docname}")
	frappe.sendmail(
		recipients=[host_email],
		reply_to=booking.guest_email,
		subject=subjects[action].format(f"{page.title} with {booking.guest_name}") + f" · {host_when}",
		template="crm_booking",
		args={
			"action": action,
			"title": page.title,
			"when": host_when,
			"with_name": f"{booking.guest_name} ({booking.guest_email})",
			"location": location_text(page, link),
			"answers": frappe.parse_json(booking.answers) or {},
			"record_url": record_url,
			"reason": booking.cancel_reason if action == "cancelled" else "",
		},
		reference_doctype="CRM Booking",
		reference_name=booking.name,
	)


def location_text(page, link) -> str:
	if page.location_type == "Google Meet":
		return link or _("Google Meet (link to follow)")
	if page.location_type == "Phone call":
		return _("Phone call")
	return page.location or page.location_type


def format_when(booking, tz_name) -> str:
	tz = ZoneInfo(valid_timezone(tz_name) or get_system_timezone())
	start = to_utc(booking.starts_on).astimezone(tz)
	end = to_utc(booking.ends_on).astimezone(tz)
	return f"{start:%A, %-d %B %Y}, {start:%-I:%M %p} – {end:%-I:%M %p} ({tz.key})"


def ics(page, booking, host_name, host_email, link) -> str:
	def stamp(dt):
		return dt.strftime("%Y%m%dT%H%M%SZ")

	def text(value):
		return (value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

	return "\r\n".join(
		[
			"BEGIN:VCALENDAR",
			"VERSION:2.0",
			"PRODID:-//Frappe CRM//Booking//EN",
			"METHOD:REQUEST",
			"BEGIN:VEVENT",
			f"UID:{booking.name}@{frappe.local.site}",
			f"DTSTAMP:{stamp(datetime.now(UTC))}",
			f"DTSTART:{stamp(to_utc(booking.starts_on))}",
			f"DTEND:{stamp(to_utc(booking.ends_on))}",
			f"SUMMARY:{text(page.title + ' with ' + host_name)}",
			f"LOCATION:{text(location_text(page, link))}",
			f"ORGANIZER;CN={text(host_name)}:mailto:{host_email}",
			f"ATTENDEE;CN={text(booking.guest_name)};RSVP=FALSE:mailto:{booking.guest_email}",
			"END:VEVENT",
			"END:VCALENDAR",
			"",
		]
	)


# Time


def page_timezone(page) -> str:
	return valid_timezone(page.timezone) or get_system_timezone()


def valid_timezone(name: str | None) -> str | None:
	if not name:
		return None
	try:
		ZoneInfo(name)
	except (ZoneInfoNotFoundError, ValueError):
		return None
	return name


def parse_utc(value: str) -> datetime:
	try:
		dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
	except ValueError:
		frappe.throw(_("Invalid time"))
	if dt.tzinfo is None:
		frappe.throw(_("Time must include a timezone"))
	return dt.astimezone(UTC)


def to_iso(dt: datetime) -> str:
	return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_utc(value) -> datetime:
	"""A naive datetime in the system timezone, as UTC."""
	return get_datetime(value).replace(tzinfo=ZoneInfo(get_system_timezone())).astimezone(UTC)


def to_system(dt: datetime):
	"""A UTC datetime, as a naive datetime in the system timezone."""
	return dt.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)
