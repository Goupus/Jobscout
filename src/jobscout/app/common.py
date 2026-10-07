"""Shared helpers for the Streamlit app pages."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from jobscout.config import Paths, Settings, load_settings, load_sources, resolve_paths
from jobscout.interview_form import FORM_FILENAME
from jobscout.profile import Interests, Profile, load_profile
from jobscout.storage import Store

PROFILE_DOC_SUFFIXES = {".pdf", ".md", ".txt", ".yaml", ".yml"}
RESERVED_PROFILE_FILES = {"profile.yaml", "interests.yaml", FORM_FILENAME}
EXAMPLE_HOSTS = ("example.org", "example.com", "uni.example")
API_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "groq": "GROQ_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


PAGE_REGISTRY: dict[str, "st.Page"] = {}


def page(url_path: str):
    """The st.Page registered under `url_path` (for st.page_link / st.switch_page)."""
    return PAGE_REGISTRY[url_path]


def _data_dir_arg() -> str | None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=None)
    args, _ = parser.parse_known_args(sys.argv[1:])
    return args.data_dir


def paths() -> Paths:
    if "paths" not in st.session_state:
        st.session_state.paths = resolve_paths(_data_dir_arg())
    return st.session_state.paths


def settings() -> Settings:
    return load_settings(paths())


def profile() -> Profile | None:
    p = paths().profile_dir
    return load_profile(p) if p.exists() else None


def store() -> Store:
    """A database connection for the current script run.

    Streamlit may execute each rerun on a different thread, and a pull from GitHub
    replaces the database file – so the connection is reopened whenever the
    thread or the file changes.
    """
    import threading

    db = paths().db
    stamp = (threading.get_ident(), db.stat().st_mtime_ns if db.exists() else 0, db.stat().st_ino if db.exists() else 0)
    cached = st.session_state.get("_store")
    if cached is None or cached[0] != stamp:
        if cached is not None:
            try:
                cached[1].close()
            except Exception:  # noqa: BLE001 - closing from another thread may fail; it's garbage anyway
                pass
        st.session_state._store = (stamp, Store(db))
    return st.session_state._store[1]


def provider_env_var(model: str) -> str | None:
    provider = model.split("/", 1)[0] if "/" in model else ("openai" if model.startswith("gpt") else "")
    return API_KEY_ENV.get(provider)


def llm_ready(s: Settings) -> tuple[bool, str]:
    if s.llm.model.startswith("ollama"):
        return True, "Local model via Ollama – no key needed."
    var = provider_env_var(s.llm.model)
    if var is None:
        return True, f"Make sure the credentials for `{s.llm.model}` are set."
    if os.environ.get(var):
        return True, f"`{var}` is set."
    return False, f"`{var}` is not set on this computer."


def user_documents(profile_dir: Path) -> list[Path]:
    if not profile_dir.exists():
        return []
    return [f for f in sorted(profile_dir.iterdir())
            if f.is_file() and f.suffix.lower() in PROFILE_DOC_SUFFIXES
            and f.name not in RESERVED_PROFILE_FILES and not f.name.startswith(("_", "."))]


@dataclass
class Step:
    title: str
    done: bool
    detail: str
    page: str  # url_path of the page that handles it


def setup_steps() -> list[Step]:
    from jobscout import sync

    p, s = paths(), settings()
    docs = user_documents(p.profile_dir)
    interests = profile().interests if p.profile_dir.exists() else Interests()
    sources = [x for x in load_sources(p) if x.enabled]
    real_sources = [x for x in sources if not any(h in x.url for h in EXAMPLE_HOSTS)]
    ok, llm_msg = llm_ready(s)
    repo = sync.status(p.data_dir)
    runs = store().last_runs(1)
    return [
        Step("Upload your CV and other documents", bool(docs),
             f"{len(docs)} document(s) in your profile." if docs else "CV as PDF or Markdown; personality tests, references, project descriptions.",
             "profile"),
        Step("Do the interview", (p.profile_dir / FORM_FILENAME).exists(),
             "Interview form saved." if (p.profile_dir / FORM_FILENAME).exists()
             else "With any chatbot (copy & paste) or right here – ends in a filled form.",
             "interview"),
        Step("Check your interests", bool(interests.topics),
             f"{len(interests.topics)} topics, {len(interests.dealbreakers)} dealbreakers." if interests.topics
             else "Topics, role types, locations and dealbreakers drive the interest score.",
             "profile"),
        Step("Choose your sources", bool(real_sources),
             f"{len(real_sources)} active source(s)." if real_sources else "Job boards, career pages, research groups.",
             "sources"),
        Step("Connect a language model", ok or (repo.is_repo and bool(repo.remote)),
             llm_msg + ("" if ok else " Fine if scans only run on GitHub – there the key is a repository secret."),
             "run"),
        Step("Save everything to GitHub", repo.is_repo and bool(repo.remote) and not repo.changed,
             ("All changes uploaded." if not repo.changed else f"{len(repo.changed)} unsaved change(s).")
             if repo.is_repo and repo.remote else "Connect the data folder to your private GitHub repository.",
             "run"),
        Step("Run the first scan", bool(runs),
             f"Last scan: {runs[0]['started_at'][:16].replace('T', ' ')} UTC" if runs
             else "Start it on GitHub (Actions) or here on the 'Scan & sync' page.",
             "run"),
    ]


def ensure_data_dir() -> bool:
    """Offer to create the data directory from templates if it is missing."""
    p = paths()
    if p.settings.exists() or p.profile_dir.exists():
        return True
    st.warning(f"No jobscout data found in `{p.data_dir}`.")
    if st.button("Create it from the templates", type="primary"):
        from jobscout.cli import copy_templates

        copy_templates(p.data_dir)
        st.rerun()
    return False
