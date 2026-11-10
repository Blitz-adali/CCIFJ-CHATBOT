"""Fault injection at real tool, graph adapter and API boundaries."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import test_api_contract as contract
from concierge.evidence import TurnEvidence, turn_evidence
from concierge.recovery import ReadOnlyTurnFailure
from concierge.conversation import apply_language_requests, conversation_language, draft_language


class LanguageSeparationTest(unittest.TestCase):
    def test_draft_language_never_overwrites_explicit_reply_language(self):
        brief=apply_language_requests({},'Write the message in Japanese, but explain your choices to me in English.')
        self.assertEqual(draft_language(brief),'ja')
        self.assertEqual(conversation_language(brief,'ja'),'en')
        brief=apply_language_requests(brief,'Change the contact email to camille.export@example.test.')
        self.assertEqual(draft_language(brief),'ja')
        self.assertEqual(conversation_language(brief,'ja'),'en')

    def test_explicit_conversation_switch_does_not_translate_the_draft(self):
        brief=apply_language_requests({},'Japanese draft, English explanations.')
        brief=apply_language_requests(brief,'Switch to French for the next answer.')
        self.assertEqual(draft_language(brief),'ja')
        self.assertEqual(conversation_language(brief,'ja'),'fr')

    def test_a_recommendation_is_not_a_language_selection(self):
        self.assertIsNone(draft_language(apply_language_requests({},'Camille Test, camille@example.test.')))
        self.assertIsNone(draft_language(apply_language_requests({},'Japanese')))
        self.assertEqual(draft_language(apply_language_requests({},'Japanese please.',True)),'ja')

    def test_legacy_ambiguous_language_is_not_a_reply_instruction(self):
        brief=apply_language_requests({'language':{'value':'Japanese','quote':'Write in Japanese.'}},'Show the final draft.')
        self.assertNotIn('language',brief)
        self.assertEqual(conversation_language(brief,'en'),'en')

    def test_business_language_adjectives_do_not_select_a_draft_language(self):
        for text in ['Help me draft an inquiry to CCIFJ about finding Japanese food distributors.', 'Draft an email about French cheese.', 'Write a message to Japanese partners.']:
            self.assertIsNone(draft_language(apply_language_requests({},text)))

    def test_original_statements_preserve_role_outside_recent_router_window(self):
        from langchain_core.messages import HumanMessage, AIMessage
        from concierge.conversation import user_history_prompt
        messages=[HumanMessage('The role is a bilingual enterprise account executive, based in Tokyo.'),AIMessage('A fabricated salary is 100000.')]
        messages += [HumanMessage('Follow-up '+str(i)) for i in range(30)]
        messages.append(HumanMessage('Correction: remote from Osaka, not Tokyo.'))
        prompt=user_history_prompt(messages)
        self.assertIn('enterprise account executive',prompt)
        self.assertIn('remote from Osaka',prompt)
        self.assertNotIn('fabricated salary',prompt)
        self.assertLess(len(prompt),14000)


class EventUncertaintyTest(unittest.TestCase):
    def test_missing_deadline_does_not_claim_open_registration(self):
        from datetime import timedelta
        from concierge.api.events import event_card
        from concierge.agents.tools import _registration_summary
        from concierge.db.relationaldb.datetimes import japan_now
        event=SimpleNamespace(event_id=1,title='Future',category='Business',location='Tokyo',start_at=japan_now()+timedelta(days=10),end_at=japan_now()+timedelta(days=10,hours=1),reg_deadline=None,price=None,member_price=None,non_member_price=None,currency='JPY')
        self.assertIsNone(event_card(event)['registration_open'])
        self.assertIn('unconfirmed',_registration_summary(event))
        event.reg_deadline=japan_now()-timedelta(days=1)
        self.assertFalse(event_card(event)['registration_open'])
        self.assertIn('closed',_registration_summary(event))


class SearchInitializationTest(unittest.TestCase):
    def test_concurrent_warmup_and_search_load_embedding_model_only_once(self):
        from concurrent.futures import ThreadPoolExecutor
        import time
        from concierge.agents import tools
        model=object()
        def load(*args, **kwargs):
            time.sleep(.03)
            return model
        with patch.object(tools,'_embed_model',None), patch.object(tools,'SentenceTransformer',side_effect=load) as constructor:
            with ThreadPoolExecutor(max_workers=4) as pool:
                results=list(pool.map(lambda _:tools._get_embed_model(),range(4)))
            self.assertTrue(all(result is model for result in results))
            constructor.assert_called_once()


class SearchProvenanceTest(unittest.TestCase):
    def test_same_numeric_id_in_different_indexes_keeps_both_source_pairs(self):
        from concierge.rag.src import rag_search as rag
        vector={'id':23,'content':'# Vector source\nRecruitment information','url':'https://www.ccifj.or.jp/recruitment.html'}
        lexical={'id':23,'text':'# Keyword source\nBusiness Connect information','url':'https://www.ccifj.or.jp/business.html'}
        with patch.object(rag,'get_tokenizer',return_value=SimpleNamespace(encode=lambda *a,**k:[1])), patch.object(rag,'get_collection',return_value=SimpleNamespace(count=lambda:1)), patch.object(rag,'semantic_search',return_value=[vector]), patch.object(rag,'lexical_search',return_value=[lexical]), patch.object(rag,'rerank',side_effect=TimeoutError):
            results=rag.search_passages(object(),object(),'CCIFJ services',2,2,2)
        self.assertEqual(len(results),2)
        pairs={r['id']:(r['text'],r['url']) for r in results}
        self.assertEqual(pairs['vector:23'],(vector['content'],vector['url']))
        self.assertEqual(pairs['23'],(lexical['text'],lexical['url']))


class ToolBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_read_tool_error_becomes_a_tool_error_not_an_empty_result(self):
        from concierge.agents.reliability import ConciergeToolBoundary
        evidence=TurnEvidence();token=turn_evidence.set(evidence)
        handler=AsyncMock(side_effect=RuntimeError('private internal details'))
        request=SimpleNamespace(tool_call={'name':'search_vectordb','id':'lookup-1'})
        try:
            result=await ConciergeToolBoundary().awrap_tool_call(request,handler)
            self.assertEqual(result.status,'error')
            self.assertIn('unavailable',result.content)
            self.assertNotIn('private internal details',result.content)
            self.assertFalse(evidence.write_started)
            self.assertIn('tool_unavailable',evidence.warnings)
        finally:turn_evidence.reset(token)
        handler.assert_awaited_once()

    async def test_write_failure_propagates_once_and_marks_uncertainty(self):
        from concierge.agents.reliability import ConciergeToolBoundary
        evidence=TurnEvidence();token=turn_evidence.set(evidence)
        handler=AsyncMock(side_effect=RuntimeError('write may have committed'))
        try:
            with self.assertRaises(RuntimeError):
                await ConciergeToolBoundary().awrap_tool_call(SimpleNamespace(tool_call={'name':'book_event_ticket','id':'write-1'}),handler)
            self.assertTrue(evidence.write_started)
        finally:turn_evidence.reset(token)
        handler.assert_awaited_once()

    async def test_graph_control_flow_is_never_swallowed(self):
        from concierge.agents.reliability import ConciergeToolBoundary
        from langgraph.errors import GraphBubbleUp
        handler=AsyncMock(side_effect=GraphBubbleUp('approval control flow'))
        with self.assertRaises(GraphBubbleUp):
            await ConciergeToolBoundary().awrap_tool_call(SimpleNamespace(tool_call={'name':'lookup','id':'read-1'}),handler)

    async def test_read_failures_are_bounded(self):
        from concierge.agents.reliability import ConciergeToolBoundary
        evidence=TurnEvidence();token=turn_evidence.set(evidence)
        handler=AsyncMock(side_effect=RuntimeError('lookup unavailable'))
        try:
            boundary=ConciergeToolBoundary()
            for _ in range(5):
                await boundary.awrap_tool_call(SimpleNamespace(tool_call={'name':'lookup','id':'read-1'}),handler)
            self.assertEqual(handler.await_count,2)
        finally:turn_evidence.reset(token)

    async def test_real_approval_pause_precedes_write_and_failure_is_never_retried(self):
        from langchain.agents import create_agent
        from langchain.agents.middleware import HumanInTheLoopMiddleware
        from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
        from langchain_core.messages import AIMessage,HumanMessage
        from langchain_core.tools import tool
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.types import Command
        from concierge.agents.reliability import ConciergeToolBoundary
        calls=[]
        class ToolModel(FakeMessagesListChatModel):
            def bind_tools(self,tools,**kwargs):return self
        @tool
        async def book_event_ticket() -> str:
            """A simulated booking write, never a real database operation."""
            calls.append('write started')
            raise RuntimeError('uncertain simulated write')
        model=ToolModel(responses=[AIMessage(content='',tool_calls=[{'name':'book_event_ticket','id':'save-1','args':{}}]),AIMessage(content='Finished')])
        agent=create_agent(model,tools=[book_event_ticket],middleware=[HumanInTheLoopMiddleware(interrupt_on={'book_event_ticket':{'allowed_decisions':['approve','reject']}},interrupt_mode='per_call'),ConciergeToolBoundary()],checkpointer=MemorySaver())
        evidence=TurnEvidence();token=turn_evidence.set(evidence)
        try:
            config={'configurable':{'thread_id':'approval-boundary-test'}}
            paused=await agent.ainvoke({'messages':[HumanMessage('Prepare this booking.')]},config)
            self.assertEqual(calls,[])
            self.assertFalse(evidence.write_started)
            interrupt=paused['__interrupt__'][0]
            with self.assertRaises(RuntimeError):
                await agent.ainvoke(Command(resume={interrupt.id:{'type':'approve'}}),config)
            self.assertEqual(calls,['write started'])
            self.assertTrue(evidence.write_started)
        finally:turn_evidence.reset(token)

    async def test_real_langchain_agent_can_continue_after_a_tool_exception(self):
        from langchain.agents import create_agent
        from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
        from langchain_core.messages import AIMessage,HumanMessage,ToolMessage
        from langchain_core.tools import tool
        from concierge.agents.reliability import ConciergeToolBoundary
        class ToolModel(FakeMessagesListChatModel):
            def bind_tools(self,tools,**kwargs):return self
        @tool
        def unavailable_lookup() -> str:
            """Look up a chamber service."""
            raise RuntimeError('source temporarily offline')
        model=ToolModel(responses=[AIMessage(content='',tool_calls=[{'name':'unavailable_lookup','id':'call-1','args':{}}]),AIMessage(content='I could not check that source. I can help prepare a staff inquiry.')])
        agent=create_agent(model,tools=[unavailable_lookup],middleware=[ConciergeToolBoundary()])
        result=await agent.ainvoke({'messages':[HumanMessage('Check this service.')]})
        self.assertEqual(result['messages'][-1].content,'I could not check that source. I can help prepare a staff inquiry.')
        self.assertTrue(any(isinstance(m,ToolMessage) and m.status=='error' for m in result['messages']))


class BackendRecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_model_failure_is_typed_and_preserves_context(self):
        from concierge.api.agent_backend import AgentBackend
        backend=AgentBackend.__new__(AgentBackend)
        backend._chat=AsyncMock(side_effect=RuntimeError('provider failed'))
        backend.graph=SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(values={'conversation_brief':{'products':{'value':'cheese','quote':'We sell cheese'}}})))
        with self.assertRaises(ReadOnlyTurnFailure) as caught:
            await backend.chat([],None,'read-test')
        self.assertEqual(caught.exception.brief['products']['value'],'cheese')
        backend._chat.assert_awaited_once()

    async def test_read_only_timeout_has_the_same_typed_recovery(self):
        from concierge.api.agent_backend import AgentBackend
        backend=AgentBackend.__new__(AgentBackend)
        async def waiting(*args):await asyncio.Event().wait()
        backend._chat=waiting
        backend.graph=SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(values={})))
        with patch('concierge.api.agent_backend.get_settings',return_value=SimpleNamespace(agent_timeout_seconds=.05)):
            with self.assertRaises(ReadOnlyTurnFailure):await backend.chat([],None,'timeout-test')

    async def test_post_write_failure_is_not_misreported_as_no_action(self):
        from concierge.api.agent_backend import AgentBackend
        backend=AgentBackend.__new__(AgentBackend)
        async def uncertain(*args):
            turn_evidence.get().write_started=True
            raise RuntimeError('write outcome uncertain')
        backend._chat=uncertain
        with self.assertRaises(RuntimeError) as caught:
            await backend.chat([],1,'write-test')
        self.assertNotIsInstance(caught.exception,ReadOnlyTurnFailure)


class RecoveryApiTest(unittest.TestCase):
    setUp=contract.ApiContractTest.setUp
    tearDown=contract.ApiContractTest.tearDown

    def test_streaming_recovery_remains_a_result_not_an_unhandled_error(self):
        async def fails(*args):raise ReadOnlyTurnFailure()
        self.backend.chat=fails
        result=self.client.post('/api/v1/chat/stream',json={'message':'Check market entry support','locale':'en'})
        self.assertEqual(result.status_code,200)
        self.assertIn('event: result',result.text)
        self.assertIn('"mode": "recovery"',result.text)
        self.assertNotIn('event: error',result.text)

    def test_read_only_recovery_is_saved_and_conversation_can_continue(self):
        original=self.backend.chat
        async def fails(*args):raise ReadOnlyTurnFailure(brief={'products':{'value':'cheese','quote':'We sell cheese'}})
        self.backend.chat=fails
        response=self.client.post('/api/v1/chat',json={'message':'Help with distributors','locale':'fr'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['mode'],'recovery')
        self.assertIn('answer_unavailable',response.json()['warnings'])
        self.assertEqual(response.json()['pending_approvals'],[])
        token=response.json()['session_id']
        history=self.client.get('/api/v1/sessions/'+token+'/messages').json()
        self.assertEqual(history['turn_status'],'ready')
        self.backend.chat=original
        self.assertEqual(self.client.post('/api/v1/chat',json={'session_id':token,'message':'Continue'}).status_code,200)


class RetrievalIsolationTest(unittest.TestCase):
    def test_vector_failure_keeps_keyword_evidence(self):
        from concierge.rag.src import rag_search as rag
        with patch.object(rag,'get_tokenizer',return_value=SimpleNamespace(encode=lambda *a,**k:[1])), patch.object(rag,'get_collection',side_effect=RuntimeError('vector unavailable')), patch.object(rag,'lexical_search',return_value=[{'id':1,'text':'# Support\nurl: https://www.ccifj.or.jp/support.html\nUseful evidence'}]), patch.object(rag,'rerank',side_effect=TimeoutError):
            result=rag.search_passages(object(),object(),'support')
        self.assertEqual(result[0]['id'],'1')
        self.assertEqual(result[0]['url'],'https://www.ccifj.or.jp/support.html')

    def test_keyword_failure_keeps_vector_evidence(self):
        from concierge.rag.src import rag_search as rag
        with patch.object(rag,'get_tokenizer',return_value=SimpleNamespace(encode=lambda *a,**k:[1])), patch.object(rag,'get_collection',return_value=SimpleNamespace(count=lambda:1)), patch.object(rag,'semantic_search',return_value=[{'id':2,'content':'# Support\nUseful evidence','url':'https://www.ccifj.or.jp/support.html'}]), patch.object(rag,'lexical_search',side_effect=RuntimeError('index unavailable')), patch.object(rag,'rerank',side_effect=TimeoutError):
            result=rag.search_passages(object(),object(),'support')
        self.assertEqual(result[0]['id'],'vector:2')

    def test_total_failure_is_not_reported_as_no_matching_documents(self):
        from concierge.rag.src import rag_search as rag
        with patch.object(rag,'get_tokenizer',return_value=SimpleNamespace(encode=lambda *a,**k:[1])):
            with self.assertRaises(rag.RetrievalUnavailable):rag.search_passages(None,None,'support')

    def test_malformed_records_are_not_reported_as_a_real_empty_search(self):
        from concierge.rag.src import rag_search as rag
        with patch.object(rag,'get_tokenizer',return_value=SimpleNamespace(encode=lambda *a,**k:[1])), patch.object(rag,'get_collection',return_value=SimpleNamespace(count=lambda:0)), patch.object(rag,'lexical_search',return_value=['outdated bare-text index record']):
            with self.assertRaises(rag.RetrievalUnavailable):rag.search_passages(object(),object(),'support')

    def test_missing_tokenizer_still_allows_local_evidence(self):
        from concierge.rag.src import rag_search as rag
        with patch.object(rag,'get_tokenizer',side_effect=RuntimeError('tokenizer unavailable')), patch.object(rag,'get_collection',return_value=SimpleNamespace(count=lambda:0)), patch.object(rag,'lexical_search',return_value=[{'id':3,'text':'url: https://www.ccifj.or.jp/support.html\nEvidence'}]), patch.object(rag,'rerank',side_effect=TimeoutError):
            result=rag.search_passages(object(),object(),'support')
        self.assertEqual(result[0]['id'],'3')
