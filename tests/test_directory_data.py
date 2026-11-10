"""Directory-export member import with contact people, hybrid matching terms, and event page parsing.

No model, network or API key is needed: the import runs against the throwaway database, and the
matching and parsing checks exercise pure functions.
"""
import sys
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import test_api_contract as contract
import test_admin_content as admin_tests
from concierge import matching
from concierge.db.relationaldb import models

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import refresh_events  # noqa: E402

DIRECTORY_CSV = (
    'Full name,Salutation,Job title,Email,Country,Primary sector,Secondary sector(s),Membership type,'
    'Communication language,Display profile in the online directory,Description\n'
    'COMPANY 01,,,info@company-01.example.com,Japan,Food & Beverage,Food Service & Restaurants,'
    'Membership - Resident Active Member - Outside Kanto rate,English,TRUE,"Premium ingredients for catering."\n'
    '"COMPANY 01, Sophie DUBOIS",Ms,CEO,sophie.dubois@company-01.example.com,Japan,Food & Beverage,,,Japanese,TRUE,\n'
    '"COMPANY 01, Thomas MOREAU",Mr,HR Manager,thomas.moreau@company-01.example.com,Japan,Food & Beverage,,,English,TRUE,\n'
    'COMPANY 02,,,info@company-02.example.com,Japan,Manufacturing,Automotive,Membership - Associate Member,English,FALSE,'
    '"Precision mechanical components."\n'
    '"COMPANY 02, Marc BERNARD",Mr,Sales Director,marc.bernard@company-02.example.com,Japan,Manufacturing,,,Japanese,TRUE,\n'
    '"COMPANY 99, Lost PERSON",Mr,CEO,lost@company-99.example.com,Japan,Other,,,English,TRUE,\n'
).encode()


class DirectoryImportTest(unittest.TestCase):
    setUp = contract.ApiContractTest.setUp
    tearDown = contract.ApiContractTest.tearDown
    staff_headers = admin_tests.AdminContentApiTest.staff_headers
    run_db = admin_tests.AdminContentApiTest.run_db

    def test_directory_export_creates_companies_with_their_contact_people(self):
        headers = self.staff_headers()

        async def seed(db):
            for name in ('Active', 'Non-Kanto', 'Associate'):
                db.add(models.MemberCategory(category_name=name, category_description=name, annual_price=1))
            await db.commit()
        self.run_db(seed)

        preview = self.client.post('/api/v1/admin/import/members', json=admin_tests.upload('members.csv', DIRECTORY_CSV), headers=headers).json()
        self.assertEqual(preview['counts'], {'create': 2, 'update': 0, 'error': 1, 'contacts': 3})
        self.assertEqual(self.run_db(lambda db: admin_tests._all(db, models.MemberContact)), [])  # preview writes nothing
        orphan = next(row for row in preview['rows'] if row['errors'])
        self.assertIn('company is not in this file', orphan['errors'][0])

        result = self.client.post('/api/v1/admin/import/members?commit=true', json=admin_tests.upload('members.csv', DIRECTORY_CSV), headers=headers).json()
        self.assertTrue(result['committed'])
        members = {m.name: m for m in self.run_db(lambda db: admin_tests._all(db, models.Member))}
        categories = {c.category_id: c.category_name for c in self.run_db(lambda db: admin_tests._all(db, models.MemberCategory))}
        first, second = members['COMPANY 01'], members['COMPANY 02']
        self.assertEqual((first.sector, first.secondary_sectors, first.language, first.contact),
                         ('Food & Beverage', 'Food Service & Restaurants', 'English', 'info@company-01.example.com'))
        self.assertEqual(categories[first.category_id], 'Non-Kanto')    # "Outside Kanto rate", not "Active"
        self.assertEqual(categories[second.category_id], 'Associate')
        self.assertTrue(first.directory_visible)
        self.assertFalse(second.directory_visible)
        self.assertEqual(first.description, 'Premium ingredients for catering.')  # people are not folded into the text

        people = self.run_db(lambda db: admin_tests._all(db, models.MemberContact))
        of_first = sorted((p.name, p.salutation, p.job_title, p.language, p.email) for p in people if p.member_id == first.member_id)
        self.assertEqual(of_first, [
            ('Sophie DUBOIS', 'Ms', 'CEO', 'Japanese', 'sophie.dubois@company-01.example.com'),
            ('Thomas MOREAU', 'Mr', 'HR Manager', 'English', 'thomas.moreau@company-01.example.com')])

        # Importing the same file again updates the companies and replaces their people, no duplicates.
        again = self.client.post('/api/v1/admin/import/members?commit=true', json=admin_tests.upload('members.csv', DIRECTORY_CSV), headers=headers).json()
        self.assertEqual((again['counts']['create'], again['counts']['update']), (0, 2))
        self.assertEqual(len(self.run_db(lambda db: admin_tests._all(db, models.MemberContact))), 3)

        listed = self.client.get('/api/v1/admin/members', headers=headers).json()['items']
        shown = next(item for item in listed if item['name'] == 'COMPANY 01')
        self.assertEqual([c['job_title'] for c in shown['contacts']], ['CEO', 'HR Manager'])
        self.assertEqual(shown['secondary_sectors'], 'Food Service & Restaurants')
        by_secondary = self.client.get('/api/v1/admin/members?sector=Automotive', headers=headers).json()['items']
        self.assertEqual([item['name'] for item in by_secondary], ['COMPANY 02'])


class HybridMatchingTermsTest(unittest.TestCase):
    SECTORS = ['Food & Beverage', 'Food Service & Restaurants', 'Technology', 'Software', 'Transportation',
               'Logistics', 'Finance', 'Banking', 'Energy', 'Medical Devices']

    def test_sector_wording_is_understood_loosely(self):
        resolve = lambda text: matching.resolve_sectors(text, self.SECTORS)  # noqa: E731
        self.assertEqual(resolve('food & beverage'), ['Food & Beverage'])
        for wording in ('F&B', 'food', 'agroalimentaire'):
            self.assertIn('Food & Beverage', resolve(wording), wording)
        self.assertEqual(set(resolve('IT')), {'Technology', 'Software'})
        self.assertEqual(set(resolve('logistique')), {'Transportation', 'Logistics'})
        self.assertEqual(resolve('medical devices'), ['Medical Devices'])
        self.assertEqual(resolve('aerospace'), [])  # not a directory sector: the caller ranks by meaning instead
        self.assertEqual(resolve(''), [])

    def test_precise_terms_outweigh_common_ones_and_filler_is_ignored(self):
        documents = {
            'solar': 'COMPANY 19. Sector: Energy. Solar solutions for commercial sites, monitoring and maintenance.',
            'storage': 'COMPANY 39. Sector: Energy. Modular energy storage systems and remote monitoring.',
            'bank': 'COMPANY 06. Sector: Finance. Cash flow forecasting and monthly reporting for SMEs.',
        }
        scores = matching.wording_scores('solar panels for our office buildings', documents)
        self.assertGreater(scores['solar'][0], scores['storage'][0])
        self.assertIn('solar', scores['solar'][1])
        self.assertEqual(scores['bank'], (0.0, []))
        # French wording reaches English text through the shared concepts.
        french = matching.wording_scores('un distributeur pour nos produits alimentaires',
                                         {'trade': 'Multichannel distribution solutions.', 'bank': documents['bank']})
        self.assertGreater(french['trade'][0], 0)
        self.assertEqual(french['bank'][0], 0.0)

    def test_fusion_promotes_a_company_that_matches_on_meaning_and_wording(self):
        semantic = {'a': 0.84, 'b': 0.83, 'c': 0.82}
        wording = {'a': (0.0, []), 'b': (0.2, ['energy']), 'c': (0.9, ['energy', 'solar'])}
        self.assertEqual(matching.fuse(semantic, wording)[0], 'c')
        self.assertEqual(matching.fuse(semantic, {key: (0.0, []) for key in semantic}), ['a', 'b', 'c'])
        self.assertTrue(matching.is_relevant(0.83, 0.0))
        self.assertTrue(matching.is_relevant(0.80, 0.7))    # precise wording makes up for a modest embedding score
        self.assertFalse(matching.is_relevant(0.80, 0.2))

    def test_contact_job_titles_do_not_describe_what_a_company_does(self):
        company = models.Member(name='COMPANY 36', sector='Finance', secondary_sectors='Banking',
                                description='International financial support services.')
        text = matching.member_text(company)
        self.assertIn('Also active in: Banking', text)
        self.assertNotIn('HR', text)


class EventPageParsingTest(unittest.TestCase):
    def test_the_three_date_layouts(self):
        when = refresh_events.parse_when
        self.assertEqual(when('Tuesday 22 December 2026 - 6:30 PM to 9:30 PM'),
                         (datetime(2026, 12, 22, 18, 30), datetime(2026, 12, 22, 21, 30)))
        self.assertEqual(when('December, 9 ( 9:00 AM) ~ Friday 11 December 2026 ( 6:00 PM)'),
                         (datetime(2026, 12, 9, 9, 0), datetime(2026, 12, 11, 18, 0)))
        self.assertEqual(when('December, 30 (10:00 AM) ~ Saturday 2 January 2027 ( 4:00 PM)')[0], datetime(2026, 12, 30, 10, 0))
        self.assertEqual(when('Thursday 8 July 2021 to 12:00 AM'), (datetime(2021, 7, 8), None))
        self.assertEqual(when('no date here'), (None, None))

    def test_deadline_ignores_the_sites_empty_value(self):
        deadline = refresh_events.parse_deadline
        self.assertEqual(deadline('Deadline for registration:\nFriday 4 December 2026 - 5:00 PM'), datetime(2026, 12, 4, 17, 0))
        self.assertIsNone(deadline('Deadline for registration:\nThursday 1 January 1970 - 1:00 AM'))

    def test_prices_are_stored_only_when_unambiguous(self):
        prices = refresh_events.parse_prices
        self.assertEqual(prices(['CCIFJ Member 45.000 JPY (excluding VAT)', 'Non-Member 55.000 JPY (excluding VAT)',
                                 'For cancellation made one week before the training, we apply a 50% cancellation fee.']),
                         {'currency': 'JPY', 'member_price': Decimal(45000), 'non_member_price': Decimal(55000)})
        self.assertEqual(prices(['Members: ¥10,000 JPY / Non-Members: ¥12,000 JPY']),
                         {'currency': 'JPY', 'member_price': Decimal(10000), 'non_member_price': Decimal(12000)})
        self.assertEqual(prices(['Member Rate', '38,000 JPY', 'Non-Member Rate', '42,000 JPY'])['non_member_price'], Decimal(42000))
        self.assertEqual(prices(['40,000 yens (TTC)', '*Participants must be at least 20 years old.']),
                         {'currency': 'JPY', 'price': Decimal(40000)})
        self.assertEqual(prices(['Free event']), {'currency': 'JPY', 'price': Decimal(0)})
        # Seats, passes and sponsor packages are not one price: they stay in the description only.
        self.assertEqual(prices(['A seat - ¥68,000', 'B seat - ¥58,000', 'C seat - ¥45,000']), {})
        self.assertEqual(prices(['- 12 000 € TTC for members of the French Chamber', '- 16 000 € TTC for non-members'])['currency'], 'EUR')

    def test_description_keeps_links_and_refreshes_the_price_section(self):
        page = {'lead': 'Startup matchmaking', 'about': 'A strategic highlight designed to foster innovation between startups.',
                'price_lines': ['Free for startups', '¥100,000 TTC for Gold companies']}
        current = ('Startup matchmaking\n\nRegistration: https://example.test/register\n'
                   'Event details: https://www.ccifj.or.jp/en/events/upcoming-events/event-details/event/x.html\n\n'
                   f'{refresh_events.PRICES_HEADING}\n- old price')
        text = refresh_events.describe(current, page, 'https://www.ccifj.or.jp/en/events/upcoming-events/event-details/event/x.html')
        self.assertIn('foster innovation', text)
        self.assertIn('Registration: https://example.test/register', text)
        self.assertEqual(text.count('Event details:'), 1)
        self.assertNotIn('old price', text)
        self.assertTrue(text.endswith('- Free for startups\n- ¥100,000 TTC for Gold companies'))
        self.assertEqual(refresh_events.describe(text, page, 'https://x'), text)  # running it twice changes nothing


if __name__ == '__main__':
    unittest.main()
