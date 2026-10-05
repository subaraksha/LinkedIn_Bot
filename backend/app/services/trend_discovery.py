"""Bounded public discovery, historical engagement and comparable ranking."""
import asyncio
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.config import get_settings
from app.storage.jobs import enqueue_job, claim_job, finish_job

KEYWORDS = ('ai', 'llm', 'agent', 'inference', 'rag', 'mcp', 'python', 'backend',
            'database', 'postgres', 'distributed', 'kubernetes', 'serverless', 'node',
            'fastapi', 'observability', 'aws', 'machine learning')


def normalize_url(url):
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError('Expected a public HTTPS article URL')
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in {'fbclid', 'gclid'}]
    return urlunsplit(('https', parts.hostname.lower(), parts.path or '/', urlencode(query), ''))


def relevant(text):
    import re
    return any(re.search(r'(?<!\w)' + re.escape(word) + r'(?!\w)', text.lower()) for word in KEYWORDS)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def score(item, previous, now):
    """Scores are heuristic 0–100 rankings; absent velocity is unknown, not zero growth."""
    metrics = item.get('engagement', {})
    total = metrics.get('points', metrics.get('stars', 0)) + metrics.get('comments', 0) * .5
    scale = 500 if item['source_kind'] == 'hackernews' else 5000
    engagement = min(100, 100 * math.log1p(total) / math.log1p(scale))
    velocity = None
    if previous:
        hours = (now - utc(previous['observed_at'])).total_seconds() / 3600
        if hours >= .25:
            old = previous['engagement']
            prior = old.get('points', old.get('stars', 0)) + old.get('comments', 0) * .5
            velocity = min(100, 100 * math.log1p(max(0, total - prior) / hours) / math.log1p(scale / 24))
    date = utc(item.get('published_at'))
    recency = 100 * math.exp(-max(0, (now - date).total_seconds() / 86400) / 7) if date else 0
    components = [(recency, .4), (engagement, .35)]
    if velocity is not None:
        components.append((velocity, .25))
    if item['source_kind'] == 'rss':
        components = [(recency, 1)]
    return {'trend_score': round(sum(v*w for v,w in components) / sum(w for _,w in components), 1),
            'velocity_score': velocity, 'engagement_score': round(engagement, 1),
            'recency_score': round(recency, 1), 'score_version': 1}


async def collect_api_sources(config):
    from app.services.topic_research import fetch_public, ResearchError
    result = []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=config['lookback_days'])
    async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
        async def hn_item(item_id):
            try:
                raw = await fetch_public(client, f'https://hacker-news.firebaseio.com/v0/item/{int(item_id)}.json', 'hacker-news.firebaseio.com', 100_000)
                row = json.loads(raw)
                if not row or row.get('type') != 'story' or row.get('deleted') or row.get('dead'):
                    return
                date = datetime.fromtimestamp(row['time'], timezone.utc)
                if not cutoff <= date <= now or not relevant(row.get('title', '')):
                    return
                url = normalize_url(row.get('url', ''))
                result.append({'url': url, 'title': row['title'][:250], 'summary': '',
                    'published_at': date, 'source': 'Hacker News', 'source_kind': 'hackernews',
                    'domain': urlsplit(url).hostname, 'external_id': str(row['id']),
                    'engagement': {'points': row.get('score', 0), 'comments': row.get('descendants', 0)}})
            except (ResearchError, ValueError, KeyError, TypeError):
                pass
        if config.get('hackernews_enabled', True):
            try:
                ids = []
                for listing in ('topstories', 'newstories'):
                    raw = await fetch_public(client, f'https://hacker-news.firebaseio.com/v0/{listing}.json', 'hacker-news.firebaseio.com', 100_000)
                    ids.extend(json.loads(raw)[:30])
                # Small batches keep concurrency bounded.
                ids = list(dict.fromkeys(ids))
                for start in range(0, len(ids), 6):
                    await asyncio.gather(*(hn_item(i) for i in ids[start:start+6]))
            except (ResearchError, ValueError, TypeError):
                pass
        if config.get('github_enabled', True):
            token = get_settings().github_token
            headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'linkedin-post-agent'}
            if token:
                headers['Authorization'] = f'Bearer {token}'
            # Fixed API host; discovered article fetching uses the pinned public fetcher.
            queries = ['topic:llm', 'topic:database', 'topic:backend', 'topic:ai-agents']
            for query in queries:
                try:
                    response = await client.get('https://api.github.com/search/repositories', headers=headers,
                        params={'q': f'{query} pushed:>={cutoff.date()} stars:>=100 archived:false',
                                'sort': 'updated', 'per_page': 8})
                    if response.status_code in (403, 429):
                        break
                    response.raise_for_status()
                    for row in response.json().get('items', []):
                        url = normalize_url(row['html_url'])
                        result.append({'url': url, 'title': row['full_name'],
                            'summary': (row.get('description') or '')[:700],
                            # Push activity is not a publication or release date.
                            'published_at': None, 'activity_at': row.get('pushed_at'),
                            'source': 'GitHub', 'source_kind': 'github', 'domain': 'github.com',
                            'external_id': str(row['id']),
                            'engagement': {'stars': row.get('stargazers_count', 0), 'forks': row.get('forks_count', 0)}})
                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                    continue
    return result


async def persist_candidates(db, items):
    now = datetime.now(timezone.utc)
    for item in items:
        key = hashlib.sha256(item['url'].encode()).hexdigest()
        observations = []
        for signal in item.get('signals', [item]):
            identity = f"{signal.get('source_kind', 'rss')}:{signal.get('external_id', key)}"
            prior = await db.trend_observations.find_one({'signal_id': identity}, sort=[('observed_at', -1)])
            signal.setdefault('source_kind', 'rss')
            scores = score(signal, prior, now)
            observations.append(scores)
            if signal.get('engagement'):
                slot = int(now.timestamp()) // 900
                await db.trend_observations.update_one({'_id': f'{identity}:{slot}'}, {'$setOnInsert': {
                    'signal_id': identity, 'research_item_id': key, 'observed_at': now,
                    'engagement': signal['engagement'], **scores}}, upsert=True)
        best = max(observations, key=lambda x: x['trend_score'])
        item.update(best)
        await db.trend_candidates.update_one({'_id': key}, {'$set': {
            **{k: v for k,v in item.items() if k != 'snapshot_id'}, 'collected_at': now}}, upsert=True)
    return sorted(items, key=lambda x: x['trend_score'], reverse=True)


async def discovery_tick(db, installation_id):
    now = datetime.now(timezone.utc)
    slot = int(now.timestamp()) // (6 * 3600)
    await enqueue_job(db, kind='discover_trends', dedupe_key=f'discovery:{installation_id}:{slot}',
                      payload={'installation_id': installation_id})


async def run_discovery_job(db, worker_id):
    from app.services.topic_research import collect_sources, load_sources
    job = await claim_job(db, kinds=['discover_trends'], worker_id=worker_id, lease_seconds=300)
    if not job:
        return False
    try:
        # At most one bounded pass; time budget stays below the lease.
        async with asyncio.timeout(240):
            items = await collect_sources(load_sources())
            if not items:
                raise ValueError('No sources available')
            await persist_candidates(db, items)
        status = 'succeeded'
    except (TimeoutError, ValueError):
        status = 'failed'
    await finish_job(db, job_id=job['_id'], worker_id=worker_id, revision=job['revision'],
                     status=status, error_code='discovery_unavailable' if status == 'failed' else None)
    return True


async def discovery_loop(db, installation_id, worker_id):
    """Run independently of topic conversations and Telegram polling."""
    await db.trend_observations.create_index([('signal_id', 1), ('observed_at', -1)], name='trend_signal_history')
    await db.trend_observations.create_index('observed_at', expireAfterSeconds=90*86400, name='trend_retention')
    await db.trend_candidates.create_index('collected_at', expireAfterSeconds=90*86400, name='candidate_retention')
    while True:
        try:
            await discovery_tick(db, installation_id)
            await run_discovery_job(db, worker_id)
        except Exception:
            # Never print provider responses or credentials.
            print('Trend discovery interrupted; retrying after durable lease recovery', flush=True)
        await asyncio.sleep(30)
