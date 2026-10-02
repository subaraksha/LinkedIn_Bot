"""Owner controlled, publication-safe context for future drafting."""

from dataclasses import dataclass
import re

from app.domain.owner_profile import DEFAULT_PROFILE, OwnerProfileInput


@dataclass(frozen=True)
class DraftContext:
    profile_revision: int
    facts: tuple[dict, ...]
    goals: dict
    style: dict


def build_draft_context(snapshot: dict) -> DraftContext:
    profile = OwnerProfileInput.model_validate(snapshot.get("profile", DEFAULT_PROFILE.model_dump()))
    facts = tuple({"id": entry["_id"], "type": entry["type"], "claim": entry["claim"]}
                  for entry in snapshot["entries"]
                  if entry.get("status") == "confirmed"
                  and entry.get("publication_permission") == "public"
                  and not blocked_terms(entry.get("claim", ""), profile.model_dump())
                  and not blocked_terms(entry.get("experience_context", ""), profile.model_dump()))
    def safe(values):
        return [value for value in values if not blocked_terms(value, profile.model_dump())]
    return DraftContext(
        profile_revision=snapshot["revision"],
        facts=facts,
        goals={"target_roles": safe(profile.target_roles),
               "audience": profile.audience if not blocked_terms(profile.audience, profile.model_dump()) else "",
               "interests": safe(profile.interests), "content_goals": safe(profile.content_goals)},
        style={"tone": profile.tone, "length": profile.length,
               "technical_depth": profile.technical_depth,
               "use_emojis": profile.use_emojis, "use_hashtags": profile.use_hashtags,
               "avoid_styles": safe(profile.avoid_styles)},
    )


def blocked_terms(draft: str, profile_data: dict) -> list[str]:
    """A post with an explicit owner boundary must return to review, never publish."""
    profile = OwnerProfileInput.model_validate(profile_data)
    terms = profile.confidential_details + profile.avoid_phrases + profile.avoid_topics
    blocked = []
    for term in terms:
        expression = re.escape(term)
        if re.fullmatch(r"\w+", term, flags=re.UNICODE):
            expression = rf"(?<!\w){expression}(?!\w)"
        if re.search(expression, draft, flags=re.IGNORECASE):
            blocked.append(term)
    return blocked
