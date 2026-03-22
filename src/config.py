"""
config.py -- Pydantic config validation for Opaux.

Validates profile.yaml and settings.yaml on startup, converting raw dicts
into typed models with clear error messages.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError, field_validator
from rich.console import Console

console = Console(stderr=True)

# ---------------------------------------------------------------------------
# Settings (config/settings.yaml)
# ---------------------------------------------------------------------------

class DatabaseSettings(BaseModel):
    path: str = "data/jobs.db"


class ApplicationSettings(BaseModel):
    default_lang: str = "en"
    default_format: str = "american"

    @field_validator("default_format")
    @classmethod
    def check_format(cls, v: str) -> str:
        allowed = {"american", "german", "europass"}
        if v not in allowed:
            raise ValueError(f"default_format must be one of {allowed}, got '{v}'")
        return v


class SearchSettings(BaseModel):
    search_term: str = "Software Engineer"
    location: str = "Germany"
    results_wanted: int = 50
    sites: list[str] = ["indeed"]
    hours_old: int = 168
    country_indeed: str = "germany"


class ScoringSettings(BaseModel):
    min_score: float = 6.0
    max_jobs_per_run: int = 50

    @field_validator("min_score")
    @classmethod
    def check_min_score(cls, v: float) -> float:
        if not (1.0 <= v <= 10.0):
            raise ValueError(f"min_score must be between 1.0 and 10.0, got {v}")
        return v


class OutputSettings(BaseModel):
    dir: str = "output"


class Settings(BaseModel):
    claude_api_key: str
    database: DatabaseSettings = DatabaseSettings()
    application: ApplicationSettings = ApplicationSettings()
    search: SearchSettings = SearchSettings()
    scoring: ScoringSettings = ScoringSettings()
    output: OutputSettings = OutputSettings()

    @field_validator("claude_api_key")
    @classmethod
    def check_api_key(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("claude_api_key must not be empty")
        return v.strip()


# ---------------------------------------------------------------------------
# Profile (config/profile.yaml)
# ---------------------------------------------------------------------------

class PersonalInfo(BaseModel):
    name: str
    email: str
    phone: str = ""
    location: str = ""
    linkedin: str = ""


class ExperienceEntry(BaseModel):
    title: str
    company: str
    start: str
    end: str = "present"
    bullets: list[str] = []
    skills: list[str] = []


class LanguageEntry(BaseModel):
    language: str
    level: str = ""
    cefr: str = ""


class ProfileConfig(BaseModel):
    personal: PersonalInfo
    summary: str = ""
    experience: list[ExperienceEntry] = []
    skills: dict[str, list[str]] = {}
    certifications: list[Any] = []
    languages: list[LanguageEntry] = []


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _fmt_errors(errors: list[dict]) -> str:
    lines = []
    for i, err in enumerate(errors, 1):
        loc = " -> ".join(str(p) for p in err["loc"])
        lines.append(f"  {i}. [{loc}] {err['msg']}")
    return "\n".join(lines)


def validate_settings(raw: dict) -> Settings:
    """Validate settings dict; exit with numbered error list on failure."""
    try:
        return Settings(**raw)
    except ValidationError as exc:
        console.print("[bold red]Error:[/] settings.yaml validation failed:\n")
        console.print(_fmt_errors(exc.errors()))
        raise SystemExit(1)


def validate_profile(raw: dict) -> ProfileConfig:
    """Validate profile dict; exit with numbered error list on failure."""
    try:
        return ProfileConfig(**raw)
    except ValidationError as exc:
        console.print("[bold red]Error:[/] config/profile.yaml validation failed:\n")
        console.print(_fmt_errors(exc.errors()))
        raise SystemExit(1)


def load_and_validate_settings(
    config_path: str = "config/settings.yaml",
) -> dict:
    """
    Load settings.yaml, apply env-var overrides, validate, and return as dict.

    Environment variable overrides:
        ANTHROPIC_API_KEY   -> claude_api_key
        OPAUX_PROFILE       -> (path, not in settings dict)
        OPAUX_FORMAT        -> application.default_format
        OPAUX_LANG          -> application.default_lang
        OPAUX_DB            -> database.path
    """
    path = Path(config_path)
    if not path.exists():
        console.print(
            f"[bold red]Error:[/] Config not found at [cyan]{config_path}[/].\n"
            "Run: [bold]cp config/profile.example.yaml config/profile.yaml[/]"
        )
        raise SystemExit(1)

    with open(path, encoding="utf-8") as f:
        raw: dict = yaml.safe_load(f) or {}

    # Apply env-var overrides
    if api_key := os.environ.get("ANTHROPIC_API_KEY"):
        raw["claude_api_key"] = api_key

    raw.setdefault("application", {})
    if fmt := os.environ.get("OPAUX_FORMAT"):
        raw["application"]["default_format"] = fmt
    if lang := os.environ.get("OPAUX_LANG"):
        raw["application"]["default_lang"] = lang

    raw.setdefault("database", {})
    if db := os.environ.get("OPAUX_DB"):
        raw["database"]["path"] = db

    if not raw.get("claude_api_key"):
        console.print(
            "[bold red]Error:[/] Claude API key not set.\n"
            "Add it to [cyan]config/settings.yaml[/] or set [bold]ANTHROPIC_API_KEY[/] env var."
        )
        raise SystemExit(1)

    validated = validate_settings(raw)
    return validated.model_dump()
