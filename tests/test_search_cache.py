import unittest
from unittest.mock import patch
from concierge.rag.src.search_cache import SearchCache,cache_key


class SearchCacheTest(unittest.TestCase):
    def test_ttl_and_defensive_copies(self):
        now=[0.0];cache=SearchCache(ttl=10,clock=lambda:now[0])
        original=[{'id':'1','text':'Public evidence','url':'https://www.ccifj.or.jp/'}]
        cache.put('key',original);original[0]['text']='Mutated'
        result=cache.get('key');self.assertEqual(result[0]['text'],'Public evidence')
        result[0]['text']='Also mutated';self.assertEqual(cache.get('key')[0]['text'],'Public evidence')
        now[0]=10.0;self.assertIsNone(cache.get('key'))

    def test_lru_and_empty_results(self):
        cache=SearchCache(capacity=2)
        cache.put('a',[{'id':'1'}]);cache.put('b',[{'id':'2'}]);cache.get('a');cache.put('c',[{'id':'3'}])
        self.assertIsNone(cache.get('b'));self.assertIsNotNone(cache.get('a'))
        cache.put('empty',[]);self.assertIsNone(cache.get('empty'))

    def test_key_hashes_query_and_changes_with_index_generation(self):
        retriever,model=object(),object()
        from types import SimpleNamespace
        with patch('pathlib.Path.stat',return_value=SimpleNamespace(st_mtime_ns=1,st_size=10)):
            first=cache_key('private company name',retriever,model)
        with patch('pathlib.Path.stat',return_value=SimpleNamespace(st_mtime_ns=2,st_size=10)):
            second=cache_key('private company name',retriever,model)
        self.assertNotEqual(first,second)
        self.assertNotIn('private company name',repr(first))

    def test_public_tool_reuses_only_a_successful_result_and_records_sources_each_turn(self):
        from concierge.agents import tools
        from concierge.evidence import TurnEvidence,turn_evidence,record_warning
        cache=SearchCache();retriever,model=object(),object()
        passages=[{'id':'1','text':'Public evidence','url':'https://www.ccifj.or.jp/support.html'}]
        with patch.object(tools,'_get_embed_model',return_value=model),patch.object(tools,'_get_retriever',return_value=retriever),patch.object(tools,'search_passages',return_value=passages) as lookup,patch('concierge.rag.src.search_cache.public_search_cache',cache):
            for _ in range(2):
                evidence=TurnEvidence();token=turn_evidence.set(evidence)
                try:tools.search_vectordb.invoke({'query':'CCIFJ services'})
                finally:turn_evidence.reset(token)
                self.assertEqual(evidence.passages,passages)
            self.assertEqual(lookup.call_count,1)
        cache=SearchCache()
        def degraded(*args, **kwargs):
            record_warning('retrieval_semantic_unavailable')
            return passages
        with patch.object(tools,'_get_embed_model',return_value=model),patch.object(tools,'_get_retriever',return_value=retriever),patch.object(tools,'search_passages',side_effect=degraded) as lookup,patch('concierge.rag.src.search_cache.public_search_cache',cache):
            for _ in range(2):
                token=turn_evidence.set(TurnEvidence())
                try:tools.search_vectordb.invoke({'query':'CCIFJ services'})
                finally:turn_evidence.reset(token)
            self.assertEqual(lookup.call_count,2)


class RerankQuotaTest(unittest.TestCase):
    """A refused or over-budget reranker is not called again and again."""

    def setUp(self):
        from concierge.rag.src import rag_search as rag
        self.rag = rag
        rag._rerank_calls.clear()
        rag._rerank_paused_until = 0.0
        self.docs = [{"id": str(n), "text": f"passage {n}", "url": "https://www.ccifj.or.jp/x.html"} for n in range(6)]

    def tearDown(self):
        self.rag._rerank_calls.clear()
        self.rag._rerank_paused_until = 0.0

    def test_one_attempt_then_a_pause_after_a_rate_limit(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        class RateLimited(Exception):
            status_code = 429

        calls = []

        def refuse(**kwargs):
            calls.append(kwargs)
            raise RateLimited()

        client = SimpleNamespace(rerank=refuse)
        with patch.object(self.rag, "COHERE_API_KEY", "key"), patch.object(self.rag, "get_rerank_client", return_value=client):
            with self.assertRaises(RateLimited):
                self.rag.rerank("q", self.docs, 3)
            with self.assertRaises(self.rag.RerankSkipped):
                self.rag.rerank("q again", self.docs, 3)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["request_options"], {"max_retries": 0})

    def test_stays_under_the_per_minute_budget(self):
        import os
        from types import SimpleNamespace
        from unittest.mock import patch
        response = SimpleNamespace(results=[SimpleNamespace(index=0, relevance_score=0.9)])
        client = SimpleNamespace(rerank=lambda **kwargs: response)
        with patch.object(self.rag, "COHERE_API_KEY", "key"), patch.object(self.rag, "get_rerank_client", return_value=client), \
                patch.dict(os.environ, {"CCIFJ_RERANK_PER_MINUTE": "2"}):
            self.assertEqual(self.rag.rerank("a", self.docs, 1)[0]["score"], 0.9)
            self.rag.rerank("b", self.docs, 1)
            with self.assertRaises(self.rag.RerankSkipped):
                self.rag.rerank("c", self.docs, 1)
