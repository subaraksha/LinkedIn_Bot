"""Deterministic, evidence-linked onboarding questions."""

import re

TEAM = re.compile(r"\b(we|our team|team|collaborat(?:ed|ion)|worked with)\b", re.I)
LEARNING = re.compile(r"\b(learn(?:ed|ing|t)|explor(?:ed|ing)|experiment(?:ed|ing)|tutorial|course)\b", re.I)


def derive_questions(profile: dict, facts: list[dict]) -> list[dict]:
    questions = []
    if not profile.get("target_roles"):
        questions.append({"id": "target_roles", "kind": "target_roles",
                          "prompt": "What roles would you like your posts to help you pursue?", "fact_id": None})
    if not profile.get("audience"):
        questions.append({"id": "audience", "kind": "audience",
                          "prompt": "Who would you like to reach with your posts?", "fact_id": None})
    if not profile.get("interests"):
        questions.append({"id": "interests", "kind": "interests",
                          "prompt": "Which technical topics do you want to write about?", "fact_id": None})
    public_stories = [item for item in facts if item.get("status") == "confirmed"
                      and item.get("publication_permission") == "public"
                      and item.get("type") in {"work", "project", "achievement", "other"}]
    if not public_stories:
        questions.append({"id": "public_story", "kind": "public_story",
                          "prompt": "Is there a project or lesson you would be comfortable discussing publicly? You can add it as a fact after answering.",
                          "fact_id": None})
    for item in facts:
        if item.get("status") != "pending_confirmation":
            continue
        claim = item.get("claim", "")
        if item.get("type") in {"work", "project", "achievement"} and TEAM.search(claim):
            questions.append({"id": f"contribution:{item['_id']}", "kind": "contribution",
                              "prompt": "What was your individual contribution to this team work? The fact stays pending until you review it.",
                              "fact_id": item["_id"]})
        if LEARNING.search(claim):
            questions.append({"id": f"experience_kind:{item['_id']}", "kind": "experience_kind",
                              "prompt": "Was this professional work, personal learning, or an experiment? The fact stays pending until you review it.",
                              "fact_id": item["_id"]})
    return questions[:30]


def merge_questions(derived: list[dict], saved: list[dict]) -> list[dict]:
    by_id = {item["_id"]: item for item in saved}
    result = []
    for item in derived:
        stored = by_id.get(item["id"])
        result.append({**item, "status": stored.get("status", "pending") if stored else "pending",
                       "answer": stored.get("answer") if stored else None,
                       "revision": stored.get("revision", 0) if stored else 0})
    return result
