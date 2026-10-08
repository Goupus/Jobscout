"""Scan & sync page: GitHub sync, run a scan, LLM settings, run history."""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from jobscout import sync
from jobscout.app import common
from jobscout.config import save_settings

LANGUAGES = {"de": "Deutsch", "en": "English", "fr": "Français", "nl": "Nederlands", "ru": "Русский"}


def render() -> None:
    st.title("🔄 Scan & sync")
    if not common.ensure_data_dir():
        return
    p = common.paths()
    _github(p)
    st.divider()
    _scan(p)
    st.divider()
    _settings(p)
    st.divider()
    _history()


def _github(p) -> None:
    st.subheader("GitHub")
    repo = sync.status(p.data_dir)
    if not repo.is_repo or not repo.remote:
        st.warning("This data folder is not connected to a GitHub repository yet, so the twice-weekly scan can't use it.")
        st.markdown(
            """
**One-time setup**
1. Create a **private** repository on GitHub (e.g. `jobscout-data`).
2. Open a terminal in the data folder and run:
   ```bash
   git init -b main && git add -A && git commit -m "jobscout data"
   git remote add origin https://github.com/<you>/jobscout-data.git
   git push -u origin main
   ```
   (Or clone the empty repository with GitHub Desktop and start the app in that folder.)
3. In the repository: *Settings → Secrets and variables → Actions* → add your API key (e.g. `ANTHROPIC_API_KEY`).
"""
        )
        return
    st.markdown(f"Connected to **[{repo.github_url or repo.remote}]({repo.github_url or repo.remote})** · branch `{repo.branch}`")
    if repo.changed:
        st.info(f"{len(repo.changed)} unsaved change(s): " + ", ".join(f"`{c}`" for c in repo.changed[:8])
                + (" …" if len(repo.changed) > 8 else ""))
    c = st.columns(2)
    if c[0].button("⬇️ Get latest results", width="stretch"):
        _do(sync.pull, p.data_dir)
    msg = c[1].text_input("Change description", "Update via jobscout app", label_visibility="collapsed")
    if c[1].button("⬆️ Save my changes to GitHub", type="primary", width="stretch", disabled=not repo.changed):
        _do(sync.push, p.data_dir, msg)
    if repo.github_url:
        st.caption(f"Secrets for the scheduled scan: [repository settings]({repo.github_url}/settings/secrets/actions)")


def _do(fn, *args) -> None:
    try:
        st.success(fn(*args))
    except sync.SyncError as exc:
        st.error(f"Git said: {exc}\n\nMake sure git is logged in to GitHub on this computer (e.g. `gh auth login` or GitHub Desktop).")


def _scan(p) -> None:
    st.subheader("Scan")
    repo = sync.status(p.data_dir)
    left, right = st.columns(2)
    with left:
        st.markdown("**On GitHub (recommended)** – runs automatically every **Monday and Thursday**.")
        if repo.github_url:
            st.link_button("Run the scan on GitHub now ↗", f"{repo.github_url}/actions/workflows/scan.yml")
            st.caption("Click *Run workflow* there. Afterwards use *Get latest results*.")
        else:
            st.caption("Available once the data folder is connected to GitHub.")
    with right:
        st.markdown("**Here on this computer** – uses the API key set below.")
        s = common.settings()
        ok, msg = common.llm_ready(s)
        only_collect = st.checkbox("Only collect postings (no matching)")
        if st.button("Run scan here", disabled=not ok):
            from jobscout.llm import LiteLLMBackend
            from jobscout.pipeline import run
            from jobscout.config import load_sources
            from jobscout.sources import make_fetcher

            with st.spinner("Scanning and matching – this can take a few minutes…"):
                report = run(p, s, load_sources(p), common.profile(), make_fetcher(s), LiteLLMBackend(s.llm),
                             model_name=s.llm.model, match=not only_collect)
            st.success(f"New: {report.new_jobs} · known: {report.seen_jobs} · matched: {report.matched}")
            for e in report.errors:
                st.warning(e)
        if not ok:
            st.caption(msg)


def _settings(p) -> None:
    st.subheader("Language model & matching")
    s = common.settings()
    var = common.provider_env_var(s.llm.model)
    if var:
        key = st.text_input(f"{var} (only for this app session – never saved)", type="password",
                            value=os.environ.get(var, ""))
        if key and key != os.environ.get(var):
            os.environ[var] = key
            st.toast("Key set for this session")
    with st.form("settings"):
        c = st.columns(2)
        model = c[0].text_input("Model", s.llm.model, help="LiteLLM model name, e.g. anthropic/claude-sonnet-5-5, openai/gpt-4o-mini, ollama/llama3.1")
        fast = c[1].text_input("Cheaper model for reading pages (optional)", s.llm.fast_model or "")
        c = st.columns(3)
        lang = c[0].selectbox("Language of tips", list(LANGUAGES), format_func=LANGUAGES.get,
                              index=list(LANGUAGES).index(s.matching.language) if s.matching.language in LANGUAGES else 1)
        threshold = c[1].slider("Score needed for a 'good' fit", 40, 90, s.matching.category_threshold, 5)
        cap = c[2].number_input("Max. postings matched per scan (cost limit)", 5, 500, s.matching.max_matches_per_run, 5)
        if st.form_submit_button("Save settings", type="primary"):
            s.llm.model, s.llm.fast_model = model.strip(), (fast.strip() or None)
            s.matching.language, s.matching.category_threshold, s.matching.max_matches_per_run = lang, threshold, int(cap)
            save_settings(p, s)
            st.success("Saved.")


def _history() -> None:
    st.subheader("Recent scans")
    runs = common.store().last_runs(10)
    if not runs:
        st.caption("No scans yet.")
        return
    df = pd.DataFrame(runs)[["started_at", "new_jobs", "matched", "errors"]]
    df["started_at"] = df["started_at"].str[:16].str.replace("T", " ")
    df["errors"] = df["errors"].map(lambda e: "" if e in ("[]", None) else e)
    st.dataframe(df, width="stretch", hide_index=True,
                 column_config={"started_at": "Started (UTC)", "new_jobs": "New", "matched": "Matched", "errors": "Errors"})
