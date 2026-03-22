"""
tailoring.py -- CV tailoring orchestrator using Claude.
"""

import json
import os
import time
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console

from src.cv_formats import load_format
from src.database import get_connection, get_job_by_id, init_db, update_job
from src.docx_builder import build_cv

console = Console()

KEYWORD_EXTRACTION_PROMPT = """\
You are an ATS (Applicant Tracking System) specialist. Extract and categorise ALL keywords \
from this job description.

Job Title: {title}
Company: {company}
Job Description:
{description}

Respond ONLY in valid JSON:
{{
  "must_have": ["<hard requirement: tools, skills, certs, qualifications>", ...],
  "nice_to_have": ["<preferred but not required>", ...],
  "company_specific": ["<values, culture, domain terms>", ...],
  "action_verbs": ["<verbs used in JD that describe the role>", ...]
}}

Rules:
- Extract 15-25 total keywords
- Use the EXACT phrasing from the JD (e.g. "SAP S/4HANA" not "SAP")
- Include both acronym and full form where both appear (e.g. "KPI" and "Key Performance Indicators")
- Prioritise hard skills, tools, certifications, and quantifiable requirements
"""

TAILORING_PROMPT = """\
You are a senior CV writer and ATS specialist. Your task is to present this candidate \
as the strongest possible match for the role. The candidate IS qualified -- your job is \
to make the CV prove it.

Mindset: Every piece of experience in the profile is raw material. Your job is to \
reframe, reword, and reorganise it using the job description's language so it reads \
like the candidate was practically doing this job already.

Candidate Profile (YAML):
{profile_yaml}

Job Title: {title}
Company: {company}

MUST-HAVE keywords (use EXACT phrasing):
{must_have}

NICE-TO-HAVE keywords:
{nice_to_have}

COMPANY-SPECIFIC terms:
{company_specific}

Full Job Description:
{description}

RULES -- follow all without exception:
1. REFRAME aggressively: take every profile bullet and rewrite it using JD language. \
If the profile says "tracked deliveries", and the JD says "end-to-end supply chain visibility", \
rewrite it as "Ensured end-to-end supply chain visibility by tracking delivery timelines..."
2. KEYWORD PLACEMENT: every must-have keyword must appear in at least 2 of: summary, \
skills section, experience bullets
3. EXACT PHRASING: copy keywords verbatim from the list above -- never paraphrase
4. SUMMARY: pack 4-5 must-have keywords into the 2-3 sentence summary naturally
5. BULLETS: each bullet = [Strong JD action verb] + [keyword-rich activity] + \
[quantified result] + [business impact]. Minimum 4 bullets per role.
6. TRANSFERABLE SKILLS: if a must-have keyword has no direct match, find the closest \
transferable skill in the profile and bridge it explicitly \
(e.g. "procurement coordination" bridges to "category management")
7. SKILLS SECTION: list ALL must-have and nice-to-have keywords the candidate can \
reasonably claim, using exact JD phrasing
8. NEVER invent roles, companies, dates, or degrees not in the profile
9. TARGET: estimated_ats_score >= 0.90. Push hard. A well-reframed transferable \
experience counts as a keyword match.

Respond ONLY in valid JSON:
{{
  "must_have": ["<keyword>", ...],
  "nice_to_have": ["<keyword>", ...],
  "company_specific": ["<keyword>", ...],
  "keyword_mapping": {{
    "<exact keyword>": "<how candidate's real experience maps to this -- be specific>"
  }},
  "keyword_gaps": ["<keywords with zero transferable evidence -- be honest>"],
  "tailored_summary": "<2-3 sentences with 4-5 must-have keywords woven in naturally>",
  "tailored_bullets": {{
    "<job_title_from_profile>": [
      "<Strong JD verb + keyword + quantified result + impact>",
      ...
    ]
  }},
  "skills_to_highlight": ["<exact keyword phrasing from JD>", ...],
  "ats_keyword_coverage": {{
    "covered": ["<keywords present in the tailored CV>"],
    "missing": ["<keywords genuinely unrepresentable>"]
  }},
  "estimated_ats_score": <float 0.0-1.0>
}}
"""

REFINEMENT_PROMPT = """\
Your previous tailoring scored {current_score:.0%}. The target is 90%+. Push harder.

The candidate IS qualified. Your job is to find the bridge between their experience \
and every missing keyword -- not to conclude it cannot be done.

Current tailored output:
{current_json}

Keywords still missing or under-represented:
{missing_keywords}

Candidate Profile (YAML):
{profile_yaml}

For each missing keyword:
1. Look at every bullet in the profile -- is there ANY activity that could be reframed \
   using this keyword? If yes, rewrite that bullet to include it
2. If it is a tool (e.g. "SAP MM"), and the candidate used SAP broadly, include it in \
   skills and note "SAP including MM module exposure" in the bullet
3. If it is a soft skill or methodology (e.g. "cross-functional collaboration", \
   "stakeholder management"), find any bullet involving multiple teams or departments \
   and rewrite it using that exact phrase
4. Only mark a keyword as truly missing if there is ZERO transferable evidence in the profile

Rewrite tailored_summary, tailored_bullets, and skills_to_highlight to maximise coverage. \
Use EXACT keyword phrasing. Respond ONLY in valid JSON using the same structure as before.
"""


def _load_profile(config: dict) -> tuple[str, dict]:
    """Load profile YAML. Returns (yaml_str, parsed_dict)."""
    profile_path = Path(config.get("_profile_path", "config/profile.yaml"))
    if not profile_path.exists():
        return "(Profile not found)", {}
    with open(profile_path, encoding="utf-8") as f:
        raw = f.read()
    parsed = yaml.safe_load(raw) or {}
    return raw, parsed


def _call_claude(client: Any, prompt: str, max_retries: int = 2) -> str:
    """Call Claude API with retry on failure."""
    last_error = None
    for attempt in range(max_retries):
        try:
            message = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except Exception as exc:
            last_error = exc
            if attempt < max_retries - 1:
                console.print(
                    f"[yellow]Claude API error (attempt {attempt + 1}): {exc}. Retrying...[/yellow]"
                )
                time.sleep(2)
    raise RuntimeError(f"Claude API failed after {max_retries} attempts: {last_error}")


def _parse_json_response(response_text: str) -> dict:
    """Extract and parse JSON from Claude's response."""
    text = response_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if text.strip().endswith("```"):
            text = text.strip()[:-3].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        raise ValueError(f"Could not parse JSON from response: {text[:300]}")


def run_tailoring(
    config: dict,
    db_path: str,
    job_id: str,
    format: str = "american",
    lang: str = "en",
    output_dir: str | None = None,
) -> str:
    """
    Tailor a CV for a specific job and generate a .docx file.

    Args:
        config: App configuration dict
        db_path: Path to the SQLite database
        job_id: The job ID to tailor for
        format: CV format ('american', 'german', 'europass')
        lang: Target language code (default 'en')

    Returns:
        Path to the generated .docx file
    """
    try:
        import anthropic
    except ImportError:
        raise ImportError("anthropic package is required. Install with: pip install anthropic")

    api_key = config.get("claude_api_key", "")
    if not api_key or api_key == "YOUR_CLAUDE_API_KEY":
        raise ValueError("claude_api_key is not set in config/settings.yaml")

    client = anthropic.Anthropic(api_key=api_key)

    # Load job from DB
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
    description = job.get("description") or ""

    console.print(f"[bold blue]Tailoring CV for:[/bold blue] {title} @ {company}")
    console.print(f"  Format: {format}  |  Language: {lang}")

    # Load profile
    profile_yaml, profile_dict = _load_profile(config)

    # Load format config
    format_config = load_format(format)

    # --- Pass 1: Extract keywords from JD ---
    console.print("[blue]Pass 1/2 -- Extracting keywords from job description...[/blue]")
    kw_prompt = KEYWORD_EXTRACTION_PROMPT.format(
        title=title,
        company=company,
        description=description[:5000],
    )
    kw_response = _call_claude(client, kw_prompt)
    kw_data = _parse_json_response(kw_response)

    must_have    = kw_data.get("must_have", [])
    nice_to_have = kw_data.get("nice_to_have", [])
    company_specific = kw_data.get("company_specific", [])

    console.print(
        f"  Found [green]{len(must_have)}[/green] must-have, "
        f"[yellow]{len(nice_to_have)}[/yellow] nice-to-have keywords."
    )

    # --- Pass 2: Tailor CV targeting 90%+ ATS ---
    console.print("[blue]Pass 2/2 -- Tailoring CV for 90%+ ATS coverage...[/blue]")
    prompt = TAILORING_PROMPT.format(
        profile_yaml=profile_yaml,
        title=title,
        company=company,
        must_have="\n".join(f"- {k}" for k in must_have),
        nice_to_have="\n".join(f"- {k}" for k in nice_to_have),
        company_specific="\n".join(f"- {k}" for k in company_specific),
        description=description[:4000],
    )
    response_text = _call_claude(client, prompt)
    tailoring_data = _parse_json_response(response_text)

    ats_score = float(tailoring_data.get("estimated_ats_score", 0.0))
    console.print(f"  ATS estimate after pass 2: [bold]{ats_score:.0%}[/bold]")

    # --- Pass 3 (optional): Refinement if score < 90% ---
    if ats_score < 0.90:
        missing = tailoring_data.get("ats_keyword_coverage", {}).get("missing", [])
        covered = tailoring_data.get("ats_keyword_coverage", {}).get("covered", [])
        # Also check must-haves not in keyword_mapping
        mapped = set(tailoring_data.get("keyword_mapping", {}).keys())
        uncovered_must = [k for k in must_have if k not in mapped and k not in covered]
        all_missing = list(set(missing + uncovered_must))

        if all_missing:
            console.print(
                f"[yellow]Score below 90% -- running refinement pass "
                f"({len(all_missing)} keywords to improve)...[/yellow]"
            )
            refine_prompt = REFINEMENT_PROMPT.format(
                current_score=ats_score,
                current_json=json.dumps(tailoring_data, indent=2)[:3000],
                missing_keywords="\n".join(f"- {k}" for k in all_missing),
                profile_yaml=profile_yaml,
            )
            refined_text = _call_claude(client, refine_prompt)
            try:
                refined_data = _parse_json_response(refined_text)
                new_score = float(refined_data.get("estimated_ats_score", ats_score))
                if new_score >= ats_score:
                    tailoring_data = refined_data
                    ats_score = new_score
                    console.print(f"  ATS after refinement: [bold green]{ats_score:.0%}[/bold green]")
                else:
                    console.print("  [yellow]Refinement did not improve score -- keeping original.[/yellow]")
            except Exception:
                pass  # Keep original if refinement fails to parse

    # Build full content dict for docx builder
    tailored_content = {
        **tailoring_data,
        "profile": profile_dict,
    }

    # Translate if needed
    if lang != "en":
        from src.translator import translate_content
        # Translate only the tailored parts, not the raw profile bullets
        translatable = {
            k: v for k, v in tailoring_data.items()
            if k in ("tailored_summary", "tailored_bullets", "skills_to_highlight",
                     "must_have", "nice_to_have")
        }
        translated = translate_content(config, translatable, lang)
        tailored_content.update(translated)

    # Determine output path using standardised naming convention
    from src.utils import cv_filename
    base_dir = output_dir or os.path.join(config.get("output", {}).get("dir", "output"), "cvs")
    os.makedirs(base_dir, exist_ok=True)
    fname = cv_filename(company, title, format, lang) + ".docx"
    output_path = os.path.join(base_dir, fname)

    # Build the .docx
    final_path = build_cv(
        tailored_content=tailored_content,
        format_config=format_config,
        format_name=format,
        language=lang,
        output_path=output_path,
    )

    # Update DB (ats_score already computed above)
    conn = get_connection(db_path)
    try:
        update_job(
            conn,
            job_id,
            cv_path=final_path,
            ats_score=ats_score,
            cv_lang=lang,
            status="tailored",
        )
    finally:
        conn.close()

    console.print(
        f"[bold green]Tailoring complete![/bold green] "
        f"ATS score estimate: {ats_score:.0%}  |  CV: {final_path}"
    )
    return final_path
