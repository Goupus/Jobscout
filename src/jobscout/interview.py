"""LLM-led interview that deepens the profile.

The model reads the current profile, asks one question at a time about the
things a CV does not show (motivation, preferred ways of working, what you are
proud of, what you want to avoid), and finally writes a structured summary to
``profile/interview_<date>.md`` – which the matcher then reads like any other
profile document.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable

from .llm import ChatBackend
from .profile import Profile

SYSTEM = """You are a warm but sharp career coach interviewing a job seeker so that
an automated matcher understands them better. You have their current profile below.
Ask ONE short question per turn. Focus on what the profile does NOT already answer:
motivations, favourite and least favourite past tasks, achievements with concrete
results, preferred team/research environment, career direction for the next 3-5
years, constraints (location, start date, salary/funding, contract type) and
dealbreakers. Follow up when an answer is vague. Do not repeat questions.
After about {n} questions, or when the user writes "done", reply with exactly
the line FINISHED and nothing else.

Current profile:
{profile}"""

SUMMARY = """Summarize the interview below as a Markdown profile addendum with these
sections: Motivation & direction, Strengths with evidence, Preferred environment,
Interests (ranked), Constraints, Dealbreakers, Open questions. Use the applicant's
own words where possible and do not invent anything. Language: {language}."""


def run_interview(
    profile: Profile,
    llm: ChatBackend,
    out_dir: Path,
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
    n_questions: int = 12,
    language: str = "English",
) -> Path:
    system = SYSTEM.replace("{n}", str(n_questions)).replace("{profile}", profile.to_prompt(20000))
    messages = [{"role": "system", "content": system}, {"role": "user", "content": "Please start the interview."}]
    transcript: list[str] = []
    for _ in range(n_questions + 5):
        question = llm.chat(messages).strip()
        if question.upper().startswith("FINISHED"):
            break
        say(f"\n🤖 {question}")
        answer = ask("👤 ").strip()
        transcript += [f"**Q:** {question}", f"**A:** {answer}"]
        messages += [{"role": "assistant", "content": question}, {"role": "user", "content": answer}]
        if answer.lower() in {"done", "fertig", "stop"}:
            break

    summary = llm.chat([
        {"role": "system", "content": SUMMARY.replace("{language}", language)},
        {"role": "user", "content": "\n\n".join(transcript)},
    ])
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"interview_{date.today().isoformat()}.md"
    path.write_text(
        f"# Interview {date.today().isoformat()}\n\n{summary.strip()}\n\n---\n\n## Transcript\n\n"
        + "\n\n".join(transcript) + "\n",
        encoding="utf-8",
    )
    return path
