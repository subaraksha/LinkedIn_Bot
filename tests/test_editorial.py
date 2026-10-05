import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from app.domain.owner_profile import OwnerProfileInput
from app.services.draft_context import build_draft_context
from app.services.editorial import safe_persona, opportunity_score
from app.services.topic_research import propose_topics
from app.services.draft_workflow import _generate, DraftError


def model_client(responses):
    generate = AsyncMock(side_effect=[SimpleNamespace(text=json.dumps(row), usage_metadata=None) for row in responses])
    return SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate), aclose=AsyncMock())), generate


def proposal(title, source, fit=90, value=85):
    return {'title': title, 'explanation':'A simple explanation of an engineering problem.',
            'reader_takeaway':'A practical way to validate extracted data.', 'persona_fit':fit,
            'practical_value':value, 'why_now':'A recent source explains this approach.',
            'why_you':'It connects to document extraction work.', 'angle':'Explain validation with a small hypothetical example.', 'source_ids':[source]}


class EditorialTests(unittest.TestCase):
    def test_persona_and_samples_keep_work_context_but_remove_private_names(self):
        profile = OwnerProfileInput(content_goals=['One year of experience building RAG', 'ClientSecret project'],
            confidential_details=['ClientSecret'], writing_samples=['Plain writing.', 'ClientSecret report']).model_dump()
        safe = safe_persona(profile)
        self.assertEqual(safe['content_goals'], ['One year of experience building RAG'])
        context = build_draft_context({'profile':profile, 'revision':1, 'entries':[]})
        self.assertEqual(context.style['writing_samples'], ['Plain writing.'])
        self.assertNotIn('ClientSecret', repr(safe))

    def test_personal_fit_beats_popularity(self):
        self.assertGreater(opportunity_score({'persona_fit':95,'practical_value':90},20),
                           opportunity_score({'persona_fit':65,'practical_value':60},100))


class EditorialModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_topic_selection_filters_weak_private_and_unknown_evidence_then_ranks_fit(self):
        responses = {'topics': [proposal('Validate extracted spreadsheet rows','a',95,95),
                                proposal('More popular but less useful topic','b',70,65),
                                proposal('Advanced research outside current work','b',30,80),
                                proposal('ClientSecret project lessons','a'),
                                proposal('Unknown source topic','unknown')]}
        client, generate = model_client([responses])
        items = [{'snapshot_id':id, 'title':'Evidence', 'url':'https://example.com/'+id,
                  'published_at':datetime.now(timezone.utc), 'summary':'Evidence text', 'trend_score':trend}
                 for id,trend in [('a',10),('b',100)]]
        profile = OwnerProfileInput(content_goals=['Early-career document extraction work'],confidential_details=['ClientSecret']).model_dump()
        with patch('app.services.topic_research.genai.Client', return_value=client):
            results = await propose_topics('key','model',items,profile,[],feedback=['Too advanced'])
        self.assertEqual(len(results),2)
        self.assertEqual(results[0]['source_ids'],['a'])
        self.assertIn('Early-career document extraction work', generate.call_args.kwargs['contents'])
        self.assertIn('Too advanced', generate.call_args.kwargs['contents'])

    async def test_new_draft_receives_source_solution_before_writing(self):
        brief={'problem':'Tool success does not prove correct saved data.', 'approach':'Check expected database changes.', 'limitations':'Only supplied examples are covered.', 'source_ids':['source']}
        client,generate=model_client([brief, {'body':'Check that the order has the expected status.', 'source_ids':['source'], 'used_fact_ids':[]}])
        context=build_draft_context({'profile':{}, 'entries':[], 'revision':1})
        with patch('app.services.draft_workflow.genai.Client', return_value=client):
            result=await _generate('key','model',{'selected_topic':{'title':'Check saved data'}},context,None,None,
                                   [{'id':'source','excerpt':'Verify database effects against expected results.'}])
        self.assertEqual(generate.await_count,2)
        self.assertIn('Check expected database changes.',generate.call_args.kwargs['contents'])
        self.assertIn('hypothetical',generate.call_args.kwargs['contents'])
        self.assertIn('order',result.body)

    async def test_unknown_brief_evidence_fails_before_writing(self):
        client,generate=model_client([{'problem':'Problem','approach':'Solution','limitations':'','source_ids':['fake']}])
        context=build_draft_context({'profile':{},'entries':[],'revision':1})
        with patch('app.services.draft_workflow.genai.Client', return_value=client), self.assertRaises(DraftError):
            await _generate('key','model',{'selected_topic':{'title':'Topic'}},context,None,None,[{'id':'real','excerpt':'Text'}])
        self.assertEqual(generate.await_count,1)
