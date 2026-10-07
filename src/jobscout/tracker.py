"""Your application tracker (status + notes per posting).

Kept in a small text file (``tracker.yaml``) instead of the database: the
scheduled scan only ever changes ``jobscout.db`` and you only ever change
``tracker.yaml``, so syncing via git never produces a conflict.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

STATES = ["new", "shortlisted", "applied", "dismissed"]


class Tracker:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) if self.path.exists() else None
        self.entries: dict[str, dict] = data or {}

    def get(self, uid: str) -> dict:
        e = self.entries.get(uid, {})
        return {"state": e.get("state", "new"), "note": e.get("note", "")}

    def set(self, uid: str, *, state: str | None = None, note: str | None = None, title: str | None = None) -> None:
        if state is not None and state not in STATES:
            raise ValueError(f"unknown state {state!r}")
        e = self.entries.setdefault(uid, {})
        if state is not None:
            e["state"] = state
        if note is not None:
            e["note"] = note
        if title:  # makes the file readable for humans
            e["title"] = title
        e["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        self.save()

    def dismissed(self) -> set[str]:
        return {uid for uid, e in self.entries.items() if e.get("state") == "dismissed"}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            "# Your application tracker – written by the jobscout app.\n"
            + yaml.safe_dump(self.entries, allow_unicode=True, sort_keys=True, width=120),
            encoding="utf-8",
        )
