import json

from jobscout.config import load_settings
from jobscout.people import (
    OpenAlexClient, Person, abstract_from_index, analyze_people, gather_material, load_people,
    needs_refresh, person_docs_dir, pick_candidate, save_people, slugify,
)
from jobscout.pipeline import run
from jobscout.profile import load_profile
from jobscout.storage import Store

AUTHORS = {"results": [
    {"id": "https://openalex.org/A111", "display_name": "Ada Example", "works_count": 120, "cited_by_count": 3000,
     "orcid": "https://orcid.org/0000-0001-0000-0001",
     "last_known_institutions": [{"display_name": "Delft University of Technology"}],
     "topics": [{"display_name": "Process systems engineering"}]},
    {"id": "https://openalex.org/A222", "display_name": "Ada Example", "works_count": 8,
     "last_known_institutions": [{"display_name": "University of Elsewhere"}]},
]}
WORKS = {"results": [
    {"display_name": "Graph neural networks for flowsheet synthesis", "publication_year": 2025,
     "doi": "https://doi.org/10.1/x", "cited_by_count": 12,
     "abstract_inverted_index": {"We": [0], "learn": [1], "flowsheets": [2]},
     "primary_location": {"source": {"display_name": "Comput. Chem. Eng."}},
     "topics": [{"display_name": "Machine learning"}]},
]}


def fake_get(calls):
    def get(url, params):
        calls.append((url, params))
        if url.endswith("/authors"):
            return AUTHORS
        if "/authors/" in url:
            return AUTHORS["results"][0]
        if url.endswith("/works"):
            assert params["filter"].startswith("author.id:A111")
            return WORKS
        raise AssertionError(url)
    return get


class PersonLLM:
    def __init__(self):
        self.prompts = []

    def chat(self, messages, *, fast=False):
        self.prompts.append(messages)
        return json.dumps({
            "overlap_score": 82, "research_summary": "Works on ML for process design.",
            "current_focus": ["GNNs for flowsheets"], "overlaps": ["GNN flowsheets ↔ membrane ML thesis"],
            "contribution_ideas": ["Apply flowsheet models to membrane units"],
            "outreach_approach": "Short e-mail referencing the 2025 paper.",
            "conversation_hooks": ["2025 GNN paper"], "questions_to_ask": ["Data availability?"],
            "draft_message": "Subject: Membranes x flowsheets\n\nDear Prof. Example, ...",
            "key_papers": [{"title": "Graph neural networks for flowsheet synthesis", "year": 2025, "why": "core"}],
            "open_positions": [], "caveats": [],
        })


def test_helpers():
    assert slugify("Prof. Dr. Jürgen Müller") == "prof-dr-jurgen-muller"
    assert abstract_from_index({"world": [1], "hello": [0]}) == "hello world"
    assert abstract_from_index(None) == ""


def test_pick_candidate_by_affiliation():
    oa = OpenAlexClient(fake_get([]))
    cands = oa.search_authors("Ada Example")
    assert pick_candidate(Person(name="Ada Example", affiliation="TU Delft"), cands).id == "A111"
    assert pick_candidate(Person(name="Ada Example", affiliation="ETH Zürich"), cands) is None
    assert pick_candidate(Person(name="Ada Example", affiliation="Delft University"), cands).id == "A111"
    assert pick_candidate(Person(name="Ada Example"), cands) is None                       # ambiguous → user confirms
    assert pick_candidate(Person(name="Ada Example"), cands[:1]).id == "A111"


def test_people_roundtrip(data_dir):
    save_people(data_dir, [Person(name="Ada Example", linkedin="https://linkedin.com/in/ada", openalex_id="A111")])
    loaded = load_people(data_dir)
    assert loaded[0].linkedin.endswith("/ada") and loaded[0].status == "not_contacted"


def test_gather_material(data_dir):
    p = Person(name="Ada Example", openalex_id="A111", homepage="https://group.example/ada")
    docs = person_docs_dir(data_dir, p)
    docs.mkdir(parents=True)
    (docs / "linkedin.md").write_text("Head of the PSE group, hiring PhD students in 2027.")
    m = gather_material(p, data_dir, OpenAlexClient(fake_get([])), lambda url: "<html><body>Group of Ada: membranes</body></html>")
    assert m.author.name == "Ada Example" and m.works[0].abstract == "We learn flowsheets"
    assert "membranes" in m.homepage_text and "hiring PhD" in m.documents["linkedin.md"]
    text = m.to_prompt()
    assert "Graph neural networks" in text and "Comput. Chem. Eng." in text


def test_analyze_people_and_refresh(data_dir):
    save_people(data_dir, [Person(name="Ada Example", affiliation="Delft University"), Person(name="Off", enabled=False)])
    settings, profile = load_settings(data_dir), load_profile(data_dir.profile_dir)
    store, llm, calls = Store(data_dir.db), PersonLLM(), []
    oa = OpenAlexClient(fake_get(calls))

    rep = analyze_people(data_dir, settings, profile, llm, store, oa, fetch=None)
    assert rep.analyzed == 1 and not rep.errors
    stored = store.get_person_insight("ada-example")
    assert stored["insight"]["overlap_score"] == 82 and stored["author"]["id"] == "A111"
    assert "Graph neural networks" in llm.prompts[0][1]["content"]       # publications reach the LLM
    assert "Alex Example" in llm.prompts[0][1]["content"]                # so does the applicant profile

    # unchanged → skipped; changed notes → re-analyzed
    assert analyze_people(data_dir, settings, profile, llm, store, oa, None).analyzed == 0
    people = load_people(data_dir)
    people[0].notes = "Met her at a conference."
    save_people(data_dir, people)
    assert analyze_people(data_dir, settings, profile, llm, store, oa, None).analyzed == 1
    # status changes alone don't trigger a new (paid) analysis
    people = load_people(data_dir)
    people[0].status = "contacted"
    save_people(data_dir, people)
    assert not needs_refresh(store.get_person_insight("ada-example"), people[0], profile.fingerprint, 30)
    store.close()


def test_scan_includes_people(data_dir, fake_llm):
    save_people(data_dir, [Person(name="Ada Example", openalex_id="A111")])
    settings, profile = load_settings(data_dir), load_profile(data_dir.profile_dir)
    report = run(data_dir, settings, [], profile, lambda u: "", PersonLLM(), openalex=OpenAlexClient(fake_get([])))
    assert report.people_analyzed == 1
