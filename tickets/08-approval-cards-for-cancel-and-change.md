# 08 · Approval cards and messages for cancelling and changing bookings

**Area:** Backend (`agent_backend.py`) + Frontend (`ApprovalPanel.tsx`) · **Priority:** Medium · **Builds on:** ticket 04

## Context (already done in the agent)
The events agent has 4 new write tools. Each one pauses for the user's approval (`HumanInTheLoopMiddleware`):

| Tool | Arguments (as sent in the approval) | What approving does |
|---|---|---|
| `cancel_event_booking` | `booking_reference` (e.g. `CCIFJ-EVT-00012`) | Cancels the whole booking (all tickets) |
| `cancel_space_reservation` | `reservation_reference` (e.g. `CCIFJ-SPC-00003`) | Cancels the reservation |
| `change_event_booking` | `booking_reference`, `new_event_id?`, `attendees?` (full new list) | Moves the booking to another event and/or replaces the attendees |
| `change_space_reservation` | `reservation_reference`, `new_start_date?`, `new_end_date?`, `new_title?`, `new_expected_attendees?` | Moves the reservation or updates its title or attendee count |

Behind the scenes, a change cancels the old row and creates a new one, and restores the old one if the new one fails. **The user must see it as one change**, never as "cancel" plus "book".

Today these approvals work, but the user only sees the generic text ("Please review the details in the action card below…") and a collapsed block of raw arguments.

## Backend (`src/concierge/api/agent_backend.py`)
1. **Approval messages:** add `APPROVAL_PROMPTS` entries (en/fr/ja) and add the names to the tuple in `approval_prompt`:
   - `cancel_event_booking`: "Please check the booking below. Confirming will cancel it and all its tickets."
   - `cancel_space_reservation`: "Please check the reservation below. Confirming will cancel it."
   - `change_event_booking`: "Please check the changes to your booking below. Confirming will update it."
   - `change_space_reservation`: "Please check the changes to your reservation below. Confirming will update it."
2. **Card data:** extend the enrichment (`enrich_ticket_approvals`, which ticket 04 renames to `enrich_approvals`) so the card can show **before → after**. Only read data that belongs to the current user.
   - Cancel booking: event title, date, venue, and current attendees (`crud.get_booking`, `get_event`, `get_booking_tickets`).
   - Cancel reservation: space name, start/end, title (`crud.get_reservation`, `get_space`).
   - Change booking: `current` = event and attendees as today; `new` = the target event (from `new_event_id`, or the same one) and the attendees (from `attendees`, or unchanged).
   - Change reservation: `current` = dates, title, attendee count; `new` = the requested values (unchanged ones copied).
   - Turn the references into IDs with the same rule as the agent: take the last group of digits (`CCIFJ-EVT-00012` → 12).

## Frontend (`ccifj-frontend/src/ApprovalPanel.tsx`)
- **Cancel cards:** a red "Cancel booking" / "Cancel reservation" title, the item that will be cancelled (event or space, date, attendees), and "All tickets on this booking will be cancelled."
- **Change cards:** a two-column **Current → New** layout, with changed values highlighted. A single title: "Change booking" / "Change reservation". Don't mention cancelling.
- Button labels: "Cancel booking" / "Keep booking", "Cancel reservation" / "Keep reservation", "Confirm change" / "Keep as is" (en/fr/ja).
- Keep the raw-arguments fallback for unknown actions only.

## Optional
A "Cancel booking" or "Change booking" next-action button would need new IDs in the agent's `NextActionId` list and in `NEXT_ACTION_IDS` (`agent_backend.py:65`). Not needed now: users can ask in words.

## Acceptance
- For each of the 4 tools, the approval card shows exactly what will be cancelled or how it will change, in the UI language.
- A change is always shown as one change, with no cancel wording.
- Rejecting leaves the booking or reservation untouched, and the agent says so.
