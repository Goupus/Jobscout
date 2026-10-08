"""Interview page: copy-paste flow for any chatbot, or chat right here."""

from __future__ import annotations

import streamlit as st
import yaml

from jobscout.app import common
from jobscout.interview import InterviewSession, save_transcript
from jobscout.interview_form import FORM_FILENAME, FormError, InterviewForm, build_prompt, merge_interests, parse_form, save_form
from jobscout.llm import LiteLLMBackend, LLMError

SECTION_LABELS = {
    "summary": "Summary", "motivation": "Motivation", "strengths": "Strengths", "achievements": "Achievements",
    "tasks": "Favourite tasks", "working_style": "Working style", "personality": "Personality",
    "interests": "Interests", "constraints": "Constraints",
}


def render() -> None:
    st.title("💬 Interview")
    if not common.ensure_data_dir():
        return
    p, s = common.paths(), common.settings()
    st.markdown(
        "A chatbot interviews you about what your CV doesn't show – motivation, strengths with evidence, "
        "preferred environment, dealbreakers – and finally fills in a **fixed form**. "
        "The form is stored in your profile and used for every match."
    )
    form_path = p.profile_dir / FORM_FILENAME
    if form_path.exists():
        st.success(f"You already have an interview form. Doing the interview again replaces it (the old one is kept as `_previous_{FORM_FILENAME}`).")

    mode = st.radio("How do you want to do the interview?",
                    ["Any chatbot (copy & paste)", "Chat here"], horizontal=True,
                    captions=["ChatGPT, Claude, Gemini, … – no API key needed", f"Uses {s.llm.model}"])
    if mode.startswith("Any"):
        _external(p, s)
    else:
        _chat(p, s)


# --- copy & paste ------------------------------------------------------------
def _external(p, s) -> None:
    st.subheader("1 · Copy the prompt")
    c = st.columns(2)
    lang = c[0].selectbox("Interview language", ["de", "en"], index=0 if s.matching.language == "de" else 1,
                          format_func={"de": "Deutsch", "en": "English"}.get)
    with_profile = c[1].toggle("Include my current profile", value=True,
                               help="So the chatbot doesn't ask what your CV already answers. Turn off if you attach your CV in the chat instead.")
    prompt = build_prompt(lang, common.profile() if with_profile else None)
    st.code(prompt, language="markdown", height=260, wrap_lines=True)
    st.download_button("Download prompt as .md", prompt, file_name="jobscout_interview_prompt.md")
    st.caption("Use the copy icon in the top-right corner of the box. Paste it into a new chat and answer the questions. "
               "Write **fertig** / **done** when you want to finish.")

    st.subheader("2 · Paste the chatbot's final answer")
    answer = st.text_area("The filled form (the code block the chatbot returns at the end)", height=220,
                          placeholder="```yaml\nform_version: 1\n...\n```")
    if answer.strip():
        try:
            form = parse_form(answer)
        except FormError as exc:
            st.error(f"{exc}\n\nTip: copy the whole code block, or ask the chatbot to “output the completed form as a yaml code block”.")
            return
        _review_and_save(p, form, key="ext")


# --- in-app chat -------------------------------------------------------------
def _chat(p, s) -> None:
    ok, msg = common.llm_ready(s)
    if not ok:
        st.warning(f"{msg} Enter a key on the **Scan & sync** page, or use the copy & paste option.")
        st.page_link(common.page("run"), label="Go to Scan & sync", icon="🔄")
        return
    lang = s.matching.language if s.matching.language in {"de", "en"} else "en"
    llm = LiteLLMBackend(s.llm)

    if "iv_messages" not in st.session_state:
        if st.button("Start the interview", type="primary"):
            session = InterviewSession(common.profile(), lang)
            with st.spinner("Preparing the first question…"):
                try:
                    session.next_question(llm)
                except LLMError as exc:
                    st.error(str(exc))
                    return
            st.session_state.iv_messages = session.messages
            st.rerun()
        return

    session = InterviewSession(None, lang, messages=st.session_state.iv_messages)
    for m in session.transcript:
        with st.chat_message("assistant" if m["role"] == "assistant" else "user"):
            st.markdown(m["content"])

    if "iv_form" in st.session_state:
        _review_and_save(p, InterviewForm.model_validate(st.session_state.iv_form), key="chat", session=session)
        return

    c = st.columns([0.5, 0.25, 0.25])
    c[0].caption(f"{sum(m['role'] == 'user' for m in session.transcript)} answers so far")
    if c[1].button("✅ Finish & create form", width="stretch"):
        with st.spinner("Filling in the form…"):
            try:
                form = session.finish(llm)
            except (LLMError, FormError) as exc:
                st.error(f"Could not create the form: {exc}")
                return
        st.session_state.iv_messages = session.messages
        st.session_state.iv_form = form.model_dump()
        st.rerun()
    if c[2].button("↺ Start over", width="stretch"):
        _reset()
        st.rerun()

    if answer := st.chat_input("Your answer"):
        session.answer(answer)
        with st.spinner("…"):
            try:
                session.next_question(llm)
            except LLMError as exc:
                st.error(str(exc))
        st.session_state.iv_messages = session.messages
        st.rerun()


def _reset() -> None:
    for k in ("iv_messages", "iv_form"):
        st.session_state.pop(k, None)


# --- shared review step --------------------------------------------------------
def _review_and_save(p, form: InterviewForm, key: str, session: InterviewSession | None = None) -> None:
    st.subheader("3 · Check and save" if key == "ext" else "Check and save")
    filled = form.filled_sections()
    st.markdown(" · ".join(f"{'✅' if ok else '⚠️'} {SECTION_LABELS[k]}" for k, ok in filled.items()))
    if not all(filled.values()):
        st.caption("⚠️ = empty. That's fine if you didn't talk about it – you can also edit the form later on the Profile page.")
    if form.summary:
        st.info(form.summary)
    with st.expander("Show the full form"):
        st.code(yaml.safe_dump(form.model_dump(), allow_unicode=True, sort_keys=False), language="yaml")
    merge = st.checkbox("Also add the interests from the form to my interests list", value=True, key=f"merge_{key}")
    if st.button("💾 Save to my profile", type="primary", key=f"save_{key}"):
        save_form(form, p.profile_dir)
        if merge:
            merge_interests(form, p.profile_dir)
        if session is not None:
            save_transcript(session, p.profile_dir)
            _reset()
        st.success("Saved! Don't forget **Save to GitHub** in the sidebar so the scheduled scan uses it.")
        st.balloons()
