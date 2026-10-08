"""Match postings against the profile.

Two stages:
1. a cheap keyword pre-score (no API cost) to skip obviously irrelevant postings
2. an LLM assessment producing two independent scores, a quadrant category
   and concrete application advice
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .config import MatchingSettings
from .llm import ChatBackend, ask_model
from .models import JobPosting, MatchResult, categorize
from .profile import Profile

LANG_NAMES = {"de": "German", "en": "English", "ru": "Russian", "fr": "French", "nl": "Dutch"}


def prescore(job: JobPosting, profile: Profile) -> float:
    """Share of profile keywords (0..1, capped) that occur in the posting."""
    kws = profile.keywords()
    if not kws:
        return 1.0
    text = f"{job.title} {job.description}".lower()
    hits = sum(1 for k in kws if k in text)
    return min(1.0, hits / max(3, len(kws) * 0.2))


SYSTEM = """You are an experienced career advisor and recruiter for industry and
academia (incl. PhD and postdoc positions). You assess ONE job posting against an
applicant's profile. Be honest and specific; never invent experience the applicant
does not have. Score two dimensions independently:

- profile_fit (0-100): how well the applicant meets the stated requirements
  (skills, degree, experience, languages, formal eligibility).
- interest_fit (0-100): how well the role matches the applicant's interests,
  values, preferred locations and role types; dealbreakers push this near 0.

Calibration: 85+ = excellent, 65-84 = good, 40-64 = partial, <40 = weak.

Return ONLY JSON with these keys:
{
  "profile_fit": int,
  "interest_fit": int,
  "summary": "2-3 sentences: what the role is and why it does/doesn't fit",
  "matching_strengths": ["requirement -> concrete evidence from the profile", ...],
  "gaps": ["missing or weak requirement", ...],
  "emphasize_in_application": ["which concrete projects/experiences to highlight and how", ...],
  "profile_tailoring": ["concrete change to CV/profile wording or ordering for THIS posting", ...],
  "red_flags": ["deadline passed, eligibility rules, contract issues, ...", ...]
}
Write all free-text fields in {language}."""


class _LLMMatch(BaseModel):
    profile_fit: int = Field(ge=0, le=100)
    interest_fit: int = Field(ge=0, le=100)
    summary: str = ""
    matching_strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    emphasize_in_application: list[str] = Field(default_factory=list)
    profile_tailoring: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)


def criteria_key(profile: Profile, settings: MatchingSettings) -> str:
    """Changes when the profile or your custom criteria change → stored postings are re-assessed."""
    import hashlib

    if not settings.custom_criteria.strip():
        return profile.fingerprint  # unchanged key for setups without custom criteria
    return hashlib.sha1(f"{profile.fingerprint}|{settings.custom_criteria.strip()}".encode()).hexdigest()[:12]


def build_system_prompt(settings: MatchingSettings) -> str:
    system = SYSTEM.replace("{language}", LANG_NAMES.get(settings.language, settings.language))
    if settings.custom_criteria.strip():
        system += ("\n\nADDITIONAL CRITERIA FROM THE APPLICANT – apply them to the scores and mention them in "
                   f"the summary when they make a difference:\n{settings.custom_criteria.strip()}")
    return system


def match_job(
    job: JobPosting, profile: Profile, llm: ChatBackend, settings: MatchingSettings, model_name: str = ""
) -> MatchResult:
    system = build_system_prompt(settings)
    user = (
        f"# APPLICANT\n{profile.to_prompt()}\n\n"
        f"# JOB POSTING\nTitle: {job.title}\nOrganization: {job.organization or '-'}\n"
        f"Location: {job.location or '-'}\nDeadline: {job.deadline or '-'}\nURL: {job.url}\n\n"
        f"{job.description[:12000] or '(no description available – judge from the title and say so)'}"
    )
    r = ask_model(llm, system, user, _LLMMatch)
    return MatchResult(
        job_uid=job.uid,
        **r.model_dump(),
        category=categorize(r.profile_fit, r.interest_fit, settings.category_threshold),
        model=model_name,
    )
