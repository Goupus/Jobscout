"""Profile page: documents upload, interests form, facts editor."""

from __future__ import annotations

import yaml
import streamlit as st

from jobscout.app import common
from jobscout.interview_form import FORM_FILENAME
from jobscout.profile import Interests

INTEREST_FIELDS = {
    "topics": ("Topics", "What do you want to work on? Most important first, one per line."),
    "role_types": ("Role types", "e.g. PhD position, research engineer, data scientist"),
    "preferred_locations": ("Preferred locations", "Countries, cities or 'remote'"),
    "values": ("What matters to you", "e.g. interdisciplinary work, impact, flexibility"),
    "dealbreakers": ("Dealbreakers", "Postings with these get a very low interest score"),
}


def render() -> None:
    st.title("👤 Profile")
    if not common.ensure_data_dir():
        return
    p = common.paths()
    p.profile_dir.mkdir(parents=True, exist_ok=True)
    st.caption("Everything here is used for matching. After a change, stored postings are re-assessed on the next scan.")

    tab_docs, tab_interests, tab_facts = st.tabs(["📄 Documents", "🎯 Interests", "🧾 Facts"])
    with tab_docs:
        _documents(p)
    with tab_interests:
        _interests(p)
    with tab_facts:
        _facts(p)


def _documents(p) -> None:
    st.markdown(
        "Upload your **CV** and anything else that describes you: personality test results, references, "
        "project descriptions, publications. Supported: PDF, Markdown, text, YAML. "
        "*Word files: please save as PDF first.*"
    )
    files = st.file_uploader("Add documents", type=["pdf", "md", "txt", "yaml", "yml"],
                             accept_multiple_files=True, key=f"upl_{st.session_state.get('upl_n', 0)}")
    if files:
        for f in files:
            name = f.name.replace(" ", "_")
            if name in common.RESERVED_PROFILE_FILES:
                name = f"uploaded_{name}"
            (p.profile_dir / name).write_bytes(f.getbuffer())
        st.session_state.upl_n = st.session_state.get("upl_n", 0) + 1  # reset the uploader
        st.toast(f"Saved {len(files)} file(s).")
        st.rerun()

    docs = common.user_documents(p.profile_dir)
    form = p.profile_dir / FORM_FILENAME
    if form.exists():
        docs = [form, *docs]
    if not docs:
        st.info("No documents yet.")
    for d in docs:
        with st.container(border=True):
            c = st.columns([0.7, 0.15, 0.15], vertical_alignment="center")
            label = "💬 Interview form" if d.name == FORM_FILENAME else f"📄 {d.name}"
            c[0].markdown(f"**{label}** · {d.stat().st_size / 1024:.0f} KB")
            if d.suffix.lower() != ".pdf":
                with c[1].popover("View"):
                    st.code(d.read_text(encoding="utf-8")[:20000], language="yaml" if d.suffix in {".yaml", ".yml"} else "markdown")
            if c[2].button("Delete", key=f"del_{d.name}"):
                d.unlink()
                st.rerun()


def _lines(text: str) -> list[str]:
    return [x.strip(" -•\t") for x in text.splitlines() if x.strip(" -•\t")]


def _interests(p) -> None:
    path = p.profile_dir / "interests.yaml"
    current = Interests.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else Interests()
    st.markdown("What you **want** – this drives the *interest fit*. One entry per line.")
    with st.form("interests"):
        values = {}
        cols = st.columns(2)
        for i, (field, (label, help_)) in enumerate(INTEREST_FIELDS.items()):
            values[field] = cols[i % 2].text_area(label, "\n".join(getattr(current, field)), help=help_, height=150)
        notes = st.text_area("Notes", current.notes, help="Anything else, e.g. strategies or special wishes")
        if st.form_submit_button("Save interests", type="primary"):
            new = Interests(**{k: _lines(v) for k, v in values.items()}, notes=notes.strip())
            path.write_text(yaml.safe_dump(new.model_dump(), allow_unicode=True, sort_keys=False, width=100), encoding="utf-8")
            st.success("Saved.")


def _facts(p) -> None:
    path = p.profile_dir / "profile.yaml"
    st.markdown(
        "Structured facts: education, experience, skills, languages, constraints. Free-form YAML – "
        "keep the indentation (spaces, no tabs). If you uploaded a detailed CV, this can stay short."
    )
    text = st.text_area("profile.yaml", path.read_text(encoding="utf-8") if path.exists() else "",
                        height=420, label_visibility="collapsed")
    if st.button("Save facts", type="primary"):
        try:
            yaml.safe_load(text)
        except yaml.YAMLError as exc:
            st.error(f"That is not valid YAML – nothing saved.\n\n{exc}")
        else:
            path.write_text(text, encoding="utf-8")
            st.success("Saved.")
