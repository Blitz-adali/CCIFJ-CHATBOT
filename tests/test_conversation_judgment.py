"""Topic-independent mechanics and counterexamples for conversational guidance."""
import unittest
import json
from concierge.conversation import BriefUpdate,merge_brief,brief_prompt,interaction_prompt
from langchain_core.messages import HumanMessage,AIMessage


class ConversationJudgmentTest(unittest.TestCase):
    def test_qualified_facts_keep_exact_wording_across_different_fields_and_languages(self):
        cases=[('timeline','September','We hope to visit in September.'),
               ('attendees','thirty','Our tentative headcount is thirty.'),
               ('job_role','engineer','We might recruit an engineer.'),
               ('objective','une formation','Nous envisageons une formation.'),
               ('timeline','来春','来春の訪問を検討しています。')]
        for field,value,text in cases:
            with self.subTest(text=text):
                brief=merge_brief({},[BriefUpdate(field=field,value=value,quote=text)],text)
                prompt=brief_prompt(brief)
                self.assertIn(text,prompt)
                self.assertIn('_qualified_user_wording',prompt)

    def test_explicit_confirmation_replaces_tentative_wording(self):
        old='We hope to visit in September.'
        brief=merge_brief({},[BriefUpdate(field='timeline',value='September',quote=old)],old)
        confirmed='Our visit is confirmed for October.'
        brief=merge_brief(brief,[BriefUpdate(field='timeline',value='October',quote=confirmed)],confirmed)
        prompt=brief_prompt(brief)
        self.assertIn('October',prompt)
        self.assertNotIn('September',prompt)
        self.assertNotIn('_qualified_user_wording',json.loads(prompt.splitlines()[-1]))

    def test_month_and_company_names_are_not_tentative_intentions(self):
        for field,value,text in [('timeline','May','Our meeting is on May 12.'),('company_name','Hope Labs','Our company is Hope Labs.')]:
            brief=merge_brief({},[BriefUpdate(field=field,value=value,quote=text)],text)
            self.assertNotIn('_qualified_user_wording',json.loads(brief_prompt(brief).splitlines()[-1]))

    def test_offer_history_works_across_topics_and_languages(self):
        for text in ['I can help draft a question about membership.',
                     'Would you like a draft about training?',
                     'Je peux rédiger un courriel à propos du sponsoring.',
                     '必要なら、担当者へのメールの下書きを作成できます。']:
            with self.subTest(text=text):
                prompt=interaction_prompt([AIMessage(text),HumanMessage('Tell me more about the service.')])
                self.assertIn('already offered',prompt)
                self.assertIn('If the visitor now asks for a draft, continue normally',prompt)
                self.assertIn('never authorizes a write',prompt)

    def test_user_quote_or_draft_body_does_not_count_as_an_assistant_offer(self):
        self.assertEqual(interaction_prompt([HumanMessage('I can help draft an email.')]),'')
        self.assertEqual(interaction_prompt([AIMessage('```email\nI can help draft a message.\n```')]),'')

    def test_old_offers_expire_and_guidance_stays_bounded(self):
        messages=[AIMessage('I can help draft an inquiry.')]+[AIMessage('Answer '+str(i)) for i in range(8)]
        self.assertEqual(interaction_prompt(messages),'')
        prompt=interaction_prompt([AIMessage('I can help draft a message.')]*100)
        self.assertLess(len(prompt),1600)

    def test_worker_context_contains_offer_history_without_extra_model_calls(self):
        from concierge.agents.body import _worker_messages
        result=_worker_messages({'messages':[AIMessage('I can help draft a newsletter inquiry.'),HumanMessage('What are the available newsletters?')]})
        self.assertIn('RECENT OPTIONAL DRAFT OFFERS',result[0].content)
