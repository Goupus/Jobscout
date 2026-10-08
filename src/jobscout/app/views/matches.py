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


def load_df(tracker: Tracker, threshold: int = 65) -> pd.DataFrame:
    from jobscout.models import categorize

    rows = common.store().overview(tracker)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    # category follows the current threshold, so changing it needs no new (paid) assessment
    df["category"] = [
        categorize(int(pf), int(inf), threshold).value if pd.notna(pf) and pd.notna(inf) else "pending"
        for pf, inf in zip(df["profile_fit"], df["interest_fit"])
    ]
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
    _last_run_problems()
    _criteria_editor(p, s)
    df = load_df(tracker, threshold)
    if df.empty:
        st.info("No postings yet. After the first scan they show up here.")
        st.page_link(common.page("run"), label="Start a scan", icon="🔄")
        return
    pending = int((df["category"] == "pending").sum())
    if pending:
        st.info(f"⏳ {pending} posting(s) found but not assessed yet – they are assessed on the next scan "
                f"(max. {s.matching.max_matches_per_run} per scan). Shown with the filter category *Not matched yet*.")

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
    st.altair_chart(points + rule.encode(x="v:Q") + rule.encode(y="v:Q"), width="stretch")
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


# --- diagnostics & criteria --------------------------------------------------------
def _last_run_problems() -> None:
    import json

    runs = common.store().last_runs(1)
    if not runs:
        return
    errors = json.loads(runs[0].get("errors") or "[]")
    if not errors:
        return
    match_errs = [e for e in errors if e.startswith("match ")]
    source_errs = [e for e in errors if not e.startswith(("match ", "person "))]
    when = runs[0]["started_at"][:16].replace("T", " ")
    if match_errs and runs[0].get("matched", 0) == 0:
        st.error(f"**The last scan ({when} UTC) could not assess any posting** – every LLM call failed. "
                 f"First error:\n\n`{match_errs[0][:400]}`\n\nCheck the model and API key on *Scan & sync*.")
    with st.expander(f"⚠️ {len(errors)} problem(s) in the last scan ({when} UTC)"):
        if source_errs:
            st.markdown("**Sources that could not be read** – the site may block automated access or need a login. "
                        "Pause them on the *Sources* page or replace them with another link.")
            st.markdown("\n".join(f"- `{e[:300]}`" for e in source_errs))
        others = [e for e in errors if e not in source_errs]
        if others:
            st.markdown("**Other errors**")
            st.markdown("\n".join(f"- `{e[:300]}`" for e in others[:10]) + (f"\n- … and {len(others) - 10} more" if len(others) > 10 else ""))


def _criteria_editor(p, s) -> None:
    from jobscout.config import save_settings

    with st.expander("⚖️ How matches are scored – and how to change it"):
        st.markdown(
            f"""
Every posting gets **two independent scores (0–100)** from the LLM, which reads the full posting and your whole
profile (facts, interests, interview form, CV and other documents):

| Score | Question | Based on |
|---|---|---|
| **Profile fit** | *Do I meet the requirements?* | degree, skills, methods, experience, languages, formal eligibility |
| **Interest fit** | *Do I want this?* | your topics, role types, locations, values – **dealbreakers push it close to 0** |

Scale: 85+ excellent · 65–84 good · 40–64 partial · below 40 weak.

The two scores give the **category** (threshold currently **{s.matching.category_threshold}**):
🟢 **Top match** both ≥ threshold · 🟣 **Stretch / dream job** only interest ≥ threshold ·
🟠 **Solid option** only profile ≥ threshold · ⚪ **No match** both below.

**Levers you have:**
1. **Interests** (Profile page) – topics, role types, locations and dealbreakers change the interest fit most.
2. **Profile & documents** – the profile fit can only use what's written down. A detailed CV helps a lot.
3. **Your own criteria** (below) – rules in plain words that the LLM applies to every posting.
4. **Threshold** (below) – lower it to see more postings as top/stretch/solid. Applies instantly, no new assessment needed.

Changes to the profile or to your criteria trigger a fresh assessment of all stored postings on the next scan.
"""
        )
        with st.form("criteria"):
            criteria = st.text_area(
                "Your own criteria (one per line)", s.matching.custom_criteria, height=140,
                placeholder="Positions in the Netherlands, Switzerland or Scandinavia: +10 interest fit.\n"
                            "MSCA doctoral networks hosted in Germany: no match (mobility rule).\n"
                            "Missing a specific software tool is only a small gap – I learn tools quickly.\n"
                            "Rate pure wet-lab synthesis roles low on interest.",
            )
            threshold = st.slider("Threshold for a 'good' fit", 40, 90, s.matching.category_threshold, 5)
            if st.form_submit_button("Save", type="primary"):
                changed = criteria.strip() != s.matching.custom_criteria.strip()
                s.matching.custom_criteria = criteria.strip()
                s.matching.category_threshold = threshold
                save_settings(p, s)
                st.success("Saved." + (" All postings are re-assessed with your criteria on the next scan." if changed else ""))
                st.rerun()
