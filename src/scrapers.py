"""
scrapers.py -- Additional job scrapers beyond python-jobspy.

Scrapers:
  - LinkedIn (guest API) -- no auth, HTML parsing via BeautifulSoup
  - Bundesagentur fuer Arbeit (BA) -- official German employment agency, free
  - Arbeitnow                      -- free feed, Germany-focused
  - Adzuna                         -- free tier, requires app_id + app_key
"""

import time
from datetime import datetime
from typing import Any

import requests
from rich.console import Console

try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False

from src.database import generate_job_id

console = Console()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _make_job(
    url: str,
    title: str,
    company: str,
    location: str,
    description: str,
    source: str,
    date_posted: str = "",
    job_type: str = "",
    is_remote: bool = False,
    salary_min: float | None = None,
    salary_max: float | None = None,
) -> dict:
    """Build a normalised job dict ready for DB insertion."""
    if not url:
        return {}
    return {
        "id": generate_job_id(url),
        "title": title,
        "company": company,
        "location": location,
        "url": url,
        "source": source,
        "description": description,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "job_type": job_type,
        "is_remote": is_remote or "remote" in location.lower(),
        "date_posted": date_posted,
        "date_discovered": datetime.utcnow().isoformat(),
        "status": "discovered",
    }


# ---------------------------------------------------------------------------
# Bundesagentur fuer Arbeit (BA)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# LinkedIn (guest API -- no auth required)
# ---------------------------------------------------------------------------

LI_SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
LI_JOB_URL    = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

# Experience level codes: 1=Intern, 2=Entry, 3=Associate, 4=Mid-Senior, 5=Director, 6=Executive
# Job type codes: F=Full-time, P=Part-time, C=Contract, T=Temporary, I=Internship
# Work model codes: 1=On-site, 2=Remote, 3=Hybrid
# Time posted: r86400=24h, r604800=1 week, r2592000=1 month


def _li_extract_job_id(url: str) -> str:
    """Extract LinkedIn numeric job ID from a full or partial job URL."""
    partial = url.split("?")[0]
    return partial.rstrip("/").split("-")[-1]


def _li_fetch_description(job_id: str, session: requests.Session) -> str:
    """Fetch the full job description from the LinkedIn job detail API."""
    if not _BS4_AVAILABLE:
        return ""
    try:
        resp = session.get(LI_JOB_URL.format(job_id=job_id), timeout=10)
        if resp.status_code != 200:
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        desc_el = soup.select_one("[class*=description] > section > div")
        return desc_el.get_text(separator="\n", strip=True) if desc_el else ""
    except Exception:
        return ""


def scrape_linkedin(
    search_term: str,
    location: str = "Germany",
    results_wanted: int = 50,
    experience_levels: list[int] | None = None,
    job_type: str = "F",
    work_model: int | None = None,
    posted_within: str = "r604800",
    fetch_descriptions: bool = True,
) -> list[dict]:
    """
    Scrape LinkedIn jobs via the public guest API (no login required).

    Args:
        search_term:       Keywords for the search.
        location:          Country or city name (English).
        results_wanted:    Max jobs to return.
        experience_levels: List of LinkedIn f_E codes (2=Entry, 3=Associate).
        job_type:          f_JT code: F=Full-time, C=Contract, etc.
        work_model:        f_WT code: 1=On-site, 2=Remote, 3=Hybrid. None = any.
        posted_within:     f_TPR value. Default = past week.
        fetch_descriptions: If True, fetches full description per job (slower).
    """
    if not _BS4_AVAILABLE:
        console.print("  [yellow]linkedin: beautifulsoup4 not installed, skipping[/yellow]")
        return []

    session = requests.Session()
    session.headers.update(HEADERS)

    params: dict[str, Any] = {
        "keywords": search_term,
        "location": location,
        "f_TPR": posted_within,
        "start": 0,
    }
    if experience_levels:
        params["f_E"] = ",".join(str(e) for e in experience_levels)
    if job_type:
        params["f_JT"] = job_type
    if work_model is not None:
        params["f_WT"] = work_model

    jobs: list[dict] = []
    start = 0

    while len(jobs) < results_wanted:
        params["start"] = start
        try:
            resp = session.get(LI_SEARCH_URL, params=params, timeout=15)
            if resp.status_code == 429:
                console.print("  [yellow]linkedin: rate limited, stopping[/yellow]")
                break
            if resp.status_code != 200:
                console.print(f"  [yellow]linkedin: HTTP {resp.status_code}[/yellow]")
                break
            if not resp.text.strip():
                break
        except Exception as exc:
            console.print(f"  [red]linkedin error:[/red] {exc}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select("li > div.base-card")
        if not cards:
            break

        for card in cards:
            title_el   = card.select_one("[class*=_title]") or card.select_one("[class*=-title]")
            url_el     = card.select_one("[class*=_full-link]") or card.select_one("a.base-card__full-link")
            company_el = card.select_one("[class*=_subtitle]") or card.select_one("[class*=-subtitle]")
            location_el= card.select_one("[class*=_location]") or card.select_one("[class*=-location]")
            time_el    = card.select_one("[class*=listdate]") or card.select_one("time")

            title   = title_el.get_text(strip=True)   if title_el   else ""
            url     = url_el.get("href", "").strip()   if url_el     else ""
            company = company_el.get_text(strip=True)  if company_el else ""
            loc     = location_el.get_text(strip=True) if location_el else ""
            posted  = time_el.get("datetime", time_el.get_text(strip=True)) if time_el else ""

            if not url or not title:
                continue

            # Clean URL -- remove tracking params
            url = url.split("?")[0]
            job_id = _li_extract_job_id(url)

            description = ""
            if fetch_descriptions and job_id:
                description = _li_fetch_description(job_id, session)
                time.sleep(0.4)   # polite delay

            job = _make_job(
                url=url,
                title=title,
                company=company,
                location=loc,
                description=description,
                source="linkedin",
                date_posted=posted,
                job_type=job_type,
            )
            if job:
                jobs.append(job)
                if len(jobs) >= results_wanted:
                    break

        start += 25
        time.sleep(1.0)   # polite delay between pages

    return jobs[:results_wanted]


BA_BASE = "https://api.arbeitsagentur.de/jobsuche/pc/v4/jobs"
BA_TOKEN_URL = "https://api.arbeitsagentur.de/oauth/token"
BA_CLIENT_ID = "c003a37f-024f-462a-b36d-b001be4cd24a"   # public test client
BA_CLIENT_SECRET = "32a39620-32b3-4307-8012-3e1f3e2e56f3"


def _ba_get_token() -> str | None:
    """Fetch a short-lived OAuth token from Bundesagentur fuer Arbeit."""
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        resp = requests.post(
            BA_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": BA_CLIENT_ID,
                "client_secret": BA_CLIENT_SECRET,
            },
            timeout=10,
            verify=False,
        )
        resp.raise_for_status()
        return resp.json().get("access_token")
    except Exception:
        return None


def scrape_bundesagentur(
    search_term: str,
    location: str = "Deutschland",
    results_wanted: int = 50,
) -> list[dict]:
    """
    Scrape jobs from Bundesagentur fuer Arbeit (German Federal Employment Agency).

    Free API, no sign-up needed. Uses public OAuth client credentials.
    Docs: https://api.arbeitsagentur.de/infosysbub/jsuche/ext/v2/swagger-ui
    """
    token = _ba_get_token()
    headers = {**HEADERS}
    if token:
        headers["OAuthAccessToken"] = token

    # BA uses "wo" (where) and "was" (what)
    # Strip "Germany" suffix if present since BA expects German city names
    wo = location.replace(", Germany", "").replace(",Germany", "").strip()
    if wo.lower() in ("germany", "deutschland", "remote"):
        wo = ""   # blank = all of Germany

    jobs: list[dict] = []
    page = 0
    page_size = min(results_wanted, 100)

    while len(jobs) < results_wanted:
        params: dict[str, Any] = {
            "was": search_term,
            "size": page_size,
            "page": page,
        }
        if wo:
            params["wo"] = wo

        try:
            resp = requests.get(BA_BASE, params=params, headers=headers, timeout=15, verify=False)
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "")
            if "html" in ct or resp.text.lstrip().startswith("<!"):
                console.print("  [yellow]bundesagentur: API under maintenance (Wartungsarbeiten)[/yellow]")
                break
            data = resp.json()
        except Exception as exc:
            console.print(f"  [red]BA API error (page {page}):[/red] {exc}")
            break

        stellenangebote = data.get("stellenangebote") or []
        if not stellenangebote:
            break

        for item in stellenangebote:
            ref_nr = item.get("refnr", "")
            url = f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{ref_nr}"
            title = item.get("titel", "")
            company = item.get("arbeitgeber", "")
            # Location can be nested
            arbeitsort = item.get("arbeitsort") or {}
            loc_parts = [
                arbeitsort.get("ort", ""),
                arbeitsort.get("land", "Deutschland"),
            ]
            loc = ", ".join(p for p in loc_parts if p)
            desc = item.get("stellenbeschreibung") or item.get("refnr", "")
            date_posted = item.get("aktuelleVeroeffentlichungsdatum", "")
            job_type = item.get("arbeitszeitmodelle", [""])[0] if item.get("arbeitszeitmodelle") else ""

            job = _make_job(
                url=url,
                title=title,
                company=company,
                location=loc,
                description=desc,
                source="bundesagentur",
                date_posted=date_posted,
                job_type=job_type,
            )
            if job:
                jobs.append(job)

        total = data.get("maxErgebnisse") or data.get("total") or 0
        page += 1
        if len(jobs) >= total or len(jobs) >= results_wanted:
            break
        time.sleep(0.5)

    return jobs[:results_wanted]


# ---------------------------------------------------------------------------
# Arbeitnow
# ---------------------------------------------------------------------------

ARBEITNOW_BASE = "https://www.arbeitnow.com/api/job-board-api"


def scrape_arbeitnow(
    search_term: str,
    location: str = "Germany",
    results_wanted: int = 50,
) -> list[dict]:
    """
    Scrape jobs from Arbeitnow -- free, no auth required.

    The API is a paginated feed. We filter by keyword and location client-side.
    Docs: https://arbeitnow.com/tools/job-board-api
    """
    keyword = search_term.lower()
    loc_filter = location.lower().replace(", germany", "").replace("germany", "").strip()

    # Domain-specific keywords only -- generic words like "manager"/"junior" excluded
    # to prevent every "X Manager" job from passing the filter.
    _LOGISTICS_KEYWORDS: list[str] = [
        "logistik", "logistics", "supply chain", "lieferkette",
        "spedition", "transport", "warehouse", "lager", "fulfillment",
        "versand", "beschaffung", "procurement", "einkauf", "disposition",
        "freight", "fracht", "distribution", "inventory", "bestand",
        "operations manager", "operations management",
        "import", "export", "zoll", "customs", "shipping",
    ]
    match_keywords = _LOGISTICS_KEYWORDS

    jobs: list[dict] = []
    page = 1
    max_pages = 25

    while len(jobs) < results_wanted and page <= max_pages:
        try:
            resp = requests.get(
                ARBEITNOW_BASE,
                params={"page": page},
                headers=HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            console.print(f"  [red]Arbeitnow error (page {page}):[/red] {exc}")
            break

        items = data.get("data", [])
        if not items:
            break

        for item in items:
            title = item.get("title", "")
            loc = item.get("location", "")
            tags = " ".join(item.get("tags", []))
            title_lower = title.lower()
            full_text = f"{title_lower} {tags.lower()}"

            # STRICT: title must contain at least one logistics/domain keyword
            title_match = any(kw in title_lower for kw in match_keywords)
            # Fallback: tags must contain at least two domain keywords
            tag_match = sum(1 for kw in match_keywords if kw in tags.lower()) >= 2
            kw_match = title_match or tag_match

            # Location filter
            loc_match = (
                not loc_filter
                or loc_filter in loc.lower()
                or "germany" in loc.lower()
                or "deutschland" in loc.lower()
                or item.get("remote", False)
            )

            if not (kw_match and loc_match):
                continue

            url = item.get("url", "")
            company = item.get("company_name", "")
            description = item.get("description", "")
            is_remote = bool(item.get("remote", False))
            date_posted = item.get("created_at", "")

            job = _make_job(
                url=url,
                title=title,
                company=company,
                location=loc,
                description=description,
                source="arbeitnow",
                date_posted=date_posted,
                is_remote=is_remote,
            )
            if job:
                jobs.append(job)
                if len(jobs) >= results_wanted:
                    break

        page += 1
        time.sleep(0.3)

    return jobs[:results_wanted]


# ---------------------------------------------------------------------------
# Adzuna
# ---------------------------------------------------------------------------

ADZUNA_BASE = "https://api.adzuna.com/v1/api/jobs/de/search"


def scrape_adzuna(
    search_term: str,
    location: str = "Germany",
    results_wanted: int = 50,
    app_id: str = "",
    app_key: str = "",
) -> list[dict]:
    """
    Scrape jobs from Adzuna Germany.

    Free tier: 250 req/month. Register at https://developer.adzuna.com
    Set adzuna_app_id and adzuna_app_key in config/settings.yaml.
    """
    if not app_id or not app_key:
        console.print("  [yellow]adzuna: skipped (no app_id/app_key in settings.yaml)[/yellow]")
        return []

    jobs: list[dict] = []
    page = 1
    page_size = min(results_wanted, 50)

    # Strip country suffix for Adzuna where param
    where = location.replace(", Germany", "").replace(",Germany", "").strip()
    if where.lower() in ("germany", "deutschland", "remote", ""):
        where = "Germany"

    while len(jobs) < results_wanted:
        params = {
            "app_id": app_id,
            "app_key": app_key,
            "results_per_page": page_size,
            "what": search_term,
            "where": where,
            "content-type": "application/json",
        }

        try:
            resp = requests.get(
                f"{ADZUNA_BASE}/{page}",
                params=params,
                headers=HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            console.print(f"  [red]Adzuna error (page {page}):[/red] {exc}")
            break

        results = data.get("results", [])
        if not results:
            break

        for item in results:
            url = item.get("redirect_url", "")
            title = item.get("title", "")
            company = (item.get("company") or {}).get("display_name", "")
            loc_data = item.get("location") or {}
            loc = loc_data.get("display_name", where)
            description = item.get("description", "")
            salary_min = item.get("salary_min")
            salary_max = item.get("salary_max")
            date_posted = item.get("created", "")[:10] if item.get("created") else ""
            job_type = item.get("contract_time", "")

            job = _make_job(
                url=url,
                title=title,
                company=company,
                location=loc,
                description=description,
                source="adzuna",
                date_posted=date_posted,
                job_type=job_type,
                salary_min=float(salary_min) if salary_min else None,
                salary_max=float(salary_max) if salary_max else None,
            )
            if job:
                jobs.append(job)
                if len(jobs) >= results_wanted:
                    break

        total = data.get("count", 0)
        page += 1
        if len(jobs) >= total or len(jobs) >= results_wanted:
            break
        time.sleep(0.5)

    return jobs[:results_wanted]
