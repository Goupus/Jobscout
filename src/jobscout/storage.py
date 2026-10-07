"""SQLite storage for postings, matches and user feedback."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from typing import TYPE_CHECKING

from .models import JobPosting, MatchResult

if TYPE_CHECKING:
    from .tracker import Tracker

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    uid TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    organization TEXT,
    location TEXT,
    description TEXT,
    deadline TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS matches (
    job_uid TEXT PRIMARY KEY REFERENCES jobs(uid),
    profile_fit INTEGER,
    interest_fit INTEGER,
    category TEXT,
    payload TEXT NOT NULL,
    model TEXT,
    matched_at TEXT NOT NULL,
    profile_hash TEXT
);
CREATE TABLE IF NOT EXISTS status (
    job_uid TEXT PRIMARY KEY REFERENCES jobs(uid),
    state TEXT NOT NULL DEFAULT 'new',   -- new | shortlisted | applied | dismissed
    note TEXT DEFAULT '',
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    new_jobs INTEGER DEFAULT 0,
    matched INTEGER DEFAULT 0,
    errors TEXT DEFAULT '[]'
);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self.conn:
            yield self.conn

    # --- jobs -----------------------------------------------------------
    def upsert_job(self, job: JobPosting) -> bool:
        """Insert or refresh a posting. Returns True if it is new."""
        ts = job.found_at.isoformat()
        with self.tx() as c:
            exists = c.execute("SELECT 1 FROM jobs WHERE uid=?", (job.uid,)).fetchone()
            if exists:
                c.execute(
                    "UPDATE jobs SET last_seen=?, description=COALESCE(NULLIF(?, ''), description) WHERE uid=?",
                    (ts, job.description, job.uid),
                )
                return False
            c.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)",
                (job.uid, job.source, job.title, job.url, job.organization, job.location,
                 job.description, job.deadline, ts, ts),
            )
            return True

    def get_job(self, uid: str) -> JobPosting | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE uid=?", (uid,)).fetchone()
        if not row:
            return None
        return JobPosting(
            source=row["source"], title=row["title"], url=row["url"],
            organization=row["organization"], location=row["location"],
            description=row["description"] or "", deadline=row["deadline"],
        )

    def unmatched_jobs(self, profile_hash: str, limit: int, exclude: set[str] | None = None) -> list[JobPosting]:
        """Jobs never matched, or matched against an older profile version."""
        exclude = exclude or set()
        rows = self.conn.execute(
            """SELECT j.uid FROM jobs j
               LEFT JOIN matches m ON m.job_uid = j.uid
               WHERE (m.job_uid IS NULL OR m.profile_hash IS NOT ?)
               ORDER BY j.first_seen DESC""",
            (profile_hash,),
        ).fetchall()
        uids = [r["uid"] for r in rows if r["uid"] not in exclude][:limit]
        return [j for j in (self.get_job(u) for u in uids) if j]

    # --- matches --------------------------------------------------------
    def save_match(self, m: MatchResult, profile_hash: str) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO matches VALUES (?,?,?,?,?,?,?,?)",
                (m.job_uid, m.profile_fit, m.interest_fit, m.category.value,
                 m.model_dump_json(), m.model, m.matched_at.isoformat(), profile_hash),
            )

    def overview(self, tracker: "Tracker | None" = None) -> list[dict]:
        rows = self.conn.execute(
            """SELECT j.*, m.payload, m.profile_fit, m.interest_fit, m.category
               FROM jobs j
               LEFT JOIN matches m ON m.job_uid = j.uid
               ORDER BY j.first_seen DESC"""
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["match"] = json.loads(d.pop("payload")) if d.get("payload") else None
            d.update(tracker.get(d["uid"]) if tracker else {"state": "new", "note": ""})
            out.append(d)
        return out

    # --- runs -----------------------------------------------------------
    def log_run(self, started: str, finished: str, new_jobs: int, matched: int, errors: list[str]) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO runs(started_at, finished_at, new_jobs, matched, errors) VALUES (?,?,?,?,?)",
                (started, finished, new_jobs, matched, json.dumps(errors)),
            )

    def last_runs(self, n: int = 10) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in rows]
