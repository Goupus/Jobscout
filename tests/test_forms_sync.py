import sqlite3
import subprocess

import pytest
import yaml

from jobscout import interview_form as f
from jobscout import sync
from jobscout.config import SourceConfig, load_source_defaults, load_sources, save_sources
from jobscout.demo import seed
from jobscout.profile import load_profile
from jobscout.storage import Store

FILLED = """Here you go:
```yaml
form_version: 1
completed_on: ""
summary: Engineer who wants to combine ML and process design.
strengths:
  - strength: Python
    evidence: thesis model
interests:
  topics: [ML for process design, Machine learning for materials and membranes]
  dealbreakers: [sales]
  notes: likes travel
open_questions: []
```
Good luck!"""


def test_prompt_contains_form_and_profile(data_dir):
    prompt = f.build_prompt("de", load_profile(data_dir.profile_dir))
    assert "form_version: 1" in prompt and "Erfinde nichts" in prompt
    assert "Alex Example" in prompt          # profile included
    assert "{FORM}" not in prompt and "{PROFILE}" not in prompt
    assert "Alex Example" not in f.build_prompt("en")
    assert "form_version" in f.build_prompt("xx")  # unknown language falls back to English


def test_blank_template_parses():
    form = f.parse_form(f.form_template())
    assert not any(form.filled_sections().values())


def test_parse_save_merge(data_dir):
    form = f.parse_form(FILLED)
    assert form.completed_on                                  # filled with today
    assert form.filled_sections()["strengths"] and not form.filled_sections()["tasks"]
    path = f.save_form(form, data_dir.profile_dir)
    f.save_form(form, data_dir.profile_dir)
    assert (data_dir.profile_dir / "_previous_interview_form.yaml").exists()
    assert "combine ML" in load_profile(data_dir.profile_dir).documents[path.name]

    f.merge_interests(form, data_dir.profile_dir)
    interests = yaml.safe_load((data_dir.profile_dir / "interests.yaml").read_text())
    assert interests["topics"].count("machine learning for materials and membranes") == 1  # dedupe, case-insensitive
    assert "ML for process design" in interests["topics"]
    assert "sales" in interests["dealbreakers"]


@pytest.mark.parametrize("bad", ["no yaml: [", "just: text", ""])
def test_parse_rejects_non_forms(bad):
    with pytest.raises(f.FormError):
        f.parse_form(bad)


def test_source_defaults_roundtrip(data_dir):
    sources = [SourceConfig(name="a", type="llm_page", url="https://a.example", focus="PhD ML"),
               SourceConfig(name="b", type="llm_page", url="https://b.example", focus="own focus", enabled=False)]
    save_sources(data_dir, sources, {"focus": "PhD ML"})
    raw = yaml.safe_load(data_dir.sources.read_text())
    assert "focus" not in raw["sources"][0] and raw["sources"][1]["focus"] == "own focus"
    loaded = load_sources(data_dir)
    assert [s.focus for s in loaded] == ["PhD ML", "own focus"]
    assert loaded[1].enabled is False
    assert load_source_defaults(data_dir) == {"focus": "PhD ML"}


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def two_clones(tmp_path, data_dir):
    """A bare 'GitHub' repo plus two clones: the user's and the scheduled scan's."""
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    user = data_dir.data_dir
    _git(user, "init", "-q", "-b", "main")
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        _git(user, "config", k, v)
    Store(data_dir.db).close()
    _git(user, "add", "-A")
    _git(user, "commit", "-q", "-m", "init")
    _git(user, "remote", "add", "origin", str(remote))
    _git(user, "push", "-q", "-u", "origin", "main")
    bot = tmp_path / "bot"
    _git(tmp_path, "clone", "-q", str(remote), str(bot))
    for k, v in (("user.email", "b@b"), ("user.name", "b")):
        _git(bot, "config", k, v)
    return user, bot


def test_sync_push_pull_and_db_merge(two_clones, data_dir):
    user, bot = two_clones
    st = sync.status(user)
    assert st.is_repo and not st.changed

    # the scheduled scan adds results on GitHub ...
    from jobscout.config import resolve_paths
    seed(resolve_paths(bot))
    _git(bot, "commit", "-q", "-am", "scan")
    _git(bot, "push", "-q")

    # ... while the user ran a local scan and edited the profile
    from jobscout.models import JobPosting

    store = Store(data_dir.db)
    store.upsert_job(JobPosting(source="local", title="Local find", url="https://local.example/1"))
    store.close()
    (data_dir.profile_dir / "interests.yaml").write_text("topics: [catalysis]\n")
    assert set(sync.status(user).changed) >= {"jobscout.db", "profile/interests.yaml"}

    assert "Uploaded" in sync.push(user, "my changes")
    assert not sync.status(user).changed

    # both the bot's results and the local find survive, on both sides
    _git(bot, "pull", "-q")
    for clone in (user, bot):
        titles = {r[0] for r in sqlite3.connect(clone / "jobscout.db").execute("SELECT title FROM jobs")}
        assert "Local find" in titles and len(titles) == 5
    assert "catalysis" in (bot / "profile" / "interests.yaml").read_text()
    assert sync.pull(user) == "Already up to date."


def test_sync_without_repo(tmp_path):
    assert not sync.status(tmp_path).is_repo
    with pytest.raises(sync.SyncError):
        sync.push(tmp_path)
