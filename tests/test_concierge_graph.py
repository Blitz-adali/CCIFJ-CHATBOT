"""Real graph-node tests with model boundaries mocked, not canned app replies."""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class ConciergeGraphTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # Import once; production graph/tools are real, model calls are patched
        # at each test boundary. No credentials or network calls are needed.
        from concierge.agents import body
        cls.body=body

    async def test_router_quotes_only_latest_user_facts_and_replaces_budget(self):
        from langchain_core.messages import HumanMessage
        from concierge.agents.schema_prompts import OrchestratorPlan, TaskItem
        from concierge.conversation import BriefUpdate
        text='Correction: our budget is EUR 800, not EUR 1500.'
        plan=OrchestratorPlan(scope='in_scope',tasks=[TaskItem(target='general',sub_query='Recommend a test under the corrected budget')],memory_updates=[BriefUpdate(field='service_budget',value='EUR 800',quote=text)])
        router=MagicMock();router.with_structured_output.return_value.ainvoke=AsyncMock(return_value=plan)
        state={'messages':[HumanMessage(text)],'conversation_brief':{'service_budget':{'value':'EUR 1500','quote':'Budget EUR 1500'}},'user_id':None}
        with patch.object(self.body,'llm_routing',router):
            result=await self.body.orchestrator_node(state)
        self.assertEqual(result['conversation_brief']['service_budget']['value'],'EUR 800')
        self.assertEqual(router.with_structured_output.return_value.ainvoke.await_count,1)
        prompt=self.body._user_context_prompt({**state,**result})
        self.assertIn('EUR 800',prompt)
        # The old amount can remain in the correction quote internally, but the
        # brief passed to workers contains the current value only.
        from concierge.conversation import brief_prompt
        self.assertNotIn('EUR 1500',brief_prompt(result['conversation_brief']))

    async def test_single_worker_answer_and_draft_are_not_rewritten(self):
        from langchain_core.messages import HumanMessage
        draft='Here is the revised draft.\n\n```email\nTo: appui.entreprises@ccifj.or.jp\nSubject: Market test\n\nPlease quote a small test.\n```'
        model=MagicMock();model.with_structured_output.side_effect=AssertionError('No collector model call for one specialist')
        with patch.object(self.body,'llm',model):
            result=await self.body.collector_node({'messages':[HumanMessage('Show the complete draft only.')],'worker_results':{'general':draft},'lang':'en'})
        self.assertEqual(result['messages'][0].content,draft)
        self.assertEqual(result['next_actions'],[])

    async def test_multi_worker_answer_still_gets_synthesized(self):
        from langchain_core.messages import HumanMessage
        from concierge.agents.schema_prompts import CollectorOutput
        model=MagicMock();model.with_structured_output.return_value.ainvoke=AsyncMock(return_value=CollectorOutput(answer='Membership and events together.'))
        with patch.object(self.body,'llm',model):
            result=await self.body.collector_node({'messages':[HumanMessage('Show membership and events.')],'worker_results':{'membership':'Fees','events':'No upcoming matches'},'lang':'en'})
        self.assertEqual(result['messages'][0].content,'Membership and events together.')
        self.assertEqual(model.with_structured_output.return_value.ainvoke.await_count,1)

    async def test_single_worker_shortcut_still_runs_scope_review(self):
        from langchain_core.messages import HumanMessage, AIMessage
        from concierge.agents.schema_prompts import FinalPolicyReview
        model=MagicMock();model.with_structured_output.return_value.ainvoke=AsyncMock(return_value=FinalPolicyReview(should_block=True,reason='Unrelated answer'))
        with patch.object(self.body,'llm_routing',model):
            result=await self.body.final_policy_node({'messages':[HumanMessage('Help with CCIFJ'),AIMessage('Unrelated answer')],'scope_status':'in_scope','lang':'en'})
        self.assertIn('CCIFJ',result['messages'][-1].content)
        self.assertEqual(model.with_structured_output.return_value.ainvoke.await_count,1)

    async def test_old_booking_discussion_does_not_block_read_only_followup(self):
        from langchain_core.messages import HumanMessage,AIMessage
        history=[HumanMessage('Book a ticket for me.'),AIMessage('Please log in.'),HumanMessage('Do not book. Which event is cheapest?')]
        self.assertFalse(self.body._anonymous_ticket_request(history))
        self.assertFalse(self.body._anonymous_ticket_request([HumanMessage('How can I book a ticket?')]))
        self.assertTrue(self.body._anonymous_ticket_request([HumanMessage('Please book a ticket for this event.')]))

    async def test_guest_booking_keeps_other_domains_available_and_offers_login(self):
        from langchain_core.messages import HumanMessage
        state={'messages':[HumanMessage('Please book a ticket for this event.')],'lang':'en','user_id':None,
               'plan':{'tasks':[{'target':'events','sub_query':'Book selected event'}]}}
        result=await self.body.events_node(state)
        self.assertIn('events',result['worker_results'])
        self.assertNotIn('messages',result)
        self.assertNotIn('new conversation',result['worker_results']['events'])
        answer=await self.body.collector_node({**state,**result})
        self.assertEqual(answer['next_actions'][0]['id'],'auth.login')

    async def test_first_draft_requires_a_user_language_selection(self):
        from langchain_core.messages import AIMessage,HumanMessage
        agent=type('Agent',(),{})()
        agent.ainvoke=AsyncMock(return_value={'messages':[HumanMessage('Camille Test, camille@example.test.'),AIMessage('```email\nSubject: Example\n\nDraft text\n```')]})
        state={'messages':[HumanMessage('Camille Test, camille@example.test.')],'plan':{'tasks':[{'target':'general','sub_query':'Prepare a staff inquiry'}]},'lang':'en','user_id':None,'conversation_brief':{}}
        with patch.object(self.body,'general_agent',agent):
            result=await self.body.general_node(state)
        self.assertIn('which language',result['worker_results']['general'])
        self.assertNotIn('```email',result['worker_results']['general'])

    async def test_router_preserves_hiring_context_when_its_recent_window_moves_on(self):
        from langchain_core.messages import HumanMessage, AIMessage
        from concierge.agents.schema_prompts import OrchestratorPlan, TaskItem
        model=MagicMock()
        model.with_structured_output.return_value.ainvoke=AsyncMock(return_value=OrchestratorPlan(scope='in_scope',tasks=[TaskItem(target='general',sub_query='Summarize hiring brief')]))
        history=[HumanMessage('The role is a bilingual enterprise account executive, based in Tokyo.')]
        for i in range(9):
            history.extend([AIMessage('Previous reply'),HumanMessage('Follow-up '+str(i))])
        history.append(HumanMessage('Summarize the brief in English.'))
        with patch.object(self.body,'llm_routing',model):
            await self.body.orchestrator_node({'messages':history,'conversation_brief':{}})
        request=model.with_structured_output.return_value.ainvoke.await_args.args[0]
        self.assertIn('enterprise account executive',request[0]['content'])
        self.assertNotIn('enterprise account executive',str(request[1:]))

    async def test_policy_review_receives_context_for_a_short_correction(self):
        from langchain_core.messages import HumanMessage, AIMessage
        from concierge.agents.schema_prompts import FinalPolicyReview
        model=MagicMock()
        model.with_structured_output.return_value.ainvoke=AsyncMock(return_value=FinalPolicyReview(should_block=False,reason='Relevant correction'))
        state={'messages':[HumanMessage('We sell recycling equipment and seek Japanese partners.'),AIMessage('CCIFJ can help.'),HumanMessage('My colleague thinks we sell cosmetics. Correct that misunderstanding.'),AIMessage('You sell recycling equipment.')],'scope_status':'in_scope','lang':'en'}
        with patch.object(self.body,'llm_routing',model):
            result=await self.body.final_policy_node(state)
        self.assertEqual(result,{})
        request=model.with_structured_output.return_value.ainvoke.await_args.args[0]
        self.assertIn('We sell recycling equipment',request[1]['content'])

    async def test_country_adjective_does_not_bypass_first_draft_language_gate(self):
        from langchain_core.messages import AIMessage,HumanMessage
        from concierge.conversation import apply_language_requests
        text='Help me draft an inquiry about finding Japanese food distributors.'
        agent=MagicMock()
        agent.ainvoke=AsyncMock(return_value={'messages':[HumanMessage(text),AIMessage('```email\nSubject: Example\nDraft\n```')]})
        state={'messages':[HumanMessage(text)],'lang':'en','conversation_brief':apply_language_requests({},text)}
        with patch.object(self.body,'general_agent',agent):
            result=await self.body.general_node(state)
        self.assertIn('which language',result['worker_results']['general'])

    async def test_approximate_trimming_does_not_need_provider_tokenizer(self):
        from langchain_core.messages import HumanMessage
        from concierge.agents.utlis import do_trim_messages
        result=do_trim_messages([HumanMessage('A short request about CCIFJ')])
        self.assertEqual(result[0].content,'A short request about CCIFJ')


if __name__=='__main__':unittest.main()
