from typer.testing import CliRunner

from jobscout.cli import app
from jobscout.config import SourceConfig, load_settings
from jobscout.interview import run_interview
from jobscout.models import MatchCategory
from jobscout.pipeline import run
from jobscout.profile import load_profile
from jobscout.storage import Store

HTML = '<li class="job"><h3>ML Process Engineer</h3><a href="/j/1">x</a></li>'


def _sources():
    return [
        SourceConfig(name="board", type="html", url="https://ex.com/", item_selector="li.job", title_selector="h3"),
        SourceConfig(name="group", type="llm_page", url="https://uni.example/"),
        SourceConfig(name="broken", type="rss", url="https://broken.example/"),
    ]


def _fetch(url):
    if "broken" in url:
        raise ConnectionError("boom")
    return HTML


def test_end_to_end(data_dir, fake_llm):
    settings = load_settings(data_dir)
    profile = load_profile(data_dir.profile_dir)

    r1 = run(data_dir, settings, _sources(), profile, _fetch, fake_llm)
    assert r1.new_jobs == 2 and r1.matched == 2
    assert len(r1.errors) == 1 and "broken" in r1.errors[0]

    store = Store(data_dir.db)
    rows = store.overview()
    assert {r["category"] for r in rows} == {MatchCategory.TOP.value}
    assert rows[0]["match"]["profile_tailoring"] == ["Move ML projects to the top"]

    # second run: nothing new, nothing re-matched
    r2 = run(data_dir, settings, _sources(), profile, _fetch, fake_llm)
    assert r2.new_jobs == 0 and r2.matched == 0

    # dismissed jobs are not re-matched; a changed profile re-matches the rest
    store.set_status(rows[0]["uid"], "dismissed")
    (data_dir.profile_dir / "notes.md").write_text("New: I also like catalysis.")
    r3 = run(data_dir, settings, _sources(), load_profile(data_dir.profile_dir), _fetch, fake_llm, scan=False)
    assert r3.matched == 1
    store.close()


def test_interview_writes_profile_doc(data_dir):
    class InterviewLLM:
        def __init__(self):
            self.n = 0

        def chat(self, messages, *, fast=False):
            if "Summarize the interview" in messages[0]["content"]:
                return "## Motivation & direction\nWants ML in process engineering."
            self.n += 1
            return "What motivates you?" if self.n == 1 else "FINISHED"

    path = run_interview(load_profile(data_dir.profile_dir), InterviewLLM(), data_dir.profile_dir,
                         ask=lambda _: "Impact", say=lambda _: None)
    text = path.read_text()
    assert "Wants ML" in text and "**A:** Impact" in text
    assert path.name in load_profile(data_dir.profile_dir).documents


def test_cli_init(tmp_path):
    result = CliRunner().invoke(app, ["init", "--data-dir", str(tmp_path / "d")])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "d" / "profile" / "interests.yaml").exists()
    assert (tmp_path / "d" / ".github" / "workflows" / "scan.yml").exists()
