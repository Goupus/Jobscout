"""People page: follow researchers / contacts and get outreach advice."""

from __future__ import annotations

import streamlit as st

from jobscout.app import common
from jobscout.people import (
    STATUS_LABELS, STATUSES, AuthorCandidate, OpenAlexClient, Person, analyze_people, default_get_json,
    load_people, person_docs_dir, save_people,
)


def _oa() -> OpenAlexClient:
    return OpenAlexClient(default_get_json(common.settings().model_copy(update={"http_timeout": 15.0})))


def render() -> None:
    st.title("👥 People")
    if not common.ensure_data_dir():
        return
    p = common.paths()
    st.markdown(
        "Follow researchers and other contacts – e.g. professors you'd like to work with. jobscout reads their "
        "**recent publications** (OpenAlex), their **group or personal page** and documents you add, and tells you "
        "**where your profile overlaps**, **how you could contribute** and **how to reach out** – incl. a draft message. "
        "People are re-analyzed with every scan when something changed, otherwise monthly."
    )
    st.caption("LinkedIn pages can't be read automatically (login required, automated access isn't allowed). "
               "Keep the link for yourself, and if you like, save the profile as PDF on LinkedIn "
               "(*More → Save to PDF*) and upload it to the person below.")

    people = load_people(p)
    _add_person(p, people)
    _pending_candidates(p)

    st.subheader(f"Your people ({len(people)})")
    if not people:
        st.info("Nobody added yet.")
        return
    insights = common.store().all_person_insights()
    order = sorted(people, key=lambda x: -(insights.get(x.slug, {}).get("insight", {}).get("overlap_score", -1)))
    for person in order:
        _person_card(p, people, person, insights.get(person.slug))


# --- add ---------------------------------------------------------------------------
def _add_person(p, people: list[Person]) -> None:
    with st.expander("➕ Add a person", expanded=not people):
        with st.form("add_person", clear_on_submit=True):
            c = st.columns(2)
            name = c[0].text_input("Name *", key="pp_name", placeholder="Artur Schweidtmann")
            affiliation = c[1].text_input("Affiliation", key="pp_aff", placeholder="TU Delft – helps find the right person")
            linkedin = c[0].text_input("LinkedIn URL", key="pp_li", placeholder="https://www.linkedin.com/in/…")
            homepage = c[1].text_input("Group / personal page", key="pp_home", placeholder="https://…")
            orcid = c[0].text_input("ORCID (optional)", key="pp_orcid", placeholder="0000-0000-0000-0000")
            notes = c[1].text_input("Why this person? (optional)", key="pp_notes", placeholder="e.g. met at ESCAPE, works on GNN flowsheets")
            ok = st.form_submit_button("Add person", type="primary")
        if not ok:
            return
        if not name.strip():
            st.error("Please enter a name.")
            return
        person = Person(name=name.strip(), affiliation=affiliation.strip() or None, linkedin=linkedin.strip() or None,
                        homepage=homepage.strip() or None, orcid=orcid.strip() or None, notes=notes.strip())
        if any(x.slug == person.slug for x in people):
            st.warning(f"{person.name} is already in your list.")
            return
        save_people(p, [*people, person])
        if not person.orcid:  # look the person up so the right OpenAlex profile can be confirmed
            try:
                cands = _oa().search_authors(person.name)
            except Exception as exc:  # noqa: BLE001
                st.session_state.oa_error = str(exc)
                cands = []
            if cands:
                st.session_state.pending_match = (person.slug, [c.model_dump() for c in cands])
        st.toast(f"Added {person.name}")
        st.rerun()


def _pending_candidates(p) -> None:
    if err := st.session_state.pop("oa_error", None):
        st.warning(f"OpenAlex could not be reached ({err}). You can search again later from the person's card.")
    pending = st.session_state.get("pending_match")
    if not pending:
        return
    slug, raw = pending
    cands = [AuthorCandidate.model_validate(c) for c in raw]
    people = load_people(p)
    person = next((x for x in people if x.slug == slug), None)
    if person is None:
        st.session_state.pop("pending_match", None)
        return
    with st.container(border=True):
        st.markdown(f"**Which of these is {person.name}?** (publications are read from the profile you pick)")
        labels = [f"{c.name} · {c.institution or 'unknown institution'} · {c.works_count} works · "
                  f"{', '.join(c.topics[:3])}" for c in cands] + ["None of these"]
        choice = st.radio("OpenAlex profiles", labels, key=f"pick_{slug}", label_visibility="collapsed")
        if st.button("Confirm", type="primary", key=f"confirm_{slug}"):
            idx = labels.index(choice)
            for x in people:
                if x.slug == slug:
                    x.openalex_id = cands[idx].id if idx < len(cands) else None
            save_people(p, people)
            st.session_state.pop("pending_match", None)
            st.rerun()


# --- card --------------------------------------------------------------------------
def _person_card(p, people: list[Person], person: Person, stored: dict | None) -> None:
    ins = (stored or {}).get("insight") or {}
    score = ins.get("overlap_score")
    head = (f"{STATUS_LABELS[person.status].split()[0]} **{person.name}**"
            + (f" — {person.affiliation}" if person.affiliation else "")
            + (f" · overlap {score}" if score is not None else " · not analyzed yet")
            + ("" if person.enabled else " · paused"))
    with st.expander(head):
        links = [f"[LinkedIn ↗]({person.linkedin})" if person.linkedin else None,
                 f"[Group page ↗]({person.homepage})" if person.homepage else None,
                 f"[OpenAlex ↗](https://openalex.org/{person.openalex_id})" if person.openalex_id else None,
                 f"[ORCID ↗](https://orcid.org/{person.orcid})" if person.orcid else None]
        st.markdown(" · ".join(x for x in links if x) or "No links yet.")

        top = st.columns([0.35, 0.65])
        status = top[0].selectbox("Contact status", STATUSES, index=STATUSES.index(person.status),
                                  format_func=STATUS_LABELS.get, key=f"st_{person.slug}")
        notes = top[1].text_area("Your notes (also used in the analysis)", person.notes, height=68, key=f"no_{person.slug}")
        if status != person.status or notes != person.notes:
            _update(p, person.slug, status=status, notes=notes)
            st.rerun()

        if ins:
            _insight(ins, stored)
        else:
            st.info("Analyzed with the next scan – or click *Analyze now*.")

        st.markdown("---")
        a, b, c, d = st.columns(4)
        if a.button("🔄 Analyze now", key=f"an_{person.slug}"):
            _analyze_now(p, person)
        if b.button("🔎 Find on OpenAlex", key=f"oa_{person.slug}"):
            try:
                st.session_state.pending_match = (person.slug, [x.model_dump() for x in _oa().search_authors(person.name)])
            except Exception as exc:  # noqa: BLE001
                st.session_state.oa_error = str(exc)
            st.rerun()
        if c.button("⏸️ Resume" if not person.enabled else "⏸️ Pause", key=f"pa_{person.slug}"):
            _update(p, person.slug, enabled=not person.enabled)
            st.rerun()
        if d.button("🗑️ Remove", key=f"rm_{person.slug}"):
            save_people(p, [x for x in load_people(p) if x.slug != person.slug])
            st.rerun()

        _documents(p, person)


def _insight(ins: dict, stored: dict) -> None:
    st.caption(f"Analyzed {stored.get('analyzed_at', '')[:10]}")
    if ins.get("research_summary"):
        st.write(ins["research_summary"])
    if ins.get("current_focus"):
        st.markdown("**Current focus:** " + " · ".join(ins["current_focus"]))
    l, r = st.columns(2)
    _bullets(l, "🔗 Where your profile overlaps", ins.get("overlaps"))
    _bullets(r, "🤝 How you could contribute", ins.get("contribution_ideas"))
    st.markdown("**✉️ How to reach out**")
    if ins.get("outreach_approach"):
        st.write(ins["outreach_approach"])
    l, r = st.columns(2)
    _bullets(l, "Hooks to mention", ins.get("conversation_hooks"))
    _bullets(r, "Questions to ask", ins.get("questions_to_ask"))
    if ins.get("draft_message"):
        st.markdown("**Draft message** – copy with the icon in the corner, then make it your own:")
        st.code(ins["draft_message"], language=None, wrap_lines=True)
    if ins.get("key_papers"):
        st.markdown("**📄 Papers to read first**")
        for paper in ins["key_papers"]:
            title = f"[{paper['title']}]({paper['url']})" if paper.get("url") else paper["title"]
            st.markdown(f"- {title} ({paper.get('year') or '–'}) – {paper.get('why', '')}")
    if ins.get("open_positions"):
        st.success("📢 Open positions mentioned: " + " · ".join(ins["open_positions"]))
    if ins.get("caveats"):
        st.caption("⚠️ " + " · ".join(ins["caveats"]))


def _bullets(col, title: str, items) -> None:
    if items:
        col.markdown(f"**{title}**")
        col.markdown("\n".join(f"- {x}" for x in items))


def _update(p, slug: str, **changes) -> None:
    people = load_people(p)
    for x in people:
        if x.slug == slug:
            for k, v in changes.items():
                setattr(x, k, v)
    save_people(p, people)


def _documents(p, person: Person) -> None:
    folder = person_docs_dir(p, person)
    files = sorted(f for f in folder.iterdir() if f.is_file()) if folder.exists() else []
    st.markdown("**Documents about this person** (LinkedIn PDF, CV, talk abstract, …)")
    for f in files:
        c = st.columns([0.85, 0.15])
        c[0].markdown(f"📄 {f.name}")
        if c[1].button("Delete", key=f"dd_{person.slug}_{f.name}"):
            f.unlink()
            st.rerun()
    up = st.file_uploader("Add documents", type=["pdf", "md", "txt"], accept_multiple_files=True,
                          key=f"up_{person.slug}_{st.session_state.get('pupl', 0)}", label_visibility="collapsed")
    if up:
        folder.mkdir(parents=True, exist_ok=True)
        for f in up:
            (folder / f.name.replace(" ", "_")).write_bytes(f.getbuffer())
        st.session_state.pupl = st.session_state.get("pupl", 0) + 1
        st.rerun()


def _analyze_now(p, person: Person) -> None:
    s = common.settings()
    ok, msg = common.llm_ready(s)
    if not ok:
        st.warning(f"{msg} Set the key on the Scan & sync page.")
        return
    from jobscout.llm import LiteLLMBackend
    from jobscout.sources import make_fetcher

    with st.spinner(f"Reading {person.name}'s work and analyzing…"):
        rep = analyze_people(p, s, common.profile(), LiteLLMBackend(s.llm), common.store(), _oa(), make_fetcher(s),
                             only=person.slug, force=True)
    if rep.errors:
        st.error(" · ".join(rep.errors))
    else:
        st.rerun()
