# 06 · User context: interests and recommended events for a more engaging agent

**Area:** Backend (`crud.load_user_context`) · **Priority:** Medium · **Related task:** 7

## Context (already done in the agent)
- Every prompt now includes the current Japan date and time and the user's turn number (`agents/body.py → _conversation_clock`).
- The collector adds at most one personal note: a booked event within 7 days, a close registration deadline, or a matching event the user hasn't booked.
- The agent already accepts an optional `recommended_events` list in `user_context` (up to 2 are used), but the backend does not send it yet.

## Constraint
**No schema changes:** we won't recreate the DB. Interests are worked out from existing data only.

## Fix in `src/concierge/db/relationaldb/crud.py → load_user_context`
1. **Interests:** collect the `Event.category` values of the user's bookings, past and upcoming (`Booking` join `Event`, confirmed bookings), counted and ordered by frequency. Add `"interests": ["Networking", "Conference", ...]` (top 3).
2. **`upcoming_bookings`:** add `category`, `location` and `days_until` (whole days from `japan_now()`) to each item.
3. **`recommended_events`:** up to 2 events where:
   - `start_at > now`,
   - registration is still open (`reg_deadline` is null or later than now),
   - the category is in `interests`,
   - the user has not booked it,
   - ordered by `start_at`.
   Shape: `{"event_id", "title", "category", "start_at", "reg_deadline", "location"}`.
   With no booking history, fall back to the next 2 open events.
4. The agent already accepts `"interests"` (top-level list) and `recommended_events`, so no agent change is needed.

## Acceptance
- A user with a booking in 3 days gets a short reminder on their first message.
- A user who booked networking events sees one upcoming networking event suggested when it fits.
- Guests and users without bookings still work, using the fallback events.
