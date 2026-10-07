"""Scan → store → match."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import Paths, Settings, SourceConfig
from .llm import ChatBackend, LLMError
from .matcher import match_job, prescore
from .profile import Profile
from .sources import Fetcher, scan_source
from .storage import Store

log = logging.getLogger(__name__)


@dataclass
class RunReport:
    new_jobs: int = 0
    seen_jobs: int = 0
    matched: int = 0
    skipped_prefilter: int = 0
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


def match_pending(store: Store, profile: Profile, llm: ChatBackend, settings: Settings, report: RunReport, model_name: str = "") -> None:
    pending = store.unmatched_jobs(profile.fingerprint, settings.matching.max_matches_per_run)
    for job in pending:
        if prescore(job, profile) < settings.matching.prefilter_min_score:
            report.skipped_prefilter += 1
            continue
        try:
            result = match_job(job, profile, llm, settings.matching, model_name)
        except LLMError as exc:
            report.errors.append(f"match {job.title[:40]}: {exc}")
            continue
        store.save_match(result, profile.fingerprint)
        report.matched += 1


def run(paths: Paths, settings: Settings, sources: list[SourceConfig], profile: Profile,
        fetch: Fetcher, llm: ChatBackend, model_name: str = "", scan: bool = True, match: bool = True) -> RunReport:
    started = datetime.now(timezone.utc).isoformat()
    report = RunReport()
    store = Store(paths.db)
    try:
        if scan:
            scan_all(sources, store, fetch, llm, report)
        if match:
            match_pending(store, profile, llm, settings, report, model_name)
        store.log_run(started, datetime.now(timezone.utc).isoformat(), report.new_jobs, report.matched, report.errors)
    finally:
        store.close()
    return report
