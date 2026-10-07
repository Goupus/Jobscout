"""Interview with the configured LLM that ends in a filled interview form.

The same prompt is used for the copy-paste flow with an external chatbot
(see ``interview_form.build_prompt``), the in-app chat and the CLI.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable

from .interview_form import FINISH_MESSAGE, InterviewForm, build_prompt, parse_form, save_form
from .llm import ChatBackend
from .profile import Profile


class InterviewSession:
    """Stateful chat; serialisable via ``messages`` (e.g. in Streamlit session state)."""

    def __init__(self, profile: Profile | None, language: str = "en", messages: list[dict] | None = None):
        self.language = language
        self.messages: list[dict[str, str]] = messages or [
            {"role": "system", "content": build_prompt(language, profile)},
            {"role": "user", "content": "Start." if language != "de" else "Los geht's."},
        ]

    @property
    def transcript(self) -> list[dict[str, str]]:
        """Visible chat (without the system prompt and the kick-off message)."""
        return self.messages[2:]

    def next_question(self, llm: ChatBackend) -> str:
        reply = llm.chat(self.messages).strip()
        self.messages.append({"role": "assistant", "content": reply})
        return reply

    def answer(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def finish(self, llm: ChatBackend) -> InterviewForm:
        self.answer(FINISH_MESSAGE.get(self.language, FINISH_MESSAGE["en"]))
        reply = self.next_question(llm)
        return parse_form(reply)

    def transcript_markdown(self) -> str:
        lines = [f"# Interview transcript {date.today().isoformat()}", ""]
        for m in self.transcript:
            who = "**Q:**" if m["role"] == "assistant" else "**A:**"
            lines += [f"{who} {m['content']}", ""]
        return "\n".join(lines)


def save_transcript(session: InterviewSession, profile_dir: Path) -> Path:
    # leading underscore → kept for reference, ignored by the matcher (the form is the summary)
    path = profile_dir / f"_interview_transcript_{date.today().isoformat()}.md"
    path.write_text(session.transcript_markdown(), encoding="utf-8")
    return path


def run_interview(
    profile: Profile,
    llm: ChatBackend,
    out_dir: Path,
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
    max_questions: int = 25,
    language: str = "en",
) -> Path:
    """CLI flow. Type 'done' / 'fertig' to finish early."""
    session = InterviewSession(profile, language)
    for _ in range(max_questions):
        question = session.next_question(llm)
        if "```" in question:  # the model already produced the form
            break
        say(f"\n🤖 {question}")
        answer = ask("👤 ").strip()
        if answer.lower() in {"done", "fertig", "stop"}:
            break
        session.answer(answer)
    last = session.messages[-1]["content"]
    form = parse_form(last) if "```" in last else session.finish(llm)
    out_dir.mkdir(parents=True, exist_ok=True)
    save_transcript(session, out_dir)
    return save_form(form, out_dir)
