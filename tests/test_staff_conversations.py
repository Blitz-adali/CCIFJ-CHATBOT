"""Staff conversation view: which chats are kept, who may read them and what they show."""
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_api_contract as contract
from concierge.api import staff
from concierge.auth.security import read_token
from concierge.config import get_settings
from concierge.db.relationaldb import crud, models
from sqlmodel.ext.asyncio.session import AsyncSession


class StaffConversationTest(unittest.TestCase):
    tearDown = contract.ApiContractTest.tearDown

    def setUp(self):
        contract.ApiContractTest.setUp(self)
        self.client.app.include_router(staff.router, prefix='/api/v1')

    def account(self, name, email):
        result = self.client.post('/api/v1/auth/register', json={'name': name, 'email': email, 'password': 'test-password'})
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()['user'], {'Authorization': 'Bearer ' + result.json()['access_token']}

    def as_staff(self, user):
        settings = replace(get_settings(), staff_user_ids=(user['user_id'],))
        started = patch('concierge.auth.access.get_settings', return_value=settings)
        started.start()
        self.addCleanup(started.stop)

    def chat(self, message, headers=None):
        result = self.client.post('/api/v1/chat', json={'message': message}, headers=headers or {})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()['session_id']

    def add_request(self, token, subject='Market entry support'):
        async def seed():
            async with AsyncSession(self.engine, expire_on_commit=False) as db:
                request = await crud.add_staff_request(
                    db, request_type='team_ticket', category='business', subject=subject,
                    message='Please call me back.', sender_name='Guest', sender_email='guest@example.com',
                    session_id=read_token(token, 'session'))
                return request.reference
        return self.client.portal.call(seed)

    def test_guest_chat_with_a_request_is_kept_and_others_are_deleted(self):
        kept = self.chat('Can you help my company find customers in Japan?')
        dropped = self.chat('What time is it?')
        reference = self.add_request(kept)
        for token in (kept, dropped):
            self.assertEqual(self.client.post(f'/api/v1/sessions/{token}/discard').status_code, 204)

        operator, headers = self.account('Operator', 'operator@example.com')
        self.as_staff(operator)
        listing = self.client.get('/api/v1/staff/conversations', headers=headers)
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual(listing.headers['cache-control'], 'no-store')
        items = listing.json()['items']
        self.assertEqual([item['conversation_id'] for item in items], [read_token(kept, 'session')])
        self.assertEqual(items[0]['preview'], 'Can you help my company find customers in Japan?')
        self.assertTrue(items[0]['visitor']['guest'])
        self.assertEqual(items[0]['requests'][0]['reference'], reference)

        # The request in the inbox links to the conversation it came from.
        request = self.client.get('/api/v1/staff/requests', headers=headers).json()['items'][0]
        self.assertEqual(request['conversation_id'], items[0]['conversation_id'])
        gone = self.client.get(f"/api/v1/staff/conversations/{read_token(dropped, 'session')}", headers=headers)
        self.assertEqual(gone.status_code, 404)

    def test_transcript_shows_visitor_and_agent_messages_only(self):
        member, member_headers = self.account('Aiko Tanaka', 'aiko@example.com')
        token = self.chat('Which membership fits a start-up?', member_headers)
        conversation_id = read_token(token, 'session')

        async def add_tool_message():
            async with AsyncSession(self.engine, expire_on_commit=False) as db:
                await crud.add_message(db, conversation_id, models.MessageRole.tool, 'internal tool output')
        self.client.portal.call(add_tool_message)

        operator, headers = self.account('Operator', 'operator@example.com')
        self.as_staff(operator)
        detail = self.client.get(f'/api/v1/staff/conversations/{conversation_id}', headers=headers)
        self.assertEqual(detail.status_code, 200, detail.text)
        body = detail.json()
        self.assertEqual(body['visitor'], {'name': 'Aiko Tanaka', 'email': 'aiko@example.com', 'guest': False})
        self.assertEqual([m['role'] for m in body['messages']], ['user', 'assistant'])
        self.assertEqual(body['messages'][0]['content'], 'Which membership fits a start-up?')
        self.assertNotIn('internal tool output', detail.text)

        listed = self.client.get('/api/v1/staff/conversations', headers=headers).json()['items']
        self.assertEqual((listed[0]['message_count'], listed[0]['visitor']['name']), (2, 'Aiko Tanaka'))
        self.assertEqual(self.client.get('/api/v1/staff/conversations?with_request=true', headers=headers).json()['items'], [])

    def test_conversations_are_staff_only(self):
        token = self.chat('Hello')
        conversation_id = read_token(token, 'session')
        _, headers = self.account('Visitor', 'visitor@example.com')
        for path in ('/api/v1/staff/conversations', f'/api/v1/staff/conversations/{conversation_id}'):
            self.assertEqual(self.client.get(path).status_code, 401)
            self.assertEqual(self.client.get(path, headers=headers).status_code, 403)


if __name__ == '__main__':
    unittest.main()
