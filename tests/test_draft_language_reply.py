"""Answering "which language should the draft use?": short replies, the model's reading, and no loop."""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage

from concierge.conversation import (apply_language_requests, asks_draft_language, draft_language,
                                    draft_language_note, draft_language_question, draft_text_language,
                                    set_draft_language)

DRAFT = '```email\nTo: appui.entreprises@ccifj.or.jp\nSubject: Distributor search\n\nDear team,\nWe sell sports clothing.\n```'
FRENCH_DRAFT = '```email\nSubject: Recherche\n\nBonjour Madame,\nNotre société souhaite votre aide.\nCordialement\n```'


class DraftLanguageChoiceTest(unittest.TestCase):
    def test_bare_answers_are_understood_without_the_model(self):
        expected = {'en': 'en', 'EN': 'en', 'English': 'en', 'in English': 'en', 'english please': 'en',
                    'fr': 'fr', 'francais': 'fr', 'en français': 'fr', 'français svp': 'fr',
                    'ja': 'ja', 'jp': 'ja', '日本語': 'ja', '日本語でお願いします': 'ja'}
        for reply, code in expected.items():
            with self.subTest(reply=reply):
                self.assertEqual(draft_language(apply_language_requests({}, reply, True)), code)

    def test_language_named_in_the_request_itself_is_recognised(self):
        """Seen while recording the demo: "in English to the ... team" was not understood."""
        requests = {
            'Help me write an email in English to the Business Support team asking about a market study.': 'en',
            'Write a message in French asking about membership fees': 'fr',
            'Draft an email in Japanese regarding our booking': 'ja',
            'Rédige un e-mail en français à l’équipe Appui aux entreprises': 'fr',
            'Write the email in English.': 'en',
        }
        for text, code in requests.items():
            with self.subTest(text=text):
                self.assertEqual(draft_language(apply_language_requests({}, text)), code)
        # Still not a guess: naming a language outside a writing request sets nothing.
        self.assertIsNone(draft_language(apply_language_requests({}, 'Do you have events in English to recommend?')))

    def test_a_sentence_answering_the_question_sets_the_draft_not_the_conversation(self):
        """Seen live: 「英語で書いてください。私の名前は…」 switched the replies to English and the
        draft language was asked a second time."""
        replies = {
            '英語で書いてください。私の名前は田中太郎、メールは taro.tanaka@example.test です。': 'en',
            'フランス語で書いてください': 'fr',
            'Please write it in English. My name is Jane Doe and my email is jane.doe@example.test.': 'en',
            'Écrivez-le en français s’il vous plaît, je m’appelle Jean Dupont.': 'fr',
        }
        for reply, code in replies.items():
            with self.subTest(reply=reply):
                brief = apply_language_requests({}, reply, True)
                self.assertEqual(draft_language(brief), code)
                self.assertNotIn('conversation_language', brief)
        # Not asked: the same words keep their old meaning (the language of the replies).
        self.assertIsNone(draft_language(apply_language_requests({}, '英語で書いてください', False)))

    def test_the_question_as_one_item_of_a_list_is_recognised(self):
        for question in ('メール案を作る前に、次の2点を教えてください。\n- メール本文は日本語・英語・フランス語のどれにしますか？\n- 差出人の氏名と返信先メールアドレスは何にしますか？',
                         'メールの下書きを作る前に、次の3点を教えてください。\n- 下書きの言語（日本語・英語・フランス語）\n- 差出人のお名前\n- 返信先にするメールアドレス',
                         'Before I write the email, please tell me:\n- the language of the draft (English, French or Japanese)\n- your name',
                         'Avant de rédiger le courriel, indiquez-moi la langue souhaitée, votre nom et votre adresse.'):
            with self.subTest(question=question):
                self.assertTrue(asks_draft_language(question))

    def test_a_bare_code_means_nothing_when_the_question_was_not_asked(self):
        for reply in ('en', 'fr', 'ok'):
            self.assertIsNone(draft_language(apply_language_requests({}, reply, False)))

    def test_set_draft_language_accepts_only_supported_codes(self):
        self.assertEqual(draft_language(set_draft_language({}, 'fr', 'ok')), 'fr')
        for code in ('unclear', 'not_asked', 'es', '', None):
            self.assertIsNone(draft_language(set_draft_language({}, code, 'ok')))

    def test_question_and_note_are_recognised_but_a_plain_draft_is_not(self):
        for locale in ('en', 'fr', 'ja', 'es'):
            self.assertTrue(asks_draft_language(draft_language_question(locale)))
        for locale in ('en', 'fr', 'ja'):
            self.assertTrue(asks_draft_language(draft_language_note('en', locale) + '\n\n' + DRAFT))
        self.assertFalse(asks_draft_language('Here is your draft in the language you chose.\n\n' + DRAFT))
        self.assertFalse(asks_draft_language('CCIFJ offers market studies.'))

    def test_the_question_in_the_models_own_words_is_recognised(self):
        for question in ('I can draft it, and I won’t send it. Which language would you like the email in: English, French, or Japanese?',
                         'Which language should I write the email in: English, French, or Japanese?',
                         'Dans quelle langue souhaitez-vous le courriel : français, anglais ou japonais ?',
                         'メールはどの言語で作成しますか？'):
            with self.subTest(question=question):
                self.assertTrue(asks_draft_language(question))
        self.assertFalse(asks_draft_language('Our training courses are taught in several languages.'))

    def test_draft_text_language_reads_the_draft_itself(self):
        self.assertEqual(draft_text_language(DRAFT), 'en')
        self.assertEqual(draft_text_language(FRENCH_DRAFT), 'fr')
        self.assertEqual(draft_text_language('```email\n件名: 販売代理店\n\nお世話になっております。\n```'), 'ja')

    def test_note_names_the_language_used_and_the_two_others(self):
        note = draft_language_note('fr', 'en')
        self.assertIn('in French', note)
        self.assertIn('English or Japanese', note)


class DraftLanguageGraphTest(unittest.IsolatedAsyncioTestCase):
    def history(self, reply, locale='en'):
        return [HumanMessage('Help me write an email to the Business Support team.'),
                AIMessage(draft_language_question(locale)), HumanMessage(reply)]

    async def route(self, reply, model_reading, brief=None, history=None):
        from concierge.agents import body
        from concierge.agents.schema_prompts import OrchestratorPlan, TaskItem
        plan = OrchestratorPlan(scope='in_scope', lang='en', draft_language_reply=model_reading,
                                tasks=[TaskItem(target='general', sub_query='Write the email draft')])
        model = MagicMock()
        model.with_structured_output.return_value.ainvoke = AsyncMock(return_value=plan)
        state = {'messages': history or self.history(reply), 'conversation_brief': brief or {}}
        with patch.object(body, 'llm_routing', model):
            return await body.orchestrator_node(state)

    async def test_model_reading_is_used_for_a_loose_answer(self):
        for reply, reading in (('ok', 'en'), ('the first one', 'en'), ('le deuxième', 'fr'), ('English is fine', 'en')):
            with self.subTest(reply=reply):
                result = await self.route(reply, reading)
                self.assertEqual(draft_language(result['conversation_brief']), reading)

    async def test_model_reading_is_ignored_when_the_question_was_not_asked(self):
        history = [HumanMessage('What are the membership fees?'), AIMessage('Fees depend on the category.'),
                   HumanMessage('merci, et pour une start-up ?')]
        result = await self.route('', 'fr', history=history)
        self.assertIsNone(draft_language(result['conversation_brief']))

    async def test_exact_answer_wins_over_the_model(self):
        result = await self.route('French', 'en')
        self.assertEqual(draft_language(result['conversation_brief']), 'fr')

    async def test_no_preference_uses_the_language_of_the_conversation(self):
        from concierge.agents import body
        from concierge.agents.schema_prompts import OrchestratorPlan, TaskItem
        question = 'Which language would you like the email in: English, French, or Japanese?'
        history = [HumanMessage('Aidez-moi à écrire un e-mail.'), AIMessage(question), HumanMessage('ok')]
        plan = OrchestratorPlan(scope='in_scope', lang='en', draft_language_reply='no_preference',
                                tasks=[TaskItem(target='general', sub_query='Write the email draft')])
        model = MagicMock()
        model.with_structured_output.return_value.ainvoke = AsyncMock(return_value=plan)
        with patch.object(body, 'llm_routing', model):
            french = await body.orchestrator_node({'messages': history, 'conversation_brief': {}, 'lang': 'fr'})
            other = await body.orchestrator_node({'messages': history, 'conversation_brief': {}, 'lang': 'es'})
        self.assertEqual(draft_language(french['conversation_brief']), 'fr')
        self.assertEqual(draft_language(other['conversation_brief']), 'en')

    async def test_unclear_reading_leaves_the_language_unset(self):
        result = await self.route('what does it cost?', 'unclear')
        self.assertIsNone(draft_language(result['conversation_brief']))

    async def general(self, history, brief, answer=DRAFT, locale='en'):
        from concierge.agents import body
        agent = MagicMock()
        agent.ainvoke = AsyncMock(return_value={'messages': [history[-1], AIMessage(answer)]})
        state = {'messages': history, 'lang': locale, 'conversation_brief': brief}
        with patch.object(body, 'general_agent', agent):
            return await body.general_node(state)

    async def test_first_draft_without_a_language_asks_once(self):
        history = [HumanMessage('Help me write an email to the Business Support team.')]
        result = await self.general(history, {})
        self.assertEqual(result['worker_results']['general'], draft_language_question('en'))
        self.assertNotIn('conversation_brief', result)

    async def test_unreadable_answer_keeps_the_draft_instead_of_asking_again(self):
        result = await self.general(self.history('hmm whatever you think'), {})
        answer = result['worker_results']['general']
        self.assertNotEqual(answer, draft_language_question('en'))
        self.assertIn('I wrote this draft in English', answer)
        self.assertIn('```email', answer)
        self.assertEqual(draft_language(result['conversation_brief']), 'en')

    async def test_fallback_names_the_language_the_draft_is_really_in(self):
        result = await self.general(self.history('comme vous voulez', 'fr'), {}, FRENCH_DRAFT, 'fr')
        self.assertIn('en français', result['worker_results']['general'])
        self.assertEqual(draft_language(result['conversation_brief']), 'fr')

    async def test_understood_answer_gives_the_draft_with_no_note(self):
        brief = apply_language_requests({}, 'en', True)
        result = await self.general(self.history('en'), brief)
        answer = result['worker_results']['general']
        self.assertIn('```email', answer)
        self.assertNotIn('no language was chosen', answer)

    async def test_the_original_loop_is_gone(self):
        """The reported conversation: the visitor answers "en" and gets the draft."""
        routed = await self.route('en', 'en')
        self.assertEqual(draft_language(routed['conversation_brief']), 'en')
        result = await self.general(self.history('en'), routed['conversation_brief'])
        self.assertIn('```email', result['worker_results']['general'])


if __name__ == '__main__':
    unittest.main()
