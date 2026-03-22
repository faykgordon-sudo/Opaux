"""
translator.py -- Translate structured CV content via Claude.
"""

import json
import time
from typing import Any

from rich.console import Console

console = Console()

SUPPORTED_LANGS = [
    "de", "fr", "es", "pt", "nl", "it", "pl", "sv", "da", "no",
    "fi", "cs", "hu", "ro", "bg", "el", "tr", "ar", "zh", "ja", "ko",
]

LANGUAGE_NAMES = {
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
    "zh": "Chinese (Simplified)",
    "ja": "Japanese",
    "ko": "Korean",
}

LOCATION_TO_LANG = {
    # Country name fragments -> language code
    "germany": "de",
    "deutschland": "de",
    "austria": "de",
    "österreich": "de",
    "switzerland": "de",
    "schweiz": "de",
    "france": "fr",
    "paris": "fr",
    "lyon": "fr",
    "spain": "es",
    "españa": "es",
    "madrid": "es",
    "barcelona": "es",
    "portugal": "pt",
    "brasil": "pt",
    "brazil": "pt",
    "netherlands": "nl",
    "amsterdam": "nl",
    "holland": "nl",
    "italy": "it",
    "italia": "it",
    "rome": "it",
    "milan": "it",
    "poland": "pl",
    "warszawa": "pl",
    "warsaw": "pl",
    "sweden": "sv",
    "stockholm": "sv",
    "denmark": "da",
    "copenhagen": "da",
    "norway": "no",
    "oslo": "no",
    "finland": "fi",
    "helsinki": "fi",
    "czech": "cs",
    "prague": "cs",
    "hungary": "hu",
    "budapest": "hu",
    "romania": "ro",
    "bucharest": "ro",
    "turkey": "tr",
    "istanbul": "tr",
    "china": "zh",
    "beijing": "zh",
    "shanghai": "zh",
    "japan": "ja",
    "tokyo": "ja",
    "korea": "ko",
    "seoul": "ko",
}

TRANSLATION_PROMPT = """\
You are a professional CV translator and localization expert.

Translate the following structured CV content into {language_name}.

Important rules:
1. Preserve all proper nouns: company names, product names, certification names, technology names
2. Adapt section headers to the professional conventions of {language_name}-speaking countries
3. Maintain professional tone appropriate for {language_name}-speaking job markets
4. Do NOT translate: programming languages, software tools, acronyms (e.g. AWS, API, SQL, CI/CD)
5. Keep numbers, dates, percentages, and quantities in their original format
6. Return ONLY valid JSON matching the exact same structure as the input

Input content (JSON):
{content_json}

Return ONLY the translated JSON object with the same keys and structure.
"""


def _call_claude_for_translation(client: Any, language_name: str, content: dict, max_retries: int = 2) -> dict:
    """Call Claude to translate content and return parsed JSON."""
    content_json = json.dumps(content, ensure_ascii=False, indent=2)
    prompt = TRANSLATION_PROMPT.format(
        language_name=language_name,
        content_json=content_json,
    )

    last_error = None
    for attempt in range(max_retries):
        try:
            message = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            response_text = message.content[0].text.strip()

            # Strip markdown code fences
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                response_text = "\n".join(lines[1:])
                if response_text.strip().endswith("```"):
                    response_text = response_text.strip()[:-3].strip()

            return json.loads(response_text)

        except json.JSONDecodeError as exc:
            last_error = exc
            if attempt < max_retries - 1:
                console.print(f"[yellow]JSON parse error (attempt {attempt + 1}), retrying...[/yellow]")
                time.sleep(2)
        except Exception as exc:
            last_error = exc
            if attempt < max_retries - 1:
                console.print(f"[yellow]Claude API error (attempt {attempt + 1}): {exc}. Retrying...[/yellow]")
                time.sleep(2)

    raise RuntimeError(f"Translation failed after {max_retries} attempts: {last_error}")


def translate_content(config: dict, content: dict, target_lang: str) -> dict:
    """
    Translate structured content dict into target_lang using Claude.

    Args:
        config: App configuration dict with claude_api_key
        content: Structured content dict (from tailoring)
        target_lang: ISO 639-1 language code (e.g. 'de', 'fr')

    Returns:
        Translated content dict with the same structure as input
    """
    if target_lang == "en":
        return content  # No translation needed

    if target_lang not in SUPPORTED_LANGS:
        raise ValueError(
            f"Unsupported language '{target_lang}'. "
            f"Supported: {', '.join(SUPPORTED_LANGS)}"
        )

    try:
        import anthropic
    except ImportError:
        raise ImportError("anthropic package is required. Install with: pip install anthropic")

    api_key = config.get("claude_api_key", "")
    if not api_key or api_key == "YOUR_CLAUDE_API_KEY":
        raise ValueError("claude_api_key is not set in config/settings.yaml")

    client = anthropic.Anthropic(api_key=api_key)
    language_name = LANGUAGE_NAMES.get(target_lang, target_lang)

    console.print(f"[blue]Translating CV content to {language_name}...[/blue]")

    translated = _call_claude_for_translation(client, language_name, content)

    console.print(f"[green]Translation to {language_name} complete.[/green]")
    return translated


def infer_language_from_location(location: str) -> str:
    """
    Infer a language code from a job location string.
    Returns 'en' (English) as default if no match found.
    """
    if not location:
        return "en"

    location_lower = location.lower()
    for fragment, lang_code in LOCATION_TO_LANG.items():
        if fragment in location_lower:
            return lang_code

    return "en"
