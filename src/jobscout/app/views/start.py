"""Start page: what jobscout does + setup walkthrough."""

from __future__ import annotations

import streamlit as st

from jobscout.app import common

PAGE_FILES = {"people": "People", "profile": "Profile", "interview": "Interview", "sources": "Sources", "run": "Scan & sync", "matches": "Matches"}


def render() -> None:
    st.title("🧭 jobscout")
    st.markdown(
        "jobscout scans the job sources **you** choose twice a week and checks every posting against "
        "your profile: **Do I meet the requirements?** (profile fit) and **Do I want this?** (interest fit) "
        "– plus concrete tips on what to emphasize and how to tailor your CV."
    )
    if not common.ensure_data_dir():
        return

    steps = common.setup_steps()
    done = sum(s.done for s in steps)
    st.progress(done / len(steps), text=f"Setup: {done} of {len(steps)} steps done")

    if done == len(steps):
        st.success("All set! New matches appear on the **Matches** page after each scan.")
        st.page_link(common.page("matches"), label="Go to your matches", icon="📋")

    next_open = next((i for i, s in enumerate(steps) if not s.done), None)
    for i, step in enumerate(steps):
        icon = "✅" if step.done else ("👉" if i == next_open else "⬜")
        with st.container(border=True):
            cols = st.columns([0.07, 0.68, 0.25], vertical_alignment="center")
            cols[0].markdown(f"### {icon}")
            cols[1].markdown(f"**{i + 1}. {step.title}**  \n{step.detail}")
            cols[2].page_link(common.page(step.page), label=f"Open {PAGE_FILES[step.page]}", icon="➡️")

    with st.expander("How it works"):
        st.markdown(
            """
1. **Profile** – your CV and other documents, facts (`profile.yaml`) and interests (`interests.yaml`).
2. **Interview** – a chatbot asks you what your CV doesn't say and fills in a fixed form.
3. **Sources** – job boards, career pages and research-group websites. Pages without structure are read by the LLM.
4. **People** – researchers you'd like to work with: overlaps with your profile, ways to contribute, outreach drafts.
5. **Scan** – runs on GitHub on Monday and Thursday (or here on demand). New postings are scored by the LLM.
6. **Matches** – fit map, filters, tips per posting and your application tracker.

Your data lives in a separate **private** GitHub repository. Use *Save to GitHub* in the sidebar after changes.
"""
        )

