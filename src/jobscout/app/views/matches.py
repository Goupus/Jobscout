"""Matches page: KPIs, fit map, filterable list with advice and tracker."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from jobscout.app import common
from jobscout.models import MatchCategory
from jobscout.tracker import STATES, Tracker

CATEGORY_COLORS = {"top_match": "#1b9e77", "stretch": "#7570b3", "solid_option": "#d95f02",
                   "no_match": "#9e9e9e", "pending": "#cfcfcf"}
CATEGORY_DOTS = {"top_match": "🟢", "stretch": "🟣", "solid_option": "🟠", "no_match": "⚪", "pending": "⏳"}
STATE_LABELS = {"new": "🆕 New", "shortlisted": "⭐ Shortlisted", "applied": "📨 Applied", "dismissed": "🗑️ Dismissed"}


def _cat_label(c: str) -> str:
    return MatchCategory(c).label if c in MatchCategory._value2member_map_ else "Not matched yet"


def load_df(tracker: Tracker) -> pd.DataFrame:
    rows = common.store().overview(tracker)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["category"] = df["category"].fillna("pending")
    df["category_label"] = df["category"].map(_cat_label)
    df["first_seen"] = pd.to_datetime(df["first_seen"], utc=True, format="ISO8601")
    return df


def render() -> None:
    st.title("📋 Matches")
    if not common.ensure_data_dir():
        return
    p, s = common.paths(), common.settings()
    threshold = s.matching.category_threshold
    tracker = Tracker(p.tracker)
    df = load_df(tracker)
    if df.empty:
        st.info("No postings yet. After the first scan they show up here.")
        st.page_link(common.page("run"), label="Start a scan", icon="🔄")
        return

    k = st.columns(5)
    for col, cat in zip(k, ["top_match", "stretch", "solid_option", "no_match", "pending"]):
        col.metric(f"{CATEGORY_DOTS[cat]} {_cat_label(cat)}", int((df["category"] == cat).sum()))

    with st.expander("🔍 Filter", expanded=False):
        c = st.columns(3)
        cats = c[0].multiselect("Category", list(CATEGORY_COLORS), default=["top_match", "stretch", "solid_option"],
                                format_func=_cat_label)
        states = c[1].multiselect("Status", STATES, default=["new", "shortlisted", "applied"], format_func=STATE_LABELS.get)
        srcs = c[2].multiselect("Sources", sorted(df["source"].unique()))
        c = st.columns(3)
        min_profile = c[0].slider("Min. profile fit", 0, 100, 0, 5)
        min_interest = c[1].slider("Min. interest fit", 0, 100, 0, 5)
        recent = c[2].toggle("Only the last 7 days")
        query = st.text_input("Search title / organization")

    view = df[df["category"].isin(cats) & df["state"].isin(states)]
    view = view[(view["profile_fit"].fillna(0) >= min_profile) & (view["interest_fit"].fillna(0) >= min_interest)]
    if srcs:
        view = view[view["source"].isin(srcs)]
    if recent:
        view = view[view["first_seen"] >= pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=7)]
    if query:
        q = query.lower()
        view = view[view["title"].str.lower().str.contains(q, regex=False)
                    | view["organization"].fillna("").str.lower().str.contains(q, regex=False)]

    tab_list, tab_map = st.tabs(["📋 List", "🗺️ Fit map"])
    with tab_map:
        _fit_map(view, threshold)
    with tab_list:
        _list(view, len(df), tracker)


def _fit_map(view: pd.DataFrame, threshold: int) -> None:
    matched = view.dropna(subset=["profile_fit", "interest_fit"])
    if matched.empty:
        st.info("No matched postings for the current filters.")
        return
    points = alt.Chart(matched).mark_circle(size=150, opacity=0.85).encode(
        x=alt.X("profile_fit:Q", title="Profile fit – do I meet the requirements?", scale=alt.Scale(domain=[0, 100])),
        y=alt.Y("interest_fit:Q", title="Interest fit – do I want this?", scale=alt.Scale(domain=[0, 100])),
        color=alt.Color("category:N", legend=None,
                        scale=alt.Scale(domain=list(CATEGORY_COLORS), range=list(CATEGORY_COLORS.values()))),
        tooltip=["title", "organization", "profile_fit", "interest_fit", "category_label"],
    )
    rule = alt.Chart(pd.DataFrame({"v": [threshold]})).mark_rule(strokeDash=[4, 4], color="#888")
    st.altair_chart(points + rule.encode(x="v:Q") + rule.encode(y="v:Q"), use_container_width=True)
    st.caption(f"Top right: top matches · top left: stretch / dream jobs · bottom right: solid options (threshold {threshold}).")


def _list(view: pd.DataFrame, total: int, tracker: Tracker) -> None:
    c = st.columns([0.6, 0.4], vertical_alignment="center")
    c[0].caption(f"{len(view)} of {total} postings")
    sort_by = c[1].selectbox("Sort by", ["Combined fit", "Profile fit", "Interest fit", "Newest"], label_visibility="collapsed")
    view = view.assign(combined=view["profile_fit"].fillna(0) + view["interest_fit"].fillna(0))
    key = {"Combined fit": "combined", "Profile fit": "profile_fit", "Interest fit": "interest_fit", "Newest": "first_seen"}[sort_by]
    view = view.sort_values(key, ascending=False, na_position="last")

    for _, row in view.iterrows():
        m = row["match"] or {}
        pf = "–" if pd.isna(row["profile_fit"]) else int(row["profile_fit"])
        inf = "–" if pd.isna(row["interest_fit"]) else int(row["interest_fit"])
        star = " ⭐" if row["state"] == "shortlisted" else (" 📨" if row["state"] == "applied" else "")
        header = f"{CATEGORY_DOTS.get(row['category'], '⏳')} **{row['title']}**{star} — {row['organization'] or row['source']} · profile {pf} · interest {inf}"
        with st.expander(header):
            top = st.columns([0.75, 0.25])
            with top[0]:
                st.markdown(f"[Open posting ↗]({row['url']}) · {row['source']}"
                            + (f" · 📍 {row['location']}" if row["location"] else "")
                            + (f" · ⏰ deadline {row['deadline']}" if row["deadline"] else ""))
                if m.get("summary"):
                    st.write(m["summary"])
            with top[1]:
                new_state = st.selectbox("Status", STATES, index=STATES.index(row["state"]),
                                         format_func=STATE_LABELS.get, key=f"s_{row['uid']}")
                if new_state != row["state"]:
                    tracker.set(row["uid"], state=new_state, title=row["title"])
                    st.rerun()
            if m:
                a, b = st.columns(2)
                _bullets(a, "✅ Matching strengths", m.get("matching_strengths"))
                _bullets(a, "🎯 Emphasize in your application", m.get("emphasize_in_application"))
                _bullets(b, "⚠️ Gaps", m.get("gaps"))
                _bullets(b, "✏️ Tailor your CV / profile", m.get("profile_tailoring"))
                if m.get("red_flags"):
                    st.warning("  \n".join(f"🚩 {x}" for x in m["red_flags"]))
            else:
                st.info("Not matched yet – it will be assessed on the next scan.")
            note = st.text_area("Notes", value=row["note"], key=f"n_{row['uid']}", height=68)
            if note != row["note"]:
                tracker.set(row["uid"], note=note, title=row["title"])
                st.toast("Note saved")
            with st.popover("Full description"):
                st.write(row["description"] or "–")


def _bullets(col, title: str, items) -> None:
    if items:
        col.markdown(f"**{title}**")
        col.markdown("\n".join(f"- {x}" for x in items))
