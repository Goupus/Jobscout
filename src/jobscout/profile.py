"""Applicant profile: everything the matcher knows about you.

The profile directory may contain:

* ``profile.yaml``    – structured facts (skills, experience, education, constraints)
* ``interests.yaml``  – what you *want*: topics, role types, values, dealbreakers
* ``interview_form.yaml`` – the filled interview form (see ``interview_form.py``)
* any ``*.md``, ``*.txt``, ``*.pdf`` or other ``*.yaml`` – CV, personality test
  results, reference letters, ... (all are read as free text)

Files whose name starts with ``_`` or ``.`` are ignored.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

TEXT_SUFFIXES = {".md", ".txt"}
MAX_DOC_CHARS = 15000


class Interests(BaseModel):
    topics: list[str] = Field(default_factory=list)
    role_types: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    values: list[str] = Field(default_factory=list)
    dealbreakers: list[str] = Field(default_factory=list)
    notes: str = ""


class Profile(BaseModel):
    facts: dict[str, Any] = Field(default_factory=dict)
    interests: Interests = Interests()
    documents: dict[str, str] = Field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        """Changes whenever the profile changes → triggers re-matching."""
        return hashlib.sha1(self.model_dump_json().encode()).hexdigest()[:12]

    def keywords(self) -> set[str]:
        """Lower-cased terms used for the cheap pre-filter."""
        terms: list[str] = [*self.interests.topics, *self.interests.role_types]
        skills = self.facts.get("skills", [])
        if isinstance(skills, dict):
            for v in skills.values():
                terms += v if isinstance(v, list) else [str(v)]
        elif isinstance(skills, list):
            terms += [str(s) for s in skills]
        out: set[str] = set()
        for t in terms:
            for w in re.split(r"[,/;()]|\band\b", t.lower()):
                w = w.strip()
                if len(w) >= 3:
                    out.add(w)
        return out

    def to_prompt(self, max_chars: int = 40000) -> str:
        parts = ["## STRUCTURED PROFILE", yaml.safe_dump(self.facts, allow_unicode=True, sort_keys=False)]
        parts += ["## INTERESTS AND PREFERENCES", yaml.safe_dump(self.interests.model_dump(), allow_unicode=True, sort_keys=False)]
        for name, text in self.documents.items():
            parts += [f"## DOCUMENT: {name}", text]
        out = "\n\n".join(parts)
        return out[:max_chars]


def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def load_profile(profile_dir: Path) -> Profile:
    if not profile_dir.exists():
        raise FileNotFoundError(
            f"Profile directory {profile_dir} not found. Run `jobscout init` or copy examples/data."
        )
    facts: dict[str, Any] = {}
    interests = Interests()
    docs: dict[str, str] = {}
    for path in sorted(profile_dir.iterdir()):
        if path.name.startswith(("_", ".")) or not path.is_file():
            continue
        if path.name == "profile.yaml":
            facts = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        elif path.name == "interests.yaml":
            interests = Interests.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        elif path.suffix.lower() in TEXT_SUFFIXES or path.suffix.lower() in {".yaml", ".yml"}:
            docs[path.name] = path.read_text(encoding="utf-8")[:MAX_DOC_CHARS]
        elif path.suffix.lower() == ".pdf":
            docs[path.name] = _read_pdf(path)[:MAX_DOC_CHARS]
    return Profile(facts=facts, interests=interests, documents=docs)
