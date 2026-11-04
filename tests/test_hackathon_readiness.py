"""Regression checks for provenance, retrieval fallback, staff access and recovery."""
import asyncio
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_api_contract as contract
from concierge.api import chat, staff
from concierge.config import get_settings
from concierge.db.relationaldb import crud, models
from sqlmodel.ext.asyncio.session import AsyncSession


class HackathonApiTest(unittest.TestCase):
    setUp = contract.ApiContractTest.setUp
    tearDown = contract.ApiContractTest.tearDown

    def account(self, email):
        result = self.client.post('/api/v1/auth/register', json={'name':'Operator', 'email':email, 'password':'test-password'})
        self.assertEqual(result.status_code, 201)
        return result.json(), {'Authorization':'Bearer '+result.json()['access_token']}

    def test_staff_inbox_is_denied_by_default_and_allowlist_is_server_only(self):
        self.client.app.include_router(staff.router, prefix='/api/v1')
        account, headers = self.account('operator@example.com')
        self.assertFalse(account['user']['is_staff'])
        self.assertEqual(self.client.get('/api/v1/staff/requests').status_code, 401)
        self.assertEqual(self.client.get('/api/v1/staff/requests', headers=headers).status_code, 403)
        # Signup cannot grant staff access, including by sending an extra field.
        self.assertEqual(self.client.post('/api/v1/auth/register', json={'name':'Fake','email':'fake@example.com','password':'test-password','is_staff':True}).status_code, 422)
        async def seed():
            async with AsyncSession(self.engine, expire_on_commit=False) as db:
                request = await crud.add_staff_request(db, request_type='staff_email', category='membership', subject='Market entry support', message='Private approved request', sender_name='Guest', sender_email='guest@example.com', recipient_emails=['staff@ccifj.or.jp'])
                return request.reference
        reference = self.client.portal.call(seed)
        settings = replace(get_settings(), staff_user_ids=(account['user']['user_id'],))
        with patch('concierge.api.staff.get_settings', return_value=settings), patch('concierge.api.auth.get_settings', return_value=settings):
            self.assertTrue(self.client.get('/api/v1/auth/me', headers=headers).json()['is_staff'])
            response = self.client.get('/api/v1/staff/requests?status=new&limit=1', headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['cache-control'], 'no-store')
            row = response.json()['items'][0]
            self.assertEqual(row['reference'], reference)
            self.assertEqual(row['recipients'], ['staff@ccifj.or.jp'])
            self.assertEqual(row['message'], 'Private approved request')
            self.assertEqual(self.client.get('/api/v1/staff/requests?status=closed', headers=headers).json()['items'], [])
            self.assertEqual(self.client.get('/api/v1/staff/requests?limit=500', headers=headers).status_code, 422)

    def test_guided_event_cards_work_without_loading_models(self):
        self.client.app.dependency_overrides.pop(chat.agent_backend)
        async def seed():
            async with AsyncSession(self.engine, expire_on_commit=False) as db:
                now = crud._now()
                event = models.Event(title='Future Summit', description='Meet business leaders.', category='Networking', start_at=now+timedelta(days=8), reg_deadline=now+timedelta(days=7), price=None, currency='JPY')
                db.add(event)
                await db.commit()
                await db.refresh(event)
                return event.event_id
        event_id = self.client.portal.call(seed)
        with patch('concierge.api.chat.get_agent_backend', side_effect=AssertionError('Guided paths must not initialize models')):
            response = self.client.post('/api/v1/chat', json={'message':'Événements', 'locale':'fr', 'path_step_id':'events.upcoming'})
            self.assertEqual(response.status_code, 200)
            card = response.json()['events'][0]
            self.assertEqual(card['event_id'], event_id)
            self.assertIsNone(card['price'])
            self.assertTrue(card['registration_open'])
            self.assertTrue(card['start_at'].endswith('+09:00'))
            details = self.client.post('/api/v1/chat', json={'message':'Détails', 'locale':'fr', 'session_id':response.json()['session_id'], 'path_step_id':'events.details', 'path_event_id':str(event_id)})
            self.assertEqual(details.status_code, 200)
            self.assertIn('Meet business leaders.', details.json()['reply'])

    def test_lost_approval_is_released_without_replaying_action(self):
        from concierge.api.agent_backend import ApprovalStateUnavailable
        first = self.client.post('/api/v1/chat', json={'message':'book'}).json()
        async def expired(*args):
            raise ApprovalStateUnavailable()
        self.backend.resume = expired
        path = '/api/v1/sessions/'+first['session_id']+'/approvals'
        response = self.client.post(path, json={'interrupt_id':'approval-1','decision':'approve','locale':'en'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['pending_approvals'], [])
        self.assertIn('approval_expired', response.json()['warnings'])
        self.assertEqual(self.client.get(path).json()['pending_approvals'], [])
        self.assertEqual(self.client.post('/api/v1/chat', json={'session_id':first['session_id'],'message':'hello'}).status_code, 200)

    def test_tool_evidence_is_persisted_for_approval_resume(self):
        from concierge.api.agent_backend import agent_inputs
        original = self.backend.chat
        evidence = {'passages':[{'id':'1','url':'https://www.ccifj.or.jp/en.html','title':'CCIFJ'}], 'event_ids':[], 'warnings':[], 'resources':[]}
        async def pending(*args):
            result = await original(*args)
            result['agent_state'] = {'out_of_scope_count':0,'tool_evidence':evidence}
            return result
        async def resumed(*args):
            self.assertEqual(agent_inputs.get()['tool_evidence'], evidence)
            return {'reply':'Action rejected; nothing saved.'}
        self.backend.chat, self.backend.resume = pending, resumed
        first = self.client.post('/api/v1/chat', json={'message':'book'}).json()
        response = self.client.post('/api/v1/sessions/'+first['session_id']+'/approvals', json={'interrupt_id':'approval-1','decision':'reject'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['pending_approvals'], [])


class RetrievalReliabilityTest(unittest.TestCase):
    def test_rerank_failure_keeps_fused_evidence_and_records_warning(self):
        from concierge.evidence import TurnEvidence, turn_evidence
        from concierge.rag.src import rag_search as rag
        evidence = TurnEvidence()
        token = turn_evidence.set(evidence)
        records = [{'id':'9','content':'# Support\nurl: https://www.ccifj.or.jp/support.html\nHelpful passage', 'url':'https://www.ccifj.or.jp/support.html'}]
        try:
            with patch.object(rag,'get_tokenizer',return_value=SimpleNamespace(encode=lambda *a,**k:[1])), patch.object(rag,'get_collection',return_value=SimpleNamespace(count=lambda:1)), patch.object(rag,'semantic_search',return_value=records), patch.object(rag,'lexical_search',return_value=[]), patch.object(rag,'rerank',side_effect=TimeoutError):
                result = rag.search_passages(object(),object(),'support')
            self.assertEqual(result[0]['id'],'vector:9')
            self.assertEqual(result[0]['url'],'https://www.ccifj.or.jp/support.html')
            self.assertEqual(evidence.warnings,['retrieval_rerank_fallback'])
        finally:
            turn_evidence.reset(token)

    def test_fresh_index_records_keep_ids_and_urls_for_fusion(self):
        from concierge.rag.src import index_init, rag_search
        captured = {}
        class FakeBM25:
            def __init__(self,corpus): captured['records']=corpus
            def index(self,tokens): pass
            def save(self,path): pass
        source = SimpleNamespace(id=7,content='A useful passage',url='https://www.ccifj.or.jp/page.html',model_dump=lambda **k:{'id':7,'content':'A useful passage','url':'https://www.ccifj.or.jp/page.html'})
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {'bm25s':SimpleNamespace(BM25=FakeBM25,tokenize=lambda texts:texts)}), patch.object(index_init,'INDEX_PATH',Path(directory)/'index'), patch.object(index_init,'CHUNKS_PATH',Path(directory)/'chunks.json'):
            index_init.lexical_index([source])
            self.assertEqual(captured['records'],[{'id':7,'text':'A useful passage','url':source.url}])
            fused = rag_search.rrf([], captured['records'], 5)
            self.assertEqual(fused[0][0],7)
            self.assertEqual(fused[0][1][1],'A useful passage')

    def test_model_links_do_not_create_sources_and_unknown_links_are_removed(self):
        from concierge.api.presentation import evidence_sources, grounded_links
        source='https://www.ccifj.or.jp/support.html'
        sources=evidence_sources([{'id':'1','url':source,'title':'Support'},{'id':'2','url':source,'title':'Support'},{'id':'3','url':'javascript:alert(1)'}])
        self.assertEqual(len(sources),1)
        self.assertEqual(sources[0]['chunk_ids'],['1','2'])
        text=grounded_links(f'[Support]({source}) [Invented](https://example.com/fake)',{source})
        self.assertIn(f'[Support]({source})',text)
        self.assertNotIn('example.com',text)

    def test_request_evidence_does_not_leak_between_conversations(self):
        from concierge.evidence import TurnEvidence, turn_evidence, record_events
        async def turn(event_id):
            token=turn_evidence.set(TurnEvidence())
            try:
                record_events([event_id])
                await asyncio.sleep(0)
                return turn_evidence.get().event_ids
            finally:
                turn_evidence.reset(token)
        async def run(): return await asyncio.gather(turn(1),turn(2))
        self.assertEqual(asyncio.run(run()),[[1],[2]])


if __name__=='__main__':
    unittest.main()
