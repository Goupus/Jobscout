"""The interview form: a fixed YAML structure that any LLM can fill in.

Workflow:
1. ``build_prompt()`` produces a copy-paste prompt (optionally with your current
   profile) for ChatGPT, Claude, Gemini, a local model, ...
2. The LLM interviews you and finally answers with the filled form.
3. ``parse_form()`` validates that answer, ``save_form()`` stores it as
   ``profile/interview_form.yaml`` – the matcher reads it like any profile document.
4. ``merge_interests()`` optionally copies the form's interests into ``interests.yaml``.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .profile import Interests, Profile

TEMPLATES = Path(__file__).parent / "templates" / "interview"
FORM_FILENAME = "interview_form.yaml"
FINISH_MESSAGE = {
    "en": "done – please output the completed form now, as instructed.",
    "de": "fertig – bitte gib jetzt das ausgefüllte Formular aus, wie vereinbart.",
}


class FormError(ValueError):
    pass


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="allow")


class Strength(_Lenient):
    strength: str = ""
    evidence: str = ""


class Achievement(_Lenient):
    what: str = ""
    result: str = ""


class InterviewForm(_Lenient):
    form_version: int = 1
    completed_on: str = ""
    summary: str = ""
    motivation: dict[str, Any] = Field(default_factory=dict)
    strengths: list[Strength] = Field(default_factory=list)
    achievements: list[Achievement] = Field(default_factory=list)
    tasks: dict[str, Any] = Field(default_factory=dict)
    working_style: dict[str, Any] = Field(default_factory=dict)
    personality: str = ""
    interests: Interests = Interests()
    constraints: dict[str, Any] = Field(default_factory=dict)
    open_questions: list[str] = Field(default_factory=list)

    def filled_sections(self) -> dict[str, bool]:
        def has(v: Any) -> bool:
            if isinstance(v, BaseModel):
                v = v.model_dump()
            if isinstance(v, dict):
                return any(has(x) for x in v.values())
            if isinstance(v, list):
                return any(has(x) for x in v)
            return bool(str(v).strip())

        keys = ["summary", "motivation", "strengths", "achievements", "tasks",
                "working_style", "personality", "interests", "constraints"]
        return {k: has(getattr(self, k)) for k in keys}


def form_template() -> str:
    return (TEMPLATES / "form.yaml").read_text(encoding="utf-8")


def build_prompt(language: str = "en", profile: Profile | None = None, max_profile_chars: int = 15000) -> str:
    lang = language if (TEMPLATES / f"prompt_{language}.md").exists() else "en"
    prompt = (TEMPLATES / f"prompt_{lang}.md").read_text(encoding="utf-8")
    form = "\n".join(line for line in form_template().splitlines() if not line.startswith("#"))
    profile_block = ""
    if profile is not None:
        title = "Mein aktuelles Profil" if lang == "de" else "My current profile"
        profile_block = f"\n## {title}\n{profile.to_prompt(max_profile_chars)}\n"
    return prompt.replace("{FORM}", form.strip()).replace("{PROFILE}", profile_block)


def parse_form(text: str) -> InterviewForm:
    """Accepts the raw LLM answer (with or without code fences)."""
    fenced = re.findall(r"```(?:ya?ml)?\s*\n(.*?)```", text, re.S)
    raw = max(fenced, key=len) if fenced else text
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise FormError(f"Not valid YAML: {exc}") from exc
    if not isinstance(data, dict) or "interests" not in data:
        raise FormError("This does not look like the interview form (no 'interests' section found).")
    try:
        form = InterviewForm.model_validate(data)
    except ValidationError as exc:
        raise FormError(f"The form does not match the expected structure: {exc}") from exc
    if not form.completed_on:
        form.completed_on = date.today().isoformat()
    return form


def save_form(form: InterviewForm, profile_dir: Path) -> Path:
    profile_dir.mkdir(parents=True, exist_ok=True)
    path = profile_dir / FORM_FILENAME
    if path.exists():  # keep the previous version, ignored by the matcher
        path.replace(profile_dir / f"_previous_{FORM_FILENAME}")
    header = "# Filled interview form – edit freely. Read by the matcher as part of your profile.\n"
    path.write_text(header + yaml.safe_dump(form.model_dump(), allow_unicode=True, sort_keys=False, width=100),
                    encoding="utf-8")
    return path


def merge_interests(form: InterviewForm, profile_dir: Path) -> Path:
    """Union of list fields; notes are appended. Existing entries are kept."""
    path = profile_dir / "interests.yaml"
    current = Interests.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else Interests()
    new = form.interests
    merged: dict[str, Any] = {}
    for field in ("topics", "role_types", "preferred_locations", "values", "dealbreakers"):
        items = list(getattr(current, field))
        lower = {i.lower() for i in items}
        items += [i for i in getattr(new, field) if i and i.lower() not in lower]
        merged[field] = items
    notes = [n for n in (current.notes.strip(), new.notes.strip()) if n]
    merged["notes"] = "\n".join(dict.fromkeys(notes))
    path.write_text(yaml.safe_dump(merged, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8")
    return path
