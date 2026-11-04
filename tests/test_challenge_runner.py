"""Exercise the challenge harness without a server or model calls."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('challenge_runner',ROOT/'scripts/challenge_conversations.py')
runner=importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class ChallengeRunnerTest(unittest.TestCase):
    def scenario(self):
        return {'id':'test','title':'Test','focus':['memory'],'turns':[
            {'prompt':'My budget is 800 EUR.','locale':'en','expectation':'Remember budget.'},
            {'prompt':'What was my budget?','locale':'en','expectation':'Recall 800 EUR.'},
        ]}

    def run_case(self,**kwargs):
        return runner.run_scenario(self.scenario(),base_url='http://test',timeout=1,save=lambda *_:None,**kwargs)

    def test_follow_up_reuses_the_same_session(self):
        with patch.object(runner,'post',side_effect=[{'session_id':'private-capability','reply':'Understood'},{'session_id':'private-capability','reply':'800 EUR'}]) as post:
            result=self.run_case(on_approval='stop')
        self.assertEqual(result['status'],'completed')
        self.assertIsNone(post.call_args_list[0].args[2]['session_id'])
        self.assertEqual(post.call_args_list[1].args[2]['session_id'],'private-capability')
        self.assertNotIn('private-capability',json.dumps(result))

    def test_proposed_action_is_rejected_before_follow_up_without_reset(self):
        pending={'session_id':'secret','reply':'Review','pending_approvals':[{'interrupt_id':'action-1'}]}
        with patch.object(runner,'post',side_effect=[pending,{'session_id':'secret','reply':'Nothing saved.'},{'session_id':'secret','reply':'800 EUR'}]) as post:
            result=self.run_case(on_approval='reject')
        self.assertEqual(result['status'],'completed')
        self.assertEqual(post.call_args_list[1].args[2]['decision'],'reject')
        self.assertEqual(post.call_args_list[2].args[2]['session_id'],'secret')
        self.assertEqual(result['turns'][1]['kind'],'approval_rejection')
        self.assertNotIn('secret',json.dumps(result))

    def test_failure_stops_conversation_without_replay(self):
        with patch.object(runner,'post',side_effect=HTTPError('http://test',502,'Uncertain',None,None)) as post:
            result=self.run_case(on_approval='reject')
        self.assertEqual(result['status'],'failed')
        self.assertEqual(post.call_count,1)
        self.assertEqual(result['turns'][0]['http_status'],502)

    def test_missing_session_cannot_silently_create_a_new_context(self):
        with patch.object(runner,'post',return_value={'reply':'No session'}) as post:
            result=self.run_case(on_approval='stop')
        self.assertEqual(result['status'],'failed')
        self.assertEqual(post.call_count,1)

    def test_repeated_approval_does_not_loop_forever(self):
        pending={'session_id':'secret','reply':'Review','pending_approvals':[{'interrupt_id':'same'}]}
        with patch.object(runner,'post',return_value=pending) as post:
            result=self.run_case(on_approval='reject')
        self.assertEqual(result['status'],'blocked_repeated_approval')
        self.assertEqual(post.call_count,2)

    def test_recovery_is_recorded_as_degraded_not_a_successful_answer(self):
        with patch.object(runner,'post',return_value={'session_id':'private-capability','reply':'I could not check that.','mode':'recovery','warnings':['answer_unavailable']}) as post:
            result=self.run_case(on_approval='reject')
        self.assertEqual(result['status'],'degraded')
        self.assertEqual(result['turns'][0]['status'],'degraded')
        self.assertEqual(post.call_count,1)

    def test_suite_has_long_conversations_with_explicit_expectations(self):
        suite=json.loads((ROOT/'tests/concierge_challenges.json').read_text())
        self.assertEqual(len(suite['scenarios']),16)
        self.assertEqual(sum(len(case['turns']) for case in suite['scenarios']),210)
        self.assertEqual(len(next(case for case in suite['scenarios'] if case['id']=='marathon')['turns']),30)
        for case in suite['scenarios']:
            self.assertGreaterEqual(len(case['turns']),12)
            for turn in case['turns']:
                self.assertTrue(turn['prompt'] and turn['expectation'])
                self.assertIn(turn['locale'],('en','fr','ja'))


if __name__=='__main__':
    unittest.main()
