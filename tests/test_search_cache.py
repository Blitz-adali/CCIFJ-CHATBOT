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
