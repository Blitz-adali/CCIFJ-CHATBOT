"""Answers built on CCIFJ's own records list that record as a source."""
import unittest

from concierge.evidence import TurnEvidence, record_reference, turn_evidence, with_reference_sources
from concierge.resources import MEMBERS_DIRECTORY_PAGE, MEMBERSHIP_PAGE, TEAM_PAGE

CATEGORIES = ['Active', 'Associate', 'Benefactor', 'Individual', 'Non-Kanto', 'Non-Resident', 'Startup']


class ReferenceSourcesTest(unittest.TestCase):
    def test_a_staff_contact_in_the_answer_cites_the_team_page(self):
        reply = 'For event sponsorship, contact Marie OZAKI at m.ozaki@ccifj.or.jp or 03-4500-6531.'
        sources = with_reference_sources([], reply, {}, 'events', CATEGORIES)
        self.assertEqual([source['url'] for source in sources], [TEAM_PAGE])
        self.assertEqual(sources[0]['provenance'], 'database')

    def test_no_contact_no_team_source(self):
        self.assertEqual(with_reference_sources([], 'Which language should the draft use?', {}, 'general', CATEGORIES), [])
        # An address outside CCIFJ (the visitor's own) is not a staff contact.
        self.assertEqual(with_reference_sources([], 'From: Jane Doe <jane.doe@example.test>', {}, 'general', CATEGORIES), [])

    def test_existing_sources_are_kept_and_not_duplicated(self):
        page = {'title': 'Our team', 'url': TEAM_PAGE, 'provenance': 'retrieved'}
        sources = with_reference_sources([page], 'Write to e.suzuki@ccifj.or.jp.', {}, 'events', CATEGORIES)
        self.assertEqual(sources, [page])

    def test_directory_lookup_is_listed_when_the_tool_returned_companies(self):
        token = turn_evidence.set(TurnEvidence())
        try:
            record_reference('CCIFJ member directory', MEMBERS_DIRECTORY_PAGE, 'member')
            record_reference('CCIFJ member directory', MEMBERS_DIRECTORY_PAGE, 'member')
            evidence = {'references': turn_evidence.get().references}
        finally:
            turn_evidence.reset(token)
        self.assertEqual(len(evidence['references']), 1)
        sources = with_reference_sources([], 'COMPANY 01 supplies premium ingredients.', evidence, 'membership', CATEGORIES)
        self.assertEqual([source['url'] for source in sources], [MEMBERS_DIRECTORY_PAGE])

    def test_a_membership_answer_naming_a_category_cites_the_membership_page(self):
        reply = 'Yes. If your company holds Active membership, its employees are members too.'
        sources = with_reference_sources([], reply, {}, 'membership', CATEGORIES)
        self.assertEqual([source['url'] for source in sources], [MEMBERSHIP_PAGE])

    def test_fees_quoted_from_the_membership_table_cite_the_fee_source(self):
        from concierge.resources import MEMBERSHIP_FEE_SOURCE
        reply = 'The 2026 annual fee snapshot lists: Active — JPY 295,000; Associate — JPY 210,000; Startup — ¥140,000.'
        sources = with_reference_sources([], reply, {}, 'membership', CATEGORIES)
        self.assertEqual([source['url'] for source in sources], [MEMBERSHIP_FEE_SOURCE])
        self.assertEqual(sources[0]['title'], 'Membership annual fees')

    def test_the_membership_page_is_not_added_without_a_category_or_outside_membership(self):
        self.assertEqual(with_reference_sources([], 'Our events are open to active professionals.', {}, 'events', CATEGORIES), [])
        self.assertEqual(with_reference_sources([], 'Tell me more about your company first.', {}, 'membership', CATEGORIES), [])
        # A sourced answer needs no fallback reference.
        page = {'title': 'Fees', 'url': 'https://www.ccifj.or.jp/en/x.html', 'provenance': 'retrieved'}
        self.assertEqual(with_reference_sources([page], 'Startup membership costs less.', {}, 'membership', CATEGORIES), [page])



class EventSourcesTest(unittest.IsolatedAsyncioTestCase):
    """An answer that names an event lists that event's CCIFJ page."""

    async def test_only_events_named_in_the_answer_are_listed_once(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        from concierge.api import events

        rows = {1: SimpleNamespace(event_id=1, title='Business Summit 2026'),
                2: SimpleNamespace(event_id=2, title='GALA KANSAI 2026'),
                3: SimpleNamespace(event_id=3, title='Internal seminar')}
        pages = {1: 'https://www.ccifj.or.jp/en/events/business-summit-2026.html',
                 2: 'https://www.ccifj.or.jp/en/events/gala-kansai-2026.html', 3: None}
        answer = 'The **Business Summit 2026** is on 1 December; the Gala Kansai 2026 follows on 2 December.'
        with patch.object(events.crud, 'get_event', AsyncMock(side_effect=lambda session, event_id: rows.get(event_id))), \
                patch('concierge.source_context.matching_event', side_effect=lambda event: {'url': pages[event.event_id]}):
            sources = await events.sources_for_ids(None, [1, 2, 3, 1, 99], answer)
            none = await events.sources_for_ids(None, [1, 2], 'Which event do you mean?')
        self.assertEqual([source['url'] for source in sources], [pages[1], pages[2]])
        self.assertEqual({source['provenance'] for source in sources}, {'database'})
        self.assertEqual(none, [])


if __name__ == '__main__':
    unittest.main()
