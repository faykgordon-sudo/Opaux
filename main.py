"""
main.py -- Opaux CLI entry point.
"""

import io
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from datetime import UTC

import rich_click as click
from rich.console import Console
from rich.panel import Panel

# Force UTF-8 output so German/special characters don't crash on Windows cp1252
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# rich-click styling
# ---------------------------------------------------------------------------

click.rich_click.USE_RICH_MARKUP = True
click.rich_click.SHOW_ARGUMENTS = True
click.rich_click.GROUP_ARGUMENTS_OPTIONS = True
click.rich_click.STYLE_COMMANDS_TABLE_SHOW_LINES = True
click.rich_click.STYLE_COMMANDS_TABLE_PAD_EDGE = True
click.rich_click.COMMAND_GROUPS = {
    "opaux": [
        {
            "name": "Pipeline",
            "commands": ["setup", "discover", "score", "tailor", "cover", "apply", "run"],
        },
        {
            "name": "Tracking",
            "commands": ["track", "status"],
        },
        {
            "name": "Utilities",
            "commands": ["pdf", "db", "web"],
        },
    ]
}

console = Console()
err_console = Console(stderr=True)

# ---------------------------------------------------------------------------
# Supported languages
# ---------------------------------------------------------------------------

SUPPORTED_LANGS = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "pt": "Portuguese",
    "nl": "Dutch",
    "it": "Italian",
    "pl": "Polish",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "fi": "Finnish",
    "cs": "Czech",
    "hu": "Hungarian",
    "ro": "Romanian",
    "bg": "Bulgarian",
    "el": "Greek",
    "tr": "Turkish",
    "ar": "Arabic",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
}

VALID_STATUSES = [
    "discovered", "scored", "tailored", "applied",
    "responded", "interview", "offer", "rejected",
]

# ---------------------------------------------------------------------------
# Config loading (with env-var support)
# ---------------------------------------------------------------------------

def _load_config(config_path: str = "config/settings.yaml") -> dict:
    """Load, validate, and return the settings config (env vars take priority)."""
    from src.config import load_and_validate_settings
    return load_and_validate_settings(config_path)


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(version="0.1.3", prog_name="opaux")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show debug output.")
@click.option("--quiet", "-q", is_flag=True, default=False, help="Show errors only.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool, quiet: bool) -> None:
    """[bold]Opaux[/bold] -- Automated job application pipeline powered by Claude AI.

    Discover jobs, score them with AI, tailor your CV, and track applications
    — all from the terminal.

    [bold]Quick start:[/bold]
      opaux setup       Configure API key and preferences
      opaux run         Run the full pipeline end-to-end
      opaux track       View your application dashboard
    """
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["quiet"] = quiet
    from src.logger import configure_logging
    configure_logging(verbose=verbose, quiet=quiet)


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

def _setup_command() -> None:
    """Configure API key, CV format, language, and search preferences."""
    settings_path = Path("config/settings.yaml")

    import yaml

    if settings_path.exists():
        with open(settings_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        console.print("[bold blue]Opaux Setup[/bold blue] -- updating existing settings\n")
    else:
        config = {}
        console.print("[bold blue]Opaux Setup[/bold blue] -- creating new settings\n")

    # --- API Key ---
    current_key = config.get("claude_api_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")
    masked = f"{current_key[:12]}...{current_key[-4:]}" if len(current_key) > 16 else "(not set)"
    console.print(f"[bold]1. Claude API Key[/bold] (current: {masked})")
    new_key = click.prompt("   API key (press Enter to keep current)", default="", show_default=False)
    if new_key.strip():
        config["claude_api_key"] = new_key.strip()
    elif current_key and not config.get("claude_api_key"):
        config["claude_api_key"] = current_key

    # --- Preferred Language ---
    console.print("\n[bold]2. Preferred CV & Cover Letter Language[/bold]")
    for code, name in SUPPORTED_LANGS.items():
        console.print(f"   [dim]{code}[/dim]  {name}")
    current_lang = config.get("application", {}).get("default_lang", "en")
    lang = click.prompt("\n   Language code", default=current_lang).strip().lower()
    if lang not in SUPPORTED_LANGS:
        console.print(f"[yellow]Unknown code '{lang}', keeping '{current_lang}'[/yellow]")
        lang = current_lang

    # --- Default CV Format ---
    console.print("\n[bold]3. Default CV Format[/bold]")
    console.print("   1. american  -- single-column, ATS-clean")
    console.print("   2. german    -- tabular layout, photo, signature block")
    console.print("   3. europass  -- CEFR grid, DIGCOMP competence table")
    current_fmt = config.get("application", {}).get("default_format", "american")
    fmt_choice = click.prompt("   Format", default=current_fmt)
    fmt_map = {"1": "american", "2": "german", "3": "europass"}
    cv_format = fmt_map.get(fmt_choice.strip(), fmt_choice.strip())
    if cv_format not in ("american", "german", "europass"):
        cv_format = current_fmt

    # --- Job Search Preferences ---
    console.print("\n[bold]4. Job Search[/bold]")
    search = config.get("search", {})
    search_term = click.prompt("   Search term", default=search.get("search_term", "Logistics Manager"))
    location = click.prompt("   Location", default=search.get("location", "Germany"))
    results_wanted = click.prompt("   Results per run", default=search.get("results_wanted", 50), type=int)

    # --- Min Score Threshold ---
    console.print("\n[bold]5. Minimum score to tailor a CV (1-10)[/bold]")
    min_score = click.prompt(
        "   Min score", default=config.get("scoring", {}).get("min_score", 6.0), type=float
    )

    # --- Save ---
    config.setdefault("application", {})["default_lang"] = lang
    config.setdefault("application", {})["default_format"] = cv_format
    config.setdefault("search", {}).update({
        "search_term": search_term,
        "location": location,
        "results_wanted": results_wanted,
    })
    config.setdefault("scoring", {})["min_score"] = min_score
    config.setdefault("database", {}).setdefault("path", "data/jobs.db")
    config.setdefault("output", {}).setdefault("dir", "output")

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    import yaml
    with open(settings_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    console.print("\n[bold green]Setup complete![/bold green]")
    console.print(f"  Language : [cyan]{lang}[/cyan] ({SUPPORTED_LANGS.get(lang, lang)})")
    console.print(f"  CV format: [cyan]{cv_format}[/cyan]")
    console.print(f"  Search   : [cyan]{search_term}[/cyan] in [cyan]{location}[/cyan]")
    console.print(f"  Min score: [cyan]{min_score}[/cyan]")
    console.print("\nRun [bold]opaux discover[/bold] to start finding jobs.")


cli.command(name="setup")(_setup_command)


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------

@cli.command()
def discover() -> None:
    """Scrape job boards and add new postings to the database.

    Pulls from all configured sources (Indeed, LinkedIn, Glassdoor,
    Bundesagentur, Arbeitnow, Adzuna). Already-seen URLs are skipped
    automatically, so running this multiple times is safe.

    [bold]Examples:[/bold]
      opaux discover
      opaux -v discover    [dim]# verbose output[/dim]
    """
    config = _load_config()
    db_path = config["database"]["path"]

    from src.discovery import run_discovery

    try:
        count = run_discovery(config, db_path)
        console.print(
            Panel(
                f"[green]Found:[/green] [bold]{count}[/bold] new jobs\n\n"
                "[dim]Next:[/dim] [bold cyan]opaux score[/bold cyan]",
                title="[bold]Discovery Complete[/bold]",
                border_style="green",
            )
        )
    except ImportError as e:
        err_console.print(f"[bold red]Error:[/] {e}")
        sys.exit(1)
    except Exception as e:
        err_console.print(f"[bold red]Error:[/] Discovery failed: {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------

@cli.command()
@click.option(
    "--limit",
    default=50,
    show_default=True,
    type=click.IntRange(1, 500),
    help="Maximum number of jobs to score per run.",
)
@click.option(
    "--min-score",
    default=None,
    type=float,
    help="Only display/return jobs above this threshold.",
)
@click.option(
    "--rescore",
    is_flag=True,
    default=False,
    help="Re-score jobs that already have a score (overwrites existing scores).",
)
def score(limit: int, min_score: float | None, rescore: bool) -> None:
    """Score jobs using Claude AI and store results in the database.

    Each job is evaluated against your profile (config/profile.yaml) and
    scored 1-10. Matched skills, gaps, and ATS keywords are stored for use
    during CV tailoring.

    By default, already-scored jobs are skipped. Use [bold]--rescore[/bold] to
    re-evaluate them.

    [bold]Examples:[/bold]
      opaux score
      opaux score --limit 20 --min-score 7
      opaux score --rescore
    """
    config = _load_config()
    db_path = config["database"]["path"]

    from src.scoring import run_scoring

    try:
        scored = run_scoring(config, db_path, limit=limit, min_score=min_score, rescore=rescore)
        above = [j for j in scored if (j.get("score") or 0) >= (min_score or 0)]
        console.print(
            Panel(
                f"[green]Scored:[/green] [bold]{len(scored)}[/bold] jobs\n"
                + (f"[green]Above {min_score}:[/green] [bold]{len(above)}[/bold]\n" if min_score else "")
                + "\n[dim]Next:[/dim] [bold cyan]opaux tailor <job_id>[/bold cyan]",
                title="[bold]Scoring Complete[/bold]",
                border_style="blue",
            )
        )
    except Exception as e:
        err_console.print(f"[bold red]Error:[/] Scoring failed: {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# tailor
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("job_id")
@click.option(
    "--format",
    "cv_format",
    default=None,
    type=click.Choice(["american", "german", "europass"]),
    help="CV format. Defaults to application.default_format in settings.",
)
@click.option(
    "--lang",
    default=None,
    help="Target language code (e.g. de, fr, es). Defaults to application.default_lang.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite existing CV even if one has already been generated.",
)
@click.option(
    "--output",
    "-o",
    "output_dir",
    default=None,
    type=click.Path(),
    help="Override output directory (default: output/cvs/).",
)
def tailor(job_id: str, cv_format: str | None, lang: str | None, force: bool, output_dir: str | None) -> None:
    """Tailor your CV for a specific job and generate a .docx file.

    Three-pass process:
      1. Extract ATS keywords from the job description
      2. Reframe your bullets using the job's language (target: 90%+ ATS match)
      3. Refinement pass if ATS score is below 90%

    The output filename follows the pattern:
      [bold]{company}_{title}_{format}_{lang}.docx[/bold]

    [bold]Examples:[/bold]
      opaux tailor abc123
      opaux tailor abc123 --format german --lang de
      opaux tailor abc123 --force
    """
    config = _load_config()
    db_path = config["database"]["path"]
    app_cfg = config.get("application", {})

    if not cv_format:
        console.print("\n[bold]CV Format:[/bold]")
        console.print("  1. american  -- single-column, ATS-clean")
        console.print("  2. german    -- tabular layout, photo placeholder, signature")
        console.print("  3. europass  -- CEFR grid, DIGCOMP table")
        fmt_choice = click.prompt("Choose format", default="1")
        cv_format = {"1": "american", "2": "german", "3": "europass"}.get(
            fmt_choice.strip(), fmt_choice.strip()
        )
        if cv_format not in ("american", "german", "europass"):
            cv_format = "american"

    if not lang:
        default_lang = app_cfg.get("default_lang", "")
        if default_lang and default_lang in SUPPORTED_LANGS:
            lang = default_lang
        else:
            lang = click.prompt("Language code (e.g. en, de)", default="en").strip().lower()
            if lang not in SUPPORTED_LANGS:
                console.print(f"[yellow]Unknown language '{lang}', defaulting to English.[/yellow]")
                lang = "en"

    # Check for existing CV (idempotency)
    if not force:
        from src.database import get_connection, get_job_by_id, init_db
        init_db(db_path)
        conn = get_connection(db_path)
        try:
            job = get_job_by_id(conn, job_id)
        finally:
            conn.close()
        if job and job.get("cv_path") and Path(job["cv_path"]).exists():
            console.print(
                f"[yellow]CV already exists:[/yellow] {job['cv_path']}\n"
                "Use [bold]--force[/bold] to regenerate."
            )
            return

    from src.tailoring import run_tailoring

    try:
        output_path = run_tailoring(
            config, db_path, job_id,
            format=cv_format, lang=lang,
            output_dir=output_dir,
        )
        console.print(
            Panel(
                f"[green]CV saved to:[/green] [bold]{output_path}[/bold]\n\n"
                "[dim]Next:[/dim] [bold cyan]opaux cover " + job_id + "[/bold cyan]",
                title="[bold]Tailoring Complete[/bold]",
                border_style="green",
            )
        )
    except ValueError as e:
        err_console.print(f"[bold red]Error:[/] {e}")
        sys.exit(1)
    except Exception as e:
        err_console.print(f"[bold red]Error:[/] Tailoring failed: {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# cover
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("job_id")
@click.option(
    "--output",
    "-o",
    "output_dir",
    default=None,
    type=click.Path(),
    help="Override output directory.",
)
def cover(job_id: str, output_dir: str | None) -> None:
    """Generate a cover letter for a specific job.

    Produces a 3-paragraph cover letter tailored to the role:
      - Hook: why you want this job at this company
      - Achievements: 2-3 concrete examples from your experience
      - CTA: clear call-to-action closing

    Language is automatically matched to your CV language setting.

    [bold]Examples:[/bold]
      opaux cover abc123
    """
    config = _load_config()
    db_path = config["database"]["path"]

    from src.cover_letter import run_cover_letter

    try:
        output_path = run_cover_letter(config, db_path, job_id, output_dir=output_dir)
        console.print(
            Panel(
                f"[green]Cover letter saved to:[/green] [bold]{output_path}[/bold]\n\n"
                "[dim]Next:[/dim] [bold cyan]opaux apply " + job_id + "[/bold cyan]",
                title="[bold]Cover Letter Complete[/bold]",
                border_style="green",
            )
        )
    except ValueError as e:
        err_console.print(f"[bold red]Error:[/] {e}")
        sys.exit(1)
    except Exception as e:
        err_console.print(f"[bold red]Error:[/] Cover letter generation failed: {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("job_id")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Navigate and fill forms but do NOT click submit.",
)
def apply(job_id: str, dry_run: bool) -> None:
    """Auto-fill and submit a job application using Playwright.

    Opens the job's application URL in a browser, fills in your personal
    details (name, email, phone, LinkedIn), uploads your CV and cover
    letter, and submits.

    [bold]--dry-run[/bold] fills the form but stops before clicking submit.
    A screenshot is saved as proof either way.

    [bold]Examples:[/bold]
      opaux apply abc123 --dry-run
      opaux apply abc123
    """
    config = _load_config()
    db_path = config["database"]["path"]

    # Safety: never re-apply to already-applied jobs
    from src.database import get_connection, get_job_by_id, init_db
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        job = get_job_by_id(conn, job_id)
    finally:
        conn.close()

    if job and job.get("status") == "applied" and not dry_run:
        err_console.print(
            f"[bold red]Error:[/] Job [bold]{job_id}[/bold] has already been applied to.\n"
            "Use [bold]--dry-run[/bold] to test the form without submitting."
        )
        sys.exit(1)

    if not dry_run:
        click.confirm(
            f"Submit application for job '{job_id}'? This will fill and click Submit.",
            abort=True,
        )

    from src.applicator import run_application

    try:
        run_application(config, db_path, job_id, dry_run=dry_run)
    except Exception as e:
        err_console.print(f"[bold red]Error:[/] Application failed: {e}")
        sys.exit(2)


# ---------------------------------------------------------------------------
# track
# ---------------------------------------------------------------------------

@cli.command()
@click.option(
    "--status",
    default=None,
    type=click.Choice(VALID_STATUSES, case_sensitive=False),
    help="Filter by application status.",
)
@click.option("--export", "export_path", default=None, help="Export results to this CSV path.")
def track(status: str | None, export_path: str | None) -> None:
    """Show your application tracker dashboard with analytics.

    Displays a colour-coded table of all jobs with scores, statuses,
    dates, and ATS match percentages. Includes summary statistics:
    response rate, interview rate, and offer rate.

    [bold]Examples:[/bold]
      opaux track
      opaux track --status applied
      opaux track --export applications.csv
    """
    config = _load_config()
    db_path = config["database"]["path"]

    from src.tracker import run_tracker

    try:
        run_tracker(config, db_path, status=status, export_path=export_path)
    except Exception as e:
        err_console.print(f"[bold red]Error:[/] Tracker failed: {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# run (full pipeline)
# ---------------------------------------------------------------------------

@cli.command(name="run")
@click.option(
    "--limit",
    default=10,
    show_default=True,
    type=click.IntRange(1, 100),
    help="Number of top-scoring jobs to tailor CVs for.",
)
@click.option(
    "--min-score",
    default=None,
    type=float,
    help="Minimum score to tailor. Defaults to scoring.min_score in settings.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Run discovery and scoring only; skip tailoring and cover letters.",
)
def run_pipeline(limit: int, min_score: float | None, dry_run: bool) -> None:
    """Run the full pipeline: discover → score → tailor → cover letters.

    This is the main end-to-end command. Run it regularly (e.g. daily)
    to keep your job pipeline fresh.

    Steps:
      1. Discover new jobs from all configured sources
      2. Score all unscored jobs with Claude AI
      3. Tailor CVs for the top N jobs above --min-score
      4. Generate cover letters for those same jobs

    Use [bold]--dry-run[/bold] to run only steps 1-2.

    [bold]Examples:[/bold]
      opaux run
      opaux run --limit 5 --min-score 7.5
      opaux run --dry-run
    """
    config = _load_config()
    db_path = config["database"]["path"]
    app_cfg = config.get("application", {})
    scoring_cfg = config.get("scoring", {})

    effective_min = min_score if min_score is not None else float(scoring_cfg.get("min_score", 6.0))
    cv_format = app_cfg.get("default_format", "american")
    cv_lang = app_cfg.get("default_lang", "en")

    console.print(
        Panel(
            f"Limit: [cyan]{limit}[/cyan]  |  Min score: [cyan]{effective_min}[/cyan]  |  "
            f"Format: [cyan]{cv_format}[/cyan]  |  Lang: [cyan]{cv_lang}[/cyan]"
            + ("\n[yellow]Dry run: steps 1-2 only[/yellow]" if dry_run else ""),
            title="[bold blue]Opaux Pipeline[/bold blue]",
            border_style="blue",
        )
    )

    # Step 1: Discover
    console.print("\n[bold]Step 1/4 -- Discovery[/bold]")
    new_jobs = 0
    try:
        from src.discovery import run_discovery
        new_jobs = run_discovery(config, db_path)
        console.print(f"  [green]{new_jobs}[/green] new jobs discovered.")
    except ImportError as e:
        err_console.print(f"[yellow]Warning:[/] Discovery skipped (import error): {e}")
    except Exception as e:
        err_console.print(f"[yellow]Warning:[/] Discovery error: {e}")

    # Step 2: Score
    console.print("\n[bold]Step 2/4 -- Scoring[/bold]")
    scored_jobs: list[dict] = []
    try:
        from src.scoring import run_scoring
        scored_jobs = run_scoring(config, db_path, limit=limit * 3)
        console.print(f"  [green]{len(scored_jobs)}[/green] jobs scored.")
    except Exception as e:
        err_console.print(f"[yellow]Warning:[/] Scoring error: {e}")

    if dry_run:
        console.print("\n[yellow]Dry run: stopping after scoring.[/yellow]")
        _show_pipeline_summary(new_jobs, len(scored_jobs), 0, 0)
        return

    # Determine top jobs above threshold
    top_jobs = sorted(
        [j for j in scored_jobs if (j.get("score") or 0) >= effective_min],
        key=lambda j: j.get("score") or 0,
        reverse=True,
    )[:limit]

    if not top_jobs:
        console.print(
            f"\n[yellow]No jobs scored above {effective_min}. "
            "Skipping tailoring and cover letter steps.[/yellow]"
        )
        _show_pipeline_summary(new_jobs, len(scored_jobs), 0, 0)
        return

    console.print(f"\n  [green]{len(top_jobs)}[/green] jobs qualify (score >= {effective_min})")

    # Step 3: Tailor CVs
    console.print("\n[bold]Step 3/4 -- CV Tailoring[/bold]")
    tailored_count = 0
    from src.tailoring import run_tailoring
    for job in top_jobs:
        title = job.get("title", "?")
        company = job.get("company", "?")
        score_val = job.get("score", 0)
        console.print(f"  [{score_val:.1f}] [cyan]{title}[/cyan] @ {company}...")
        try:
            run_tailoring(config, db_path, job["id"], format=cv_format, lang=cv_lang)
            tailored_count += 1
        except Exception as e:
            err_console.print(f"  [red]Error tailoring {job['id']}:[/] {e}")

    # Step 4: Cover Letters
    console.print("\n[bold]Step 4/4 -- Cover Letters[/bold]")
    cover_count = 0
    from src.cover_letter import run_cover_letter
    for job in top_jobs:
        title = job.get("title", "?")
        company = job.get("company", "?")
        console.print(f"  [cyan]{title}[/cyan] @ {company}...")
        try:
            run_cover_letter(config, db_path, job["id"])
            cover_count += 1
        except Exception as e:
            err_console.print(f"  [red]Error on {job['id']}:[/] {e}")

    _show_pipeline_summary(new_jobs, len(scored_jobs), tailored_count, cover_count)


# Keep old "pipeline" alias for backwards compatibility
cli.command(name="pipeline", hidden=True)(run_pipeline.callback)  # type: ignore[arg-type]


def _show_pipeline_summary(
    discovered: int, scored: int, tailored: int, cover_letters: int
) -> None:
    console.print(
        Panel(
            f"[green]Discovered:[/green]  {discovered} new jobs\n"
            f"[blue]Scored:[/blue]      {scored} jobs\n"
            f"[cyan]CVs tailored:[/cyan] {tailored}\n"
            f"[cyan]Cover letters:[/cyan] {cover_letters}\n\n"
            "[dim]Next:[/dim] [bold cyan]opaux track[/bold cyan]",
            title="[bold]Pipeline Complete[/bold]",
            border_style="green",
        )
    )


# ---------------------------------------------------------------------------
# pdf
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("job_id")
@click.option("--cv", "do_cv", is_flag=True, default=False, help="Convert CV .docx to PDF.")
@click.option("--cover", "do_cover", is_flag=True, default=False, help="Convert cover letter .docx to PDF.")
def pdf(job_id: str, do_cv: bool, do_cover: bool) -> None:
    """Render a job's CV and/or cover letter .docx files to PDF.

    If neither --cv nor --cover is given, both are rendered.

    Requires Microsoft Word (Windows/macOS) or LibreOffice (Linux/fallback).

    [bold]Examples:[/bold]
      opaux pdf abc123
      opaux pdf abc123 --cv
    """
    config = _load_config()
    db_path = config["database"]["path"]

    from src.database import get_all_jobs, get_connection, get_job_by_id, init_db
    from src.pdf_renderer import render_pdf

    if not do_cv and not do_cover:
        do_cv = True
        do_cover = True

    init_db(db_path)
    conn = get_connection(db_path)
    try:
        job = get_job_by_id(conn, job_id)
        if not job:
            all_jobs = get_all_jobs(conn)
            matches = [j for j in all_jobs if j["id"].startswith(job_id)]
            if len(matches) == 1:
                job = matches[0]
            elif len(matches) > 1:
                err_console.print(
                    f"[bold red]Error:[/] Ambiguous job ID prefix '{job_id}'. Be more specific."
                )
                sys.exit(1)
    finally:
        conn.close()

    if not job:
        err_console.print(
            f"[bold red]Error:[/] Job ID '{job_id}' not found in database.\n"
            "Run [bold]opaux track[/bold] to see available jobs."
        )
        sys.exit(1)

    title = job.get("title", "?")
    company = job.get("company", "?")
    console.print(f"[bold blue]Rendering PDFs:[/bold blue] {title} @ {company}")

    generated = []
    if do_cv and job.get("cv_path"):
        try:
            out = render_pdf(job["cv_path"])
            console.print(f"[green]CV PDF:[/green] {out}")
            generated.append(out)
        except Exception as e:
            err_console.print(f"[bold red]Error:[/] CV PDF failed: {e}")
    elif do_cv:
        console.print("[yellow]Warning:[/] No CV found for this job. Run [bold]opaux tailor[/bold] first.")

    if do_cover and job.get("cover_letter_path"):
        try:
            out = render_pdf(job["cover_letter_path"])
            console.print(f"[green]Cover Letter PDF:[/green] {out}")
            generated.append(out)
        except Exception as e:
            err_console.print(f"[bold red]Error:[/] Cover letter PDF failed: {e}")
    elif do_cover:
        console.print("[yellow]Warning:[/] No cover letter found. Run [bold]opaux cover[/bold] first.")

    if generated:
        console.print(f"\n[bold green]{len(generated)} PDF(s) rendered.[/bold green]")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("job_id")
@click.argument("new_status", metavar="STATUS", type=click.Choice(VALID_STATUSES, case_sensitive=False))
@click.option("--notes", default=None, help="Optional notes to attach to this status change.")
def status(job_id: str, new_status: str, notes: str | None) -> None:
    """Manually update the status of a job application.

    STATUS must be one of: discovered, scored, tailored, applied,
    responded, interview, offer, rejected

    Timestamps are set automatically when transitioning to 'applied',
    'responded', 'interview', or 'offer'.

    [bold]Examples:[/bold]
      opaux status abc123 applied
      opaux status abc123 interview --notes "Phone screen scheduled"
    """
    config = _load_config()
    db_path = config["database"]["path"]

    from datetime import datetime

    from src.database import get_all_jobs, get_connection, get_job_by_id, init_db, update_job

    init_db(db_path)
    conn = get_connection(db_path)
    try:
        job = get_job_by_id(conn, job_id)
        if not job:
            all_jobs = get_all_jobs(conn)
            matches = [j for j in all_jobs if j["id"].startswith(job_id)]
            if len(matches) == 1:
                job = matches[0]
                job_id = job["id"]
            elif len(matches) > 1:
                err_console.print(
                    f"[bold red]Error:[/] Ambiguous job ID prefix '{job_id}'. Be more specific."
                )
                sys.exit(1)

        if not job:
            err_console.print(
                f"[bold red]Error:[/] Job ID '{job_id}' not found in database.\n"
                "Run [bold]opaux track[/bold] to see available jobs."
            )
            sys.exit(1)

        kwargs: dict = {"status": new_status}
        if notes:
            kwargs["notes"] = notes
        if new_status == "applied" and not job.get("applied_date"):
            kwargs["applied_date"] = datetime.now(UTC).isoformat()
        elif new_status in ("responded", "interview", "offer") and not job.get("response_date"):
            kwargs["response_date"] = datetime.now(UTC).isoformat()

        update_job(conn, job_id, **kwargs)
    finally:
        conn.close()

    old_status = job.get("status", "?")
    console.print(
        f"[bold green]Status updated:[/bold green] {job.get('title', '?')} @ {job.get('company', '?')}\n"
        f"  [dim]{old_status}[/dim] → [bold]{new_status}[/bold]"
    )
    if notes:
        console.print(f"  Notes: {notes}")


# ---------------------------------------------------------------------------
# db (subcommand group)
# ---------------------------------------------------------------------------

@cli.group()
def db() -> None:
    """Database management commands.

    Manage the Opaux SQLite database: initialise, reset, export, and inspect.
    """


@db.command(name="init")
def db_init() -> None:
    """Create a fresh database (safe to run on existing DB)."""
    config = _load_config()
    db_path = config["database"]["path"]
    from src.database import init_db
    init_db(db_path)
    console.print(f"[bold green]Database ready:[/bold green] {db_path}")


@db.command(name="reset")
@click.option("--confirm", is_flag=True, default=False, help="Skip confirmation prompt.")
def db_reset(confirm: bool) -> None:
    """Drop and recreate the database. [bold red]Deletes all data.[/bold red]"""
    config = _load_config()
    db_path = config["database"]["path"]

    if not confirm:
        click.confirm(
            f"This will delete ALL data in {db_path}. Are you sure?",
            abort=True,
        )

    import os
    if os.path.exists(db_path):
        os.remove(db_path)
        console.print(f"[yellow]Deleted:[/yellow] {db_path}")

    from src.database import init_db
    init_db(db_path)
    console.print(f"[bold green]Database recreated:[/bold green] {db_path}")


@db.command(name="export")
@click.option(
    "--format",
    "fmt",
    default="csv",
    type=click.Choice(["csv", "excel"]),
    show_default=True,
    help="Export format.",
)
@click.option(
    "--output",
    "-o",
    "output_path",
    default=None,
    help="Output file path (default: data/export.csv or data/export.xlsx).",
)
def db_export(fmt: str, output_path: str | None) -> None:
    """Export all jobs to CSV or Excel."""
    config = _load_config()
    db_path = config["database"]["path"]

    if not output_path:
        output_path = f"data/export.{'xlsx' if fmt == 'excel' else 'csv'}"

    from src.database import get_all_jobs, get_connection, init_db
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        jobs = get_all_jobs(conn)
    finally:
        conn.close()

    if not jobs:
        console.print("[yellow]No jobs to export.[/yellow]")
        return

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if fmt == "csv":
        import csv
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(jobs[0].keys()))
            writer.writeheader()
            for job in jobs:
                writer.writerow({k: (v or "") for k, v in job.items()})
    else:
        try:
            import openpyxl
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Jobs"
            cols = list(jobs[0].keys())
            ws.append(cols)
            for job in jobs:
                ws.append([job.get(c, "") or "" for c in cols])
            wb.save(output_path)
        except ImportError:
            err_console.print("[bold red]Error:[/] openpyxl not installed. Run: pip install openpyxl")
            sys.exit(1)

    console.print(f"[bold green]Exported {len(jobs)} jobs to:[/bold green] {output_path}")


@db.command(name="stats")
def db_stats() -> None:
    """Show database statistics: counts by status, score distribution, sources."""
    config = _load_config()
    db_path = config["database"]["path"]

    from rich.table import Table

    from src.database import get_all_jobs, get_connection, init_db

    init_db(db_path)
    conn = get_connection(db_path)
    try:
        jobs = get_all_jobs(conn)
    finally:
        conn.close()

    if not jobs:
        console.print("[yellow]Database is empty.[/yellow]")
        return

    # Status breakdown
    status_counts: dict[str, int] = {}
    for job in jobs:
        s = job.get("status") or "unknown"
        status_counts[s] = status_counts.get(s, 0) + 1

    # Score distribution
    scores = [j["score"] for j in jobs if j.get("score") is not None]
    avg_score = sum(scores) / len(scores) if scores else 0

    # Source breakdown
    source_counts: dict[str, int] = {}
    for job in jobs:
        src = job.get("source") or "unknown"
        source_counts[src] = source_counts.get(src, 0) + 1

    t = Table(title="Database Statistics", show_lines=True)
    t.add_column("Metric", style="bold")
    t.add_column("Value", justify="right")
    t.add_row("Total jobs", str(len(jobs)))
    t.add_row("Scored", str(len(scores)))
    t.add_row("Avg score", f"{avg_score:.2f}" if scores else "--")

    console.print(t)
    console.print()

    t2 = Table(title="By Status", show_lines=True)
    t2.add_column("Status", style="bold")
    t2.add_column("Count", justify="right")
    for st, cnt in sorted(status_counts.items()):
        t2.add_row(st, str(cnt))
    console.print(t2)
    console.print()

    t3 = Table(title="By Source", show_lines=True)
    t3.add_column("Source", style="bold")
    t3.add_column("Count", justify="right")
    for src, cnt in sorted(source_counts.items(), key=lambda x: -x[1]):
        t3.add_row(src, str(cnt))
    console.print(t3)


# ---------------------------------------------------------------------------
# web
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Host to bind to.")
@click.option("--port", default=5000, show_default=True, type=click.IntRange(1, 65535), help="Port to listen on.")
@click.option("--debug", is_flag=True, default=False, help="Enable Flask debug mode.")
def web(host: str, port: int, debug: bool) -> None:
    """Launch the Opaux web UI in your browser.

    Starts the Flask web server and opens your default browser automatically.
    The web UI provides a visual job tracker, profile editor, and background
    pipeline scheduling.

    [bold]Examples:[/bold]
      opaux web
      opaux web --port 8080
    """
    import webbrowser
    url = f"http://{host}:{port}"
    console.print(f"[bold green]Starting Opaux Web UI[/bold green] at [cyan]{url}[/cyan]")
    console.print("Press [bold]Ctrl+C[/bold] to stop.")

    if not debug:
        import threading
        def _open() -> None:
            import time
            time.sleep(1.2)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    from web.app import run_server
    run_server(host=host, port=port, debug=debug)


if __name__ == "__main__":
    cli()
