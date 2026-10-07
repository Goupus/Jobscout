import json
import shutil
from pathlib import Path

import pytest

from jobscout.config import resolve_paths

TEMPLATES = Path(__file__).parents[1] / "src" / "jobscout" / "templates" / "data"


class FakeLLM:
    """Returns canned JSON depending on which prompt it receives."""

    def __init__(self, profile_fit=80, interest_fit=70):
        self.profile_fit = profile_fit
        self.interest_fit = interest_fit
        self.calls = []

    def chat(self, messages, *, fast=False):
        self.calls.append(messages)
        system = messages[0]["content"]
        if "extract open job" in system:
            return json.dumps({"positions": [
                {"title": "PhD position: ML for membranes", "url": "/jobs/phd-1",
                 "organization": "Uni X", "description": "Machine learning, membranes, Python."},
            ]})
        return "```json\n" + json.dumps({
            "profile_fit": self.profile_fit,
            "interest_fit": self.interest_fit,
            "summary": "Good fit.",
            "matching_strengths": ["Python -> thesis"],
            "gaps": ["No PhD-level publications"],
            "emphasize_in_application": ["Membrane ML thesis"],
            "profile_tailoring": ["Move ML projects to the top"],
            "red_flags": [],
        }) + "\n```"


@pytest.fixture
def fake_llm():
    return FakeLLM()


@pytest.fixture
def data_dir(tmp_path):
    dst = tmp_path / "data"
    shutil.copytree(TEMPLATES, dst)
    return resolve_paths(dst)
