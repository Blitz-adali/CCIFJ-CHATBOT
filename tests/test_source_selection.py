import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import asdict
from concierge.evidence import TurnEvidence, turn_evidence, source_candidates, selected_sources

A = 'https://www.ccifj.or.jp/en/membership/benefits.html'
B = 'https://www.ccifj.or.jp/en/commercial-support.html'

class SourceSelectionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.evidence = TurnEvidence(passages=[{'id':'a','url':A,'text':'Membership includes networking.'}, {'id':'b','url':B,'text':'Market studies are available.'}])

    def test_only_selected_evidence_is_public_without_inline_links(self):
        candidates = source_candidates(asdict(self.evidence))
        selected = next(sid for sid, item in candidates.items() if item['url'] == A)
        sources = selected_sources(candidates, [selected, 'invented'], 'Members can network.')
        self.assertEqual([s['url'] for s in sources], [A])
        self.assertNotIn('text', sources[0])
        self.assertEqual(selected_sources(candidates, [], 'No factual answer.'), [])

    def test_equivalent_urls_share_one_stable_id(self):
        first = source_candidates(asdict(self.evidence))
        self.evidence.passages.append({'id':'c','url':A+'/#details','text':'More details.'})
        second = source_candidates(asdict(self.evidence))
        self.assertEqual(list(first), list(second))
        self.assertIn('More details.', next(iter(second.values()))['text'])

    def test_fallback_keeps_only_verified_explicit_citations(self):
        candidates = source_candidates(asdict(self.evidence))
        sources = selected_sources(candidates, [], f'See [benefits]({A}). [Fake](https://example.com)')
        self.assertEqual([s['url'] for s in sources], [A])

    async def test_single_worker_uses_structured_collector_with_evidence(self):
        from langchain_core.messages import HumanMessage
        from concierge.agents import body
        from concierge.agents.schema_prompts import CollectorOutput
        candidates = source_candidates(asdict(self.evidence))
        selected = next(iter(candidates))
        model = MagicMock()
        model.with_structured_output.return_value.ainvoke = AsyncMock(return_value=CollectorOutput(answer='Members can network.', used_source_ids=[selected, 'invented']))
        token = turn_evidence.set(self.evidence)
        try:
            with patch.object(body, 'llm', model):
                result = await body.collector_node({'messages':[HumanMessage('What are the benefits?')], 'worker_results':{'membership':'Members can network.'}, 'lang':'en'})
        finally:
            turn_evidence.reset(token)
        self.assertEqual(result['used_source_ids'], [selected])
        context = model.with_structured_output.return_value.ainvoke.call_args.args[0][1]['content']
        self.assertIn('Membership includes networking.', context)
        from concierge.api.agent_backend import AgentBackend
        backend = object.__new__(AgentBackend)
        backend.pending = lambda result: []
        result['tool_evidence'] = asdict(self.evidence)
        answer = await backend.format_result(result)
        self.assertEqual([s['url'] for s in answer['sources']], [A])

    async def test_multi_worker_drops_sources_for_omitted_findings(self):
        from langchain_core.messages import HumanMessage
        from concierge.agents import body
        from concierge.agents.schema_prompts import CollectorOutput
        selected = next(iter(source_candidates(asdict(self.evidence))))
        model = MagicMock()
        model.with_structured_output.return_value.ainvoke = AsyncMock(return_value=CollectorOutput(answer='Members can network.', used_source_ids=[selected]))
        token = turn_evidence.set(self.evidence)
        try:
            with patch.object(body, 'llm', model):
                result = await body.collector_node({'messages':[HumanMessage('Focus on membership.')], 'worker_results':{'membership':'Networking benefits', 'general':'Market studies'}, 'lang':'en'})
        finally:
            turn_evidence.reset(token)
        sources = selected_sources(source_candidates(asdict(self.evidence)), result['used_source_ids'], result['messages'][0].content)
        self.assertEqual([s['url'] for s in sources], [A])

    async def test_structured_failure_does_not_publish_all_candidates(self):
        from langchain_core.messages import HumanMessage, AIMessage
        from concierge.agents import body
        model = MagicMock()
        model.with_structured_output.return_value.ainvoke = AsyncMock(side_effect=RuntimeError('unavailable'))
        model.ainvoke = AsyncMock(return_value=AIMessage('Please contact the team.'))
        token = turn_evidence.set(self.evidence)
        try:
            with patch.object(body, 'llm', model):
                result = await body.collector_node({'messages':[HumanMessage('Help')], 'worker_results':{'general':'Help'}, 'lang':'en'})
        finally:
            turn_evidence.reset(token)
        self.assertEqual(result['used_source_ids'], [])
        self.assertEqual(result['messages'][0].content, 'Please contact the team.')
