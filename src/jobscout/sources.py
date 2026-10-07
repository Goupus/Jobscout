"""Job sources.

Three source types cover most cases:

* ``rss``      – job boards that publish a feed (many do, e.g. university portals)
* ``html``     – pages with a stable list structure, scraped with CSS selectors
* ``llm_page`` – anything else (research-group pages, career pages without
                 structure): the page text is handed to the LLM, which extracts
                 the open positions.

Please respect each site's terms of use and robots.txt; scanning twice a week
is a deliberately gentle default.
"""

from __future__ import annotations

import logging
from typing import Callable
from urllib.parse import urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup

from .config import Settings, SourceConfig
from .llm import ChatBackend, ask_json
from .models import JobPosting

log = logging.getLogger(__name__)

Fetcher = Callable[[str], str]


def make_fetcher(settings: Settings) -> Fetcher:
    client = httpx.Client(
        timeout=settings.http_timeout,
        follow_redirects=True,
        headers={"User-Agent": settings.user_agent},
    )

    def fetch(url: str) -> str:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.text

    return fetch


def html_to_text(html: str, max_chars: int = 20000) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ").split())
    return text[:max_chars]


def _keyword_filter(jobs: list[JobPosting], cfg: SourceConfig) -> list[JobPosting]:
    def haystack(j: JobPosting) -> str:
        return f"{j.title} {j.description}".lower()

    if cfg.include_keywords:
        inc = [k.lower() for k in cfg.include_keywords]
        jobs = [j for j in jobs if any(k in haystack(j) for k in inc)]
    if cfg.exclude_keywords:
        exc = [k.lower() for k in cfg.exclude_keywords]
        jobs = [j for j in jobs if not any(k in haystack(j) for k in exc)]
    return jobs


# --- rss -----------------------------------------------------------------
def scan_rss(cfg: SourceConfig, fetch: Fetcher) -> list[JobPosting]:
    feed = feedparser.parse(fetch(cfg.url))
    jobs = []
    for e in feed.entries:
        desc = e.get("summary") or e.get("description") or ""
        jobs.append(
            JobPosting(
                source=cfg.name,
                title=e.get("title", "").strip(),
                url=e.get("link", cfg.url),
                organization=cfg.organization or e.get("author"),
                description=html_to_text(desc, 8000) if "<" in desc else desc,
            )
        )
    return jobs


# --- html ----------------------------------------------------------------
def scan_html(cfg: SourceConfig, fetch: Fetcher) -> list[JobPosting]:
    if not cfg.item_selector:
        raise ValueError(f"{cfg.name}: html sources need item_selector")
    soup = BeautifulSoup(fetch(cfg.url), "html.parser")
    jobs = []
    for item in soup.select(cfg.item_selector):
        title_el = item.select_one(cfg.title_selector) if cfg.title_selector else item
        link_el = item.select_one(cfg.link_selector) if cfg.link_selector else item.find("a")
        if title_el is None:
            continue
        href = link_el.get("href") if link_el is not None else None
        desc_el = item.select_one(cfg.description_selector) if cfg.description_selector else None
        loc_el = item.select_one(cfg.location_selector) if cfg.location_selector else None
        jobs.append(
            JobPosting(
                source=cfg.name,
                title=" ".join(title_el.get_text(" ").split()),
                url=urljoin(cfg.url, href) if href else cfg.url,
                organization=cfg.organization,
                location=loc_el.get_text(" ", strip=True) if loc_el else None,
                description=" ".join(desc_el.get_text(" ").split()) if desc_el else "",
            )
        )
    return jobs


# --- llm_page ------------------------------------------------------------
EXTRACT_SYSTEM = """You extract open job/PhD/research positions from web page text.
Return JSON: {"positions": [{"title": str, "url": str|null, "organization": str|null,
"location": str|null, "deadline": str|null, "description": str}]}
Only include positions that are currently open. "description" should be a faithful
summary (max ~150 words) of tasks and requirements. If there are none, return {"positions": []}."""


def scan_llm_page(cfg: SourceConfig, fetch: Fetcher, llm: ChatBackend) -> list[JobPosting]:
    html = fetch(cfg.url)
    soup = BeautifulSoup(html, "html.parser")
    links = "\n".join(
        f"- {' '.join(a.get_text().split())[:80]} -> {urljoin(cfg.url, a['href'])}"
        for a in soup.find_all("a", href=True)
        if a.get_text(strip=True)
    )[:6000]
    system = EXTRACT_SYSTEM
    if cfg.focus:
        system += (f"\nOnly include positions that could plausibly be relevant to: {cfg.focus}. "
                   "When in doubt, include the position.")
    user = f"Page URL: {cfg.url}\n\nPAGE TEXT:\n{html_to_text(html)}\n\nLINKS ON PAGE:\n{links}"
    data = ask_json(llm, system, user, fast=True)
    jobs = []
    for p in (data or {}).get("positions", []):
        if not p.get("title"):
            continue
        url = urljoin(cfg.url, p["url"]) if p.get("url") else cfg.url
        description = p.get("description") or ""
        if cfg.fetch_details and url != cfg.url:
            try:
                description = html_to_text(fetch(url), 8000)
            except Exception as exc:  # noqa: BLE001
                log.warning("detail fetch failed for %s: %s", url, exc)
        jobs.append(
            JobPosting(
                source=cfg.name, title=p["title"], url=url,
                organization=p.get("organization") or cfg.organization,
                location=p.get("location"), deadline=p.get("deadline"),
                description=description,
            )
        )
    return jobs


def scan_source(cfg: SourceConfig, fetch: Fetcher, llm: ChatBackend | None) -> list[JobPosting]:
    """Scan `cfg.url` plus every entry of `cfg.extra_urls`."""
    jobs: list[JobPosting] = []
    for url in [cfg.url, *cfg.extra_urls]:
        page_cfg = cfg.model_copy(update={"url": url, "extra_urls": []})
        try:
            jobs += _scan_one(page_cfg, fetch, llm)
        except Exception:
            if url == cfg.url:
                raise  # first page failing = the source is broken
            log.warning("%s: extra page %s failed", cfg.name, url, exc_info=True)
    seen: set[str] = set()
    return [j for j in jobs if not (j.uid in seen or seen.add(j.uid))]


def _scan_one(cfg: SourceConfig, fetch: Fetcher, llm: ChatBackend | None) -> list[JobPosting]:
    if cfg.type == "rss":
        jobs = scan_rss(cfg, fetch)
    elif cfg.type == "html":
        jobs = scan_html(cfg, fetch)
    elif cfg.type == "llm_page":
        if llm is None:
            raise ValueError("llm_page sources need an LLM backend")
        jobs = scan_llm_page(cfg, fetch, llm)
    else:  # pragma: no cover - guarded by pydantic Literal
        raise ValueError(cfg.type)
    return _keyword_filter([j for j in jobs if j.title], cfg)
