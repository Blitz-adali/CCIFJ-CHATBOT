"""Regression checks derived from the two real 72-turn transcripts."""
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from concierge.source_context import documents, event_facts, matching_event, effective_deadline, page_passage, source_memory, source_prompt
from concierge.conversation import apply_language_requests, conversation_language, draft_language

FNB='https://www.ccifj.or.jp/en/events/upcoming-events/event-details/event/ask-me-anything-with-leaders-from-the-fb-industry.html'
ROMAIN='https://www.ccifj.or.jp/en/events/upcoming-events/event-details/event/meet-engage-with-romain-millet.html'


class SourceFactsTest(unittest.TestCase):
    def test_event_metadata_retains_venue_missing_from_prose_excerpt(self):
        page=page_passage(FNB)
        self.assertIn('Nihonbashi Honcho YS Bldg.',page['text'])
        self.assertIn('16 November 2026',page['text'])
        self.assertIn('not live availability',page['text'])

    def test_unknown_url_is_never_fetched_or_retained(self):
        self.assertIsNone(page_passage('http://127.0.0.1/private'))
        self.assertEqual(source_memory(['http://127.0.0.1/private','https://example.com']),[])
        self.assertEqual(source_memory([FNB,FNB]),[FNB])

    def test_source_memory_is_bounded_and_contains_real_metadata(self):
        urls=[url for url,page in documents().items() if page.get('events_jsonld')]
        self.assertLessEqual(len(source_memory(urls)),6)
        self.assertIn('Nihonbashi',source_prompt([FNB]))

    def test_exact_event_match_closes_expired_source_deadline_despite_missing_db_deadline(self):
        from concierge.agents.tools import _registration_problem
        from concierge.api.events import event_card
        event=SimpleNamespace(event_id=1,title='Meet & Engage with Romain Millet',start_at=datetime(2026,11,10,18,30),end_at=datetime(2026,11,10,20),reg_deadline=None,category='Business',location=None,price=None,member_price=None,non_member_price=None,currency='JPY')
        self.assertEqual(effective_deadline(event),datetime(2026,11,3,17))
        with patch('concierge.agents.tools._now',return_value=datetime(2026,11,4)),patch('concierge.api.events.japan_now',return_value=datetime(2026,11,4)):
            self.assertIn('registration closed',_registration_problem(event))
            card=event_card(event)
        self.assertFalse(card['registration_open'])
        self.assertIn('Shinagawa',card['location'])
        self.assertEqual(card['source_url'],ROMAIN)
        event.start_at=datetime(2027,11,10,18,30)
        self.assertIsNone(matching_event(event))

    def test_future_crawl_deadline_does_not_claim_current_registration_is_open(self):
        from concierge.api.events import event_card
        from concierge.agents.tools import _registration_summary
        event=SimpleNamespace(event_id=1,title='"Ask me Anything" with Leaders from the F&B industry',start_at=datetime(2026,11,19,18,30),end_at=datetime(2026,11,19,20,30),reg_deadline=None,category='Networking',location=None,price=None,member_price=None,non_member_price=None,currency='JPY')
        with patch('concierge.api.events.japan_now',return_value=datetime(2026,11,4)),patch('concierge.agents.tools._now',return_value=datetime(2026,11,4)):
            card=event_card(event)
            summary=_registration_summary(event)
        self.assertEqual(card['reg_deadline'],'2026-11-16T12:00:00+09:00')
        self.assertIsNone(card['registration_open'])
        self.assertIn('unconfirmed',summary)

    def test_transient_reply_request_does_not_override_a_later_french_turn(self):
        text='Now answer in English: which one service best matches that goal?'
        brief=apply_language_requests({},text)
        self.assertEqual(conversation_language(brief,'ja',text),'en')
        self.assertEqual(conversation_language(brief,'fr','En fait, notre budget passe à 2000 euros.'),'fr')
        brief=apply_language_requests({},'Write the message in Japanese, but explain your choices to me in English.')
        self.assertEqual(conversation_language(brief,'fr','Make the draft shorter.'),'en')
        self.assertEqual(draft_language(brief),'ja')

    def test_structured_event_search_finds_a_named_event_without_vectors(self):
        from concierge.source_context import find_events
        urls=find_events('Ask Anything leaders F&B',datetime(2026,11,4),location='Tokyo')
        self.assertIn(FNB,urls)
        self.assertEqual(find_events('Ask Anything leaders F&B',datetime(2030,1,1)),[])

    def test_keyword_warmup_survives_an_embedding_failure(self):
        from concierge.agents import tools
        with patch.object(tools,'_get_embed_model',side_effect=OSError('not cached')),patch.object(tools,'_get_retriever',return_value=object()) as lexical,patch.object(tools,'get_tokenizer',return_value=object()),patch.object(tools,'get_collection',return_value=object()):
            tools.warm_up()
        lexical.assert_called_once()

    def test_warmup_rejects_readiness_when_both_usable_search_paths_fail(self):
        from concierge.agents import tools
        with patch.object(tools,'_get_embed_model',return_value=object()),patch.object(tools,'_get_retriever',side_effect=OSError('index unavailable')),patch.object(tools,'get_tokenizer',return_value=object()),patch.object(tools,'get_collection',side_effect=OSError('vector store unavailable')):
            with self.assertRaisesRegex(RuntimeError,'No search backend'):
                tools.warm_up()

    def test_complete_source_lookup_records_real_provenance(self):
        from concierge.agents.tools import get_source_details
        from concierge.evidence import TurnEvidence,turn_evidence
        evidence=TurnEvidence();token=turn_evidence.set(evidence)
        try:
            response=get_source_details.invoke({'url':FNB})
        finally:turn_evidence.reset(token)
        self.assertIn('Nihonbashi',response)
        self.assertEqual(evidence.passages[0]['url'],FNB)
        self.assertTrue(evidence.passages[0]['id'].startswith('page:'))

    def test_diagnostics_never_persist_queries_messages_or_full_paths(self):
        from concierge.rag.src import diagnostics
        with tempfile.TemporaryDirectory() as directory,patch.object(diagnostics,'PATH',Path(directory)/'health.json'),patch.object(diagnostics,'_state',{}),patch.object(diagnostics,'_persist',True):
            try:raise RuntimeError('secret-token and private user prompt')
            except RuntimeError as error:diagnostics.record('semantic',error)
            rendered=(Path(directory)/'health.json').read_text()
            self.assertNotIn('secret-token',rendered)
            self.assertNotIn('private user prompt',rendered)
            self.assertNotIn(str(Path(__file__).parent),rendered)
            self.assertNotIn('frames',json.dumps(diagnostics.snapshot()))
            self.assertIn('RuntimeError',rendered)

    def test_cache_first_embedding_load_does_not_make_a_network_attempt(self):
        from concierge.agents import tools
        with patch.object(tools,'_embed_model',None),patch.object(tools,'SentenceTransformer',return_value=object()) as constructor:
            tools._get_embed_model()
        self.assertTrue(constructor.call_args.kwargs['local_files_only'])
        self.assertEqual(constructor.call_args.kwargs['device'],'cpu')


class SourceStateTest(unittest.IsolatedAsyncioTestCase):
    async def test_source_urls_flow_through_backend_formatting_and_next_worker_prompt(self):
        from langchain_core.messages import HumanMessage,AIMessage
        from concierge.api.agent_backend import AgentBackend
        from concierge.agents.body import _user_context_prompt
        backend=object.__new__(AgentBackend)
        backend.pending=lambda result: []
        result={'messages':[HumanMessage('Tell me more.'),AIMessage('The indexed venue is Nihonbashi.')], 'source_urls':[], 'tool_evidence':{'passages':[page_passage(FNB)]}}
        answer=await backend.format_result(result)
        self.assertEqual(answer['agent_state']['source_urls'],[FNB])
        self.assertIn('Nihonbashi',_user_context_prompt({'source_urls':answer['agent_state']['source_urls']}))

    async def test_citation_to_an_actual_prior_source_retains_provenance(self):
        from langchain_core.messages import HumanMessage,AIMessage
        from concierge.api.agent_backend import AgentBackend
        backend=object.__new__(AgentBackend);backend.pending=lambda result:[]
        result={'messages':[HumanMessage('What was the venue?'),AIMessage('See [event details]('+FNB+').')],'source_urls':[FNB],'tool_evidence':{}}
        answer=await backend.format_result(result)
        self.assertEqual(answer['sources'][0]['url'],FNB)
        self.assertEqual(answer['sources'][0]['provenance'],'retrieved')
        self.assertIn(FNB,answer['reply'])

    async def test_router_uses_french_after_an_english_one_turn_instruction(self):
        from langchain_core.messages import HumanMessage,AIMessage
        from concierge.agents import body
        from concierge.agents.schema_prompts import OrchestratorPlan,TaskItem
        text='En fait, notre budget passe à 2000 euros.'
        brief=apply_language_requests({},'Now answer in English: which one service best matches that goal?')
        model=MagicMock();model.with_structured_output.return_value.ainvoke=AsyncMock(return_value=OrchestratorPlan(scope='in_scope',lang='fr',tasks=[TaskItem(target='general',sub_query='Update budget')]))
        with patch.object(body,'llm_routing',model):
            result=await body.orchestrator_node({'messages':[HumanMessage(text)],'conversation_brief':brief})
        self.assertEqual(result['lang'],'fr')


class SourceApiPersistenceTest(unittest.TestCase):
    import test_api_contract as contract
    setUp=contract.ApiContractTest.setUp
    tearDown=contract.ApiContractTest.tearDown

    def test_source_memory_and_brief_survive_faq_and_guided_paths(self):
        from concierge.api.agent_backend import agent_inputs
        seen=[]
        brief={'products':{'value':'recycling equipment','quote':'We sell recycling equipment.'}}
        async def answer(*args):
            seen.append(agent_inputs.get())
            return {'reply':'Indexed event details.', 'agent_state':{'source_urls':[FNB],'conversation_brief':brief}}
        self.backend.chat=answer
        first=self.client.post('/api/v1/chat',json={'message':'We sell recycling equipment. Tell me about the F&B event.'}).json()
        token=first['session_id']
        faq=self.client.post('/api/v1/chat',json={'session_id':token,'message':'What are the CCIFJ membership fees?'})
        self.assertEqual(faq.status_code,200)
        guided=self.client.post('/api/v1/chat',json={'session_id':token,'message':'Events','path_step_id':'events.upcoming'})
        self.assertEqual(guided.status_code,200)
        result=self.client.post('/api/v1/chat',json={'session_id':token,'message':'What was that venue again?'})
        self.assertEqual(result.status_code,200)
        self.assertEqual(seen[-1]['source_urls'],[FNB])
        self.assertEqual(seen[-1]['conversation_brief'],brief)


class ExpiredSourceBookingTest(unittest.IsolatedAsyncioTestCase):
    async def test_booking_cannot_bypass_an_expired_exact_source_deadline(self):
        from concierge.db.relationaldb import crud
        session=MagicMock()
        event=SimpleNamespace(title='Meet & Engage with Romain Millet',start_at=datetime(2026,11,10,18,30),end_at=datetime(2026,11,10,20),reg_deadline=None)
        session.get=AsyncMock(side_effect=[object(),event])
        with patch.object(crud,'_now',return_value=datetime(2026,11,4)):
            with self.assertRaisesRegex(ValueError,'closed'):
                await crud.book_event(session,1,1,[{'name':'Test','email':'test@example.test'}])
        session.add.assert_not_called()
        session.commit.assert_not_called()


class RoomCheckContinuityTest(unittest.IsolatedAsyncioTestCase):
    async def test_actual_lookup_is_recorded_and_survives_result_formatting(self):
        from concierge.agents import tools,body
        from concierge.evidence import TurnEvidence,turn_evidence
        from concierge.api.agent_backend import AgentBackend
        from langchain_core.messages import HumanMessage,AIMessage
        space=SimpleNamespace(space_id=1,name="L'Espace@CCIFJ",capacity=None)
        session=MagicMock();session.__aenter__=AsyncMock(return_value=session);session.__aexit__=AsyncMock(return_value=None)
        evidence=TurnEvidence();token=turn_evidence.set(evidence)
        try:
            with patch.object(tools,'AsyncSessionLocal',return_value=session),patch.object(tools.crud,'list_spaces',AsyncMock(return_value=[space])),patch.object(tools.crud,'list_space_reservations',AsyncMock(return_value=[])):
                result=await tools.check_space_availability.ainvoke({'start_date':'2026-12-08 15:00','end_date':'2026-12-08 17:00'})
        finally:turn_evidence.reset(token)
        self.assertIn('FREE',result)
        self.assertEqual(len(evidence.space_checks),1)
        backend=object.__new__(AgentBackend);backend.pending=lambda result:[]
        formatted=await backend.format_result({'messages':[HumanMessage('Check availability'),AIMessage(result)],'tool_evidence':{'space_checks':evidence.space_checks}})
        checks=formatted['agent_state']['space_checks']
        prompt=body._user_context_prompt({'space_checks':checks})
        self.assertIn('2026-12-08T15:00:00',prompt)
        self.assertIn('These checks actually ran',prompt)
        self.assertIn('not guaranteed current availability',prompt)

    async def test_room_checks_stay_bounded_and_do_not_retain_private_reservation_details(self):
        from concierge.evidence import space_check_memory
        checks=[{'space_id':1,'name':'Room','start':str(i),'end':str(i+1),'checked_at':'now','free':True,'capacity':None,'private_title':'Secret meeting'} for i in range(10)]
        kept=space_check_memory([],checks)
        self.assertEqual(len(kept),4)
        self.assertNotIn('private_title',kept[-1])
