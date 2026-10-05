"""Shared topic and writing rules; owner-specific context lives in the saved profile."""
from app.services.draft_context import blocked_terms

TOPIC_RULES = (
    'Select practical post opportunities, not just popular articles. Use the owner career stage and '
    'recent hands-on areas in content_goals as selection boundaries; a tool in interests is not evidence '
    'of expertise. Prefer day-to-day problems, small decisions, implementation lessons and tradeoffs. '
    'Reject topics that require senior architecture/research expertise or cannot be explained simply. '
    'Write a short plain-English title without stacked jargon. Explain what it means and what readers '
    'will learn. Use everyday wording in previews: say agent workflow rather than harness, expensive model '
    'rather than frontier model, and generating one token at a time rather than autoregressive decoding. '
    'Do not make explaining runtime internals the central lesson unless it solves a task the owner works on. '
    'Require a useful source-backed approach or lesson; reject launch announcements with '
    'no transferable engineering idea. Score persona_fit and practical_value from 0 to 100 honestly. '
    'Do not fill the list with weak options; one to five suitable topics is acceptable. '
    'Prioritize current work over a forced 50/50 category split. Respect prior rejection reasons. '
    'Use general work descriptions only; never expose employer, client or internal project names. '
)

WRITING_RULES = (
    'Use plain, conversational English and short connected sentences for the saved audience. '
    'Explain necessary technical terms at first use and follow the word_target in style. '
    'Use agent workflow rather than harness, and expensive model rather than frontier model. '
    'Prefer a concrete action over abstract phrases such as '
    '"assert terminal backend states" (say "check that the database contains the expected changes"). '
    'For a new draft, develop one point: problem, small illustrative example when useful, engineering '
    'approach, takeaway. Do not force a template, analogy, rhetorical hook, question or numbered list. '
    'Include the useful solution from the evidence, not only the problem. If none is available, say '
    'what remains uncertain; do not invent a source finding. Distinguish a transferable technique from '
    'the vendor product demonstrating it; attribute findings without turning the post into promotion. '
    'Label invented examples as hypothetical; never present them as owner experience or measured results. '
    'Follow the saved writing samples for voice, not as evidence of facts. Use general work descriptions; '
    'never include employer, client or internal project names. Avoid hype, academic phrasing and unsupported '
    'claims of expertise. During revisions preserve wording except where feedback or privacy requires changes. '
)


def safe_persona(profile):
    keys = ('interests', 'target_roles', 'audience', 'content_goals', 'technical_depth',
            'tone', 'length', 'avoid_styles', 'writing_samples')
    result = {}
    for key in keys:
        value = profile.get(key, [] if key in ('interests', 'target_roles', 'content_goals', 'avoid_styles', 'writing_samples') else '')
        result[key] = [v for v in value if not blocked_terms(v, profile)] if isinstance(value, list) else (value if not blocked_terms(value, profile) else '')
    return result


def opportunity_score(topic, trend):
    # LLM-assessed fit/value are heuristic ratings, not probabilities.
    return round(.60 * topic['persona_fit'] + .25 * topic['practical_value'] + .15 * trend, 1)
