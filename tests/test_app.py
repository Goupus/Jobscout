"""Render every page of the Streamlit app against demo data."""

import importlib.util

import pytest
from streamlit.testing.v1 import AppTest

from jobscout.demo import seed
from jobscout.interview_form import FORM_FILENAME, form_template

MAIN = importlib.util.find_spec("jobscout.app.main").origin


def _open(page, data_dir, monkeypatch):
    monkeypatch.setenv("JOBSCOUT_DATA_DIR", str(data_dir.data_dir))
    monkeypatch.setenv("JOBSCOUT_START_PAGE", page)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return AppTest.from_file(MAIN, default_timeout=60).run()


@pytest.mark.parametrize("page,title", [
    ("start", "🧭 jobscout"), ("profile", "👤 Profile"), ("interview", "💬 Interview"),
    ("sources", "🔎 Sources"), ("matches", "📋 Matches"), ("run", "🔄 Scan & sync"),
])
def test_pages_render(page, title, data_dir, monkeypatch):
    seed(data_dir)
    at = _open(page, data_dir, monkeypatch)
    assert not at.exception, [e.value for e in at.exception]
    assert at.title[0].value == title


def test_start_shows_progress(data_dir, monkeypatch):
    at = _open("start", data_dir, monkeypatch)
    progress_text = at.get("progress")[0].proto.text
    assert "of 7 steps" in progress_text


def test_interview_paste_flow_saves_form(data_dir, monkeypatch):
    at = _open("interview", data_dir, monkeypatch)
    filled = form_template().replace('summary: ""', 'summary: "Wants ML for process design."')
    at.text_area[0].set_value(filled).run()
    assert not at.exception
    save = next(b for b in at.button if "Save to my profile" in b.label)
    save.click().run()
    assert (data_dir.profile_dir / FORM_FILENAME).exists()


def test_matches_status_change_goes_to_tracker(data_dir, monkeypatch):
    seed(data_dir)
    at = _open("matches", data_dir, monkeypatch)
    at.selectbox(key=at.selectbox[1].key).set_value("shortlisted").run()  # [0] is "Sort by"
    assert "shortlisted" in data_dir.tracker.read_text()


def test_sources_save_keeps_advanced_keys(data_dir, monkeypatch):
    import yaml

    data_dir.sources.write_text(yaml.safe_dump({
        "defaults": {"focus": "PhD ML"},
        "sources": [{"name": "AP", "type": "llm_page", "url": "https://a.example/jobs",
                     "extra_urls": ["https://a.example/jobs?page=2"], "include_keywords": ["phd"]}],
    }))
    at = _open("sources", data_dir, monkeypatch)
    next(b for b in at.button if "Save sources" in b.label).click().run()
    assert not at.exception
    saved = yaml.safe_load(data_dir.sources.read_text())
    assert saved["defaults"]["focus"] == "PhD ML"
    assert saved["sources"][0]["extra_urls"] == ["https://a.example/jobs?page=2"]
    assert saved["sources"][0]["include_keywords"] == ["phd"]
    assert "focus" not in saved["sources"][0]


def test_store_survives_thread_switch(data_dir, monkeypatch):
    """Streamlit reruns can happen on another thread – the store must still work."""
    import threading

    from jobscout.app import common

    seed(data_dir)
    monkeypatch.setattr(common, "paths", lambda: data_dir)
    fake_state = {}
    monkeypatch.setattr(common.st, "session_state", type("S", (dict,), {"__getattr__": dict.get, "__setattr__": dict.__setitem__})(fake_state))
    first = common.store()
    assert first.overview()
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("rows", common.store().overview()))
    t.start(); t.join()
    assert len(out["rows"]) == 4


def test_sources_add_by_link(data_dir, monkeypatch):
    import yaml

    from jobscout import sources as src_mod

    monkeypatch.setattr(src_mod, "make_fetcher", lambda s: (lambda url: "<html><title>PSE Group – Open positions</title></html>"))
    at = _open("sources", data_dir, monkeypatch)
    at.text_area[0].set_value("https://uni.example/pse/jobs\nhttps://example.org/research-group/open-positions\n\nboard.example/search?q=phd")
    next(b for b in at.button if b.label == "Add").click().run()
    assert not at.exception, [e.value for e in at.exception]
    saved = yaml.safe_load(data_dir.sources.read_text())["sources"]
    urls = [s["url"] for s in saved]
    assert "https://uni.example/pse/jobs" in urls and "https://board.example/search?q=phd" in urls
    assert urls.count("https://example.org/research-group/open-positions") == 1   # already existed
    new = next(s for s in saved if s["url"] == "https://uni.example/pse/jobs")
    assert new["name"] == "PSE Group – Open positions" and new["fetch_details"] is True


def test_people_page_add_confirm_and_show(data_dir, monkeypatch):
    from jobscout.app.views import people as page
    from jobscout.people import Person, load_people, save_people
    from jobscout.storage import Store

    from test_people import fake_get

    monkeypatch.setattr(page, "default_get_json", lambda s: fake_get([]))
    at = _open("people", data_dir, monkeypatch)
    assert not at.exception
    at.text_input(key="pp_name").set_value("Ada Example")
    at.text_input(key="pp_aff").set_value("Somewhere Else")
    at.text_input(key="pp_li").set_value("https://www.linkedin.com/in/ada")
    next(b for b in at.button if b.label == "Add person").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert load_people(data_dir)[0].linkedin == "https://www.linkedin.com/in/ada"
    # two OpenAlex candidates → user confirms the first one
    next(b for b in at.button if b.label == "Confirm").click().run()
    assert load_people(data_dir)[0].openalex_id == "A111"

    store = Store(data_dir.db)
    store.save_person_insight("ada-example", {"insight": {"overlap_score": 77, "research_summary": "ML x PSE",
                                                         "draft_message": "Subject: Hello"}, "analyzed_at": "2026-10-07T00:00:00+00:00"})
    store.close()
    at = _open("people", data_dir, monkeypatch)
    assert not at.exception
    assert any("overlap 77" in e.label for e in at.expander)


def test_matches_shows_failed_scan_and_threshold(data_dir, monkeypatch):
    import json

    from jobscout.config import load_settings, save_settings
    from jobscout.models import JobPosting
    from jobscout.storage import Store

    store = Store(data_dir.db)
    store.upsert_job(JobPosting(source="s", title="PhD X", url="https://x.example/1"))
    store.log_run("2026-10-07T07:38:00+00:00", "2026-10-07T07:39:00+00:00", 1, 0, [
        "AcademicPositions: Client error '403 Forbidden'",
        "match PhD X: anthropic/m: does not support temperature=0.2"])
    store.close()
    at = _open("matches", data_dir, monkeypatch)
    assert not at.exception
    assert any("could not assess any posting" in e.value for e in at.error)
    assert any("not assessed yet" in i.value for i in at.info)

    # threshold changes the category without a new assessment
    seed(data_dir)
    s = load_settings(data_dir)
    s.matching.category_threshold = 90
    save_settings(data_dir, s)
    at = _open("matches", data_dir, monkeypatch)
    # demo scores (88/94, 82/71, 41/86, 74/22) at threshold 90 → only one stretch, three no match
    assert [m.value for m in at.metric][:4] == ["0", "1", "0", "3"]
