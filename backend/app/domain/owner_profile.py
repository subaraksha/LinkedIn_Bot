"""Validated single-owner goals, writing preferences, and publication boundaries."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OwnerProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_roles: list[str] = Field(default_factory=list, max_length=20)
    audience: str = Field(default="", max_length=240)
    interests: list[str] = Field(default_factory=list, max_length=30)
    content_goals: list[str] = Field(default_factory=list, max_length=20)
    tone: Literal["conversational", "professional", "technical", "reflective", "mixed"] = "conversational"
    length: Literal["short", "medium", "long"] = "medium"
    technical_depth: Literal["introductory", "balanced", "deep"] = "balanced"
    use_emojis: bool = False
    use_hashtags: bool = False
    avoid_phrases: list[str] = Field(default_factory=list, max_length=30)
    avoid_styles: list[str] = Field(default_factory=list, max_length=30)
    avoid_topics: list[str] = Field(default_factory=list, max_length=30)
    confidential_details: list[str] = Field(default_factory=list, max_length=50)
    writing_samples: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("target_roles", "interests", "content_goals", "avoid_phrases",
                     "avoid_styles", "avoid_topics", "confidential_details", "writing_samples")
    @classmethod
    def clean_list(cls, values: list[str]) -> list[str]:
        cleaned = []
        seen = set()
        for value in values:
            item = value.strip()
            if not item or len(item) > 2000:
                raise ValueError("Each entry must contain 1–2000 characters")
            key = item.casefold()
            if key not in seen:
                cleaned.append(item)
                seen.add(key)
        return cleaned

    @field_validator("audience")
    @classmethod
    def clean_audience(cls, value: str) -> str:
        return value.strip()


DEFAULT_PROFILE = OwnerProfileInput()
