"""Streamlit dashboard. Start with ``jobscout dashboard``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from jobscout.config import load_settings, resolve_paths
from jobscout.models import MatchCategory
from jobscout.storage import Store

CATEGORY_COLORS = {
    "top_match": "#1b9e77",
    "stretch": "#7570b3",
    "solid_option": "#d95f02",
    "no_match": "#9e9e9e",
    "pending": "#cfcfcf",
}
STATES = ["new", "shortlisted", "applied", "dismissed"]
STATE_LABELS = {"new": "🆕 New", "shortlisted": "⭐ Shortlisted", "applied": "📨 Applied", "dismissed": "🗑️ Dismissed"}


def _data_dir() -> str | None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=None)
    args, _ = parser.parse_known_args(sys.argv[1:])
    return args.data_dir


st.set_page_config(page_title="jobscout", page_icon="🧭", layout="wide")
paths = resolve_paths(_data_dir())
settings = load_settings(paths)
threshold = settings.matching.category_threshold


@st.cache_resource
def get_store(db: str) -> Store:
    return Store(db)


store = get_store(str(paths.db))


def load_df() -> pd.DataFrame:
    rows = store.overview()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["category"] = df["category"].fillna("pending")
    df["category_label"] = df["category"].map(
        lambda c: MatchCategory(c).label if c in MatchCategory._value2member_map_ else "Not matched yet"
    )
    df["first_seen"] = pd.to_datetime(df["first_seen"], utc=True, format="ISO8601")
    return df


df = load_df()

# --- sidebar -------------------------------------------------------------
st.sidebar.title("🧭 jobscout")
st.sidebar.caption(f"Data: `{paths.data_dir}`")
if df.empty:
    st.info("No postings yet. Run `jobscout scan` (or wait for the next scheduled run).")
    st.stop()

cats = st.sidebar.multiselect(
    "Category",
    options=["top_match", "stretch", "solid_option", "no_match", "pending"],
    default=["top_match", "stretch", "solid_option"],
    format_func=lambda c: MatchCategory(c).label if c != "pending" else "Not matched yet",
)
states = st.sidebar.multiselect("Status", STATES, default=["new", "shortlisted", "applied"], format_func=STATE_LABELS.get)
min_profile = st.sidebar.slider("Min. profile fit", 0, 100, 0, 5)
min_interest = st.sidebar.slider("Min. interest fit", 0, 100, 0, 5)
sources = st.sidebar.multiselect("Sources", sorted(df["source"].unique()))
only_recent = st.sidebar.checkbox("Only found in the last 7 days", value=False)
query = st.sidebar.text_input("Search title / organization")

runs = store.last_runs(1)
if runs:
    st.sidebar.caption(f"Last scan: {runs[0]['started_at'][:16].replace('T', ' ')} UTC · {runs[0]['new_jobs']} new")

view = df[df["category"].isin(cats) & df["state"].isin(states)]
view = view[(view["profile_fit"].fillna(0) >= min_profile) & (view["interest_fit"].fillna(0) >= min_interest)]
if sources:
    view = view[view["source"].isin(sources)]
if only_recent:
    view = view[view["first_seen"] >= pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=7)]
if query:
    q = query.lower()
    view = view[view["title"].str.lower().str.contains(q) | view["organization"].fillna("").str.lower().str.contains(q)]

# --- header & KPIs ---------------------------------------------------------
st.title("Job matches")
k = st.columns(5)
for col, (cat, label) in zip(k, [("top_match", "Top matches"), ("stretch", "Stretch"),
                                 ("solid_option", "Solid options"), ("no_match", "No match"), ("pending", "Not matched")]):
    col.metric(label, int((df["category"] == cat).sum()))

tab_list, tab_map, tab_profile = st.tabs(["📋 Matches", "🗺️ Fit map", "👤 Profile"])

# --- fit map -------------------------------------------------------------
with tab_map:
    matched = view.dropna(subset=["profile_fit", "interest_fit"])
    if matched.empty:
        st.info("No matched postings for the current filters.")
    else:
        base = alt.Chart(matched).mark_circle(size=140, opacity=0.85).encode(
            x=alt.X("profile_fit:Q", title="Profile fit – do I meet the requirements?", scale=alt.Scale(domain=[0, 100])),
            y=alt.Y("interest_fit:Q", title="Interest fit – do I want this?", scale=alt.Scale(domain=[0, 100])),
            color=alt.Color("category:N", scale=alt.Scale(domain=list(CATEGORY_COLORS), range=list(CATEGORY_COLORS.values())), legend=None),
            tooltip=["title", "organization", "profile_fit", "interest_fit", "category_label"],
        )
        rules = alt.Chart(pd.DataFrame({"v": [threshold]})).mark_rule(strokeDash=[4, 4], color="#888")
        st.altair_chart(base + rules.encode(x="v:Q") + rules.encode(y="v:Q"), use_container_width=True)
        st.caption(f"Top right: top matches · top left: stretch/dream jobs · bottom right: solid options. Threshold {threshold}.")

# --- list + details --------------------------------------------------------
with tab_list:
    st.caption(f"{len(view)} of {len(df)} postings")
    sort_by = st.radio("Sort by", ["Combined fit", "Profile fit", "Interest fit", "Newest"], horizontal=True)
    view = view.assign(combined=view["profile_fit"].fillna(0) + view["interest_fit"].fillna(0))
    key = {"Combined fit": "combined", "Profile fit": "profile_fit", "Interest fit": "interest_fit", "Newest": "first_seen"}[sort_by]
    view = view.sort_values(key, ascending=False, na_position="last")

    for _, row in view.iterrows():
        m = row["match"] or {}
        pf = "–" if pd.isna(row["profile_fit"]) else int(row["profile_fit"])
        inf = "–" if pd.isna(row["interest_fit"]) else int(row["interest_fit"])
        dot = {"top_match": "🟢", "stretch": "🟣", "solid_option": "🟠", "no_match": "⚪"}.get(row["category"], "⏳")
        header = f"{dot} **{row['title']}** — {row['organization'] or row['source']} · profile {pf} · interest {inf}"
        with st.expander(header):
            top = st.columns([3, 1])
            with top[0]:
                st.markdown(f"[Open posting ↗]({row['url']}) · source: {row['source']}"
                            + (f" · 📍 {row['location']}" if row["location"] else "")
                            + (f" · ⏰ deadline {row['deadline']}" if row["deadline"] else ""))
                if m.get("summary"):
                    st.write(m["summary"])
            with top[1]:
                new_state = st.selectbox("Status", STATES, index=STATES.index(row["state"]),
                                         format_func=STATE_LABELS.get, key=f"s_{row['uid']}")
                if new_state != row["state"]:
                    store.set_status(row["uid"], new_state)
                    st.rerun()

            if m:
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown("**✅ Matching strengths**")
                    for x in m.get("matching_strengths", []):
                        st.markdown(f"- {x}")
                    st.markdown("**🎯 Emphasize in your application**")
                    for x in m.get("emphasize_in_application", []):
                        st.markdown(f"- {x}")
                with c2:
                    st.markdown("**⚠️ Gaps**")
                    for x in m.get("gaps", []):
                        st.markdown(f"- {x}")
                    st.markdown("**✏️ Tailor your profile/CV**")
                    for x in m.get("profile_tailoring", []):
                        st.markdown(f"- {x}")
                if m.get("red_flags"):
                    st.warning("  \n".join(f"🚩 {x}" for x in m["red_flags"]))
            else:
                st.info("Not matched yet – it will be assessed on the next run.")

            note = st.text_area("Notes", value=row["note"], key=f"n_{row['uid']}", height=68)
            if note != row["note"] and st.button("Save note", key=f"b_{row['uid']}"):
                store.set_status(row["uid"], row["state"], note)
                st.rerun()
            with st.popover("Full description"):
                st.write(row["description"] or "–")

# --- profile -------------------------------------------------------------
with tab_profile:
    st.markdown("Everything in `profile/` is used for matching. Changing the profile triggers re-matching on the next run.")
    for f in sorted(Path(paths.profile_dir).glob("*")):
        if f.is_file() and f.suffix in {".yaml", ".md", ".txt"}:
            with st.expander(f.name):
                text = st.text_area(f.name, f.read_text(encoding="utf-8"), height=300, key=f"p_{f.name}", label_visibility="collapsed")
                if st.button("Save", key=f"ps_{f.name}"):
                    f.write_text(text, encoding="utf-8")
                    st.success("Saved. Commit/push your data repo so the scheduled scan uses it.")
        elif f.is_file():
            st.markdown(f"📄 `{f.name}`")
