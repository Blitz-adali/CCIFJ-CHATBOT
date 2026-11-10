"""Exercise the real routing/collection boundary and model-free next steps."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage


class FollowupFlowTest(unittest.IsolatedAsyncioTestCase):
    async def test_router_questions_survive_single_domain_answers_in_multiple_languages(self):
        from concierge.agents import body
        from concierge.agents.schema_prompts import OrchestratorPlan, TaskItem
        for domain, lang, question in [
            ('general', 'en', 'How can I compare support options?'),
            ('membership', 'fr', 'Quels avantages correspondent à mon entreprise ?'),
            ('general', 'ja', '次に何を確認すればよいですか？'),
        ]:
            with self.subTest(domain=domain, lang=lang):
                model=MagicMock()
                model.with_structured_output.return_value.ainvoke=AsyncMock(return_value=OrchestratorPlan(
                    scope='in_scope', lang=lang, tasks=[TaskItem(target=domain,sub_query='Answer this request')],
                    suggested_questions=[question]))
                state={'messages':[HumanMessage('Help me choose the next step.')],'conversation_brief':{},'user_id':None}
                with patch.object(body,'llm_routing',model):
                    routed=await body.orchestrator_node(state)
                with patch.object(body,'llm',MagicMock(side_effect=AssertionError('No extra model call'))):
                    answer=await body.collector_node({**state,**routed,'worker_results':{domain:'A useful answer.'}})
                self.assertEqual(answer['suggested_questions'],[question])
                self.assertEqual(answer['messages'][0].content,'A useful answer.')

    async def test_plain_text_synthesis_failure_keeps_candidate_questions(self):
        from concierge.agents import body
        model=MagicMock()
        model.with_structured_output.return_value.ainvoke=AsyncMock(side_effect=ValueError('Invalid structured output'))
        model.ainvoke=AsyncMock(return_value=AIMessage('A combined answer.'))
        with patch.object(body,'llm',model):
            result=await body.collector_node({'messages':[HumanMessage('Compare services and membership.')],
                'worker_results':{'general':'Services','membership':'Membership'},
                'suggested_questions':['What should I prepare next?'],'lang':'en'})
        self.assertEqual(result['messages'][0].content,'A combined answer.')
        self.assertEqual(result['suggested_questions'],['What should I prepare next?'])

    async def test_empty_questions_remain_empty_for_a_draft_only_answer(self):
        from concierge.agents import body
        result=await body.collector_node({'messages':[HumanMessage('Show the draft only.')],
            'worker_results':{'general':'```email\nTo: team@example.test\nSubject: Inquiry\n\nHello\n```'},
            'suggested_questions':[]})
        self.assertEqual(result['suggested_questions'],[])

    async def test_membership_faqs_have_localized_non_repeating_next_steps(self):
        from concierge.api.answers import common_answer
        from concierge.api.catalog import localized_question
        category=SimpleNamespace(category_name='Example',annual_price=100,currency='JPY')
        for locale in ('en','fr','ja'):
            for question in ('What are the CCIFJ membership fees?','How do I become a CCIFJ member?'):
                with self.subTest(locale=locale, question=question), patch('concierge.api.answers.crud.list_member_categories',AsyncMock(return_value=[category])):
                    result=await common_answer(localized_question(question,locale),locale,None)
                self.assertEqual(len(result['suggested_questions']),2)
                self.assertNotIn(localized_question(question,locale),result['suggested_questions'])
                self.assertIn(localized_question('What are the benefits of CCIFJ membership?',locale),result['suggested_questions'])

    async def test_missing_membership_data_does_not_offer_a_misleading_journey(self):
        from concierge.api.answers import common_answer
        with patch('concierge.api.answers.crud.list_member_categories',AsyncMock(return_value=[])):
            result=await common_answer('What are the CCIFJ membership fees?','en',None)
        self.assertEqual(result['suggested_questions'],[])
