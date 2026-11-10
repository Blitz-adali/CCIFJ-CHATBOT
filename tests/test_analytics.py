"""Agent analytics: usage numbers from stored replies, staff-only access, LangSmith status."""
import json
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

import test_api_contract as contract
from concierge import analytics
from concierge.api import admin_analytics
from concierge.config import get_settings
from concierge.db.relationaldb import models
from sqlmodel.ext.asyncio.session import AsyncSession


def reply(intent, sources=(), warnings=()):
    return [{"type": "api_response", "response": {"intent": {"code": intent} if intent else None,
                                                  "sources": list(sources), "warnings": list(warnings)}}]


class AnalyticsTest(unittest.TestCase):
    setUp = contract.ApiContractTest.setUp
    tearDown = contract.ApiContractTest.tearDown

    def test_summary_counts_questions_topics_quality_and_response_time(self):
        async def seed(session):
            now = models.utcnow()
            member = models.User(name='M', email='m@example.com')
            company = models.Member(name='Co')
            session.add_all([member, company])
            await session.flush()
            session.add(models.Belongs(user_id=member.user_id, member_id=company.member_id))
            guest_chat, member_chat = models.ChatSession(user_id=None), models.ChatSession(user_id=member.user_id)
            session.add_all([guest_chat, member_chat])
            await session.flush()
            for hour, (chat, intent, delay, sources) in enumerate(((guest_chat, 'events', 10, ['x']), (member_chat, 'membership, events', 20, []),
                                                                   (guest_chat, None, 30, [])), start=1):
                asked = now - timedelta(hours=hour)
                session.add(models.Message(session_id=chat.session_id, role=models.MessageRole.user, content='q', created_at=asked))
                session.add(models.Message(session_id=chat.session_id, role=models.MessageRole.assistant, content='a',
                                           created_at=asked + timedelta(seconds=delay), tool_calls=reply(intent, sources)))
            await session.commit()

        async def run():
            async with AsyncSession(self.engine, expire_on_commit=False) as session:
                await seed(session)
                return await analytics.summary(session, 7)
        data = self.client.portal.call(run)
        self.assertEqual(data['totals'], {'questions': 3, 'conversations': 2, 'replies': 3})
        self.assertEqual(data['audience'], {'guest': 2, 'user': 0, 'member': 1, 'staff': 0})
        self.assertEqual({t['topic']: t['count'] for t in data['topics']},
                         {'Events': 2, 'Membership & directory': 1, 'Out of scope / clarification': 1})
        self.assertEqual(data['quality']['answers_with_sources'], 33)
        self.assertEqual(data['quality']['median_response_seconds'], 20.0)
        self.assertEqual(sum(d['questions'] for d in data['daily']), 3)
        self.assertEqual(len(data['daily']), 7)

    def test_routes_are_staff_only_and_langsmith_reports_missing_key(self):
        self.client.app.include_router(admin_analytics.router, prefix='/api/v1')
        result = self.client.post('/api/v1/auth/register', json={'name': 'B', 'email': 'b@example.com', 'password': 'test-password'})
        headers = {'Authorization': 'Bearer ' + result.json()['access_token']}
        self.assertEqual(self.client.get('/api/v1/admin/analytics', headers=headers).status_code, 403)
        settings = replace(get_settings(), staff_user_ids=(result.json()['user']['user_id'],))
        analytics._cache.clear()
        with patch('concierge.auth.access.get_settings', return_value=settings), \
                patch.dict('os.environ', {'LANGSMITH_API_KEY': '', 'LANGSMITH_TRACING': 'false'}):
            self.assertEqual(self.client.get('/api/v1/admin/analytics?days=7', headers=headers).json()['totals']['questions'], 0)
            status = self.client.get('/api/v1/admin/analytics/langsmith', headers=headers).json()
        self.assertEqual(status['status'], 'off')


if __name__ == '__main__':
    unittest.main()
