"""
utils.py -- Shared utilities for Opaux.
"""

import re
from pathlib import Path


def sanitize_filename(text: str) -> str:
    """
    Convert an arbitrary string into a safe, lowercase filename component.

    - Lowercases
    - Replaces spaces and underscores with hyphens
    - Removes characters that are not alphanumeric or hyphens
    - Collapses multiple consecutive hyphens
    - Strips leading/trailing hyphens
    """
    s = text.lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^\w-]", "", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


def cv_filename(company: str, title: str, cv_format: str, lang: str) -> str:
    """
    Return the standard CV filename (without .docx extension).

    Pattern: {company}_{title}_{format}_{lang}
    Example: ecosia_senior-pm_german_de
    """
    return "_".join([
        sanitize_filename(company or "unknown"),
        sanitize_filename(title or "cv"),
        sanitize_filename(cv_format or "american"),
        sanitize_filename(lang or "en"),
    ])


def cover_filename(company: str, title: str, lang: str) -> str:
    """
    Return the standard cover-letter filename (without .docx extension).

    Pattern: {company}_{title}_{lang}
    Example: ecosia_senior-pm_de
    """
    return "_".join([
        sanitize_filename(company or "unknown"),
        sanitize_filename(title or "cover"),
        sanitize_filename(lang or "en"),
    ])
