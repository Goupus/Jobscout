"""Fictional demo data for trying the dashboard."""

from __future__ import annotations

from .config import Paths
from .models import JobPosting, MatchResult, categorize
from .storage import Store

DEMO = [
    ("PhD candidate: machine learning for membrane materials", "Example Tech University", "Delft, NL", 88, 94,
     "Doctoral project on ML-guided discovery of separation membranes.",
     ["ML for pervaporation (thesis) -> core of the project", "Python/ML stack -> required"],
     ["Little experience with molecular simulation"],
     ["Thesis results on predicting membrane performance", "Industrial process experience as a bridge to application"],
     ["Put the thesis title and key metric in the first lines of the CV", "Rename 'Data analysis' to 'Machine learning for materials'"],
     ["Deadline in 10 days"]),
    ("Research engineer – AI for process design", "Example Chemicals AG", "Leverkusen, DE", 82, 71,
     "Industrial R&D role building surrogate models for process design.",
     ["Process simulation -> required", "Industry experience at a chemical company"],
     ["No experience with their optimisation tool"],
     ["Commissioning and CO2-reduction projects as evidence for practical impact"],
     ["Lead with process-design projects rather than research"], []),
    ("Postdoc: technology assessment & society", "Example Institute", "Zurich, CH", 41, 86,
     "Interdisciplinary postdoc on societal impact of AI in industry.",
     ["Interdisciplinary background"], ["Requires a PhD", "Social-science methods expected"],
     ["Interest in technology & society, any related coursework"],
     ["Ask about pre-doc/research-assistant options instead"], ["PhD required – formally not eligible"]),
    ("Sales engineer filtration systems", "Example Filters GmbH", "Hamburg, DE", 74, 22,
     "Technical sales of membrane filtration systems.",
     ["Membrane knowledge"], [], ["Membrane know-how"], [], ["Sales role – listed as dealbreaker"]),
]


def seed(paths: Paths) -> int:
    store = Store(paths.db)
    try:
        for i, (title, org, loc, pf, inf, summary, strengths, gaps, emph, tailor, flags) in enumerate(DEMO):
            job = JobPosting(source="demo", title=title, url=f"https://example.org/demo/{i}",
                             organization=org, location=loc, description=summary)
            store.upsert_job(job)
            store.save_match(
                MatchResult(job_uid=job.uid, profile_fit=pf, interest_fit=inf, category=categorize(pf, inf),
                            summary=summary, matching_strengths=strengths, gaps=gaps,
                            emphasize_in_application=emph, profile_tailoring=tailor, red_flags=flags,
                            model="demo"),
                "demo",
            )
        store.log_run("2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00", len(DEMO), len(DEMO), [])
    finally:
        store.close()
    return len(DEMO)
