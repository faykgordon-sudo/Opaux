"""
discovery.py -- Job discovery via multiple scrapers.

Sources:
  jobspy   -> indeed (reliable)
  scrapers -> bundesagentur, arbeitnow, adzuna
"""

from datetime import datetime
from typing import Any

import pandas as pd
from rich.console import Console
from rich.table import Table

from src.database import generate_job_id, get_connection, init_db, insert_job

console = Console()

# Sites routed through python-jobspy
JOBSPY_SITES = {"indeed", "glassdoor", "zip_recruiter"}

# Sites handled by our own scrapers (linkedin now uses guest API directly)
CUSTOM_SITES = {"linkedin", "bundesagentur", "arbeitnow", "adzuna"}


def _check_jobspy() -> None:
    """Raise a helpful ImportError if python-jobspy is not installed."""
    try:
        import jobspy  # noqa: F401
    except ImportError:
        raise ImportError(
            "python-jobspy is required for indeed/linkedin discovery.\n"
            "Install with:  pip install python-jobspy --no-deps\n"
            "Then re-run:   opaux discover"
        )


def _normalize_jobspy_row(raw: Any) -> dict:
    """Convert a jobspy DataFrame row to our DB schema."""
    if hasattr(raw, "to_dict"):
        raw = raw.to_dict()

    url = str(raw.get("job_url") or raw.get("url") or "")
    if not url:
        return {}

    salary_min = None
    salary_max = None
    try:
        s = raw.get("min_amount") or raw.get("salary_min")
        if s is not None:
            salary_min = float(s)
        e = raw.get("max_amount") or raw.get("salary_max")
        if e is not None:
            salary_max = float(e)
    except (TypeError, ValueError):
        pass

    is_remote = bool(raw.get("is_remote") or raw.get("remote"))
    if not is_remote:
        is_remote = "remote" in str(raw.get("location") or "").lower()

    return {
        "id": generate_job_id(url),
        "title": str(raw.get("title") or ""),
        "company": str(raw.get("company") or raw.get("company_name") or ""),
        "location": str(raw.get("location") or ""),
        "url": url,
        "source": str(raw.get("site") or raw.get("source") or ""),
        "description": str(raw.get("description") or ""),
        "salary_min": salary_min,
        "salary_max": salary_max,
        "job_type": str(raw.get("job_type") or ""),
        "is_remote": is_remote,
        "date_posted": str(raw.get("date_posted") or ""),
        "date_discovered": datetime.utcnow().isoformat(),
        "status": "discovered",
    }


def _scrape_jobspy_site(
    site: str,
    search_term: str,
    location: str,
    results_wanted: int,
    hours_old: int,
    country_indeed: str,
) -> list[dict]:
    """Scrape a single site via python-jobspy. Returns list of normalised job dicts."""
    try:
        from jobspy import scrape_jobs
    except ImportError:
        return []

    kwargs: dict = dict(
        site_name=[site],
        search_term=search_term,
        location=location,
        results_wanted=results_wanted,
        hours_old=hours_old,
        country_indeed=country_indeed,
    )
    if site == "linkedin":
        kwargs["linkedin_fetch_description"] = True

    df = scrape_jobs(**kwargs)
    if df is None or len(df) == 0:
        return []

    jobs = []
    for _, row in df.iterrows():
        job = _normalize_jobspy_row(row)
        if job:
            jobs.append(job)
    return jobs


def _scrape_custom_site(
    site: str,
    search_term: str,
    location: str,
    results_wanted: int,
    config: dict,
) -> list[dict]:
    """Scrape a site using our custom scrapers module."""
    from src.scrapers import scrape_arbeitnow, scrape_adzuna, scrape_bundesagentur, scrape_linkedin

    if site == "linkedin":
        li_cfg = config.get("linkedin", {})
        return scrape_linkedin(
            search_term=search_term,
            location=location,
            results_wanted=results_wanted,
            experience_levels=li_cfg.get("experience_levels", [2, 3]),
            job_type=li_cfg.get("job_type", "F"),
            work_model=li_cfg.get("work_model", None),
            posted_within=li_cfg.get("posted_within", "r604800"),
            fetch_descriptions=li_cfg.get("fetch_descriptions", True),
        )

    if site == "bundesagentur":
        return scrape_bundesagentur(search_term, location, results_wanted)

    if site == "arbeitnow":
        return scrape_arbeitnow(search_term, location, results_wanted)

    if site == "adzuna":
        adzuna_cfg = config.get("adzuna", {})
        return scrape_adzuna(
            search_term,
            location,
            results_wanted,
            app_id=adzuna_cfg.get("app_id", ""),
            app_key=adzuna_cfg.get("app_key", ""),
        )

    return []


def run_discovery(config: dict, db_path: str) -> int:
    """
    Scrape jobs from all configured sites and insert new ones into the DB.

    jobspy sites  : indeed, linkedin, glassdoor
    custom sites  : bundesagentur, arbeitnow, adzuna

    Returns the count of newly inserted jobs.
    """
    search_cfg = config.get("search", {})
    search_term = search_cfg.get("search_term", "Software Engineer")
    location = search_cfg.get("location", "Remote")
    sites: list[str] = search_cfg.get(
        "sites", ["indeed", "bundesagentur", "arbeitnow"]
    )
    results_wanted = int(search_cfg.get("results_wanted", 50))
    hours_old = int(search_cfg.get("hours_old", 168))
    country_indeed = search_cfg.get("country_indeed", "germany")

    console.print(
        f"[bold blue]Searching for[/bold blue] [green]{search_term}[/green] "
        f"in [green]{location}[/green] on {', '.join(sites)}..."
    )

    jobspy_sites = [s for s in sites if s in JOBSPY_SITES]
    custom_sites = [s for s in sites if s in CUSTOM_SITES]

    # Check jobspy is available if any jobspy sites are requested
    if jobspy_sites:
        try:
            _check_jobspy()
        except ImportError as exc:
            console.print(f"[yellow]jobspy unavailable:[/yellow] {exc}")
            jobspy_sites = []

    all_jobs: list[dict] = []

    # --- jobspy sites ---
    for site in jobspy_sites:
        try:
            jobs = _scrape_jobspy_site(
                site, search_term, location, results_wanted, hours_old, country_indeed
            )
            if jobs:
                console.print(f"  [green]{site}[/green]: {len(jobs)} results")
                all_jobs.extend(jobs)
            else:
                console.print(f"  [yellow]{site}[/yellow]: 0 results")
        except Exception as exc:
            console.print(f"  [red]{site} failed:[/red] {exc}")

    # --- custom scrapers ---
    for site in custom_sites:
        try:
            jobs = _scrape_custom_site(site, search_term, location, results_wanted, config)
            if jobs:
                console.print(f"  [green]{site}[/green]: {len(jobs)} results")
                all_jobs.extend(jobs)
            else:
                console.print(f"  [yellow]{site}[/yellow]: 0 results")
        except Exception as exc:
            console.print(f"  [red]{site} failed:[/red] {exc}")

    if not all_jobs:
        console.print("[yellow]No jobs returned from any source.[/yellow]")
        return 0

    # --- persist to DB ---
    init_db(db_path)
    conn = get_connection(db_path)
    new_count = 0
    new_jobs: list[dict] = []

    try:
        existing_ids: set[str] = {
            row[0] for row in conn.execute("SELECT id FROM jobs").fetchall()
        }
        for job in all_jobs:
            if not job.get("id"):
                continue
            if job["id"] not in existing_ids:
                insert_job(conn, job)
                new_jobs.append(job)
                existing_ids.add(job["id"])
                new_count += 1
    finally:
        conn.close()

    # --- display table ---
    if new_jobs:
        table = Table(
            title=f"[bold]New Jobs Discovered ({new_count})[/bold]",
            show_lines=False,
        )
        table.add_column("ID", style="dim", width=16)
        table.add_column("Title", style="bold white", max_width=35)
        table.add_column("Company", style="cyan", max_width=22)
        table.add_column("Location", max_width=20)
        table.add_column("Source", style="dim")
        table.add_column("Remote", justify="center")

        for job in new_jobs[:50]:
            table.add_row(
                job["id"],
                job.get("title", ""),
                job.get("company", ""),
                job.get("location", ""),
                job.get("source", ""),
                "[green]Yes[/green]" if job.get("is_remote") else "No",
            )
        console.print(table)
    else:
        console.print("[yellow]No new jobs (all already in database).[/yellow]")

    console.print(
        f"[bold green]Discovery complete:[/bold green] {new_count} new jobs added "
        f"({len(all_jobs)} total scraped)."
    )
    return new_count
