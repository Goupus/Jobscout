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


def copy_templates(data_dir: Path, force: bool = False) -> list[tuple[Path, bool]]:
    """Copy the example data directory. Returns (path, created) pairs."""
    out = []
    for src in TEMPLATES.rglob("*"):
        if src.is_dir():
            continue
        dst = data_dir / src.relative_to(TEMPLATES)
        if dst.exists() and not force:
            out.append((dst, False))
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        out.append((dst, True))
    return out


@app.command()
def init(data_dir: Optional[Path] = DataDir, force: bool = typer.Option(False, help="Overwrite existing files")) -> None:
    """Create a data directory with example settings, sources and profile."""
    paths = resolve_paths(data_dir)
    for path, created in copy_templates(paths.data_dir, force):
        typer.echo(f"{'create' if created else 'skip  '} {path}")
    typer.echo("\nNext: run `jobscout app` and follow the setup steps.")


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
def interview(data_dir: Optional[Path] = DataDir, max_questions: int = typer.Option(25, help="Upper limit of questions")) -> None:
    """Interview with your configured LLM; ends with a filled interview form in profile/."""
    from .interview import run_interview

    paths = resolve_paths(data_dir)
    settings = load_settings(paths)
    path = run_interview(
        load_profile(paths.profile_dir), LiteLLMBackend(settings.llm), paths.profile_dir,
        max_questions=max_questions, language=settings.matching.language,
    )
    typer.echo(f"\nSaved to {path}. Stored matches will be refreshed on the next scan.")


@app.command("interview-prompt")
def interview_prompt(
    data_dir: Optional[Path] = DataDir,
    language: Optional[str] = typer.Option(None, help="en or de (default: settings)"),
    with_profile: bool = typer.Option(True, help="Include your current profile in the prompt"),
    out: Optional[Path] = typer.Option(None, help="Write to a file instead of printing"),
) -> None:
    """Print a prompt to run the interview with any chatbot (ChatGPT, Claude, ...)."""
    from .interview_form import build_prompt

    paths = resolve_paths(data_dir)
    lang = language or load_settings(paths).matching.language
    profile = load_profile(paths.profile_dir) if with_profile and paths.profile_dir.exists() else None
    text = build_prompt(lang, profile)
    if out:
        out.write_text(text, encoding="utf-8")
        typer.echo(f"Prompt written to {out}")
    else:
        typer.echo(text)


@app.command("import-form")
def import_form(
    file: Path = typer.Argument(..., help="Text file with the chatbot's final answer"),
    data_dir: Optional[Path] = DataDir,
    merge_interests: bool = typer.Option(True, help="Also add the form's interests to interests.yaml"),
) -> None:
    """Validate a filled interview form and store it in profile/."""
    from . import interview_form as f

    paths = resolve_paths(data_dir)
    form = f.parse_form(file.read_text(encoding="utf-8"))
    path = f.save_form(form, paths.profile_dir)
    typer.echo(f"Saved {path}")
    missing = [k for k, ok in form.filled_sections().items() if not ok]
    if missing:
        typer.echo(f"Empty sections: {', '.join(missing)}")
    if merge_interests:
        typer.echo(f"Updated {f.merge_interests(form, paths.profile_dir)}")


@app.command("app")
def run_app(data_dir: Optional[Path] = DataDir, port: int = 8501) -> None:
    """Open the jobscout app (setup, profile, interview, sources, matches)."""
    paths = resolve_paths(data_dir)
    app_path = Path(__file__).parent / "app" / "main.py"
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(app_path), "--server.port", str(port),
         "--browser.gatherUsageStats", "false", "--client.toolbarMode", "minimal", "--", "--data-dir", str(paths.data_dir)],
        check=False,
    )


@app.command("dashboard", hidden=True)
def dashboard(data_dir: Optional[Path] = DataDir, port: int = 8501) -> None:
    """Alias for `jobscout app`."""
    run_app(data_dir, port)


@app.command()
def demo(data_dir: Optional[Path] = DataDir) -> None:
    """Fill the database with fictional matches to try the dashboard without an API key."""
    from .demo import seed

    paths = resolve_paths(data_dir)
    n = seed(paths)
    typer.echo(f"Added {n} demo postings to {paths.db}. Run `jobscout app`.")


@app.command()
def status(data_dir: Optional[Path] = DataDir) -> None:
    """Show the last runs."""
    from .storage import Store

    store = Store(resolve_paths(data_dir).db)
    for r in store.last_runs():
        typer.echo(f"{r['started_at'][:16]}  new={r['new_jobs']:<4} matched={r['matched']:<4} errors={r['errors']}")


if __name__ == "__main__":
    app()
