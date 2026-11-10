import unittest
from types import SimpleNamespace as NS

from concierge.agents import tools


def member(member_id, name):
    return NS(member_id=member_id, name=name)


MEMBERS = [member(1, 'COMPANY 01'), member(11, 'COMPANY 11'), member(3, 'Maison Dupré K.K.'),
           member(4, '株式会社サクラ食品')]
CONTACTS = {11: [NS(name='Marie Lefèvre')]}


def names(query):
    return [m.name for m, _ in tools._members_by_name(MEMBERS, CONTACTS, query)]


class MemberNameLookupTest(unittest.TestCase):
    def test_spelling_variants_find_one_company(self):
        for query in ('company 11', 'COMPANY 11', 'Company11', 'compnay 11', 'company 11 Co., Ltd.'):
            self.assertEqual(names(query), ['COMPANY 11'], query)

    def test_leading_zero_does_not_matter_but_the_number_does(self):
        self.assertEqual(names('company 1'), ['COMPANY 01'])
        self.assertEqual(names('company 12'), [])

    def test_legal_forms_and_partial_names(self):
        self.assertEqual(names('maison dupre'), ['Maison Dupré K.K.'])  # accents ignored
        self.assertEqual(names('Maison Dupré'), ['Maison Dupré K.K.'])
        self.assertEqual(names('Dupré'), ['Maison Dupré K.K.'])
        self.assertEqual(names('サクラ食品'), ['株式会社サクラ食品'])

    def test_contact_person_name(self):
        found = tools._members_by_name(MEMBERS, CONTACTS, 'marie lefevre')
        self.assertEqual([m.name for m, _ in found], ['COMPANY 11'])
        self.assertIn('Marie Lefèvre', found[0][1])

    def test_unknown_name(self):
        self.assertEqual(names('Acme Wines'), [])


if __name__ == '__main__':
    unittest.main()
