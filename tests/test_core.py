import pytest

from jobscout.config import SourceConfig, load_settings, load_sources
from jobscout.llm import LLMError, extract_json
from jobscout.matcher import prescore
from jobscout.models import JobPosting, MatchCategory, categorize
from jobscout.profile import load_profile
from jobscout.sources import scan_source


@pytest.mark.parametrize(
    "pf, inf, expected",
    [(80, 80, MatchCategory.TOP), (30, 90, MatchCategory.STRETCH),
     (90, 20, MatchCategory.SOLID), (10, 10, MatchCategory.NO_MATCH), (65, 65, MatchCategory.TOP)],
)
def test_categorize(pf, inf, expected):
    assert categorize(pf, inf, 65) == expected


def test_uid_ignores_tracking_params():
    a = JobPosting(source="s", title="A", url="https://www.x.org/job/1?utm_source=feed")
    b = JobPosting(source="s", title="A", url="http://x.org/job/1/")
    assert a.uid == b.uid


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Sure!\n```json\n{"a": 2}\n```') == {"a": 2}
    assert extract_json('Here: {"a": 3} hope that helps') == {"a": 3}
    with pytest.raises(LLMError):
        extract_json("no json here")


def test_templates_load(data_dir):
    settings = load_settings(data_dir)
    assert settings.matching.category_threshold == 65
    assert len(load_sources(data_dir)) == 4
    profile = load_profile(data_dir.profile_dir)
    assert "cv.md" in profile.documents
    assert "machine learning" in " ".join(profile.keywords())


def test_prescore(data_dir):
    profile = load_profile(data_dir.profile_dir)
    good = JobPosting(source="s", title="PhD in machine learning for membrane separation", url="u1",
                      description="Python, process simulation, data analysis")
    bad = JobPosting(source="s", title="Sales manager", url="u2", description="Cold calls")
    assert prescore(good, profile) > prescore(bad, profile)


HTML = """
<ul>
  <li class="job"><h3>Process Data Scientist</h3><a href="/careers/42">more</a><span class="location">Berlin</span></li>
  <li class="job"><h3>Sales Intern</h3><a href="/careers/43">more</a></li>
</ul>"""

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Jobs</title>
<item><title>PhD Membranes</title><link>https://u.example/phd</link><description>&lt;p&gt;ML and membranes&lt;/p&gt;</description></item>
</channel></rss>"""


def test_scan_html_with_filters():
    cfg = SourceConfig(name="c", type="html", url="https://example.com/careers", item_selector="li.job",
                       title_selector="h3", link_selector="a", location_selector=".location",
                       exclude_keywords=["intern"])
    jobs = scan_source(cfg, lambda url: HTML, None)
    assert [j.title for j in jobs] == ["Process Data Scientist"]
    assert jobs[0].url == "https://example.com/careers/42"
    assert jobs[0].location == "Berlin"


def test_scan_rss():
    cfg = SourceConfig(name="r", type="rss", url="https://u.example/feed")
    jobs = scan_source(cfg, lambda url: RSS, None)
    assert jobs[0].title == "PhD Membranes"
    assert jobs[0].description == "ML and membranes"


def test_scan_llm_page(fake_llm):
    cfg = SourceConfig(name="g", type="llm_page", url="https://uni.example/group/")
    jobs = scan_source(cfg, lambda url: "<html><body><a href='/jobs/phd-1'>PhD</a></body></html>", fake_llm)
    assert jobs[0].url == "https://uni.example/jobs/phd-1"
    assert jobs[0].organization == "Uni X"


def test_extra_urls_and_focus(fake_llm):
    cfg = SourceConfig(name="g", type="llm_page", url="https://uni.example/a", extra_urls=["https://uni.example/b"],
                       focus="machine learning for process engineering")
    jobs = scan_source(cfg, lambda url: "<html></html>", fake_llm)
    assert len(fake_llm.calls) == 2                      # both pages scanned
    assert len(jobs) == 1                                 # same posting deduplicated
    assert "machine learning for process engineering" in fake_llm.calls[0][0]["content"]


def test_broken_extra_page_is_skipped():
    def fetch(url):
        if url.endswith("2"):
            raise ConnectionError("down")
        return HTML

    cfg = SourceConfig(name="c", type="html", url="https://example.com/p1", extra_urls=["https://example.com/p2"],
                       item_selector="li.job", title_selector="h3")
    assert len(scan_source(cfg, fetch, None)) == 2


ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Jobs</title>
  <entry><title>Doctoral researcher – hybrid models</title>
    <link rel="alternate" href="https://uni.example/jobs/7"/>
    <author><name>Uni Y</name></author>
    <summary type="html">&lt;b&gt;ML&lt;/b&gt; meets process design</summary></entry>
</feed>"""


def test_scan_atom():
    cfg = SourceConfig(name="a", type="rss", url="https://uni.example/atom")
    jobs = scan_source(cfg, lambda url: ATOM, None)
    assert jobs[0].title == "Doctoral researcher – hybrid models"
    assert jobs[0].url == "https://uni.example/jobs/7"
    assert jobs[0].organization == "Uni Y"
    assert jobs[0].description == "ML meets process design"


def test_pagination_and_detail_pages():
    from jobscout.sources import scan_llm_page

    pages = {
        "https://jobs.example/list": ({"positions": [{"title": "PhD A", "url": "/a"}], "next_page": "/list?p=2"}),
        "https://jobs.example/list?p=2": ({"positions": [{"title": "PhD B", "url": "/b"}], "next_page": "/list?p=3"}),
    }

    class PagedLLM:
        def chat(self, messages, *, fast=False):
            import json, re
            url = re.search(r"Page URL: (\S+)", messages[1]["content"]).group(1)
            return json.dumps(pages[url])

    fetched = []

    def fetch(url):
        fetched.append(url)
        return f"<html><body>Full text of {url} " + "x" * 300 + "</body></html>"

    cfg = SourceConfig(name="board", type="llm_page", url="https://jobs.example/list", fetch_details=True, max_pages=2)
    jobs = scan_llm_page(cfg, fetch, PagedLLM())
    assert [j.title for j in jobs] == ["PhD A", "PhD B"]           # followed one "next page", stopped at max_pages
    assert "Full text of https://jobs.example/a" in jobs[0].description   # opened the posting
    assert "https://jobs.example/list?p=3" not in fetched

    cfg = cfg.model_copy(update={"max_details": 1})
    jobs = scan_llm_page(cfg, fetch, PagedLLM())
    assert "Full text" in jobs[0].description and "Full text" not in jobs[1].description


def test_source_from_url():
    from jobscout.sources import source_from_url

    s = source_from_url("https://www.uni.example/pse/jobs/", lambda u: "<html><head><title>Jobs – PSE Group</title></head></html>")
    assert s.name == "Jobs – PSE Group" and s.type == "llm_page" and s.fetch_details
    s = source_from_url("https://www.uni.example/pse/jobs/", None)
    assert s.name == "uni.example – pse / jobs"


def test_temperature_rejected_is_retried_without(monkeypatch):
    import litellm

    from jobscout.config import LLMSettings
    from jobscout.llm import LiteLLMBackend

    calls = []

    class Resp:
        choices = [type("C", (), {"message": type("M", (), {"content": "ok"})()})()]

    def completion(**kw):
        calls.append(kw)
        if "temperature" in kw:
            raise Exception("claude-x does not support temperature=0.2. Only temperature=1 is supported.")
        return Resp()

    monkeypatch.setattr(litellm, "completion", completion)
    assert LiteLLMBackend(LLMSettings(model="anthropic/x")).chat([{"role": "user", "content": "hi"}]) == "ok"
    assert "temperature" not in calls[0]                      # default: no temperature sent at all
    assert LiteLLMBackend(LLMSettings(model="anthropic/x", temperature=0.2)).chat([{"role": "user", "content": "hi"}]) == "ok"
    assert "temperature" in calls[1] and "temperature" not in calls[2]   # rejected → retried without


def test_custom_criteria_reach_prompt_and_trigger_rematch(data_dir, fake_llm):
    from jobscout.config import load_settings
    from jobscout.matcher import build_system_prompt, criteria_key
    from jobscout.pipeline import run

    settings = load_settings(data_dir)
    profile = load_profile(data_dir.profile_dir)
    assert "ADDITIONAL CRITERIA" not in build_system_prompt(settings.matching)
    assert criteria_key(profile, settings.matching) == profile.fingerprint

    src = [SourceConfig(name="c", type="html", url="https://example.com/", item_selector="li.job", title_selector="h3")]
    assert run(data_dir, settings, src, profile, lambda u: HTML, fake_llm).matched == 2
    assert run(data_dir, settings, src, profile, lambda u: HTML, fake_llm).matched == 0
    settings.matching.custom_criteria = "Netherlands: +10 interest."
    assert "Netherlands: +10 interest." in build_system_prompt(settings.matching)
    assert run(data_dir, settings, src, profile, lambda u: HTML, fake_llm).matched == 2   # re-assessed
    assert "Netherlands" in fake_llm.calls[-1][0]["content"]
