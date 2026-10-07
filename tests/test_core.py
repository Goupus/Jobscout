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
