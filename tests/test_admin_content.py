"""Admin content API: events, member/event imports and knowledge uploads (indexing mocked)."""
import base64
import io
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_api_contract as contract
from concierge.api import admin_content
from concierge.config import get_settings
from concierge.db.relationaldb import models
from concierge.imports import read_table, validate_event
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession


def upload(name, data: bytes, mime=''):
    return {'name': name, 'mime_type': mime, 'data_base64': base64.b64encode(data).decode()}


class ImportParsingTest(unittest.TestCase):
    def test_xlsx_and_csv_rows_use_normalised_headers(self):
        import openpyxl
        book = openpyxl.Workbook()
        book.active.append(['Name', 'Sector', 'Manpower'])
        book.active.append(['Agri Co', 'Food & Beverage', 12])
        buffer = io.BytesIO()
        book.save(buffer)
        self.assertEqual(read_table(buffer.getvalue(), 'm.xlsx'), [{'name': 'Agri Co', 'sector': 'Food & Beverage', 'manpower': 12}])
        self.assertEqual(read_table('﻿Name,Sector\nAgri Co,Food\n'.encode(), 'm.csv'), [{'name': 'Agri Co', 'sector': 'Food'}])

    def test_event_validation_reports_every_problem(self):
        clean, errors = validate_event({'title': 'Gala', 'description': 'x', 'category': 'networking',
                                        'start_at': '2027-03-12 18:30', 'end_at': '2027-03-12 17:00',
                                        'member_price': 'abc', 'languages': 'en, French, klingon'})
        self.assertEqual(clean['category'], 'Networking')
        self.assertEqual(clean['languages'], ['en', 'fr'])
        self.assertTrue(any('end_at' in e for e in errors))
        self.assertTrue(any('member_price' in e for e in errors))
        self.assertTrue(any('klingon' in e for e in errors))


class AdminContentApiTest(unittest.TestCase):
    setUp = contract.ApiContractTest.setUp
    tearDown = contract.ApiContractTest.tearDown

    def staff_headers(self):
        result = self.client.post('/api/v1/auth/register', json={'name': 'Boss', 'email': 'boss@example.com', 'password': 'test-password'})
        user = result.json()['user']
        settings = replace(get_settings(), staff_user_ids=(user['user_id'],))
        self.staff_patch = patch('concierge.auth.access.get_settings', return_value=settings)
        self.staff_patch.start()
        self.addCleanup(self.staff_patch.stop)
        self.client.app.include_router(admin_content.router, prefix='/api/v1')
        return {'Authorization': 'Bearer ' + result.json()['access_token']}

    def run_db(self, work):
        async def call():
            async with AsyncSession(self.engine, expire_on_commit=False) as db:
                return await work(db)
        return self.client.portal.call(call)

    def test_add_and_edit_one_event(self):
        headers = self.staff_headers()
        body = {'title': 'Spring Gala', 'description': 'Members evening', 'category': 'Networking',
                'start_at': '2099-04-01 18:30', 'member_price': 5000, 'languages': ['en', 'ja']}
        created = self.client.post('/api/v1/admin/events', json=body, headers=headers)
        self.assertEqual(created.status_code, 201, created.text)
        event = created.json()
        self.assertEqual(event['currency'], 'JPY')
        self.assertEqual(event['languages'], ['en', 'ja'])
        self.assertEqual(self.client.post('/api/v1/admin/events', json=body, headers=headers).status_code, 409)
        bad = self.client.post('/api/v1/admin/events', json={**body, 'title': 'X', 'category': 'Party'}, headers=headers)
        self.assertEqual(bad.status_code, 422)
        updated = self.client.put(f"/api/v1/admin/events/{event['event_id']}", json={**body, 'location': 'Tokyo', 'languages': ['fr']}, headers=headers)
        self.assertEqual(updated.json()['location'], 'Tokyo')
        self.assertEqual(updated.json()['languages'], ['fr'])
        listed = self.client.get('/api/v1/admin/events?q=gala', headers=headers).json()
        self.assertEqual([item['title'] for item in listed['items']], ['Spring Gala'])

    def test_member_import_previews_then_commits_valid_rows_and_updates_existing(self):
        headers = self.staff_headers()

        async def seed(db):
            db.add(models.MemberCategory(category_name='Active', category_description='Active members', annual_price=100000))
            db.add(models.Member(name='Agri Co', sector='Food', region='Osaka'))
            await db.commit()
        self.run_db(seed)
        csv_file = ('name,sector,category,region,manpower\n'
                    'agri co,Food & Beverage,Active,,30\n'
                    'New Wine KK,Wine and Spirits,Unknown,Minato-ku,5\n'
                    'Cheese SAS,Food & Beverage,,Lyon,abc\n'
                    'Bread KK,Food & Beverage,active,Kyoto,4\n').encode()
        preview = self.client.post('/api/v1/admin/import/members', json=upload('members.csv', csv_file), headers=headers).json()
        self.assertEqual(preview['counts'], {'create': 1, 'update': 1, 'error': 2})
        self.assertEqual(self.run_db(lambda db: _count(db, models.Member)), 1)  # preview writes nothing
        result = self.client.post('/api/v1/admin/import/members?commit=true', json=upload('members.csv', csv_file), headers=headers).json()
        self.assertTrue(result['committed'])
        members = self.run_db(lambda db: _all(db, models.Member))
        self.assertEqual(sorted(m.name for m in members), ['Agri Co', 'Bread KK'])
        agri = next(m for m in members if m.name == 'Agri Co')
        self.assertEqual((agri.sector, agri.region, agri.manpower), ('Food & Beverage', 'Osaka', 30))  # empty cell kept Osaka

    def test_event_import_and_template(self):
        headers = self.staff_headers()
        template = self.client.get('/api/v1/admin/import/events/template', headers=headers)
        self.assertTrue(template.text.startswith('title,description,category,start_at'))
        result = self.client.post('/api/v1/admin/import/events?commit=true', json=upload('events.csv', template.text.encode()), headers=headers).json()
        self.assertEqual(result['counts']['create'], 1)
        again = self.client.post('/api/v1/admin/import/events', json=upload('events.csv', template.text.encode()), headers=headers).json()
        self.assertEqual(again['counts'], {'create': 0, 'update': 1, 'error': 0})

    def test_knowledge_upload_list_and_delete(self):
        headers = self.staff_headers()
        text = b'CCIFJ domiciliation service: a business address in Tokyo for 30,000 JPY per month.'
        with patch('concierge.knowledge.index_document', return_value=1) as index, \
                patch('concierge.knowledge.remove_document') as remove:
            created = self.client.post('/api/v1/admin/knowledge', json={**upload('domiciliation.txt', text, 'text/plain'), 'title': 'Domiciliation'}, headers=headers)
            self.assertEqual(created.status_code, 201, created.text)
            doc = created.json()
            doc_id, title, url, indexed_text = index.call_args.args
            self.assertEqual((title, url), ('Domiciliation', f'ccifj-doc://{doc_id}'))
            self.assertIn('domiciliation', indexed_text)
            self.assertEqual(self.client.post('/api/v1/admin/knowledge', json=upload('copy.txt', text, 'text/plain'), headers=headers).status_code, 409)
            self.assertEqual(self.client.post('/api/v1/admin/knowledge', json={**upload('x.txt', b'other', 'text/plain'), 'source_url': 'http://insecure'}, headers=headers).status_code, 422)
            self.assertEqual(len(self.client.get('/api/v1/admin/knowledge', headers=headers).json()), 1)
            self.assertEqual(self.client.delete(f"/api/v1/admin/knowledge/{doc['doc_id']}", headers=headers).status_code, 204)
            remove.assert_called_once_with(doc['doc_id'])
        self.assertEqual(self.client.get('/api/v1/admin/knowledge', headers=headers).json(), [])


async def _all(db, model):
    return list((await db.exec(select(model))).all())


async def _count(db, model):
    return len(await _all(db, model))


if __name__ == '__main__':
    unittest.main()
