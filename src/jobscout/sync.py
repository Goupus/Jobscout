"""Sync the data directory with its (private) GitHub repository.

The app uses this so you never have to type git commands:

* ``pull``  – fetch the latest scan results and any changes made on GitHub
* ``push``  – upload your profile / sources / tracker changes

Only the scheduled scan changes ``jobscout.db``. If you also ran a scan locally,
``pull`` merges both databases instead of failing with a conflict.
Uses your local git credentials (e.g. GitHub Desktop or ``gh auth login``).
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

DB_NAME = "jobscout.db"


class SyncError(RuntimeError):
    pass


def _git(path: Path, *args: str, check: bool = True, strip: bool = True) -> str:
    try:
        proc = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=120)
    except FileNotFoundError as exc:
        raise SyncError("git is not installed") from exc
    if check and proc.returncode != 0:
        raise SyncError((proc.stderr or proc.stdout).strip() or f"git {' '.join(args)} failed")
    return proc.stdout.strip() if strip else proc.stdout


def is_repo(path: Path) -> bool:
    try:
        return _git(path, "rev-parse", "--is-inside-work-tree") == "true"
    except SyncError:
        return False


@dataclass
class RepoStatus:
    is_repo: bool
    remote: str | None = None
    branch: str | None = None
    changed: list[str] | None = None

    @property
    def github_url(self) -> str | None:
        if not self.remote:
            return None
        m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", self.remote)
        return f"https://github.com/{m.group(1)}" if m else None


def status(path: Path) -> RepoStatus:
    if not is_repo(path):
        return RepoStatus(is_repo=False)
    remote = _git(path, "remote", "get-url", "origin", check=False) or None
    branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD", check=False) or None
    lines = _git(path, "status", "--porcelain", "-uall", strip=False).splitlines()
    changed = [ln[3:].strip().strip('"') for ln in lines if ln.strip()]
    return RepoStatus(True, remote, branch, changed)


def merge_databases(target: Path, other: Path) -> None:
    """Add postings, matches and runs from `other` that `target` doesn't have yet."""
    conn = sqlite3.connect(target)
    try:
        conn.execute("ATTACH DATABASE ? AS other", (str(other),))
        with conn:
            conn.execute("INSERT OR IGNORE INTO jobs SELECT * FROM other.jobs")
            conn.execute(
                """INSERT OR REPLACE INTO matches SELECT o.* FROM other.matches o
                   LEFT JOIN matches m ON m.job_uid = o.job_uid
                   WHERE m.job_uid IS NULL OR o.matched_at > m.matched_at"""
            )
            conn.execute(
                """INSERT INTO runs(started_at, finished_at, new_jobs, matched, errors)
                   SELECT started_at, finished_at, new_jobs, matched, errors FROM other.runs
                   WHERE started_at NOT IN (SELECT started_at FROM runs)"""
            )
        conn.execute("DETACH DATABASE other")
    finally:
        conn.close()


def pull(path: Path) -> str:
    """Get the newest state from GitHub. Returns a short human-readable summary."""
    st = status(path)
    if not st.is_repo or not st.remote:
        raise SyncError("The data folder is not connected to a GitHub repository.")
    db = path / DB_NAME
    backup = None
    if DB_NAME in (st.changed or []) and db.exists():
        backup = path / f".{DB_NAME}.local"
        shutil.copy2(db, backup)
        _git(path, "checkout", "--", DB_NAME)
    before = _git(path, "rev-parse", "HEAD", check=False)
    try:
        _git(path, "pull", "--rebase", "--autostash")
    finally:
        if backup is not None:
            if db.exists():
                merge_databases(db, backup)
            else:
                shutil.copy2(backup, db)
            backup.unlink(missing_ok=True)
    after = _git(path, "rev-parse", "HEAD", check=False)
    if before == after:
        return "Already up to date."
    n = _git(path, "rev-list", "--count", f"{before}..{after}", check=False) or "?"
    return f"Fetched {n} new commit(s)."


def push(path: Path, message: str = "Update via jobscout app") -> str:
    st = status(path)
    if not st.is_repo or not st.remote:
        raise SyncError("The data folder is not connected to a GitHub repository.")
    if not st.changed:
        return "Nothing to upload."
    pull(path)  # integrate scan results first (merges a locally changed DB), then commit on top
    changed = status(path).changed or []
    _git(path, "add", "-A")
    _git(path, "commit", "-m", message)
    _git(path, "push")
    return f"Uploaded {len(changed)} changed file(s)."
