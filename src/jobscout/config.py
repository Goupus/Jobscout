"""Configuration loading.

All personal data lives in a *data directory* that is kept outside the code
repository (e.g. a private GitHub repo checked out next to it):

    <data_dir>/
        settings.yaml      # LLM model, thresholds, ...
        sources.yaml       # which job sources to scan
        profile/           # CV, interview notes, personality tests, interests
        jobscout.db        # SQLite results (created automatically)

The data directory is taken from ``--data-dir`` or ``$JOBSCOUT_DATA_DIR``
and defaults to ``./data``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


class LLMSettings(BaseModel):
    # Any LiteLLM model string, e.g. "anthropic/claude-sonnet-5-5",
    # "openai/gpt-4o-mini", "ollama/llama3.1".
    model: str = "anthropic/claude-sonnet-5-5"
    # Cheaper model for extraction / pre-filtering. Falls back to `model`.
    fast_model: str | None = None
    temperature: float = 0.2
    max_tokens: int = 2000
    api_base: str | None = None


class MatchingSettings(BaseModel):
    category_threshold: int = Field(65, ge=0, le=100)
    # Skip the expensive LLM call for postings whose keyword pre-score is below this.
    prefilter_min_score: float = 0.0
    max_matches_per_run: int = 60
    language: str = "en"  # language of the generated advice, e.g. "de"


class Settings(BaseModel):
    llm: LLMSettings = LLMSettings()
    matching: MatchingSettings = MatchingSettings()
    http_timeout: float = 30.0
    user_agent: str = "jobscout/0.1 (+https://github.com/Goupus/Jobscout)"


class SourceConfig(BaseModel):
    name: str
    type: Literal["rss", "html", "llm_page"]
    url: str
    enabled: bool = True
    # html: CSS selectors
    item_selector: str | None = None
    title_selector: str | None = None
    link_selector: str | None = None
    description_selector: str | None = None
    location_selector: str | None = None
    organization: str | None = None
    # all: keep only postings whose title/description contains one of these
    include_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    # all: further pages scanned with the same settings (e.g. ?page=2)
    extra_urls: list[str] = Field(default_factory=list)
    # llm_page: only extract positions relevant to this description (saves matching cost)
    focus: str | None = None
    # llm_page: open every posting's own page for the full text
    fetch_details: bool = False
    # llm_page: follow "next page" links of a result list up to this many pages
    max_pages: int = Field(3, ge=1, le=20)
    # llm_page: open at most this many postings per scan (politeness / speed)
    max_details: int = Field(25, ge=0, le=200)
    extra: dict[str, Any] = Field(default_factory=dict)


class Paths(BaseModel):
    data_dir: Path

    @property
    def settings(self) -> Path:
        return self.data_dir / "settings.yaml"

    @property
    def sources(self) -> Path:
        return self.data_dir / "sources.yaml"

    @property
    def profile_dir(self) -> Path:
        return self.data_dir / "profile"

    @property
    def db(self) -> Path:
        return self.data_dir / "jobscout.db"

    @property
    def tracker(self) -> Path:
        return self.data_dir / "tracker.yaml"


def resolve_paths(data_dir: str | Path | None = None) -> Paths:
    raw = data_dir or os.environ.get("JOBSCOUT_DATA_DIR") or "data"
    return Paths(data_dir=Path(raw).expanduser().resolve())


def _read_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_settings(paths: Paths) -> Settings:
    data = _read_yaml(paths.settings) or {}
    return Settings.model_validate(data)


def load_source_defaults(paths: Paths) -> dict[str, Any]:
    data = _read_yaml(paths.sources) or {}
    return dict(data.get("defaults") or {}) if isinstance(data, dict) else {}


def load_sources(paths: Paths) -> list[SourceConfig]:
    """Sources with the optional top-level ``defaults:`` applied (source keys win)."""
    data = _read_yaml(paths.sources) or {}
    items = data.get("sources", []) if isinstance(data, dict) else data
    defaults = load_source_defaults(paths)
    return [SourceConfig.model_validate({**defaults, **s}) for s in items or []]


def save_sources(paths: Paths, sources: list[SourceConfig], defaults: dict[str, Any] | None = None) -> None:
    """Write sources.yaml; values equal to the defaults are not repeated per source."""
    defaults = defaults or {}
    items = []
    for s in sources:
        d = s.model_dump(exclude_defaults=True)
        for k in ("name", "type", "url"):
            d[k] = getattr(s, k)
        for k, v in defaults.items():
            if d.get(k) == v:
                d.pop(k)
        ordered = {k: d.pop(k) for k in ("name", "type", "url")}
        items.append({**ordered, **d})
    body: dict[str, Any] = {}
    if defaults:
        body["defaults"] = defaults
    body["sources"] = items
    header = ("# Job sources for jobscout – edit here or on the 'Sources' page of the app.\n"
              "# 'defaults' apply to every source unless the source sets the key itself.\n")
    paths.sources.write_text(header + yaml.safe_dump(body, allow_unicode=True, sort_keys=False, width=100),
                             encoding="utf-8")


def save_settings(paths: Paths, settings: Settings) -> None:
    paths.settings.write_text(
        "# jobscout settings – edit here or on the 'Scan & sync' page of the app.\n"
        + yaml.safe_dump(settings.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
