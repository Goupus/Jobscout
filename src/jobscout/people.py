"""People: researchers / contacts whose work you want to follow.

For every person in ``people.yaml`` jobscout collects *professional, public*
material – recent publications from OpenAlex, the text of their group or
homepage, and documents you added yourself (e.g. a LinkedIn profile saved as
PDF via "More → Save to PDF") – and asks the LLM:

* where does their work overlap with your profile?
* how could you contribute to or support it?
* how should you approach them (hooks, questions, a draft message)?

LinkedIn pages are *not* fetched: they require a login and LinkedIn's terms
forbid automated access. The link is kept for you to open yourself.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal

import yaml
from pydantic import BaseModel, Field

from .config import Paths, Settings
from .llm import ChatBackend, LLMError, ask_model
from .profile import Profile

log = logging.getLogger(__name__)

STATUSES = ["not_contacted", "contacted", "in_conversation", "no_reply", "collaborating"]
STATUS_LABELS = {
    "not_contacted": "⚪ Not contacted", "contacted": "📨 Contacted", "in_conversation": "💬 In conversation",
    "no_reply": "⏳ No reply", "collaborating": "🤝 Collaborating",
}
OPENALEX = "https://api.openalex.org"
PERSON_DOC_SUFFIXES = {".pdf", ".md", ".txt"}


# --- configuration --------------------------------------------------------------
class Person(BaseModel):
    name: str
    affiliation: str | None = None
    linkedin: str | None = None
    homepage: str | None = None          # personal / group page
    orcid: str | None = None
    openalex_id: str | None = None       # e.g. A5023888391 – set when you confirm a match
    notes: str = ""                       # what you already know / why this person
    status: Literal["not_contacted", "contacted", "in_conversation", "no_reply", "collaborating"] = "not_contacted"
    enabled: bool = True

    @property
    def slug(self) -> str:
        return slugify(self.name)

    def config_hash(self) -> str:
        """Changes when anything that influences the analysis changes (not the status)."""
        relevant = self.model_dump(exclude={"status", "enabled"})
        return hashlib.sha1(json.dumps(relevant, sort_keys=True).encode()).hexdigest()[:12]


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "person"


def people_file(paths: Paths) -> Path:
    return paths.data_dir / "people.yaml"


def person_docs_dir(paths: Paths, person: Person) -> Path:
    return paths.data_dir / "people" / person.slug


def load_people(paths: Paths) -> list[Person]:
    f = people_file(paths)
    if not f.exists():
        return []
    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    items = data.get("people", []) if isinstance(data, dict) else data
    return [Person.model_validate(p) for p in items or []]


def save_people(paths: Paths, people: list[Person]) -> None:
    body = {"people": [p.model_dump(exclude_defaults=True) | {"name": p.name} for p in people]}
    people_file(paths).write_text(
        "# People whose work jobscout follows – edit here or on the 'People' page of the app.\n"
        + yaml.safe_dump(body, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )


# --- OpenAlex ---------------------------------------------------------------------
class AuthorCandidate(BaseModel):
    id: str
    name: str
    institution: str | None = None
    works_count: int = 0
    cited_by_count: int = 0
    orcid: str | None = None
    topics: list[str] = Field(default_factory=list)

    @property
    def url(self) -> str:
        return f"https://openalex.org/{self.id}"


class Work(BaseModel):
    title: str
    year: int | None = None
    venue: str | None = None
    url: str | None = None
    cited_by_count: int = 0
    abstract: str = ""
    topics: list[str] = Field(default_factory=list)


GetJson = Callable[[str, dict], Any]


def default_get_json(settings: Settings) -> GetJson:
    import httpx

    client = httpx.Client(timeout=settings.http_timeout, headers={"User-Agent": settings.user_agent})
    key = os.environ.get("OPENALEX_API_KEY")

    def get(url: str, params: dict) -> Any:
        if key:
            params = {**params, "api_key": key}
        r = client.get(url, params=params)
        r.raise_for_status()
        return r.json()

    return get


def _short_id(openalex_id: str) -> str:
    return openalex_id.rstrip("/").rsplit("/", 1)[-1]


def _candidate(a: dict) -> AuthorCandidate:
    insts = a.get("last_known_institutions") or ([a["last_known_institution"]] if a.get("last_known_institution") else [])
    return AuthorCandidate(
        id=_short_id(a.get("id", "")),
        name=a.get("display_name") or "",
        institution=(insts[0] or {}).get("display_name") if insts else None,
        works_count=a.get("works_count") or 0,
        cited_by_count=a.get("cited_by_count") or 0,
        orcid=(a.get("orcid") or "").rsplit("/", 1)[-1] or None,
        topics=[t.get("display_name", "") for t in (a.get("topics") or [])[:5]],
    )


def abstract_from_index(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    words: dict[int, str] = {}
    for word, positions in index.items():
        for pos in positions:
            words[pos] = word
    return " ".join(words[i] for i in sorted(words))


class OpenAlexClient:
    def __init__(self, get_json: GetJson):
        self.get = get_json

    def search_authors(self, name: str, n: int = 6) -> list[AuthorCandidate]:
        data = self.get(f"{OPENALEX}/authors", {"search": name, "per-page": n})
        return [_candidate(a) for a in (data or {}).get("results", [])]

    def author(self, ref: str) -> AuthorCandidate | None:
        """`ref` is an OpenAlex id (A123…) or 'orcid:0000-…'."""
        try:
            return _candidate(self.get(f"{OPENALEX}/authors/{ref}", {}))
        except Exception as exc:  # noqa: BLE001
            log.warning("OpenAlex author %s: %s", ref, exc)
            return None

    def recent_works(self, author_id: str, years: int = 6, n: int = 15) -> list[Work]:
        since = date(date.today().year - years, 1, 1).isoformat()
        data = self.get(f"{OPENALEX}/works", {
            "filter": f"author.id:{author_id},from_publication_date:{since}",
            "sort": "publication_date:desc",
            "per-page": n,
            "select": "display_name,publication_year,doi,id,abstract_inverted_index,primary_location,cited_by_count,topics",
        })
        works = []
        for w in (data or {}).get("results", []):
            loc = w.get("primary_location") or {}
            works.append(Work(
                title=w.get("display_name") or "",
                year=w.get("publication_year"),
                venue=((loc.get("source") or {}).get("display_name")),
                url=w.get("doi") or w.get("id"),
                cited_by_count=w.get("cited_by_count") or 0,
                abstract=abstract_from_index(w.get("abstract_inverted_index"))[:1500],
                topics=[t.get("display_name", "") for t in (w.get("topics") or [])[:3]],
            ))
        return [w for w in works if w.title]


_GENERIC = {"university", "universitat", "universiteit", "universite", "universita", "technology", "technical",
            "technische", "institute", "institut", "college", "school", "school", "national", "research", "center",
            "centre", "sciences", "science", "applied", "of", "the", "and", "for"}


def _name_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z]+", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower())
    return {w for w in words if len(w) >= 3 and w not in _GENERIC}


def pick_candidate(person: Person, candidates: list[AuthorCandidate]) -> AuthorCandidate | None:
    """Only auto-pick when it's unambiguous; otherwise the user confirms in the app."""
    if not candidates:
        return None
    if person.affiliation:
        want = _name_tokens(person.affiliation)
        hits = [c for c in candidates if c.institution and want & _name_tokens(c.institution)]
        if hits:
            return max(hits, key=lambda c: c.works_count)
    return candidates[0] if len(candidates) == 1 else None


# --- material ----------------------------------------------------------------------
class PersonMaterial(BaseModel):
    author: AuthorCandidate | None = None
    works: list[Work] = Field(default_factory=list)
    homepage_text: str = ""
    documents: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    def digest(self) -> str:
        return hashlib.sha1(self.model_dump_json().encode()).hexdigest()[:12]

    def to_prompt(self, max_chars: int = 30000) -> str:
        parts = []
        if self.author:
            a = self.author
            parts.append(f"## Bibliometric profile (OpenAlex)\n{a.name} · {a.institution or '-'} · "
                         f"{a.works_count} works · {a.cited_by_count} citations · topics: {', '.join(a.topics)}")
        if self.works:
            lines = ["## Recent publications (newest first)"]
            for w in self.works:
                lines.append(f"- [{w.year}] {w.title} ({w.venue or '-'}; cited {w.cited_by_count}; {w.url or ''})"
                             + (f"\n  Abstract: {w.abstract}" if w.abstract else ""))
            parts.append("\n".join(lines))
        if self.homepage_text:
            parts.append(f"## Homepage / group page\n{self.homepage_text}")
        for name, text in self.documents.items():
            parts.append(f"## Document added by the user: {name}\n{text}")
        return "\n\n".join(parts)[:max_chars]


def gather_material(person: Person, paths: Paths, oa: OpenAlexClient | None, fetch: Callable[[str], str] | None,
                    n_works: int = 15) -> PersonMaterial:
    from .profile import _read_pdf
    from .sources import html_to_text

    m = PersonMaterial()
    if oa is not None:
        try:
            ref = person.openalex_id or (f"orcid:{person.orcid}" if person.orcid else None)
            if ref:
                m.author = oa.author(ref)
            else:
                m.author = pick_candidate(person, oa.search_authors(person.name))
                if m.author is None:
                    m.warnings.append("Several or no OpenAlex matches – confirm the right person on the People page.")
            if m.author:
                m.works = oa.recent_works(m.author.id, n=n_works)
        except Exception as exc:  # noqa: BLE001
            m.warnings.append(f"OpenAlex unavailable: {exc}")
    if person.homepage and fetch is not None:
        try:
            m.homepage_text = html_to_text(fetch(person.homepage), 12000)
        except Exception as exc:  # noqa: BLE001
            m.warnings.append(f"Homepage could not be read: {exc}")
    docs = person_docs_dir(paths, person)
    if docs.exists():
        for f in sorted(docs.iterdir()):
            if f.suffix.lower() in PERSON_DOC_SUFFIXES and not f.name.startswith(("_", ".")):
                try:
                    text = _read_pdf(f) if f.suffix.lower() == ".pdf" else f.read_text(encoding="utf-8")
                    m.documents[f.name] = text[:12000]
                except Exception as exc:  # noqa: BLE001
                    m.warnings.append(f"{f.name}: {exc}")
    return m


# --- analysis ----------------------------------------------------------------------
class Paper(BaseModel):
    title: str
    year: int | None = None
    url: str | None = None
    why: str = ""


class PersonInsight(BaseModel):
    overlap_score: int = Field(0, ge=0, le=100)
    research_summary: str = ""
    current_focus: list[str] = Field(default_factory=list)
    overlaps: list[str] = Field(default_factory=list)
    contribution_ideas: list[str] = Field(default_factory=list)
    outreach_approach: str = ""
    conversation_hooks: list[str] = Field(default_factory=list)
    questions_to_ask: list[str] = Field(default_factory=list)
    draft_message: str = ""
    key_papers: list[Paper] = Field(default_factory=list)
    open_positions: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


SYSTEM = """You are a sharp academic career strategist. You compare ONE researcher's / professional's
public work with an applicant's profile and help the applicant build a genuine professional connection.
Only use the professional material provided (publications, group page, documents). Ignore and never
speculate about private life. Be specific – name concrete papers, methods and projects. Never invent
facts about either person; if material is thin, say so in "caveats".

Return ONLY JSON with these keys:
{
  "overlap_score": int 0-100 (how strongly the applicant's skills and interests overlap with this work),
  "research_summary": "3-4 sentences: what this person works on",
  "current_focus": ["topic or project they are focusing on now", ...],
  "overlaps": ["their <work/method> ↔ applicant's <experience/skill/interest> – why it connects", ...],
  "contribution_ideas": ["concrete way the applicant could support or contribute (data, method, code, case study, student/remote collaboration, ...)", ...],
  "outreach_approach": "how and when to approach this person best (channel, angle, tone, what to attach)",
  "conversation_hooks": ["specific paper/result to reference and what to say about it", ...],
  "questions_to_ask": ["thoughtful question that shows understanding of their work", ...],
  "draft_message": "short first message/e-mail (max ~180 words) in the applicant's voice, with a subject line on the first line",
  "key_papers": [{"title": str, "year": int|null, "url": str|null, "why": "why the applicant should read it"}],
  "open_positions": ["any open position / call mentioned in the material"],
  "caveats": ["missing or ambiguous information, possible wrong person match, ..."]
}
Write all free text in {language}."""

LANG_NAMES = {"de": "German", "en": "English", "ru": "Russian", "fr": "French", "nl": "Dutch"}


def analyze_person(person: Person, material: PersonMaterial, profile: Profile, llm: ChatBackend,
                   language: str = "en") -> PersonInsight:
    system = SYSTEM.replace("{language}", LANG_NAMES.get(language, language))
    user = (
        f"# APPLICANT\n{profile.to_prompt(20000)}\n\n"
        f"# PERSON: {person.name}" + (f" ({person.affiliation})" if person.affiliation else "") + "\n"
        + (f"Applicant's own notes about this person: {person.notes}\n" if person.notes else "")
        + f"\n{material.to_prompt() or '(no material could be collected – say so in caveats)'}"
    )
    insight = ask_model(llm, system, user, PersonInsight)
    if material.warnings:
        insight.caveats = [*insight.caveats, *material.warnings]
    return insight


class PeopleReport(BaseModel):
    analyzed: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)


def needs_refresh(stored: dict | None, person: Person, profile_fp: str, refresh_days: int) -> bool:
    if not stored:
        return True
    if stored.get("person_hash") != person.config_hash() or stored.get("profile_fp") != profile_fp:
        return True
    try:
        analyzed = datetime.fromisoformat(stored["analyzed_at"])
    except (KeyError, ValueError):
        return True
    return datetime.now(timezone.utc) - analyzed > timedelta(days=refresh_days)


def analyze_people(paths: Paths, settings: Settings, profile: Profile, llm: ChatBackend, store,
                   oa: OpenAlexClient | None, fetch: Callable[[str], str] | None,
                   only: str | None = None, force: bool = False) -> PeopleReport:
    rep = PeopleReport()
    cfg = settings.people
    done = 0
    for person in load_people(paths):
        if not person.enabled or (only and person.slug != only):
            continue
        stored = store.get_person_insight(person.slug)
        if not force and not needs_refresh(stored, person, profile.fingerprint, cfg.refresh_days):
            rep.skipped += 1
            continue
        if done >= cfg.max_per_run:
            rep.skipped += 1
            continue
        material = gather_material(person, paths, oa, fetch, cfg.works)
        try:
            insight = analyze_person(person, material, profile, llm, settings.matching.language)
        except LLMError as exc:
            rep.errors.append(f"person {person.name}: {exc}")
            continue
        store.save_person_insight(person.slug, {
            "insight": insight.model_dump(),
            "author": material.author.model_dump() if material.author else None,
            "works": [w.model_dump(exclude={"abstract"}) for w in material.works],
            "person_hash": person.config_hash(),
            "profile_fp": profile.fingerprint,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        })
        rep.analyzed += 1
        done += 1
    return rep
