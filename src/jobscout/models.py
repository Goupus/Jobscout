"""Core data models."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobPosting(BaseModel):
    """A single posting found by a source."""

    source: str
    title: str
    url: str
    organization: str | None = None
    location: str | None = None
    description: str = ""
    deadline: str | None = None
    found_at: datetime = Field(default_factory=_now)

    @property
    def uid(self) -> str:
        """Stable id: normalized URL, falling back to title+org."""
        key = _normalize_url(self.url) or f"{self.title}|{self.organization}".lower()
        return hashlib.sha1(key.encode()).hexdigest()[:16]


def _normalize_url(url: str) -> str:
    url = url.strip().lower()
    url = re.sub(r"^https?://(www\.)?", "", url)
    url = re.sub(r"[?#].*$", "", url)  # drop tracking params / anchors
    return url.rstrip("/")


class MatchCategory(str, Enum):
    TOP = "top_match"            # strong profile fit + strong interest fit
    STRETCH = "stretch"          # you'd love it, but the profile has gaps
    SOLID = "solid_option"       # you'd get it, but it's less exciting
    NO_MATCH = "no_match"

    @property
    def label(self) -> str:
        return {
            "top_match": "Top match",
            "stretch": "Stretch / dream job",
            "solid_option": "Solid option",
            "no_match": "No match",
        }[self.value]


class MatchResult(BaseModel):
    """LLM assessment of one posting against the profile."""

    job_uid: str
    profile_fit: int = Field(ge=0, le=100, description="How well the applicant meets the requirements")
    interest_fit: int = Field(ge=0, le=100, description="How well the role matches the applicant's interests")
    category: MatchCategory = MatchCategory.NO_MATCH
    summary: str = ""
    matching_strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    emphasize_in_application: list[str] = Field(default_factory=list)
    profile_tailoring: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    model: str = ""
    matched_at: datetime = Field(default_factory=_now)


def categorize(profile_fit: int, interest_fit: int, threshold: int = 65) -> MatchCategory:
    """Map the two scores onto a 2x2 quadrant."""
    high_profile = profile_fit >= threshold
    high_interest = interest_fit >= threshold
    if high_profile and high_interest:
        return MatchCategory.TOP
    if high_interest:
        return MatchCategory.STRETCH
    if high_profile:
        return MatchCategory.SOLID
    return MatchCategory.NO_MATCH
