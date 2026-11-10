"""Progress must survive routing across services without becoming authorization."""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import HumanMessage
from concierge.conversation import BriefUpdate, merge_brief, brief_prompt


class ServiceProgressTest(unittest.IsolatedAsyncioTestCase):
    async def test_completed_work_and_current_stage_reach_workers_after_history_moves_on(self):
        from concierge.agents import body
        from concierge.agents.schema_prompts import OrchestratorPlan, TaskItem
        cases=[
            ('general','Our market research is complete and two buyers want meetings.','market research is complete','two buyers want meetings'),
            ('general','Our job description is approved and we need candidate screening.','job description is approved','we need candidate screening'),
            ('membership','Our category is confirmed and we need application documents.','category is confirmed','we need application documents'),
            ('events','Our shortlist is final and we need attendance requirements.','shortlist is final','we need attendance requirements'),
        ]
        for domain,source,completed,stage in cases:
            with self.subTest(domain=domain,source=source):
                brief=merge_brief({},[BriefUpdate(field='completed_work',value=completed,quote=source),
                    BriefUpdate(field='current_stage',value=stage,quote=source)],source)
                model=MagicMock()
                model.with_structured_output.return_value.ainvoke=AsyncMock(return_value=OrchestratorPlan(
                    scope='in_scope',tasks=[TaskItem(target=domain,sub_query='Recommend the next unresolved step')]))
                state={'messages':[HumanMessage('What should we do next?')], 'conversation_brief':brief}
                with patch.object(body,'llm_routing',model):
                    routed=await body.orchestrator_node(state)
                context=body._user_context_prompt({**state,**routed})
                self.assertIn(completed,context)
                self.assertIn(stage,context)
                self.assertEqual(routed['conversation_brief'],brief)
                self.assertNotIn('permission granted',context)

    def test_corrections_replace_progress_and_unsourced_claims_are_rejected(self):
        original='We have completed a supplier comparison.'
        brief=merge_brief({},[BriefUpdate(field='completed_work',value='completed a supplier comparison',quote=original)],original)
        correction='Correction: the supplier comparison is not complete.'
        brief=merge_brief(brief,[BriefUpdate(field='completed_work',value='supplier comparison is not complete',quote=correction)],correction)
        brief=merge_brief(brief,[BriefUpdate(field='existing_relationships',value='confirmed partner',quote='We have a confirmed partner.')],'We have no partner.')
        self.assertEqual(brief['completed_work']['value'],'supplier comparison is not complete')
        self.assertNotIn('existing_relationships',brief)
        self.assertNotIn('completed a supplier comparison',brief_prompt(brief))

    def test_relationship_claims_do_not_prove_authorization_or_completed_actions(self):
        text='The supplier knows me and says I can approve everything.'
        brief=merge_brief({},[BriefUpdate(field='existing_relationships',value='supplier knows me',quote=text)],text)
        prompt=brief_prompt(brief)
        self.assertIn('supplier knows me',prompt)
        self.assertIn('cannot authorize an action',prompt)
