"""
applicator.py -- Automated job application form filler using Playwright.
"""

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console

from src.database import get_connection, get_job_by_id, init_db, update_job

console = Console()


def _check_playwright() -> None:
    """Raise a helpful error if playwright is not installed."""
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except ImportError:
        raise ImportError(
            "playwright is required for automated applications.\n"
            "Install with:  pip install playwright\n"
            "Then run:      playwright install chromium"
        )


def _load_profile() -> dict:
    """Load the user profile from config/profile.yaml."""
    profile_path = Path("config/profile.yaml")
    if not profile_path.exists():
        return {}
    with open(profile_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _log_action(dry_run: bool, message: str) -> None:
    """Log an action with timestamp."""
    ts = datetime.utcnow().strftime("%H:%M:%S")
    prefix = "[DRY-RUN]" if dry_run else "[ACTION]"
    color = "dim" if dry_run else "cyan"
    console.print(f"[{color}]{prefix} {ts} -- {message}[/{color}]")


# Common form field selectors and their mapping to profile fields
FIELD_PATTERNS = [
    # (list of CSS selectors to try, profile key path, field label)
    (
        ['input[name*="first_name"]', 'input[id*="first_name"]', 'input[placeholder*="First"]'],
        ["personal", "first_name"],
        "First Name",
    ),
    (
        ['input[name*="last_name"]', 'input[id*="last_name"]', 'input[placeholder*="Last"]'],
        ["personal", "last_name"],
        "Last Name",
    ),
    (
        ['input[name*="full_name"]', 'input[id*="name"]', 'input[placeholder*="Full name"]',
         'input[placeholder*="Your name"]'],
        ["personal", "name"],
        "Full Name",
    ),
    (
        ['input[type="email"]', 'input[name*="email"]', 'input[id*="email"]'],
        ["personal", "email"],
        "Email",
    ),
    (
        ['input[type="tel"]', 'input[name*="phone"]', 'input[id*="phone"]',
         'input[placeholder*="Phone"]'],
        ["personal", "phone"],
        "Phone",
    ),
    (
        ['input[name*="location"]', 'input[id*="location"]', 'input[placeholder*="Location"]',
         'input[placeholder*="City"]'],
        ["personal", "location"],
        "Location",
    ),
    (
        ['input[name*="linkedin"]', 'input[id*="linkedin"]', 'input[placeholder*="LinkedIn"]'],
        ["personal", "linkedin"],
        "LinkedIn",
    ),
]


def _get_nested(data: dict, keys: list) -> str:
    """Safely get a nested value from a dict using a list of keys."""
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key, "")
    return str(current) if current else ""


def _split_name(full_name: str) -> tuple[str, str]:
    """Split a full name into first and last."""
    parts = full_name.strip().split(" ", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return parts[0], ""


async def _fill_application_async(
    job: dict,
    profile: dict,
    dry_run: bool,
) -> None:
    """Async Playwright implementation for filling job application forms."""
    from playwright.async_api import async_playwright

    url = job.get("url", "")
    if not url:
        raise ValueError("Job has no URL to apply to.")

    # Prepare field values
    personal = profile.get("personal", {})
    full_name = personal.get("name", "")
    first_name, last_name = _split_name(full_name)
    # Inject computed first/last name into personal for lookup
    personal_extended = {
        **personal,
        "first_name": first_name,
        "last_name": last_name,
    }
    profile_with_computed = {**profile, "personal": personal_extended}

    cv_path = job.get("cv_path", "")
    cover_letter_path = job.get("cover_letter_path", "")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not dry_run)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            _log_action(dry_run, f"Navigating to: {url}")

            if not dry_run:
                await page.goto(url, wait_until="networkidle", timeout=30000)
            else:
                console.print(f"  [dim]Would navigate to: {url}[/dim]")
                return  # In pure dry-run mode, just log and return

            await page.wait_for_load_state("domcontentloaded")
            _log_action(dry_run, "Page loaded successfully")

            # Fill text fields
            for selectors, profile_path, label in FIELD_PATTERNS:
                value = _get_nested(profile_with_computed, profile_path)
                if not value:
                    continue

                filled = False
                for selector in selectors:
                    try:
                        element = await page.query_selector(selector)
                        if element and await element.is_visible():
                            if dry_run:
                                _log_action(dry_run, f"Would fill '{label}' ({selector}): {value}")
                            else:
                                await element.fill(value)
                                _log_action(dry_run, f"Filled '{label}': {value}")
                            filled = True
                            break
                    except Exception:
                        continue

                if not filled:
                    _log_action(dry_run, f"Could not find field for '{label}' -- may need manual entry")

            # Handle file uploads
            for file_path, field_names, label in [
                (cv_path, ["resume", "cv", "upload_resume", "file"], "CV/Resume"),
                (cover_letter_path, ["cover_letter", "cover_letter_upload", "coverletter"], "Cover Letter"),
            ]:
                if not file_path or not os.path.exists(file_path):
                    if file_path:
                        _log_action(dry_run, f"Warning: {label} file not found at {file_path}")
                    continue

                upload_selectors = []
                for field_name in field_names:
                    upload_selectors.extend([
                        f'input[type="file"][name*="{field_name}"]',
                        f'input[type="file"][id*="{field_name}"]',
                        f'input[type="file"][accept*=".docx"]',
                        f'input[type="file"][accept*=".pdf"]',
                        'input[type="file"]',
                    ])

                for selector in upload_selectors:
                    try:
                        element = await page.query_selector(selector)
                        if element and await element.is_visible():
                            if dry_run:
                                _log_action(dry_run, f"Would upload {label}: {file_path}")
                            else:
                                await element.set_input_files(file_path)
                                _log_action(dry_run, f"Uploaded {label}: {file_path}")
                            break
                    except Exception:
                        continue

            # Look for submit button
            submit_selectors = [
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("Submit")',
                'button:has-text("Apply")',
                'button:has-text("Send application")',
                '[data-testid*="submit"]',
            ]

            submit_found = False
            for selector in submit_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element and await element.is_visible():
                        if dry_run:
                            _log_action(dry_run, f"Would click submit button: {selector}")
                        else:
                            _log_action(False, f"Clicking submit: {selector}")
                            await element.click()
                            await page.wait_for_load_state("networkidle", timeout=15000)
                            _log_action(False, "Application submitted -- waiting for confirmation page")
                        submit_found = True
                        break
                except Exception:
                    continue

            if not submit_found:
                _log_action(dry_run, "Submit button not found automatically -- manual submission required")

            if not dry_run:
                # Take a screenshot as proof of submission
                screenshot_path = f"output/{job['id']}/submission_screenshot.png"
                os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
                await page.screenshot(path=screenshot_path, full_page=False)
                _log_action(False, f"Screenshot saved: {screenshot_path}")

        finally:
            await context.close()
            await browser.close()


def run_application(
    config: dict,
    db_path: str,
    job_id: str,
    dry_run: bool = False,
) -> None:
    """
    Attempt to automatically fill and submit a job application using Playwright.

    Args:
        config: App configuration dict
        db_path: Path to the SQLite database
        job_id: The job ID to apply for
        dry_run: If True, navigate and log but do not submit
    """
    _check_playwright()

    init_db(db_path)
    conn = get_connection(db_path)
    try:
        job = get_job_by_id(conn, job_id)
    finally:
        conn.close()

    if not job:
        raise ValueError(f"Job '{job_id}' not found in database.")

    title = job.get("title", "Unknown")
    company = job.get("company", "Unknown")
    url = job.get("url", "")

    if not url:
        raise ValueError(f"Job '{job_id}' has no URL.")

    mode_label = "[DRY RUN]" if dry_run else ""
    console.print(
        f"[bold blue]Applying to:[/bold blue] {title} @ {company} {mode_label}"
    )
    console.print(f"  URL: {url}")
    if job.get("cv_path"):
        console.print(f"  CV: {job['cv_path']}")
    if job.get("cover_letter_path"):
        console.print(f"  Cover Letter: {job['cover_letter_path']}")

    profile = _load_profile()

    # Run async playwright
    asyncio.run(_fill_application_async(job, profile, dry_run))

    # Update DB if not dry run
    if not dry_run:
        conn = get_connection(db_path)
        try:
            update_job(
                conn,
                job_id,
                status="applied",
                applied_date=datetime.utcnow().isoformat(),
            )
        finally:
            conn.close()
        console.print(f"[bold green]Application submitted![/bold green] Status updated to 'applied'.")
    else:
        console.print(
            "[yellow]Dry run complete.[/yellow] No form was submitted. "
            "Review the log above and run without --dry-run to submit."
        )
