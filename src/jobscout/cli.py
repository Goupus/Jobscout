"""Command line interface: ``jobscout --help``."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer

from .config import load_settings, load_sources, resolve_paths
from .llm import LiteLLMBackend
from .profile import load_profile

app = typer.Typer(add_completion=False, help="Scan job sources and match them against your profile.")
TEMPLATES = Path(__file__).parent / "templates" / "data"

DataDir = typer.Option(None, "--data-dir", "-d", envvar="JOBSCOUT_DATA_DIR", help="Private data directory (default ./data)")


@app.callback()
def _main(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(levelname)s %(message)s")


@app.command()
def init(data_dir: Optional[Path] = DataDir, force: bool = typer.Option(False, help="Overwrite existing files")) -> None:
    """Create a data directory with example settings, sources and profile."""
    paths = resolve_paths(data_dir)
    for src in TEMPLATES.rglob("*"):
        if src.is_dir():
            continue
        dst = paths.data_dir / src.relative_to(TEMPLATES)
        if dst.exists() and not force:
            typer.echo(f"skip   {dst}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        typer.echo(f"create {dst}")
    typer.echo("\nNext: edit profile/ and sources.yaml, then run `jobscout interview` and `jobscout scan`.")


@app.command()
def scan(
    data_dir: Optional[Path] = DataDir,
    no_match: bool = typer.Option(False, "--no-match", help="Only collect postings"),
    match_only: bool = typer.Option(False, "--match-only", help="Only (re-)match stored postings"),
) -> None:
    """Scan all enabled sources and match new postings."""
    from .pipeline import run
    from .sources import make_fetcher

    paths = resolve_paths(data_dir)
    settings = load_settings(paths)
    llm = LiteLLMBackend(settings.llm)
    report = run(
        paths, settings, load_sources(paths), load_profile(paths.profile_dir),
        make_fetcher(settings), llm, model_name=settings.llm.model,
        scan=not match_only, match=not no_match,
    )
    typer.echo(
        f"new: {report.new_jobs} | already known: {report.seen_jobs} | matched: {report.matched} "
        f"| skipped by pre-filter: {report.skipped_prefilter} | errors: {len(report.errors)}"
    )
    for e in report.errors:
        typer.echo(f"  ! {e}", err=True)


@app.command()
def interview(data_dir: Optional[Path] = DataDir, questions: int = typer.Option(12, help="Approximate number of questions")) -> None:
    """Deepen your profile in an interactive interview with the LLM."""
    from .interview import run_interview
    from .matcher import LANG_NAMES

    paths = resolve_paths(data_dir)
    settings = load_settings(paths)
    path = run_interview(
        load_profile(paths.profile_dir), LiteLLMBackend(settings.llm), paths.profile_dir,
        n_questions=questions, language=LANG_NAMES.get(settings.matching.language, settings.matching.language),
    )
    typer.echo(f"\nSaved to {path}. Stored matches will be refreshed on the next scan.")


@app.command()
def dashboard(data_dir: Optional[Path] = DataDir, port: int = 8501) -> None:
    """Open the Streamlit dashboard."""
    paths = resolve_paths(data_dir)
    app_path = Path(__file__).parent / "dashboard.py"
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(app_path), "--server.port", str(port), "--", "--data-dir", str(paths.data_dir)],
        check=False,
    )


@app.command()
def demo(data_dir: Optional[Path] = DataDir) -> None:
    """Fill the database with fictional matches to try the dashboard without an API key."""
    from .demo import seed

    paths = resolve_paths(data_dir)
    n = seed(paths)
    typer.echo(f"Added {n} demo postings to {paths.db}. Run `jobscout dashboard`.")


@app.command()
def status(data_dir: Optional[Path] = DataDir) -> None:
    """Show the last runs."""
    from .storage import Store

    store = Store(resolve_paths(data_dir).db)
    for r in store.last_runs():
        typer.echo(f"{r['started_at'][:16]}  new={r['new_jobs']:<4} matched={r['matched']:<4} errors={r['errors']}")


if __name__ == "__main__":
    app()
