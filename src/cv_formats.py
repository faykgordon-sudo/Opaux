"""
cv_formats.py -- Load and parse CV format configuration from YAML templates.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class FormatConfig:
    name: str
    rules: dict = field(default_factory=dict)
    section_order: list[str] = field(default_factory=list)
    headers: dict = field(default_factory=dict)
    extras: dict = field(default_factory=dict)


VALID_FORMATS = {"american", "german", "europass"}


def load_format(format_name: str) -> dict:
    """
    Load the CV format config from templates/cv_{format_name}.yaml.
    Returns the raw dict from the YAML file.
    Raises ValueError if the format name is not recognized.
    Raises FileNotFoundError if the template file is missing.
    """
    if format_name not in VALID_FORMATS:
        raise ValueError(
            f"Unknown CV format '{format_name}'. "
            f"Valid formats: {', '.join(sorted(VALID_FORMATS))}"
        )

    template_path = Path("templates") / f"cv_{format_name}.yaml"
    if not template_path.exists():
        raise FileNotFoundError(
            f"CV template not found: {template_path}\n"
            f"Ensure the templates/ directory contains cv_{format_name}.yaml"
        )

    with open(template_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Invalid template format in {template_path}")

    return data


def load_format_config(format_name: str) -> FormatConfig:
    """
    Load the CV format config and return a typed FormatConfig dataclass.
    """
    data = load_format(format_name)

    known_keys = {"name", "rules", "section_order", "headers"}
    extras = {k: v for k, v in data.items() if k not in known_keys}

    return FormatConfig(
        name=data.get("name", format_name),
        rules=data.get("rules", {}),
        section_order=data.get("section_order", []),
        headers=data.get("headers", {}),
        extras=extras,
    )
