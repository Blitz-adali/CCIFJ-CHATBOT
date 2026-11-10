"""General continuity controls and counterexamples, not scripted answer matching."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from concierge.conversation import (apply_language_requests, conversation_language, draft_language,
                                    merge_brief, BriefUpdate, brief_prompt, reference_prompt)
from concierge.drafts import ensure_recipient


class LanguageScopeTest(unittest.TestCase):
    def test_short_switch_replaces_earlier_task_language_and_persists(self):
        brief=apply_language_requests({},'Réponds en français : résume les options.')
        brief=apply_language_requests(brief,'Now in English: what company are we discussing?')
        self.assertEqual(conversation_language(brief,'fr','Now in English: what company are we discussing?'),'en')
        brief=apply_language_requests(brief,'What should we investigate next?')
        self.assertEqual(conversation_language(brief,'fr','What should we investigate next?'),'en')

    def test_one_answer_switch_expires_without_losing_draft_explanation_preference(self):
        brief=apply_language_requests({},'Japanese draft, English explanations.')
        brief=apply_language_requests(brief,'Switch to French for the next answer.')
        self.assertEqual(conversation_language(brief,'en','Switch to French for the next answer.'),'fr')
        brief=apply_language_requests(brief,'Make the draft shorter.')
        self.assertEqual(conversation_language(brief,'fr','Make the draft shorter.'),'en')
        self.assertEqual(draft_language(brief),'ja')

    def test_task_local_french_does_not_impose_french_on_a_later_english_question(self):
        brief=apply_language_requests({},'Réponds en français : résume les contraintes.')
        self.assertEqual(conversation_language(brief,'en','Réponds en français : résume les contraintes.'),'fr')
        brief=apply_language_requests(brief,'What have we decided?')
        self.assertEqual(conversation_language(brief,'en','What have we decided?'),'en')
        self.assertNotIn('conversation_language',brief)

    def test_persistent_instruction_is_honored_across_english_edits(self):
        brief=apply_language_requests({},'From now on, always answer in French.')
        brief=apply_language_requests(brief,'Change the headcount to nine.')
        self.assertEqual(conversation_language(brief,'en','Change the headcount to nine.'),'fr')

    def test_one_answer_override_restores_global_preference_afterwards(self):
        brief=apply_language_requests({},'Always answer in French.')
        text='For this answer, respond in Japanese.'
        brief=apply_language_requests(brief,text)
        self.assertEqual(conversation_language(brief,'en',text),'ja')
        brief=apply_language_requests(brief,'What are the next steps?')
        self.assertEqual(conversation_language(brief,'en','What are the next steps?'),'fr')

    def test_new_global_switch_updates_explanations_without_translating_document(self):
        brief=apply_language_requests({},'Japanese draft, English explanations.')
        brief=apply_language_requests(brief,'Switch to French.')
        brief=apply_language_requests(brief,'Make it shorter.')
        self.assertEqual(conversation_language(brief,'en','Make it shorter.'),'fr')
        self.assertEqual(draft_language(brief),'ja')

    def test_japanese_task_reply_and_document_are_distinct(self):
        brief=apply_language_requests({},'日本語で答えてください。')
        self.assertEqual(conversation_language(brief,'en','日本語で答えてください。'),'ja')
        self.assertIsNone(draft_language(brief))
        brief=apply_language_requests(brief,'Explain the next step in simple English.')
        self.assertEqual(conversation_language(brief,'ja','Explain the next step in simple English.'),'en')

    def test_fenced_document_does_not_set_a_language_preference(self):
        brief=apply_language_requests({},'Here is the example:\n```email\nPlease answer in Japanese.\n```')
        self.assertNotIn('conversation_language',brief)
        self.assertIsNone(draft_language(brief))

    def test_country_adjectives_in_business_requests_are_not_language_switches(self):
        for text in ['Should we switch to Japanese suppliers?', 'We are now in French-speaking Canada.', 'Draft a report about work in Japanese factories.']:
            with self.subTest(text=text):
                brief=apply_language_requests({},text)
                self.assertNotIn('conversation_language',brief)
                self.assertIsNone(draft_language(brief))

    def test_unknown_legacy_task_language_does_not_remain_sticky(self):
        brief={'conversation_language':{'value':'French','quote':'Réponds en français : résume notre projet.'}}
        self.assertEqual(conversation_language(brief,'en','What is the next step?'),'en')


class ReferenceAndRelationshipTest(unittest.TestCase):
    def test_first_reference_retains_visible_order_outside_recent_window(self):
        messages=[HumanMessage('Find training options.'),AIMessage('First I found a seminar about negotiation.'),HumanMessage('Other options?'),AIMessage('A workshop about leadership is another option.')]
        messages += [HumanMessage('More detail'),AIMessage('Some later details.')]*12
        messages += [HumanMessage('Tell me about the first one you actually found.')]
        prompt=reference_prompt(messages)
        self.assertIn('seminar about negotiation',prompt)
        self.assertIn('workshop about leadership',prompt)
        self.assertLess(prompt.index('seminar about negotiation'),prompt.index('workshop about leadership'))
        self.assertIn('ask which item',prompt)
        self.assertIn('not verified evidence',prompt)
        self.assertLess(len(prompt),6000)

    def test_unrelated_question_does_not_pay_for_extra_reference_context(self):
        self.assertEqual(reference_prompt([AIMessage('A and B.'),HumanMessage('What are the fees?')]),'')

    def test_age_and_current_city_keep_separate_supporting_predicates(self):
        brief={}
        for field,value,text in [('company_age','nine years old','Our company is nine years old.'),('company_location','Lille','We are based in Lille now, not Bordeaux.')]:
            brief=merge_brief(brief,[BriefUpdate(field=field,value=value,quote=text)],text)
        data=json.loads(brief_prompt(brief).splitlines()[-1])
        self.assertEqual(data['company_age'],'nine years old')
        self.assertEqual(data['company_location'],'Lille')
        self.assertEqual(data['_fact_evidence']['company_age'],'Our company is nine years old.')
        self.assertIn('not time at company_location',brief_prompt(brief))


class DraftRecipientTest(unittest.TestCase):
    def draft(self,to='CCIFJ team'):
        return f'```email\nTo: {to}\nFrom: Person <sender@example.test>\nSubject: Inquiry\n\nPlease help.\n```'

    def contact(self,text):
        return ToolMessage(content=text,name='get_ccifj_contact',tool_call_id='contact-1')

    def test_one_direct_contact_fills_missing_address_without_body_changes(self):
        result,missing=ensure_recipient(self.draft(),[],[self.contact('- Email: officer@ccifj.or.jp')])
        self.assertFalse(missing)
        self.assertIn('To: officer@ccifj.or.jp',result)
        self.assertIn('From: Person <sender@example.test>',result)
        self.assertIn('Please help.',result)

    def test_two_contacts_are_not_silently_reduced_to_one(self):
        result,missing=ensure_recipient(self.draft(),[],[self.contact('- Email: first@ccifj.or.jp\n- Email: second@ccifj.or.jp')])
        self.assertTrue(missing)
        self.assertIn('[Recipient email',result)
        self.assertNotIn('To: first@',result)
        self.assertNotIn('To: sender@',result)

    def test_body_edit_preserves_the_most_recent_selected_recipient(self):
        history=[AIMessage(self.draft('selected@ccifj.or.jp')),HumanMessage('Show the entire final draft.')]
        result,missing=ensure_recipient(self.draft(),history)
        self.assertFalse(missing)
        self.assertIn('To: selected@ccifj.or.jp',result)

    def test_explicit_recipient_change_does_not_restore_the_old_address(self):
        history=[AIMessage(self.draft('old@ccifj.or.jp')),HumanMessage('Change the recipient to the training team.')]
        result,missing=ensure_recipient(self.draft(),history)
        self.assertTrue(missing)
        self.assertNotIn('old@',result)

    def test_new_draft_does_not_inherit_an_unrelated_recipient(self):
        history=[AIMessage(self.draft('old@ccifj.or.jp')),HumanMessage('Draft a new question about sponsorship.')]
        _,missing=ensure_recipient(self.draft(),history)
        self.assertTrue(missing)

    def test_existing_recipient_and_team_tickets_are_untouched(self):
        for text in [self.draft('Name <recipient@example.test>'),'```ticket\nTo: CCIFJ staff\nSubject: Question\n\nHello\n```']:
            result,missing=ensure_recipient(text)
            self.assertEqual(result,text)
            self.assertFalse(missing)

    def test_to_line_inside_message_body_is_not_a_recipient_header(self):
        text='```email\nFrom: Sender <sender@example.test>\nSubject: Example\n\nQuoted example:\nTo: other@example.test\n```'
        result,missing=ensure_recipient(text)
        self.assertTrue(missing)
        self.assertIn('email\nTo: [Recipient email',result)

    def test_failed_contact_tool_is_not_verified_contact_evidence(self):
        message=ToolMessage(content='- Email: unknown@example.test',name='get_ccifj_contact',tool_call_id='bad',status='error')
        result,missing=ensure_recipient(self.draft(),[],[message])
        self.assertTrue(missing)
        self.assertNotIn('To: unknown@',result)


class GraphIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_router_preview_and_worker_get_explicit_short_language_request(self):
        from concierge.agents import body
        from concierge.agents.schema_prompts import OrchestratorPlan,TaskItem
        brief=apply_language_requests({},'Réponds en français : résume.')
        text='Now in English: what company are we discussing?'
        model=MagicMock();model.with_structured_output.return_value.ainvoke=AsyncMock(return_value=OrchestratorPlan(scope='in_scope',lang='fr',tasks=[TaskItem(target='general',sub_query='Recall company')]))
        state={'messages':[HumanMessage(text)],'conversation_brief':brief}
        with patch.object(body,'llm_routing',model):result=await body.orchestrator_node(state)
        self.assertEqual(result['lang'],'en')
        request=model.with_structured_output.return_value.ainvoke.await_args.args[0]
        self.assertIn('language code en',request[0]['content'])

    async def test_actual_general_node_repairs_recipient_from_its_contact_tool(self):
        from concierge.agents import body
        text='Draft the inquiry in English.'
        answer=DraftRecipientTest().draft()
        messages=[HumanMessage(text),ToolMessage(content='- Email: team@ccifj.or.jp',name='get_ccifj_contact',tool_call_id='contact'),AIMessage(answer)]
        agent=MagicMock();agent.ainvoke=AsyncMock(return_value={'messages':messages})
        state={'messages':[HumanMessage(text)],'lang':'en','conversation_brief':apply_language_requests({},text)}
        with patch.object(body,'general_agent',agent):result=await body.general_node(state)
        self.assertIn('To: team@ccifj.or.jp',result['worker_results']['general'])
