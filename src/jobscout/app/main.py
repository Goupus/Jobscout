"""jobscout app – start with ``jobscout app``."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

# allow `streamlit run src/jobscout/app/main.py` without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobscout import sync  # noqa: E402
from jobscout.app import common  # noqa: E402
from jobscout.app.views import interview, matches, people, profile, run, sources, start  # noqa: E402

st.set_page_config(page_title="jobscout", page_icon="🧭", layout="wide")

# JOBSCOUT_START_PAGE lets tests (and bookmarks) open a specific page first
_first = os.environ.get("JOBSCOUT_START_PAGE", "start")
PAGES = [
    st.Page(fn, title=title, icon=icon, url_path=slug, default=(slug == _first))
    for fn, title, icon, slug in [
        (start.render, "Start", "🧭", "start"),
        (profile.render, "Profile", "👤", "profile"),
        (interview.render, "Interview", "💬", "interview"),
        (sources.render, "Sources", "🔎", "sources"),
        (people.render, "People", "👥", "people"),
        (matches.render, "Matches", "📋", "matches"),
        (run.render, "Scan & sync", "🔄", "run"),
    ]
]
common.PAGE_REGISTRY.update({pg.url_path: pg for pg in PAGES})
nav = st.navigation(PAGES)

# --- sidebar: data folder + sync status --------------------------------------
with st.sidebar:
    p = common.paths()
    st.caption(f"Data folder: `{p.data_dir}`")
    repo = sync.status(p.data_dir)
    if repo.is_repo and repo.remote:
        if repo.changed:
            st.info(f"{len(repo.changed)} unsaved change(s)")
            if st.button("⬆️ Save to GitHub", width="stretch"):
                try:
                    st.toast(sync.push(p.data_dir))
                except sync.SyncError as exc:
                    st.error(str(exc))
                st.rerun()
        else:
            st.caption("✅ In sync with GitHub")
        if st.button("⬇️ Get latest results", width="stretch"):
            try:
                st.toast(sync.pull(p.data_dir))
            except sync.SyncError as exc:
                st.error(str(exc))
            st.rerun()
    elif p.settings.exists():
        st.caption("Not connected to GitHub – see 'Scan & sync'.")

nav.run()
