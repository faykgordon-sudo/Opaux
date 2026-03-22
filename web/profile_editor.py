"""
web/profile_editor.py -- Profile editor blueprint for Opaux.
"""

import logging

import yaml
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from web.auth import user_profile_path

log = logging.getLogger(__name__)

profile_bp = Blueprint("profile", __name__)

_MAX_ENTRIES = 20  # maximum indexed entries to scan for exp_N_*, edu_N_*, etc.


# ---------------------------------------------------------------------------
# Helper: parse indexed form entries
# ---------------------------------------------------------------------------

def _collect_indexed(form, prefix: str, fields: list[str]) -> list[dict]:
    """
    Collect all entries for a given prefix (e.g. "exp") and list of field
    suffixes (e.g. ["title", "company", ...]) up to _MAX_ENTRIES.
    Returns only non-empty entries (at least one field has a value).
    """
    entries = []
    for n in range(_MAX_ENTRIES):
        entry = {}
        for field in fields:
            key = f"{prefix}_{n}_{field}"
            value = form.get(key, "").strip()
            if value:
                entry[field] = value
        if entry:
            entries.append(entry)
    return entries


def _split_lines(text: str) -> list[str]:
    """Split a textarea value by newlines, stripping blank lines."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def _split_csv(text: str) -> list[str]:
    """Split a comma-separated textarea value, stripping blank items."""
    return [item.strip() for item in text.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@profile_bp.route("/profile", methods=["GET"])
@login_required
def profile_editor():
    profile_path = user_profile_path(current_user.id)
    profile = {}
    if profile_path.exists():
        try:
            with open(profile_path, "r", encoding="utf-8") as f:
                profile = yaml.safe_load(f) or {}
        except Exception as exc:
            log.warning("Could not load profile for user %s: %s", current_user.id, exc)
    return render_template("profile_editor.html", profile=profile)


@profile_bp.route("/profile", methods=["POST"])
@login_required
def profile_editor_save():
    form = request.form

    # ── Personal ─────────────────────────────────────────────────────────────
    personal_fields = [
        "name", "email", "phone", "location", "linkedin", "github",
        "date_of_birth", "nationality", "marital_status",
    ]
    personal = {}
    for field in personal_fields:
        value = form.get(f"personal_{field}", "").strip()
        if value:
            personal[field] = value

    # ── Summary ───────────────────────────────────────────────────────────────
    summary = form.get("summary", "").strip()

    # ── Experience ────────────────────────────────────────────────────────────
    experience = []
    for n in range(_MAX_ENTRIES):
        title = form.get(f"exp_{n}_title", "").strip()
        company = form.get(f"exp_{n}_company", "").strip()
        if not title and not company:
            continue
        entry = {}
        if title:
            entry["title"] = title
        if company:
            entry["company"] = company
        location = form.get(f"exp_{n}_location", "").strip()
        if location:
            entry["location"] = location
        start = form.get(f"exp_{n}_start", "").strip()
        if start:
            entry["start"] = start
        end = form.get(f"exp_{n}_end", "").strip()
        if end:
            entry["end"] = end
        bullets_raw = form.get(f"exp_{n}_bullets", "")
        bullets = _split_lines(bullets_raw)
        if bullets:
            entry["bullets"] = bullets
        experience.append(entry)

    # ── Education ────────────────────────────────────────────────────────────
    education = []
    for n in range(_MAX_ENTRIES):
        degree = form.get(f"edu_{n}_degree", "").strip()
        institution = form.get(f"edu_{n}_institution", "").strip()
        if not degree and not institution:
            continue
        entry = {}
        if degree:
            entry["degree"] = degree
        if institution:
            entry["institution"] = institution
        location = form.get(f"edu_{n}_location", "").strip()
        if location:
            entry["location"] = location
        year = form.get(f"edu_{n}_year", "").strip()
        if year:
            entry["year"] = year
        grade = form.get(f"edu_{n}_grade", "").strip()
        if grade:
            entry["grade"] = grade
        education.append(entry)

    # ── Skills ───────────────────────────────────────────────────────────────
    tools_raw = form.get("skills_tools", "")
    soft_raw = form.get("skills_soft", "")
    skills = {}
    tools = _split_csv(tools_raw)
    soft = _split_csv(soft_raw)
    if tools:
        skills["tools"] = tools
    if soft:
        skills["soft"] = soft

    # ── Languages ────────────────────────────────────────────────────────────
    languages = []
    for n in range(_MAX_ENTRIES):
        language = form.get(f"lang_{n}_language", "").strip()
        if not language:
            continue
        entry = {"language": language}
        level = form.get(f"lang_{n}_level", "").strip()
        if level:
            entry["level"] = level
        cefr = form.get(f"lang_{n}_cefr", "").strip()
        if cefr:
            entry["cefr"] = cefr
        languages.append(entry)

    # ── Assemble profile ─────────────────────────────────────────────────────
    profile = {}
    if personal:
        profile["personal"] = personal
    if summary:
        profile["summary"] = summary
    if experience:
        profile["experience"] = experience
    if education:
        profile["education"] = education
    if skills:
        profile["skills"] = skills
    if languages:
        profile["languages"] = languages

    # ── Save ─────────────────────────────────────────────────────────────────
    profile_path = user_profile_path(current_user.id)
    try:
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        with open(profile_path, "w", encoding="utf-8") as f:
            yaml.dump(profile, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        flash("Profile updated.", "success")
    except Exception as exc:
        log.error("Failed to save profile for user %s: %s", current_user.id, exc)
        flash("Failed to save profile. Please try again.", "error")

    return redirect(url_for("profile.profile_editor"))
