"""Admin dashboard API: staff-only access, roles, member detection and the staff directory."""
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_api_contract as contract
from concierge.api import admin, staff
from concierge.config import get_settings
from concierge.db.relationaldb import models
from concierge.staff_directory import describe_contacts
from sqlmodel.ext.asyncio.session import AsyncSession


class AdminDashboardTest(unittest.TestCase):
    setUp = contract.ApiContractTest.setUp
    tearDown = contract.ApiContractTest.tearDown

    def account(self, email):
        result = self.client.post('/api/v1/auth/register', json={'name': email.split('@')[0], 'email': email, 'password': 'test-password'})
        self.assertEqual(result.status_code, 201)
        return result.json()['user'], {'Authorization': 'Bearer ' + result.json()['access_token']}

    def run_db(self, work):
        async def call():
            async with AsyncSession(self.engine, expire_on_commit=False) as db:
                return await work(db)
        return self.client.portal.call(call)

    def as_bootstrap_staff(self, user):
        settings = replace(get_settings(), staff_user_ids=(user['user_id'],))
        return patch('concierge.auth.access.get_settings', return_value=settings)

    def test_dashboard_is_staff_only_and_staff_can_grant_staff(self):
        self.client.app.include_router(admin.router, prefix='/api/v1')
        boss, boss_headers = self.account('boss@example.com')
        other, other_headers = self.account('other@example.com')
        self.assertEqual(other['access_level'], 'user')
        self.assertEqual(self.client.get('/api/v1/admin/overview').status_code, 401)
        self.assertEqual(self.client.get('/api/v1/admin/overview', headers=other_headers).status_code, 403)
        with self.as_bootstrap_staff(boss):
            self.assertEqual(self.client.get('/api/v1/auth/me', headers=boss_headers).json()['access_level'], 'staff')
            self.assertEqual(self.client.get('/api/v1/admin/overview', headers=boss_headers).status_code, 200)
            # Staff cannot remove their own access (no lock-out), but can promote others.
            self.assertEqual(self.client.patch(f"/api/v1/admin/users/{boss['user_id']}", json={'role': 'user'}, headers=boss_headers).status_code, 400)
            granted = self.client.patch(f"/api/v1/admin/users/{other['user_id']}", json={'role': 'staff'}, headers=boss_headers)
            self.assertEqual(granted.json()['access_level'], 'staff')
        # The promoted account is staff through its role, without the bootstrap list.
        self.assertEqual(self.client.get('/api/v1/admin/overview', headers=other_headers).status_code, 200)

    def test_member_access_level_comes_from_company_link(self):
        user, headers = self.account('member@example.com')

        async def link(db):
            company = models.Member(name='Agri Co', sector='Food & Beverage')
            db.add(company)
            await db.commit()
            db.add(models.Belongs(user_id=user['user_id'], member_id=company.member_id))
            await db.commit()
        self.run_db(link)
        self.assertEqual(self.client.get('/api/v1/auth/me', headers=headers).json()['access_level'], 'member')

    def test_staff_directory_drives_the_agent_contact_lookup(self):
        self.client.app.include_router(admin.router, prefix='/api/v1')
        self.client.app.include_router(staff.router, prefix='/api/v1')
        boss, headers = self.account('boss@example.com')
        with self.as_bootstrap_staff(boss):
            self.assertEqual(self.client.post('/api/v1/admin/staff-contacts', json={'email': 'x@ccifj.or.jp', 'category': 'Events', 'topics': ['nope']}, headers=headers).status_code, 422)
            created = self.client.post('/api/v1/admin/staff-contacts', json={
                'name': 'Aiko TEST', 'role': 'Front desk', 'email': 'A.Test@ccifj.or.jp', 'category': 'General',
                'topics': ['events'], 'is_triage': True}, headers=headers)
            self.assertEqual(created.status_code, 201, created.text)
            contact = created.json()
            self.assertEqual(contact['email'], 'a.test@ccifj.or.jp')
            self.assertEqual(self.client.post('/api/v1/admin/staff-contacts', json={'email': 'a.test@ccifj.or.jp', 'category': 'X'}, headers=headers).status_code, 409)

        events = self.run_db(lambda db: describe_contacts(db, 'events'))
        self.assertIn('- Email: a.test@ccifj.or.jp', events)
        # Nobody covers membership, so the triage contact is returned.
        fallback = self.run_db(lambda db: describe_contacts(db, 'membership'))
        self.assertIn('triage', fallback)
        self.assertIn('a.test@ccifj.or.jp', fallback)

        with self.as_bootstrap_staff(boss):
            self.client.patch(f"/api/v1/admin/staff-contacts/{contact['contact_id']}", json={'is_active': False}, headers=headers)
        self.assertIn('team_ticket', self.run_db(lambda db: describe_contacts(db, 'events')))


if __name__ == '__main__':
    unittest.main()
