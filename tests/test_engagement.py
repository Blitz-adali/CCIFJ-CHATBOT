"""Onboarding and engagement rules, checked with a fixed clock (`as_of`).

Matching is mocked: these tests prove the rules (who gets what, when, and never twice),
not the embeddings.
"""
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import test_api_contract as contract
from concierge import engagement, matching
from concierge.api import admin_automation, me
from concierge.config import get_settings
from concierge.db.relationaldb import models
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

NOW = datetime(2027, 3, 1, 9, 0)


def event_match(event_id, title, score=0.9):
    return matching.Match(event_id, title, score, '', {'category': 'Networking', 'location': 'Tokyo',
                                                       'start_at': (NOW + timedelta(days=10)).isoformat(), 'reg_deadline': None})


class EngagementRulesTest(unittest.TestCase):
    setUp = contract.ApiContractTest.setUp
    tearDown = contract.ApiContractTest.tearDown

    def db(self, work):
        async def call():
            async with AsyncSession(self.engine, expire_on_commit=False) as session:
                return await work(session)
        return self.client.portal.call(call)

    def member(self, name, joined_days_ago=None, active_days_ago=None, goals=None, interests=None, completed=False):
        async def create(session):
            user = models.User(name=name, email=f"{name.lower()}@demo.test", created_at=NOW - timedelta(days=400))
            company = models.Member(name=f"{name} SAS", sector='Food & Beverage')
            session.add_all([user, company])
            await session.flush()
            session.add(models.Belongs(user_id=user.user_id, member_id=company.member_id))
            session.add(models.MemberProfile(
                user_id=user.user_id, goals=goals, interests=interests,
                joined_at=NOW - timedelta(days=joined_days_ago) if joined_days_ago is not None else None,
                profile_completed_at=NOW - timedelta(days=1) if completed else None))
            if active_days_ago is not None:
                session.add(models.ActivityEvent(user_id=user.user_id, kind='chat', created_at=NOW - timedelta(days=active_days_ago)))
            await session.commit()
            return user.user_id
        return self.db(create)

    def run_job(self, as_of=NOW):
        with patch.object(matching, 'match_events', AsyncMock(return_value=[])), \
                patch.object(matching, 'match_members', AsyncMock(return_value=[])):
            return self.db(lambda session: engagement.run_engagement(session, as_of))

    def fired(self, result):
        return sorted((item['user_id'], item['rule']) for item in result['items'])

    def test_each_rule_fires_at_the_right_time_and_never_twice(self):
        aiko = self.member('Aiko', joined_days_ago=0)
        mina = self.member('Mina', joined_days_ago=4, active_days_ago=1)
        pierre = self.member('Pierre', joined_days_ago=7, active_days_ago=1, goals='find_clients', completed=True)
        sato = self.member('Sato', joined_days_ago=14, active_days_ago=2, completed=True)
        claire = self.member('Claire', active_days_ago=30)
        kenji = self.member('Kenji', active_days_ago=65)
        marc = self.member('Marc', active_days_ago=95)

        first = self.run_job()
        fired = self.fired(first)
        self.assertIn((aiko, 'welcome'), fired)
        self.assertIn((mina, 'profile_reminder'), fired)
        self.assertNotIn((pierre, 'profile_reminder'), fired)  # profile completed
        self.assertIn((pierre, 'first_steps'), fired)
        self.assertIn((sato, 'meet_members'), fired)
        self.assertFalse([rule for user, rule in fired if user == claire])  # active 30 days ago: nothing
        self.assertIn((kenji, 'inactive_60'), fired)
        self.assertNotIn((kenji, 'staff_call_90'), fired)
        self.assertIn((marc, 'staff_call_90'), fired)

        again = self.run_job()
        self.assertEqual(again['created'], 0)  # the proof that nothing is sent twice
        self.assertEqual(again['duplicates'], first['created'])

    def test_approval_rules_wait_and_staff_tasks_reach_the_inbox(self):
        kenji = self.member('Kenji', active_days_ago=65)
        marc = self.member('Marc', active_days_ago=95)
        self.run_job()
        rows = self.db(lambda s: _all(s, models.Outreach))
        status = {(row.user_id, row.rule_key): row.status for row in rows}
        self.assertEqual(status[(kenji, 'inactive_60')], models.OutreachStatus.pending)  # needs approval by default
        self.assertEqual(status[(marc, 'staff_call_90')], models.OutreachStatus.sent)
        requests = self.db(lambda s: _all(s, models.StaffRequest))
        self.assertEqual(len(requests), 1)
        self.assertIn('Re-engagement call', requests[0].subject)
        self.assertTrue(requests[0].reference.startswith('SR-'))

    def test_simulated_future_run_saves_and_sends_nothing(self):
        self.member('Kenji', active_days_ago=65)
        with patch.object(matching, 'match_events', AsyncMock(return_value=[])),                 patch.object(matching, 'match_members', AsyncMock(return_value=[])):
            preview = self.db(lambda s: engagement.run_engagement(s, NOW + timedelta(days=30), dry_run=True))
        self.assertEqual([item['rule'] for item in preview['items']], ['inactive_60', 'staff_call_90'])
        self.assertEqual(self.db(lambda s: _all(s, models.Outreach)), [])
        self.assertEqual(self.db(lambda s: _all(s, models.StaffRequest)), [])
        self.assertEqual(self.db(lambda s: _all(s, models.AutomationRun)), [])

    def test_disabled_rule_fires_for_nobody_and_inactivity_follows_the_clock(self):
        aiko = self.member('Aiko', joined_days_ago=0)
        self.db(lambda s: _disable(s, 'welcome'))
        self.assertEqual(self.run_job()['created'], 0)
        later = self.run_job(NOW + timedelta(days=61))  # simulated date: Aiko never came back
        self.assertIn((aiko, 'inactive_60'), self.fired(later))

    def test_new_event_matches_only_interested_members(self):
        wine = self.member('Pierre', active_days_ago=1, interests='wine import and distribution', completed=True)
        hr = self.member('Yuki', active_days_ago=1, interests='HR and recruitment', completed=True)

        async def add_event(session):
            event = models.Event(title='HR Trends 2027', description='Recruitment', category='Conference',
                                 start_at=NOW + timedelta(days=10), created_at=NOW - timedelta(days=1))
            session.add(event)
            await session.commit()
            return event.event_id
        event_id = self.db(add_event)

        async def events_for(session, text, limit=5):
            return [event_match(event_id, 'HR Trends 2027')] if 'recruitment' in text.lower() else []
        with patch.object(matching, 'match_events', side_effect=events_for), \
                patch.object(matching, 'match_members', AsyncMock(return_value=[])):
            result = self.db(lambda s: engagement.run_engagement(s, NOW))
        self.assertEqual(self.fired(result), [(hr, 'event_match')])
        self.assertNotIn(wine, [item['user_id'] for item in result['items']])


class OnboardingApiTest(unittest.TestCase):
    setUp = contract.ApiContractTest.setUp
    tearDown = contract.ApiContractTest.tearDown

    def account(self, email):
        result = self.client.post('/api/v1/auth/register', json={'name': email.split('@')[0], 'email': email, 'password': 'test-password'})
        return result.json()['user'], {'Authorization': 'Bearer ' + result.json()['access_token']}

    def test_approval_welcomes_member_who_then_completes_profile(self):
        for router in (admin_automation.router, me.router):
            self.client.app.include_router(router, prefix='/api/v1')
        boss, boss_headers = self.account('boss@example.com')
        new, new_headers = self.account('new@example.com')

        async def company(session):
            member = models.Member(name='Fresh Foods KK')
            session.add(member)
            await session.commit()
            return member.member_id
        member_id = self.client.portal.call(_with_session, self.engine, company)
        settings = replace(get_settings(), staff_user_ids=(boss['user_id'],))
        with patch('concierge.auth.access.get_settings', return_value=settings):
            approved = self.client.post(f'/api/v1/admin/members/{member_id}/accounts', json={'email': 'NEW@example.com'}, headers=boss_headers)
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual([item['rule'] for item in approved.json()['welcome']], ['welcome'])

        inbox = self.client.get('/api/v1/me/notifications', headers=new_headers).json()
        self.assertEqual(len(inbox), 1)
        self.assertIn('Welcome to CCIFJ', inbox[0]['subject'])
        self.assertEqual(self.client.post(f"/api/v1/me/notifications/{inbox[0]['id']}/read", headers=new_headers).status_code, 204)

        profile = self.client.get('/api/v1/me/profile', headers=new_headers).json()
        self.assertEqual((profile['access_level'], profile['profile_completed']), ('member', False))
        self.assertEqual(self.client.put('/api/v1/me/profile', json={'goals': ['teleport']}, headers=new_headers).status_code, 422)
        saved = self.client.put('/api/v1/me/profile', json={'goals': ['find_clients', 'network'], 'interests': 'cheese import',
                                                            'preferred_language': 'fr', 'preferred_channel': 'line'}, headers=new_headers)
        self.assertEqual(saved.status_code, 200)
        profile = self.client.get('/api/v1/me/profile', headers=new_headers).json()
        self.assertEqual((profile['goals'], profile['preferred_channel'], profile['profile_completed']), (['find_clients', 'network'], 'line', True))


async def _with_session(engine, work):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        return await work(session)


async def _all(session, model):
    return list((await session.exec(select(model))).all())


async def _disable(session, key):
    settings = await engagement.rule_settings(session)
    settings[key].enabled = False
    await session.commit()


if __name__ == '__main__':
    unittest.main()
