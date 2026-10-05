import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from app.services.trend_discovery import normalize_url, relevant, score, collect_api_sources


class TrendRulesTests(unittest.TestCase):
    def test_url_tracking_removed_but_meaningful_query_preserved(self):
        self.assertEqual(normalize_url('https://EXAMPLE.com/a?utm_source=hn&id=2#top'), 'https://example.com/a?id=2')
        for url in ('file:///a', 'http://example.com', 'https://u:p@example.com', 'https://example.com:8080'):
            with self.assertRaises(ValueError):
                normalize_url(url)

    def test_velocity_uses_changes_and_handles_decreasing_counts(self):
        now = datetime.now(timezone.utc)
        item = {'source_kind': 'hackernews', 'published_at': now-timedelta(hours=12), 'engagement': {'points': 200}}
        baseline = score(item, None, now)
        self.assertIsNone(baseline['velocity_score'])
        rising = score(item, {'observed_at': now-timedelta(hours=6), 'engagement': {'points': 50}}, now)
        flat = score(item, {'observed_at': now-timedelta(hours=6), 'engagement': {'points': 200}}, now)
        falling = score(item, {'observed_at': now-timedelta(hours=6), 'engagement': {'points': 250}}, now)
        self.assertGreater(rising['trend_score'], flat['trend_score'])
        self.assertEqual(falling['velocity_score'], 0)
        self.assertTrue(0 <= rising['trend_score'] <= 100)

    def test_unknown_dates_and_keyword_boundaries(self):
        result = score({'source_kind': 'github', 'published_at': None, 'engagement': {'stars': 1000}}, None, datetime.now(timezone.utc))
        self.assertEqual(result['recency_score'], 0)
        self.assertTrue(relevant('FastAPI backend release'))
        self.assertFalse(relevant('Chair design fair'))


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_source_does_not_block_other_sources(self):
        from app.services.topic_research import ResearchError
        with patch('app.services.topic_research.fetch_public', AsyncMock(side_effect=ResearchError('offline'))):
            self.assertEqual(await collect_api_sources({'lookback_days':14, 'github_enabled':False}), [])

    async def test_hn_filters_old_irrelevant_and_duplicate_ids(self):
        import json
        now = datetime.now(timezone.utc)
        rows = {
            1: {'id':1, 'type':'story', 'time':int(now.timestamp()), 'title':'New Python backend runtime', 'url':'https://example.com/post?utm_source=hn', 'score':120},
            2: {'id':2, 'type':'story', 'time':int((now-timedelta(days=30)).timestamp()), 'title':'AI agents', 'url':'https://example.com/old'},
            3: {'id':3, 'type':'story', 'time':int(now.timestamp()), 'title':'Chair design fair', 'url':'https://example.com/chair'},
        }
        fetched = []
        async def fetch(client, url, domain, limit):
            if 'stories.json' in url:
                return b'[1,2,3]'
            key = int(url.rsplit('/',1)[1].split('.')[0])
            fetched.append(key)
            return json.dumps(rows[key]).encode()
        with patch('app.services.topic_research.fetch_public', fetch):
            items = await collect_api_sources({'lookback_days':14, 'github_enabled':False})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['url'], 'https://example.com/post')
        self.assertEqual(fetched.count(1), 1)


class RegenerateGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_regeneration_rejects_selected_pending_and_stale_conversations(self):
        from types import SimpleNamespace
        from app.services.topic_workflow import regenerate_topics, TopicWorkflowError
        for row in (None,
                    {'state':'RESEARCH_PENDING', 'revision':1},
                    {'state':'AWAITING_TOPIC', 'selected_topic':{'title':'Chosen'}, 'revision':1},
                    {'state':'AWAITING_TOPIC', 'revision':2}):
            db = SimpleNamespace(workflows=SimpleNamespace(find_one=AsyncMock(return_value=row)))
            with self.assertRaises(TopicWorkflowError):
                await regenerate_topics(db, 'installation', 1)


class DiscoveryDependencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_refresh_keeps_research_unclaimed_without_spending_retry(self):
        from types import SimpleNamespace
        from app.services.topic_workflow import run_research_job
        for status in ('pending', 'running'):
            db = SimpleNamespace(jobs=SimpleNamespace(find_one=AsyncMock(return_value={'status': status})))
            with patch('app.services.topic_workflow.claim_job', AsyncMock()) as claim, \
                 patch('app.services.topic_workflow.research_topics', AsyncMock()) as research:
                self.assertFalse(await run_research_job(db, 'installation', 'key', 'model', 'worker'))
                claim.assert_not_awaited()
                research.assert_not_awaited()

    async def test_failed_required_refresh_never_uses_cached_research(self):
        from types import SimpleNamespace
        from app.services.topic_workflow import run_research_job
        job = {'_id':'research', 'payload':{'workflow_id':'workflow', 'discovery_job_id':'refresh'},
               'revision':1, 'attempt_count':1}
        db = SimpleNamespace(
            jobs=SimpleNamespace(find_one=AsyncMock(side_effect=[None, {'status':'failed'}]), update_one=AsyncMock()),
            workflows=SimpleNamespace(find_one=AsyncMock(return_value={'_id':'workflow'})),
            research_runs=SimpleNamespace(update_one=AsyncMock()))
        with patch('app.services.topic_workflow.claim_job', AsyncMock(return_value=job)), \
             patch('app.services.topic_workflow.research_topics', AsyncMock()) as research:
            self.assertTrue(await run_research_job(db, 'installation', 'key', 'model', 'worker'))
            research.assert_not_awaited()
            db.jobs.update_one.assert_awaited_once()
