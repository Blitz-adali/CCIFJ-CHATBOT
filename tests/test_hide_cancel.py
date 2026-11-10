"""Staff can cancel an event and hide a member company; nothing is deleted."""
import unittest
from types import SimpleNamespace

import test_api_contract as contract
from concierge.agents import tools
from concierge.db.relationaldb import crud, models
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
import test_admin_content as admin_tests

EVENT = {'title': 'Spring Gala', 'description': 'Members evening', 'category': 'Networking',
         'start_at': '2099-04-01 18:30', 'member_price': 5000, 'languages': ['en']}


class HideAndCancelTest(unittest.TestCase):
    staff_headers = admin_tests.AdminContentApiTest.staff_headers
    run_db = admin_tests.AdminContentApiTest.run_db
    tearDown = contract.ApiContractTest.tearDown

    def setUp(self):
        contract.ApiContractTest.setUp(self)

    def test_cancelled_event_is_not_offered_or_bookable_and_can_be_restored(self):
        headers = self.staff_headers()
        event_id = self.client.post('/api/v1/admin/events', json=EVENT, headers=headers).json()['event_id']

        async def offered(db):
            return [event.title for event in await crud.search_events(db, name='Gala')]

        async def book(db):
            user = await crud.get_user_by_email(db, 'aiko@example.com')
            if user is None:
                user = models.User(name='Aiko', email='aiko@example.com')
                db.add(user)
                await db.commit()
                await db.refresh(user)
            try:
                await crud.book_event(db, user.user_id, event_id, [{'name': 'Aiko', 'email': 'aiko@example.com'}])
                return 'booked'
            except ValueError as exc:
                return str(exc)

        self.assertEqual(self.run_db(offered), ['Spring Gala'])
        cancelled = self.client.patch(f'/api/v1/admin/events/{event_id}/status', json={'cancelled': True}, headers=headers)
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertTrue(cancelled.json()['cancelled'])
        self.assertEqual(cancelled.json()['confirmed_bookings'], 0)

        self.assertEqual(self.run_db(offered), [])
        self.assertEqual(self.run_db(book), 'This event has been cancelled.')
        # Staff still see it, marked as cancelled; the row was not deleted.
        listed = self.client.get('/api/v1/admin/events?q=gala', headers=headers).json()['items']
        self.assertEqual([(item['title'], item['cancelled']) for item in listed], [('Spring Gala', True)])
        # Editing the event does not silently bring it back.
        self.client.put(f'/api/v1/admin/events/{event_id}', json={**EVENT, 'location': 'Tokyo'}, headers=headers)
        self.assertEqual(self.run_db(offered), [])

        restored = self.client.patch(f'/api/v1/admin/events/{event_id}/status', json={'cancelled': False}, headers=headers)
        self.assertFalse(restored.json()['cancelled'])
        self.assertEqual(self.run_db(offered), ['Spring Gala'])
        self.assertEqual(self.run_db(book), 'booked')

    def test_cancelling_reports_existing_bookings_and_keeps_them(self):
        headers = self.staff_headers()
        event_id = self.client.post('/api/v1/admin/events', json=EVENT, headers=headers).json()['event_id']

        async def book(db):
            user = models.User(name='Kenji', email='kenji@example.com')
            db.add(user)
            await db.commit()
            await db.refresh(user)
            await crud.book_event(db, user.user_id, event_id, [{'name': 'Kenji', 'email': 'kenji@example.com'}])
        self.run_db(book)
        cancelled = self.client.patch(f'/api/v1/admin/events/{event_id}/status', json={'cancelled': True}, headers=headers)
        self.assertEqual(cancelled.json()['confirmed_bookings'], 1)

        async def bookings(db):
            return len((await db.exec(select(models.Booking))).all())
        self.assertEqual(self.run_db(bookings), 1)

    def test_agent_is_told_a_cancelled_event_cannot_be_booked(self):
        event = SimpleNamespace(cancelled=True, start_at=None, end_at=None, reg_deadline=None)
        self.assertEqual(tools._registration_problem(event), 'the event has been cancelled by CCIFJ')
        self.assertIn('cancelled', tools._registration_summary(event))

    def test_member_company_can_be_hidden_and_shown_again(self):
        headers = self.staff_headers()

        async def seed(db):
            member = models.Member(name='Agri Co', sector='Food', region='Osaka')
            db.add(member)
            await db.commit()
            await db.refresh(member)
            return member.member_id
        member_id = self.run_db(seed)

        def listed():
            return self.client.get('/api/v1/admin/members', headers=headers).json()['items'][0]['directory_visible']

        self.assertTrue(listed())
        hidden = self.client.patch(f'/api/v1/admin/members/{member_id}', json={'directory_visible': False}, headers=headers)
        self.assertEqual(hidden.json(), {'member_id': member_id, 'directory_visible': False})
        self.assertFalse(listed())  # still in the staff list, marked hidden: not deleted

        async def visible_to_agent(db):
            return [m.name for m in await crud.search_members(db, limit=50) if m.directory_visible is not False]
        self.assertEqual(self.run_db(visible_to_agent), [])
        shown = self.client.patch(f'/api/v1/admin/members/{member_id}', json={'directory_visible': True}, headers=headers)
        self.assertTrue(shown.json()['directory_visible'])
        self.assertEqual(self.run_db(visible_to_agent), ['Agri Co'])

    def test_unknown_ids_and_non_staff_are_refused(self):
        headers = self.staff_headers()
        self.assertEqual(self.client.patch('/api/v1/admin/events/999/status', json={'cancelled': True}, headers=headers).status_code, 404)
        self.assertEqual(self.client.patch('/api/v1/admin/members/999', json={'directory_visible': False}, headers=headers).status_code, 404)
        for path, body in (('/api/v1/admin/events/1/status', {'cancelled': True}), ('/api/v1/admin/members/1', {'directory_visible': False})):
            self.assertEqual(self.client.patch(path, json=body).status_code, 401)


if __name__ == '__main__':
    unittest.main()
