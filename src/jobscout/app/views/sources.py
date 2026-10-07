"""Sources page: table editor, default focus, test a source, raw YAML."""

from __future__ import annotations

import pandas as pd
import streamlit as st
import yaml
from pydantic import ValidationError

from jobscout.app import common
from jobscout.config import SourceConfig, load_source_defaults, load_sources, save_sources

TYPE_HELP = {
    "llm_page": "Any web page – the LLM finds the open positions (research groups, career pages). Easiest choice.",
    "rss": "A job feed (RSS/Atom). Cheapest and most robust if the site offers one.",
    "html": "A list page read with CSS selectors – no LLM cost, needs the selectors (edit in YAML).",
}
CORE = ["enabled", "name", "type", "url", "organization", "fetch_details"]


def render() -> None:
    st.title("🔎 Sources")
    if not common.ensure_data_dir():
        return
    p = common.paths()
    try:
        sources = load_sources(p)
        raw_defaults = load_source_defaults(p)
    except (ValidationError, yaml.YAMLError) as exc:
        st.error(f"`sources.yaml` has an error – fix it below.\n\n{exc}")
        _raw_editor(p)
        return

    st.markdown(
        "Where jobscout looks for postings. Add a row for every job board, career page or research-group page. "
        "For most pages, type **llm_page** is right."
    )
    with st.expander("Which type should I use?"):
        for t, h in TYPE_HELP.items():
            st.markdown(f"- **{t}** – {h}")
        st.markdown("Tip: for job boards, run a search in your browser and paste the **URL of the results page**.")

    # raw source dicts (without defaults) so advanced keys survive table edits
    file_items = (yaml.safe_load(p.sources.read_text(encoding="utf-8")) or {}).get("sources", []) if p.sources.exists() else []
    df = pd.DataFrame([{k: getattr(s, k) for k in CORE} for s in sources], columns=CORE)
    df["organization"] = df["organization"].fillna("")
    edited = st.data_editor(
        df, num_rows="dynamic", use_container_width=True, hide_index=True, key="src_editor",
        column_config={
            "enabled": st.column_config.CheckboxColumn("On", width="small", default=True),
            "name": st.column_config.TextColumn("Name", required=True),
            "type": st.column_config.SelectboxColumn("Type", options=list(TYPE_HELP), required=True, default="llm_page"),
            "url": st.column_config.LinkColumn("URL", required=True, validate=r"^https?://", width="large"),
            "organization": st.column_config.TextColumn("Organization (optional)"),
            "fetch_details": st.column_config.CheckboxColumn("Open each posting", help="llm_page: read every posting's page for the full text (better matches, slower)", default=False),
        },
    )

    st.markdown("**Focus for the LLM** – applied to every `llm_page` source that doesn't set its own. "
                "Postings clearly outside this focus are skipped before matching, which saves cost.")
    focus = st.text_area("Focus", raw_defaults.get("focus", ""), height=120, label_visibility="collapsed",
                         placeholder="e.g. PhD positions combining chemical engineering with machine learning …")

    if st.button("💾 Save sources", type="primary"):
        new_sources, errors = [], []
        for idx, row in edited.iterrows():
            if not str(row.get("name") or "").strip() and not str(row.get("url") or "").strip():
                continue
            i = int(idx) if str(idx).isdigit() else -1
            base = dict(file_items[i]) if 0 <= i < len(file_items) and i < len(df) else {}
            base.update({k: (None if pd.isna(row[k]) or row[k] == "" else row[k]) for k in CORE})
            base["enabled"] = bool(base.get("enabled", True))
            base["fetch_details"] = bool(base.get("fetch_details", False))
            try:
                new_sources.append(SourceConfig.model_validate({k: v for k, v in base.items() if v is not None}))
            except ValidationError as exc:
                errors.append(f"Row '{row.get('name')}': {exc.errors()[0]['msg']}")
        if errors:
            st.error("Nothing saved:\n\n" + "\n".join(f"- {e}" for e in errors))
        else:
            defaults = {**raw_defaults, "focus": focus.strip()} if focus.strip() else {k: v for k, v in raw_defaults.items() if k != "focus"}
            save_sources(p, new_sources, defaults)
            st.success(f"Saved {len(new_sources)} source(s).")
            st.rerun()

    st.divider()
    _tester(sources)
    st.divider()
    with st.expander("Advanced: edit sources.yaml directly (keywords, extra pages, CSS selectors)"):
        _raw_editor(p)


def _tester(sources: list[SourceConfig]) -> None:
    st.subheader("Test a source")
    st.caption("Fetches the page now and shows what jobscout finds – nothing is stored and nothing is matched.")
    if not sources:
        st.info("Add a source first.")
        return
    s = common.settings()
    names = [x.name for x in sources]
    choice = st.selectbox("Source", names)
    cfg = sources[names.index(choice)]
    needs_llm = cfg.type == "llm_page"
    ok, msg = common.llm_ready(s)
    if needs_llm and not ok:
        st.warning(f"{msg} This source type needs the LLM – set a key on the Scan & sync page.")
        return
    if st.button("Run test"):
        from jobscout.llm import LiteLLMBackend
        from jobscout.sources import preview_source

        with st.spinner(f"Reading {cfg.url} …"):
            try:
                jobs = preview_source(cfg, s, LiteLLMBackend(s.llm) if needs_llm else None)
            except Exception as exc:  # noqa: BLE001 - show any fetch/parse error to the user
                st.error(f"The source failed: {exc}")
                return
        if not jobs:
            st.warning("No postings found. Either there are none right now, the focus/keywords filter everything out, "
                       "or the page needs JavaScript (then try the site's search results page or an RSS feed).")
        else:
            st.success(f"Found {len(jobs)} posting(s):")
            st.dataframe(pd.DataFrame([{"title": j.title, "organization": j.organization, "url": j.url} for j in jobs]),
                         use_container_width=True, hide_index=True,
                         column_config={"url": st.column_config.LinkColumn("url")})


def _raw_editor(p) -> None:
    text = st.text_area("sources.yaml", p.sources.read_text(encoding="utf-8") if p.sources.exists() else "sources: []\n",
                        height=400, label_visibility="collapsed", key="raw_sources")
    if st.button("Save YAML"):
        try:
            data = yaml.safe_load(text) or {}
            defaults = data.get("defaults") or {}
            [SourceConfig.model_validate({**defaults, **x}) for x in data.get("sources", [])]
        except (yaml.YAMLError, ValidationError, AttributeError, TypeError) as exc:
            st.error(f"Not saved – the file has an error:\n\n{exc}")
        else:
            p.sources.write_text(text, encoding="utf-8")
            st.success("Saved.")
            st.rerun()
