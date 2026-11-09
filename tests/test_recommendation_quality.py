"""Cross-topic recommendation boundaries: retrieval is not endorsement."""
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch, MagicMock


class EventSelectionTest(unittest.IsolatedAsyncioTestCase):
    def event(self, id, title, closed=False):
        return SimpleNamespace(event_id=id,title=title,category='Business',location=None,
            start_at=datetime(2099,1,2),end_at=None,reg_deadline=datetime(2000 if closed else 2099,1,1),
            price=None,member_price=None,non_member_price=None,currency='JPY')

    async def test_only_events_named_in_answer_get_cards_and_closed_options_are_not_recommended(self):
        from concierge.api.events import cards_for_ids
        events={1:self.event(1,'Relevant food forum'),2:self.event(2,'Unrelated training'),3:self.event(3,'Closed food forum',True)}
        with patch('concierge.api.events.crud.get_event',AsyncMock(side_effect=lambda db,id:events[id])):
            cards=await cards_for_ids(None,[1,2,3],answer='Consider Relevant food forum; Closed food forum has ended.')
        self.assertEqual([card['event_id'] for card in cards],[1])

    def test_open_only_constraints_override_browsing_and_negated_history_keywords(self):
        from concierge.api.events import registration_preferences
        self.assertEqual(registration_preferences('Only show events with confirmed open registration.',browsing=True),(False,False))
        self.assertEqual(registration_preferences('Show events but do not show closed registrations.',browsing=True),(False,True))
        self.assertEqual(registration_preferences('Show my bookings, including past events.',browsing=True),(True,True))

    async def test_upcoming_browse_drops_closed_cards_and_caps_the_shortlist(self):
        from concierge.api.paths import run_path_step
        from concierge.api.schemas import ChatRequest
        events=[self.event(i,'Forum '+str(i),closed=i==1) for i in range(1,6)]
        with patch('concierge.api.paths.crud.search_events',AsyncMock(return_value=events)):
            result=await run_path_step(ChatRequest(message='Upcoming events',path_step_id='events.upcoming'),None,None)
        self.assertEqual([card['event_id'] for card in result['events']],[2,3,4])

    async def test_explicit_closed_event_details_are_still_available(self):
        from concierge.api.events import cards_for_ids
        with patch('concierge.api.events.crud.get_event',AsyncMock(return_value=self.event(1,'Past forum',True))):
            cards=await cards_for_ids(None,[1],answer='Past forum registration closed.',include_closed=True)
        self.assertEqual(cards[0]['registration_open'],False)

    async def test_open_recommendations_are_capped_and_unconfirmed_browsing_is_explicit(self):
        from concierge.api.events import cards_for_ids
        events={i:self.event(i,'Forum '+str(i)) for i in range(1,6)}
        events[1].reg_deadline=None
        with patch('concierge.api.events.crud.get_event',AsyncMock(side_effect=lambda db,id:events[id])):
            cards=await cards_for_ids(None,list(events),answer=' '.join(e.title for e in events.values()))
            browse=await cards_for_ids(None,list(events),answer=' '.join(e.title for e in events.values()),include_unconfirmed=True)
        self.assertEqual(len(cards),3)
        self.assertTrue(all(card['registration_open'] is True for card in cards))
        self.assertIsNone(browse[0]['registration_open'])


class LateSkipTest(unittest.IsolatedAsyncioTestCase):
    async def test_late_skip_summarizes_existing_answer_without_new_research(self):
        from concierge.agents import body
        from langchain_core.messages import AIMessage, HumanMessage
        original='First validate demand.\n\nThen compare prospecting options: https://www.ccifj.or.jp/en.html\n\nOnly consider setup after validation.'
        model=MagicMock();model.ainvoke=AsyncMock(return_value=AIMessage('Here is the short version: validate, then prospect, then consider setup.'))
        with patch.object(body,'llm_routing',model):
            result=await body.orchestrator_node({'messages':[HumanMessage('Expand to Japan'),AIMessage(original),HumanMessage('skip, just answer')], 'lang':'en'})
        self.assertEqual(result['plan']['tasks'],[])
        self.assertEqual(result['next_actions'],[])
        self.assertEqual(model.ainvoke.await_count,1)
        self.assertEqual(model.ainvoke.await_args.args[0][-1]['content'],original)
        self.assertFalse(model.with_structured_output.called)

    def test_skip_detector_does_not_swallow_intake_answers_or_new_requests(self):
        from concierge.recommendations import late_skip_source
        from langchain_core.messages import AIMessage,HumanMessage
        for latest in ('Skip the events; find meeting rooms instead.','Skip approval and book it.','Do not skip the import requirements.'):
            self.assertIsNone(late_skip_source([AIMessage('Useful advice.\n\nAnother step.'),HumanMessage(latest)]))
        self.assertIsNone(late_skip_source([AIMessage('Could you share your industry? You can skip these details.'),HumanMessage('skip, just answer')]))


class DirectoryEvidenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_sector_only_record_is_explicitly_not_a_verified_commercial_match(self):
        from concierge.agents import tools
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def db(): yield None
        member=SimpleNamespace(name='Example Industrial Group',sector='Manufacturing',region='Tokyo',country='Japan',description='Sector: Manufacturing\nWebsite: https://example.test')
        with patch.object(tools,'AsyncSessionLocal',db),patch.object(tools.crud,'search_members',AsyncMock(return_value=[member])):
            result=await tools.search_member_directory.ainvoke({'industry':'Manufacturing'})
        self.assertIn('Commercial role: unverified',result)
        self.assertIn('not a verified buyer',result)
        self.assertIn('https://example.test',result)
