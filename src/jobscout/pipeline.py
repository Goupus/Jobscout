"""Scan → store → match."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import Paths, Settings, SourceConfig
from .llm import ChatBackend, LLMError
from .matcher import criteria_key, match_job, prescore
from .profile import Profile
from .sources import Fetcher, scan_source
from .storage import Store
from .tracker import Tracker

log = logging.getLogger(__name__)


@dataclass
class RunReport:
    new_jobs: int = 0
    seen_jobs: int = 0
    matched: int = 0
    skipped_prefilter: int = 0
    people_analyzed: int = 0
    errors: list[str] = field(default_factory=list)


def scan_all(sources: list[SourceConfig], store: Store, fetch: Fetcher, llm: ChatBackend | None, report: RunReport) -> None:
    for cfg in sources:
        if not cfg.enabled:
            continue
        try:
            jobs = scan_source(cfg, fetch, llm)
        except Exception as exc:  # noqa: BLE001 - one broken source must not stop the run
            report.errors.append(f"{cfg.name}: {exc}")
            log.warning("source %s failed: %s", cfg.name, exc)
            continue
        for job in jobs:
            if store.upsert_job(job):
                report.new_jobs += 1
            else:
                report.seen_jobs += 1
        log.info("%s: %d postings", cfg.name, len(jobs))


def match_pending(store: Store, profile: Profile, llm: ChatBackend, settings: Settings, report: RunReport,
                  model_name: str = "", dismissed: set[str] | None = None) -> None:
    key = criteria_key(profile, settings.matching)
    pending = store.unmatched_jobs(key, settings.matching.max_matches_per_run, exclude=dismissed)
    for job in pending:
        if prescore(job, profile) < settings.matching.prefilter_min_score:
            report.skipped_prefilter += 1
            continue
        try:
            result = match_job(job, profile, llm, settings.matching, model_name)
        except LLMError as exc:
            report.errors.append(f"match {job.title[:40]}: {exc}")
            continue
        store.save_match(result, key)
        report.matched += 1


def run(paths: Paths, settings: Settings, sources: list[SourceConfig], profile: Profile,
        fetch: Fetcher, llm: ChatBackend, model_name: str = "", scan: bool = True, match: bool = True,
        people: bool = True, openalex=None) -> RunReport:
    started = datetime.now(timezone.utc).isoformat()
    report = RunReport()
    store = Store(paths.db)
    try:
        if scan:
            scan_all(sources, store, fetch, llm, report)
        if match:
            match_pending(store, profile, llm, settings, report, model_name, Tracker(paths.tracker).dismissed())
        if people:
            from .people import OpenAlexClient, analyze_people, default_get_json, load_people

            if load_people(paths):
                oa = openalex if openalex is not None else OpenAlexClient(default_get_json(settings))
                prep = analyze_people(paths, settings, profile, llm, store, oa, fetch)
                report.people_analyzed = prep.analyzed
                report.errors += prep.errors
        store.log_run(started, datetime.now(timezone.utc).isoformat(), report.new_jobs, report.matched, report.errors)
    finally:
        store.close()
    return report
