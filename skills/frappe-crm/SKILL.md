---
name: frappe-crm
description: Work with sales data in Frappe CRM on a Frappe site. Use to find, create or update leads, deals, contacts and organizations, convert a lead to a deal, read a lead's or deal's timeline, add notes, tasks, reminders or comments, log a call, write and send an email or a reply from a lead or deal, or assign owners, when the site runs the CRM app.
---

# Frappe CRM

CRM tracks sales in two documents. A `CRM Lead` is a prospect that is not yet qualified. A `CRM Deal` is a qualified opportunity, usually made by converting a lead. Every step here is a document read or write, or a whitelisted method, so it works the same from the site's assistant, MCP or frappectl. Each user sees the leads and deals that their roles and the sales hierarchy allow.

## 1. Data model

- `CRM Lead`: a person (`first_name` is required) and an `organization` name as text. Also `email`, `mobile_no`, `status`, `lead_owner`, `source`, and `converted` (1 after conversion).
- `CRM Deal`: `organization` links to a `CRM Organization`. The `contacts` table links `Contact` records, with one marked `is_primary`. Also `status`, `deal_owner`, `deal_value`, `expected_deal_value`, `expected_closure_date`, `probability`, and `lead` (the source lead).
- `CRM Lead Status` and `CRM Deal Status`: each site defines its own statuses. Read them first. The `type` field gives the meaning: `Open`, `Ongoing`, `On Hold`, `Won` or `Lost`.
- `CRM Organization` holds companies. `Contact` holds people, with emails in `email_ids` and phones in `phone_nos`.
- `FCRM Note` (`title`, `content`) and `CRM Task` (`title`, `status`, `priority`, `assigned_to`, `due_date`) link to a lead or deal through `reference_doctype` and `reference_docname`.
- `CRM Call Log` records calls. Its `reference_doctype` and `reference_docname` point to the lead or deal.

## 2. Operations

On the site's assistant, call a whitelisted method with `execute`: `code` is one line such as `result = frappe.call("crm.fcrm.doctype.crm_lead.crm_lead.convert_to_deal", lead="CRM-LEAD-2026-00001", if_converted="Return Existing")`, and `description` says in the user's words what will happen, such as "Convert Nina Rao to a deal". Create and update records with `create` and `update`.

- Convert a lead: call `crm.fcrm.doctype.crm_lead.crm_lead.convert_to_deal` with `lead`. It makes the deal, contact and organization and marks the lead converted. It returns the deal name. Pass `if_converted: "Return Existing"` to get the existing deal on a retry instead of a second deal.
- Read a timeline: call `crm.api.activities.get_activities` with `name` (a lead or deal). It returns field changes, emails, comments, calls, notes, tasks and attachments.
- Change deal contacts: call `add_contact`, `remove_contact` or `set_primary_contact` in `crm.fcrm.doctype.crm_deal.crm_deal`, each with `deal` and `contact`.
- Add a comment: call `crm.api.comment.add_comment` with `reference_doctype`, `reference_name` and `content` (HTML).
- Find a lead or deal by a person's or company's name or email: call `crm.api.assistant.find_records` with `text`. It searches open leads and deals together and returns each match's `doctype`, `record` and `link`.
- Send an email from a lead or deal: call `crm.api.assistant.send_email` with `doctype`, `record`, `subject` and `message` (plain text, a blank line between paragraphs), and optional `to` and `cc` lists. It sends from the user's own email account, refuses addresses that unsubscribed, and shows on the record's timeline, where the reply lands. Without `to`, it goes to the record's email. `frappe.core.doctype.communication.email.make` (with `doctype`, `name`, `recipients`, `subject`, `content` and `send_email: 1`) also works, without those checks.
- Add a note or log a call: create an `FCRM Note` with `title`, `content` (HTML paragraphs) and the lead or deal in `reference_doctype` and `reference_docname`.
- Add a task or reminder: create a `CRM Task` with `title`, `status: "Todo"`, `priority`, `assigned_to` (a user's email), `due_date` (`YYYY-MM-DD HH:MM:SS`) and the lead or deal in `reference_doctype` and `reference_docname`. To move, finish or hand over a task, update its `due_date`, `status` (`Done`) or `assigned_to`.
- Assign an owner: set `lead_owner` or `deal_owner`. CRM then shares the document with that user and assigns it to them.
- Create a lead from a call: call `crm.fcrm.doctype.crm_call_log.crm_call_log.create_lead_from_call_log` with `call_log` and `lead_details`.

## 3. Traps

- **Do not make a deal from a lead's data yourself.** Use `convert_to_deal`, or the lead stays unconverted and loses its link to the deal.
- **A lead with a `Lost` status cannot be converted.**
- **A `Lost` status needs `lost_reason`.** When `lost_reason` is `Other`, `lost_notes` is also required.
- **Forecasting can make deal fields mandatory.** When it is on in `FCRM Settings`, every deal save needs `expected_deal_value` and `expected_closure_date`.
- **Statuses are site data.** Do not assume `New`, `Qualified` or `Won` exist. Read the status doctypes.

## 4. In a chat with a salesperson

The person asking usually works in sales and isn't technical. Talk like a helpful colleague. Never mention record IDs, field names, tools or code.

- **Find the record first.** On the site's assistant, use `read_records`, which needs no approval: `CRM Deal`, then `CRM Lead` with `converted: 0`, filtered on `lead_name`, `organization` or `email` with `["like", "%<what they said>%"]`. If more than one matches, ask which. If none does, say so and ask.
- **Emails.** For a reply, read their last emails first: `read_records("Communication", {"reference_doctype": <lead or deal>, "reference_name": <its name>}, order_by="communication_date desc", limit=3)`. Write in the user's voice: short, friendly, plain text, with their name at the end. Show the whole draft, subject first, with who it goes to and the lead or deal as a link, and send only after they say so. If no email account can send, say email isn't connected yet. If someone unsubscribed, say they asked not to get emails.
- **Notes and calls.** Write what they told you, in their words, tidied up. Title a call like "Call with Nina". Don't make up details they didn't give.
- **Tasks.** A short title of what to do, such as "Call Nina about pricing". Turn "Friday" or "next week" into a date from today's date, and say the day back to them, such as "Friday 24 October, 10 am". Without a time, use 10 am. It goes to them unless they name a teammate; check the teammate is an enabled user with `read_records("User", ...)`.
- **Links.** Name every lead, deal, task, note or email you mention as a link, lists included: a lead is /crm/leads/<name>, a deal /crm/deals/<name>, a contact /crm/contacts/<name>, an organization /crm/organizations/<name>. A task, note, email or call opens its lead's or deal's tab: add #tasks, #notes, #emails or #calls, such as [Call Nina about pricing](/crm/deals/CRM-DEAL-2026-00004#tasks). Never write a record's id as text.
- **After it's done**, say in one line what was done, with a link to the lead or deal, and offer one next step, such as a follow-up reminder after an email.
