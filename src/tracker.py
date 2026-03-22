"""
tracker.py -- Application tracking dashboard with rich terminal UI.
"""

import csv
import os
from datetime import datetime
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

from src.database import get_all_jobs, get_connection, init_db

console = Console()

# Status color mapping
STATUS_STYLES = {
    "offer": "bold green",
    "interview": "green",
    "responded": "yellow",
    "applied": "blue",
    "tailored": "white",
    "scored": "white",
    "discovered": "dim",
}


def _status_text(status: str) -> Text:
    """Return a colored Rich Text object for the given status."""
    style = STATUS_STYLES.get((status or "").lower(), "dim")
    return Text(str(status or ""), style=style)


def _short_id(job_id: str, length: int = 8) -> str:
    """Return a shortened version of the job ID."""
    return str(job_id)[:length] if job_id else ""


def _format_date(date_str: str | None) -> str:
    """Format a date string for display."""
    if not date_str:
        return ""
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return str(date_str)[:10]


def _compute_analytics(jobs: list[dict]) -> dict:
    """Compute application analytics from the job list."""
    total = len(jobs)
    applied = sum(1 for j in jobs if (j.get("status") or "") in ("applied", "responded", "interview", "offer"))
    responded = sum(1 for j in jobs if (j.get("status") or "") in ("responded", "interview", "offer"))
    interview = sum(1 for j in jobs if (j.get("status") or "") in ("interview", "offer"))
    offer = sum(1 for j in jobs if (j.get("status") or "") == "offer")

    response_rate = (responded / applied * 100) if applied > 0 else 0.0
    interview_rate = (interview / applied * 100) if applied > 0 else 0.0
    offer_rate = (offer / applied * 100) if applied > 0 else 0.0

    return {
        "total": total,
        "applied": applied,
        "responded": responded,
        "interview": interview,
        "offer": offer,
        "response_rate": response_rate,
        "interview_rate": interview_rate,
        "offer_rate": offer_rate,
    }


def _export_csv(jobs: list[dict], export_path: str) -> None:
    """Export jobs to a CSV file."""
    if not jobs:
        console.print("[yellow]No jobs to export.[/yellow]")
        return

    os.makedirs(os.path.dirname(os.path.abspath(export_path)), exist_ok=True)
    fieldnames = list(jobs[0].keys())

    with open(export_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for job in jobs:
            writer.writerow({k: (v if v is not None else "") for k, v in job.items()})

    console.print(f"[green]Exported {len(jobs)} jobs to:[/green] {export_path}")


def run_tracker(
    config: dict,
    db_path: str,
    status: str | None = None,
    export_path: str | None = None,
) -> None:
    """
    Display a rich dashboard of all tracked job applications.

    Args:
        config: App configuration dict
        db_path: Path to the SQLite database
        status: Optional status filter
        export_path: If set, export results to this CSV path
    """
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        jobs = get_all_jobs(conn, status=status)
    finally:
        conn.close()

    if not jobs:
        filter_msg = f" with status '{status}'" if status else ""
        console.print(f"[yellow]No jobs found{filter_msg}.[/yellow]")
        return

    # --- Main Table ---
    title = f"[bold]Job Application Tracker[/bold]"
    if status:
        title += f" -- [yellow]{status}[/yellow]"

    table = Table(title=title, show_lines=False, expand=False)
    table.add_column("ID", style="dim", width=9, no_wrap=True)
    table.add_column("Score", justify="center", width=6, no_wrap=True)
    table.add_column("Title", style="bold white", min_width=20, max_width=30, no_wrap=True)
    table.add_column("Company", style="cyan", min_width=14, max_width=20, no_wrap=True)
    table.add_column("Location", min_width=12, max_width=16, no_wrap=True)
    table.add_column("Status", width=10, no_wrap=True)
    table.add_column("Applied", width=10, no_wrap=True)
    table.add_column("Response", width=10, no_wrap=True)
    table.add_column("ATS", justify="center", width=5, no_wrap=True)

    for job in jobs:
        score = job.get("score")
        if score is not None:
            score_val = float(score)
            if score_val >= 7:
                score_str = f"[green]{score_val:.1f}[/green]"
            elif score_val >= 5:
                score_str = f"[yellow]{score_val:.1f}[/yellow]"
            else:
                score_str = f"[red]{score_val:.1f}[/red]"
        else:
            score_str = "[dim]--[/dim]"

        ats = job.get("ats_score")
        ats_str = f"{float(ats):.0%}" if ats is not None else "--"

        table.add_row(
            _short_id(job.get("id", "")),
            score_str,
            job.get("title", ""),
            job.get("company", ""),
            job.get("location", ""),
            _status_text(job.get("status", "")),
            _format_date(job.get("applied_date")),
            _format_date(job.get("response_date")),
            ats_str,
        )

    console.print(table)

    # --- Analytics Summary ---
    analytics = _compute_analytics(jobs)

    console.print()
    console.print("[bold underline]Application Analytics[/bold underline]")
    console.print(
        f"  Total tracked:   [white]{analytics['total']}[/white]\n"
        f"  Applied:         [blue]{analytics['applied']}[/blue]\n"
        f"  Responded:       [yellow]{analytics['responded']}[/yellow]"
        f"  ([yellow]{analytics['response_rate']:.1f}%[/yellow] response rate)\n"
        f"  Interviews:      [green]{analytics['interview']}[/green]"
        f"  ([green]{analytics['interview_rate']:.1f}%[/green] interview rate)\n"
        f"  Offers:          [bold green]{analytics['offer']}[/bold green]"
        f"  ([bold green]{analytics['offer_rate']:.1f}%[/bold green] offer rate)"
    )

    # Export if requested
    if export_path:
        _export_csv(jobs, export_path)
