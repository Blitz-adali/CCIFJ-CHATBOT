"""Matching: what each access level sees, introduction requests, and the staff queue.

The vector search is mocked; these tests check the rules around it, not embeddings.
"""
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import test_api_contract as contract
from concierge import matching
from concierge.agents import tools
from concierge.api import admin_matching
from concierge.config import get_settings
from concierge.db.relationaldb import models
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession


def runtime(user_id):
    return SimpleNamespace(state={'user_id': user_id})


MATCH = matching.Match(7, 'Agri Distribution KK', 0.86, 'Distributes French cheese to supermarkets.',
                       {'sector': 'Food & Beverage', 'region': 'Minato-ku', 'country': 'Japan',
                        'description': 'Distributes French cheese to supermarkets.', 'contact': 'info@agri.example'})


class WhyExcerptTest(unittest.TestCase):
    def test_prefers_prose_and_skips_directory_field_lines(self):
        description = 'Sector: Food & Beverage\nWebsite: https://x.example\nWe import French wine and cheese for Japanese retail chains.'
        self.assertEqual(matching.why_excerpt('wine distribution retail', description),
                         'We import French wine and cheese for Japanese retail chains.')
        self.assertEqual(matching.why_excerpt('anything', 'Sector: IT\nListed location: Tokyo'), '')


class MatchingVisibilityTest(unittest.IsolatedAsyncioTestCase):
    async def call(self, level, **args):
        with patch.object(tools, '_access_level', AsyncMock(return_value=level)), \
                patch.object(matching, 'match_members', AsyncMock(return_value=[MATCH])), \
                patch.object(matching, 'count_relevant', AsyncMock(return_value=(4, {'Food & Beverage': 3, 'Retail': 1}))):
            return await tools.find_matching_members.coroutine(need='agrifood distribution in Japan', runtime=None, **args)

    async def test_names_only_for_members_and_staff(self):
        guest, user = await self.call('guest'), await self.call('user')
        member, staff = await self.call('member'), await self.call('staff')
        for text in (guest, user):
            self.assertNotIn('Agri Distribution', text)
            self.assertIn('members only', text)
        self.assertIn('About 4', guest)
        self.assertNotIn('Food & Beverage', guest)  # guests: counts only
        self.assertIn('Food & Beverage (3)', user)  # non-members: sectors, no names
        for text in (member, staff):
            self.assertIn('Agri Distribution KK', text)
            self.assertIn('Distributes French cheese', text)
        self.assertIn('request_introduction', member)

    async def test_directory_hides_names_from_guests(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def db():
            yield None
        company = SimpleNamespace(name='Secret Foods KK', sector='Food & Beverage', region='Tokyo', country='Japan', description='')
        with patch.object(tools, 'AsyncSessionLocal', db), \
                patch.object(tools.crud, 'search_members', AsyncMock(return_value=[company])), \
                patch.object(tools, '_access_level', AsyncMock(return_value='guest')):
            text = await tools.search_member_directory.ainvoke({'industry': 'Food & Beverage'})
        self.assertNotIn('Secret Foods', text)
        self.assertIn('1 member companies match', text)


class IntroductionFlowTest(unittest.TestCase):
    setUp = contract.ApiContractTest.setUp
    tearDown = contract.ApiContractTest.tearDown

    def run_db(self, work):
        async def call():
            async with AsyncSession(self.engine, expire_on_commit=False) as db:
                return await work(db)
        return self.client.portal.call(call)

    def account(self, email):
        result = self.client.post('/api/v1/auth/register', json={'name': email.split('@')[0], 'email': email, 'password': 'test-password'})
        return result.json()['user'], {'Authorization': 'Bearer ' + result.json()['access_token']}

    def test_member_request_reaches_staff_queue_and_non_members_are_refused(self):
        member_user, _ = self.account('member@example.com')
        outsider, _ = self.account('outsider@example.com')
        boss, boss_headers = self.account('boss@example.com')

        async def seed(db):
            own, target = models.Member(name='Requester SAS'), models.Member(name='Agri Distribution KK', sector='Food & Beverage')
            db.add_all([own, target])
            await db.commit()
            db.add(models.Belongs(user_id=member_user['user_id'], member_id=own.member_id))
            await db.commit()
        self.run_db(seed)
        sessions = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

        async def ask(user_id, names):
            with patch.object(tools, 'AsyncSessionLocal', sessions):
                return await tools.request_introduction.coroutine(company_names=names, message='We look for a distributor.', runtime=runtime(user_id))
        refused = self.client.portal.call(ask, outsider['user_id'], ['Agri Distribution KK'])
        self.assertIn('Only CCIFJ members', refused)
        stored = self.client.portal.call(ask, member_user['user_id'], ['agri distribution kk', 'Requester SAS', 'Nope Ltd'])
        self.assertIn('Agri Distribution KK: reference IR-', stored)
        self.assertIn('Requester SAS, Nope Ltd', stored)  # own company and unknown names are not requested

        self.client.app.include_router(admin_matching.router, prefix='/api/v1')
        self.assertEqual(self.client.get('/api/v1/admin/intro-requests', headers=boss_headers).status_code, 403)
        settings = replace(get_settings(), staff_user_ids=(boss['user_id'],))
        with patch('concierge.auth.access.get_settings', return_value=settings):
            queue = self.client.get('/api/v1/admin/intro-requests', headers=boss_headers).json()['items']
            self.assertEqual(len(queue), 1)
            self.assertEqual((queue[0]['requester_company']['name'], queue[0]['target']['name']), ('Requester SAS', 'Agri Distribution KK'))
            update = self.client.patch(f"/api/v1/admin/intro-requests/{queue[0]['intro_id']}", json={'status': 'proposed', 'staff_note': 'Called Agri on Monday'}, headers=boss_headers)
            self.assertEqual(update.json()['status'], 'proposed')
        rows = self.run_db(lambda db: _all(db))
        self.assertEqual(rows[0].staff_note, 'Called Agri on Monday')


async def _all(db):
    return list((await db.exec(select(models.IntroRequest))).all())


if __name__ == '__main__':
    unittest.main()
