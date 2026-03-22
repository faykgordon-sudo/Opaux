"""
scoring.py -- AI-powered job scoring via Claude.
"""

import json
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from src.database import get_connection, get_unscored_jobs, init_db, update_job

console = Console()

SCORING_PROMPT = """\
You are an expert recruiter and ATS specialist. Score this job for the candidate.

Candidate profile:
{profile_yaml}

Job Title: {title}
Company: {company}
Location: {location}
Job Description:
{description}

Respond ONLY in valid JSON with this exact structure:
{{
  "score": <float 1-10>,
  "reasoning": "<1-2 sentence explanation>",
  "key_matches": ["<matched skill or experience>", ...],
  "gaps": ["<missing requirement>", ...],
  "ats_keywords": ["<important ATS keyword from JD>", ...]
}}

Score criteria:
- 9-10: Exceptional match, candidate clearly exceeds requirements
- 7-8: Strong match, most requirements met
- 5-6: Moderate match, worth applying with tailored CV
- 3-4: Weak match, significant gaps
- 1-2: Poor match, not recommended
"""


def _load_profile(config: dict) -> str:
    """Load the profile YAML file and return it as a string."""
    profile_path = Path(config.get("_profile_path", "config/profile.yaml"))
    if not profile_path.exists():
        return "(Profile not found -- using empty profile)"
    with open(profile_path, encoding="utf-8") as f:
        return f.read()


def _call_claude(client: Any, prompt: str, max_retries: int = 2) -> str:
    """Call Claude API with retry on failure."""
    last_error = None
    for attempt in range(max_retries):
        try:
            message = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except Exception as exc:
            last_error = exc
            if attempt < max_retries - 1:
                console.print(f"[yellow]Claude API error (attempt {attempt + 1}): {exc}. Retrying...[/yellow]")
                time.sleep(2)
    raise RuntimeError(f"Claude API failed after {max_retries} attempts: {last_error}")


def _parse_score_response(response_text: str) -> dict:
    """Extract JSON from Claude's response text."""
    # Strip markdown code fences if present
    text = response_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if text.strip().endswith("```"):
            text = text.strip()[:-3].strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Attempt to find JSON object in text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(text[start:end])
        else:
            raise ValueError(f"Could not parse JSON from response: {text[:200]}")

    return data


def run_scoring(
    config: dict,
    db_path: str,
    limit: int = 50,
    min_score: float | None = None,
    rescore: bool = False,
) -> list[dict]:
    """
    Score unscored jobs using Claude. Returns the list of scored job dicts.

    If rescore=True, re-scores jobs that already have a score.
    """
    try:
        import anthropic
    except ImportError:
        raise ImportError(
            "anthropic package is required. Install with: pip install anthropic"
        )

    api_key = config.get("claude_api_key", "")
    if not api_key or api_key == "YOUR_CLAUDE_API_KEY":
        raise ValueError(
            "claude_api_key is not set in config/settings.yaml. "
            "Please add your Anthropic API key."
        )

    client = anthropic.Anthropic(api_key=api_key)
    profile_yaml = _load_profile(config)

    init_db(db_path)
    conn = get_connection(db_path)

    try:
        if rescore:
            from src.database import get_all_jobs as _get_all
            unscored = _get_all(conn)
        else:
            unscored = get_unscored_jobs(conn)
    finally:
        conn.close()

    if not unscored:
        msg = "No jobs to score." if rescore else "No unscored jobs found."
        console.print(f"[yellow]{msg}[/yellow]")
        return []

    # Respect the limit from config as well
    scoring_cfg = config.get("scoring", {})
    max_per_run = int(scoring_cfg.get("max_jobs_per_run", 50))
    jobs_to_score = unscored[: min(limit, max_per_run)]

    console.print(
        f"[bold blue]Scoring {len(jobs_to_score)} jobs with Claude...[/bold blue]"
    )

    scored_jobs: list[dict] = []

    for i, job in enumerate(jobs_to_score, 1):
        job_id = job["id"]
        title = job.get("title", "Unknown")
        company = job.get("company", "Unknown")
        console.print(f"  [{i}/{len(jobs_to_score)}] Scoring: {title} @ {company}...")

        description = job.get("description") or ""
        prompt = SCORING_PROMPT.format(
            profile_yaml=profile_yaml,
            title=title,
            company=company,
            location=job.get("location", ""),
            description=description[:4000],  # Limit context size
        )

        try:
            response_text = _call_claude(client, prompt)
            parsed = _parse_score_response(response_text)

            score = float(parsed.get("score", 0))
            reasoning = parsed.get("reasoning", "")
            key_matches = parsed.get("key_matches", [])
            ats_keywords = parsed.get("ats_keywords", [])

            keywords_str = json.dumps(key_matches + ats_keywords)

            conn = get_connection(db_path)
            try:
                update_job(
                    conn,
                    job_id,
                    score=score,
                    score_reasoning=reasoning,
                    keywords_matched=keywords_str,
                    status="scored",
                )
            finally:
                conn.close()

            job["score"] = score
            job["score_reasoning"] = reasoning
            job["keywords_matched"] = keywords_str
            job["status"] = "scored"
            scored_jobs.append(job)

        except Exception as exc:
            console.print(f"  [red]Error scoring job {job_id}: {exc}[/red]")
            continue

    # Sort by score descending
    scored_jobs.sort(key=lambda j: j.get("score") or 0, reverse=True)

    # Display results table
    if scored_jobs:
        table = Table(
            title=f"[bold]Scored Jobs ({len(scored_jobs)})[/bold]",
            show_lines=False,
        )
        table.add_column("Score", justify="center", style="bold", width=7)
        table.add_column("Title", style="white", max_width=40)
        table.add_column("Company", style="cyan", max_width=25)
        table.add_column("Location", max_width=20)
        table.add_column("Reasoning", max_width=50)

        for job in scored_jobs:
            score = job.get("score") or 0
            score_color = "green" if score >= 7 else "yellow" if score >= 5 else "red"
            score_str = f"[{score_color}]{score:.1f}[/{score_color}]"
            # Filter by min_score for display
            if min_score is not None and score < min_score:
                continue
            table.add_row(
                score_str,
                job.get("title", ""),
                job.get("company", ""),
                job.get("location", ""),
                (job.get("score_reasoning") or "")[:80],
            )

        console.print(table)

    console.print(
        f"[bold green]Scoring complete:[/bold green] {len(scored_jobs)} jobs scored."
    )
    return scored_jobs
