#!/usr/bin/env python3
"""
Job Fetcher — Phase 1 of the autonomous resume-tailoring pipeline.

Pulls Product Manager and Scrum Master job openings from:
  1. Fantastic.Jobs (via RapidAPI) — ATS / career site aggregator
  2. JSearch (via RapidAPI) — aggregates Google for Jobs
  3. Remotive — free, no auth, remote roles

Deduplicates, filters, saves new JDs as markdown, and tracks state.

Usage:
    python fetcher.py                # normal run
    python fetcher.py --dry-run      # preview without saving
    python fetcher.py --source remotive   # single source only
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from html import unescape

import requests
import yaml


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(os.environ["PIPELINE_WORKDIR"]) if "PIPELINE_WORKDIR" in os.environ else Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# State management — tracks which jobs we've already seen
# ---------------------------------------------------------------------------
def load_state(path: Path) -> dict:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("seen_jobs", {})
        return data
    return {"seen_jobs": {}}


def save_state(state: dict, path: Path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def job_fingerprint(title: str, company: str) -> str:
    """Create a stable ID from title + company to deduplicate across sources."""
    normalized = f"{title.lower().strip()}|{company.lower().strip()}"
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def strip_html(html_text: str) -> str:
    """Convert HTML to plain text (good enough for JD content)."""
    if not html_text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", html_text, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>", "\n- ", text, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<h[1-6][^>]*>", "\n## ", text, flags=re.IGNORECASE)
    text = re.sub(r"</h[1-6]>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    # collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def sanitize_filename(name: str) -> str:
    """Make a string safe for use as a filename."""
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = re.sub(r"\s+", "_", name.strip())
    return name[:80]


PROVINCE_ABBREVIATIONS = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba",
    "NB": "New Brunswick", "NL": "Newfoundland and Labrador",
    "NT": "Northwest Territories", "NS": "Nova Scotia", "NU": "Nunavut",
    "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec",
    "SK": "Saskatchewan", "YT": "Yukon",
}

PROVINCE_REGIONAL_ALIASES = {
    "GTA": "Ontario", "Greater Toronto": "Ontario", "Toronto": "Ontario",
    "Ottawa": "Ontario", "Mississauga": "Ontario", "Hamilton": "Ontario",
    "Kitchener": "Ontario", "Waterloo": "Ontario", "London, ON": "Ontario",
    "Greater Vancouver": "British Columbia", "Vancouver": "British Columbia",
    "Victoria": "British Columbia", "Surrey": "British Columbia",
    "Calgary": "Alberta", "Edmonton": "Alberta",
    "Montreal": "Quebec", "Montréal": "Quebec", "Quebec City": "Quebec",
    "Winnipeg": "Manitoba",
    "Saskatoon": "Saskatchewan", "Regina": "Saskatchewan",
    "Halifax": "Nova Scotia",
    "Fredericton": "New Brunswick", "Saint John": "New Brunswick",
    "St. John's": "Newfoundland and Labrador",
    "Charlottetown": "Prince Edward Island",
    "Whitehorse": "Yukon", "Yellowknife": "Northwest Territories",
    "Iqaluit": "Nunavut",
}

PROVINCES = [
    "Alberta", "British Columbia", "Manitoba", "New Brunswick",
    "Newfoundland and Labrador", "Northwest Territories", "Nova Scotia",
    "Nunavut", "Ontario", "Prince Edward Island", "Quebec", "Saskatchewan",
    "Yukon",
]


def _extract_province(location: str) -> str:
    """Extract Canadian province from a location string like 'Brantford, Ontario'.

    Checks: full province names → two-letter abbreviations (word boundary) → city/region aliases.
    """
    if not location:
        return ""
    loc_lower = location.lower()

    # 1. Full province names
    for prov in PROVINCES:
        if prov.lower() in loc_lower:
            return prov

    # 2. Two-letter abbreviations with word-boundary matching
    for abbr, prov in PROVINCE_ABBREVIATIONS.items():
        if re.search(rf"\b{abbr}\b", location):
            return prov

    # 3. Regional aliases (city names, area names)
    for alias, prov in PROVINCE_REGIONAL_ALIASES.items():
        if alias.lower() in loc_lower:
            return prov

    return ""


def _job_is_remote(job: dict) -> bool:
    """Check if a job is explicitly remote (API flag, title, or description cues)."""
    if job.get("is_remote"):
        return True
    title = job.get("title", "").lower()
    if "remote" in title:
        return True
    return False


# ---------------------------------------------------------------------------
# URL expiration screening — validates apply URLs before saving JDs
# ---------------------------------------------------------------------------
_EXPIRED_PHRASES = [
    "this job has expired",
    "this position has been filled",
    "no longer available",
    "no longer accepting applications",
    "job not found",
    "posting has been removed",
    "this listing has expired",
    "position is no longer available",
    "job has been closed",
    "this job is no longer active",
    "the job you are looking for is no longer available",
    "this opportunity is closed",
]


def check_url_alive(url: str, timeout: int = 8) -> tuple[bool, str]:
    """Check if a job apply URL is still live.

    Returns (is_alive, reason). Uses GET with stream=True to read
    only the first chunk of the page for content scanning.
    """
    if not url or not url.startswith("http"):
        return True, "no_url"  # No URL to check — pass through

    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "RESUME_GEN Pipeline/1.0"},
            stream=True,  # Don't download full page
            allow_redirects=True,
        )

        # Hard failures
        if resp.status_code in (404, 410):
            resp.close()
            return False, f"http_{resp.status_code}"

        # Redirect to homepage (common pattern for expired listings)
        if resp.status_code in (200, 301, 302) and resp.url:
            from urllib.parse import urlparse
            final_path = urlparse(resp.url).path.rstrip("/")
            if not final_path and url != resp.url:
                resp.close()
                return False, "redirect_to_homepage"

        # Content scan — read first 50KB only
        if resp.status_code == 200:
            content = resp.raw.read(50_000).decode("utf-8", errors="ignore").lower()
            resp.close()
            for phrase in _EXPIRED_PHRASES:
                if phrase in content:
                    return False, f"content:{phrase}"
            return True, "ok"

        resp.close()
        # 2xx/3xx — assume alive; 5xx — assume temporary, pass through
        return True, f"http_{resp.status_code}"

    except requests.exceptions.Timeout:
        return True, "timeout"  # Benefit of the doubt
    except requests.RequestException:
        return True, "error"  # Network issue, don't penalize


def _extract_salary_from_description(description: str) -> tuple[int | None, int | None, str]:
    """Extract annual salary from job description text.

    Returns (salary_min, salary_max, raw_string). Returns (None, None, "") if not found.
    Skips hourly rates and applies a $20,000 floor to avoid false positives.
    """
    if not description:
        return None, None, ""

    # Patterns ordered from most specific to broadest
    # Use \d[\d,]* (must start with digit) to avoid matching bare commas
    salary_patterns = [
        # "$120,000 - $165,000" or "$120,000 to $165,000" (with optional CA/CAD prefix)
        r"(?:CA|CAD)?\$\s*(\d[\d,]*)\s*(?:-|–|to)\s*(?:CA|CAD)?\$?\s*(\d[\d,]*)",
        # "$120K - $165K" or "$120k-$165k"
        r"(?:CA|CAD)?\$\s*(\d+)\s*[kK]\s*(?:-|–|to)\s*(?:CA|CAD)?\$?\s*(\d+)\s*[kK]",
        # "salary: $120,000" or "Salary $120,000"
        r"[Ss]alary[:\s]+(?:CA|CAD)?\$\s*(\d[\d,]*)",
        # Standalone "$120,000" (only in salary-context lines)
        r"(?:CA|CAD)?\$\s*(\d[\d,]*)",
    ]

    for line in description.split("\n"):
        line_lower = line.lower()

        # Skip hourly rates
        if any(h in line_lower for h in ["/hour", "per hour", "hourly", "/hr"]):
            continue

        for i, pattern in enumerate(salary_patterns):
            match = re.search(pattern, line)
            if not match:
                continue

            groups = match.groups()

            if i == 1:  # K-notation pattern
                val1 = int(groups[0]) * 1000
                val2 = int(groups[1]) * 1000
                if val1 >= 20000 and val2 >= 20000:
                    return min(val1, val2), max(val1, val2), match.group(0)
            elif len(groups) == 2:  # Range pattern
                val1 = int(groups[0].replace(",", ""))
                val2 = int(groups[1].replace(",", ""))
                if val1 >= 20000 and val2 >= 20000:
                    return min(val1, val2), max(val1, val2), match.group(0)
            elif len(groups) == 1:  # Single value
                val = int(groups[0].replace(",", ""))
                if val >= 20000:
                    return val, None, match.group(0)

    return None, None, ""


def compute_relevance_score(jd_text: str, title: str, relevance_config: dict) -> tuple[float, dict]:
    """Score how well a JD aligns with the candidate's focus areas.

    Returns (score, breakdown) where score is 0.0–1.0 and breakdown maps
    area names to their weighted contribution.
    """
    focus_areas = relevance_config.get("focus_areas", [])
    if not focus_areas:
        return (1.0, {})

    text_lower = jd_text.lower()
    title_lower = title.lower()
    total_weight = sum(a.get("weight", 1) for a in focus_areas)

    area_scores = {}
    for area in focus_areas:
        keywords = area.get("keywords", [])
        weight = area.get("weight", 1)
        if not keywords:
            continue

        matched = [kw for kw in keywords if kw.lower() in text_lower]
        breadth = len(matched) / len(keywords)

        density = sum(text_lower.count(kw.lower()) for kw in matched)
        density_norm = min(density / 20, 1.0)

        area_score = (breadth * 0.7 + density_norm * 0.3) * weight
        area_scores[area["name"]] = area_score

    total_weighted = sum(area_scores.values()) / total_weight if total_weight else 0.0

    # Anti-signal penalty (word-boundary matching to avoid false positives)
    anti_signals = relevance_config.get("anti_signals", [])
    anti_penalty = 0.0
    if anti_signals:
        # Check title first (heavy penalty — title defines the role)
        if any(re.search(rf"\b{re.escape(sig.lower())}\b", title_lower) for sig in anti_signals):
            anti_penalty = 0.5
        else:
            # Check first 5 lines of JD (light penalty — may be incidental mention)
            header_lines = "\n".join(jd_text.split("\n")[:5]).lower()
            if any(re.search(rf"\b{re.escape(sig.lower())}\b", header_lines) for sig in anti_signals):
                anti_penalty = 0.1

    final_score = max(total_weighted - anti_penalty, 0.0)
    return (final_score, area_scores)


def matches_filters(job: dict, filters: dict, candidate_province: str = "") -> bool:
    """Return True if the job passes all configured filters."""
    description = (job.get("description") or "").lower()
    title = (job.get("title") or "").lower()
    company = (job.get("company") or "").lower()
    combined = f"{title} {company} {description}"

    # Exclude companies
    for exc in filters.get("exclude_companies", []):
        if exc.lower() in company:
            return False

    # Must-contain keywords (any match passes)
    must = filters.get("must_contain", [])
    if must and not any(kw.lower() in combined for kw in must):
        return False

    # Exclude keywords
    for kw in filters.get("exclude_keywords", []):
        if kw.lower() in combined:
            return False

    # Min salary
    min_sal = filters.get("min_salary", 0)
    if min_sal:
        job_sal = job.get("salary_max") or job.get("salary_min") or 0
        if job_sal and job_sal < min_sal:
            return False

    # Include-only domains in apply URL (whitelist — if set, reject jobs not matching)
    apply_url = job.get("apply_url", "").lower()
    include_domains = filters.get("include_domains", [])
    if include_domains:
        if not any(domain.lower() in apply_url for domain in include_domains):
            return False

    # Exclude domains in apply URL
    for domain in filters.get("exclude_domains", []):
        if domain.lower() in apply_url:
            return False

    # Province filter — reject out-of-province jobs unless fully remote
    if candidate_province and not _job_is_remote(job):
        job_location = job.get("location", "")
        if job_location and candidate_province.lower() not in job_location.lower():
            return False

    return True


# ---------------------------------------------------------------------------
# Source: Fantastic.Jobs (RapidAPI) — ATS / Career Site job aggregator
# ---------------------------------------------------------------------------
def _fetch_ats_description(url: str) -> str:
    """Fetch job description from an ATS career page.

    Extracts text content from the page body, stripping navigation,
    scripts, and styles. Returns empty string on failure.
    """
    if not url:
        return ""
    try:
        resp = requests.get(
            url, timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (compatible; job-pipeline/1.0)"},
        )
        if resp.status_code != 200:
            return ""
        html = resp.text

        # Strip scripts, styles, nav, header, footer
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<nav[^>]*>.*?</nav>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<header[^>]*>.*?</header>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<footer[^>]*>.*?</footer>", "", html, flags=re.DOTALL | re.IGNORECASE)

        # Convert structural tags to whitespace
        html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
        html = re.sub(r"<li[^>]*>", "\n- ", html, flags=re.IGNORECASE)
        html = re.sub(r"<p[^>]*>", "\n\n", html, flags=re.IGNORECASE)
        html = re.sub(r"<h[1-6][^>]*>", "\n## ", html, flags=re.IGNORECASE)
        html = re.sub(r"</h[1-6]>", "\n", html, flags=re.IGNORECASE)
        html = re.sub(r"<[^>]+>", "", html)
        text = unescape(html)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        text = re.sub(r"[ \t]+", " ", text)
        return text
    except Exception:
        return ""


def fetch_fantastic_jobs(config: dict) -> list[dict]:
    """Fetch jobs from fantastic.jobs Active Jobs DB (via RapidAPI).

    Uses the /active-ats-7d endpoint which returns jobs posted to employer
    ATS / career sites in the last 7 days. Descriptions are fetched from
    the original ATS page since the API provides metadata only.
    """
    api_key = config["api_keys"].get("fantastic_jobs_rapidapi", "")
    if not api_key:
        # Fall back to shared jsearch key (same RapidAPI account)
        api_key = config["api_keys"].get("jsearch_rapidapi", "")
    if not api_key:
        print("  [Fantastic.Jobs] No API key configured — skipping.")
        return []

    src = config["sources"].get("fantastic_jobs", {})
    if not src.get("enabled", True):
        print("  [Fantastic.Jobs] Disabled in config — skipping.")
        return []

    headers = {
        "X-RapidAPI-Key": api_key,
        "X-RapidAPI-Host": "active-jobs-db.p.rapidapi.com",
    }

    base_url = src.get("base_url", "https://active-jobs-db.p.rapidapi.com")
    max_api_calls = src.get("max_api_calls", 0)  # 0 = unlimited
    all_jobs = []
    search = config["search"]
    seen_ids = set()
    limit_per_query = search.get("results_per_query", 10)
    api_calls_made = 0

    for query in search["queries"]:
        for location in search["locations"]:
            # Respect max_api_calls cap
            if max_api_calls and api_calls_made >= max_api_calls:
                print(f"  [Fantastic.Jobs] Reached max_api_calls limit ({max_api_calls}). Stopping.")
                print(f"  [Fantastic.Jobs] Fetched {len(all_jobs)} jobs total.")
                return all_jobs

            params = {
                "title_filter": f'"{query}"',
                "location_filter": f'"{location}"',
                "limit": str(limit_per_query),
                "offset": "0",
            }

            print(f"  [Fantastic.Jobs] Searching: {query} in {location} ({api_calls_made + 1}/{max_api_calls or '∞'})")
            try:
                resp = requests.get(
                    f"{base_url}/active-ats-7d",
                    headers=headers, params=params, timeout=30,
                )
                api_calls_made += 1

                if resp.status_code == 403:
                    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                    msg = body.get("message", resp.text[:200])
                    print(f"  [Fantastic.Jobs] 403 Forbidden: {msg}")
                    print("  → Subscribe at: https://rapidapi.com/fantastic-jobs-fantastic-jobs-default/api/active-jobs-db/pricing")
                    return all_jobs

                if resp.status_code == 429:
                    print(f"  [Fantastic.Jobs] Rate limited (429). Stopping to preserve quota.")
                    return all_jobs

                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, list):
                    data = []

            except requests.RequestException as e:
                print(f"  [Fantastic.Jobs] Error: {e}")
                continue

            for item in data:
                rid = str(item.get("id", ""))
                if rid in seen_ids:
                    continue
                seen_ids.add(rid)

                # Build location from derived fields
                locations_derived = item.get("locations_derived") or []
                location_str = locations_derived[0] if locations_derived else ""

                # Employment type comes as a list
                emp_type_raw = item.get("employment_type") or []
                emp_type = emp_type_raw[0] if emp_type_raw else ""

                apply_url = item.get("url", "")

                # Fetch description from the ATS page
                raw_desc = _fetch_ats_description(apply_url)
                # Require at least 200 chars for a meaningful description
                # (JS-rendered pages like Workday return near-empty content)
                if len(raw_desc) >= 200:
                    description = raw_desc
                    print(f"    ✓ Fetched description ({len(description)} chars) from {item.get('source_domain', 'ATS')}")
                else:
                    description = ""
                    reason = f"too short ({len(raw_desc)} chars)" if raw_desc else "failed"
                    print(f"    ✗ Could not fetch description ({reason}) from {item.get('source_domain', apply_url[:60])}")

                # Parse salary from salary_raw field
                salary_min = None
                salary_max = None
                salary_raw_str = item.get("salary_raw") or ""
                if isinstance(salary_raw_str, dict):
                    # Structured JSON-LD salary: {value: {minValue, maxValue}}
                    val = salary_raw_str.get("value", salary_raw_str)
                    if isinstance(val, dict):
                        salary_min = val.get("minValue")
                        salary_max = val.get("maxValue")
                    else:
                        salary_min = salary_raw_str.get("minValue")
                        salary_max = salary_raw_str.get("maxValue")
                    currency = salary_raw_str.get("currency", "")
                    if salary_min and salary_max:
                        salary_raw_str = f"{currency} ${salary_min:,} – ${salary_max:,}/yr".strip()
                    elif salary_min:
                        salary_raw_str = f"{currency} ${salary_min:,}/yr".strip()
                    else:
                        salary_raw_str = ""
                elif salary_raw_str:
                    nums = re.findall(r"[\d,]+", salary_raw_str.replace(",", ""))
                    if len(nums) >= 2:
                        salary_min = int(nums[0])
                        salary_max = int(nums[1])
                    elif len(nums) == 1:
                        salary_min = int(nums[0])

                job = {
                    "source": "fantastic_jobs",
                    "source_id": rid,
                    "title": item.get("title", "Unknown"),
                    "company": item.get("organization", "Unknown"),
                    "location": location_str,
                    "description": description,
                    "apply_url": apply_url,
                    "posted_date": item.get("date_posted", ""),
                    "salary_min": salary_min,
                    "salary_max": salary_max,
                    "employment_type": emp_type,
                    "is_remote": item.get("remote_derived", False),
                    "employer_logo": item.get("organization_logo", ""),
                    "highlights": {},
                }

                if salary_raw_str:
                    job["salary_raw"] = salary_raw_str

                # Backfill salary from description when API fields are null
                if not job["salary_min"] and not job["salary_max"] and description:
                    sal_min, sal_max, sal_raw = _extract_salary_from_description(description)
                    if sal_min:
                        job["salary_min"] = sal_min
                        job["salary_max"] = sal_max
                        job["salary_raw"] = sal_raw

                all_jobs.append(job)

                # Brief pause between ATS page fetches
                time.sleep(0.5)

            # Respect API rate limits
            time.sleep(1.5)

    print(f"  [Fantastic.Jobs] Fetched {len(all_jobs)} jobs total.")
    return all_jobs


# ---------------------------------------------------------------------------
# Source: JSearch (RapidAPI)
# ---------------------------------------------------------------------------
def fetch_jsearch(config: dict) -> list[dict]:
    """Fetch jobs from JSearch API."""
    api_key = config["api_keys"].get("jsearch_rapidapi", "")
    if not api_key:
        print("  [JSearch] No API key configured — skipping.")
        print("  Get a free key at: https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch")
        return []

    src = config["sources"]["jsearch"]
    if not src.get("enabled", True):
        print("  [JSearch] Disabled in config — skipping.")
        return []

    headers = {
        "X-RapidAPI-Key": api_key,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    }

    all_jobs = []
    search = config["search"]

    for query in search["queries"]:
        for location in search["locations"]:
            full_query = f"{query} in {location}"
            params = {
                "query": full_query,
                "page": "1",
                "num_pages": "1",
                "date_posted": search.get("date_posted", "week"),
                "remote_jobs_only": str(search.get("remote_only", False)).lower(),
            }

            print(f"  [JSearch] Searching: {full_query}")
            try:
                resp = requests.get(src["base_url"], headers=headers, params=params, timeout=30)

                # Handle subscription / auth errors
                if resp.status_code == 403:
                    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                    msg = body.get("message", resp.text[:200])
                    print(f"  [JSearch] 403 Forbidden: {msg}")
                    print("  → Subscribe to the free plan at: https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch/pricing")
                    return all_jobs  # no point retrying other queries

                # Handle rate limits — back off and stop
                if resp.status_code == 429:
                    print(f"  [JSearch] Rate limited (429). Stopping to preserve quota.")
                    return all_jobs

                resp.raise_for_status()
                data = resp.json().get("data", [])
            except requests.RequestException as e:
                print(f"  [JSearch] Error: {e}")
                continue

            count = 0
            limit = search.get("results_per_query", 10)
            for item in data:
                if count >= limit:
                    break
                job = {
                    "source": "jsearch",
                    "source_id": item.get("job_id", ""),
                    "title": item.get("job_title", "Unknown"),
                    "company": item.get("employer_name", "Unknown"),
                    "location": ", ".join(
                        p for p in [
                            item.get("job_city") or "",
                            item.get("job_state") or "",
                            item.get("job_country") or "",
                        ] if p
                    ),
                    "description": item.get("job_description", ""),
                    "apply_url": (item.get("job_apply_link") or ""),
                    "posted_date": item.get("job_posted_at_datetime_utc", ""),
                    "salary_min": item.get("job_min_salary"),
                    "salary_max": item.get("job_max_salary"),
                    "employment_type": item.get("job_employment_type", ""),
                    "is_remote": item.get("job_is_remote", False),
                    "employer_logo": item.get("employer_logo", ""),
                    "highlights": item.get("job_highlights", {}),
                }

                # Backfill salary from description when API fields are null
                if not job["salary_min"] and not job["salary_max"]:
                    sal_min, sal_max, sal_raw = _extract_salary_from_description(
                        item.get("job_description", "")
                    )
                    if sal_min:
                        job["salary_min"] = sal_min
                        job["salary_max"] = sal_max
                        job["salary_raw"] = sal_raw

                all_jobs.append(job)
                count += 1

            # Respect rate limits — 1 req/sec on free tier
            time.sleep(1.5)

    print(f"  [JSearch] Fetched {len(all_jobs)} jobs total.")
    return all_jobs


# ---------------------------------------------------------------------------
# Source: Remotive (free, no auth)
# ---------------------------------------------------------------------------
def fetch_remotive(config: dict) -> list[dict]:
    """Fetch jobs from Remotive API (remote jobs only).

    Note: Remotive is a curated board with fewer listings than aggregators.
    Search-only (no category filter) returns the broadest results.
    """
    src = config["sources"].get("remotive", {})
    if not src.get("enabled", True):
        print("  [Remotive] Disabled in config — skipping.")
        return []

    search = config["search"]
    all_jobs = []
    seen_ids = set()

    for query in search["queries"]:
        # Don't combine search + category — too restrictive on a small board
        params = {
            "search": query,
            "limit": search.get("results_per_query", 10),
        }

        print(f"  [Remotive] Searching: {query}")
        try:
            resp = requests.get(src["base_url"], params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json().get("jobs", [])
        except requests.RequestException as e:
            print(f"  [Remotive] Error: {e}")
            continue

        for item in data:
            rid = str(item.get("id", ""))
            if rid in seen_ids:
                continue
            seen_ids.add(rid)

            raw_desc = item.get("description", "")
            clean_desc = strip_html(raw_desc)

            job = {
                "source": "remotive",
                "source_id": rid,
                "title": item.get("title", "Unknown"),
                "company": item.get("company_name", "Unknown"),
                "location": item.get("candidate_required_location", "Anywhere"),
                "description": clean_desc,
                "apply_url": item.get("url", ""),
                "posted_date": item.get("publication_date", ""),
                "salary_min": None,
                "salary_max": None,
                "employment_type": item.get("job_type", ""),
                "is_remote": True,
                "employer_logo": item.get("company_logo", ""),
                "highlights": {},
            }

            # Parse salary string if present (e.g. "$120,000 - $150,000")
            salary_str = item.get("salary", "")
            if salary_str:
                nums = re.findall(r"[\d,]+", salary_str.replace(",", ""))
                if len(nums) >= 2:
                    job["salary_min"] = int(nums[0])
                    job["salary_max"] = int(nums[1])
                elif len(nums) == 1:
                    job["salary_min"] = int(nums[0])
                job["salary_raw"] = salary_str

            all_jobs.append(job)

        # Respect rate limits (max 2 req/min)
        time.sleep(31)

    print(f"  [Remotive] Fetched {len(all_jobs)} jobs total.")
    return all_jobs


# ---------------------------------------------------------------------------
# Source: Adzuna (free API key required)
# ---------------------------------------------------------------------------
def fetch_adzuna(config: dict) -> list[dict]:
    """Fetch jobs from Adzuna API (16+ countries, salary data).

    Register for free keys at: https://developer.adzuna.com/
    """
    src = config["sources"].get("adzuna", {})
    if not src.get("enabled", True):
        print("  [Adzuna] Disabled in config — skipping.")
        return []

    app_id = config["api_keys"].get("adzuna_app_id", "")
    app_key = config["api_keys"].get("adzuna_app_key", "")
    if not app_id or not app_key:
        print("  [Adzuna] No API keys configured — skipping.")
        print("  Get free keys at: https://developer.adzuna.com/")
        return []

    country = src.get("country", "ca")
    base_url = src.get("base_url", "https://api.adzuna.com/v1/api/jobs")
    search = config["search"]
    all_jobs = []

    for query in search["queries"]:
        for location in search["locations"]:
            url = f"{base_url}/{country}/search/1"
            params = {
                "app_id": app_id,
                "app_key": app_key,
                "what": query,
                "where": location,
                "results_per_page": search.get("results_per_query", 10),
                "max_days_old": 7 if search.get("date_posted") == "week" else 30,
                "sort_by": "date",
            }

            print(f"  [Adzuna] Searching: {query} in {location}")
            try:
                resp = requests.get(url, params=params, timeout=30)
                if resp.status_code == 401:
                    print("  [Adzuna] 401 Unauthorized — check your app_id/app_key.")
                    return all_jobs
                if resp.status_code == 429:
                    print("  [Adzuna] Rate limited (429). Stopping.")
                    return all_jobs
                resp.raise_for_status()
                results = resp.json().get("results", [])
            except requests.RequestException as e:
                print(f"  [Adzuna] Error: {e}")
                continue

            for item in results:
                company_name = item.get("company", {}).get("display_name", "Unknown") if isinstance(item.get("company"), dict) else str(item.get("company", "Unknown"))
                location_name = item.get("location", {}).get("display_name", "") if isinstance(item.get("location"), dict) else str(item.get("location", ""))

                job = {
                    "source": "adzuna",
                    "source_id": str(item.get("id", "")),
                    "title": item.get("title", "Unknown"),
                    "company": company_name,
                    "location": location_name,
                    "description": strip_html(item.get("description", "")),
                    "apply_url": item.get("redirect_url", ""),
                    "posted_date": item.get("created", ""),
                    "salary_min": item.get("salary_min"),
                    "salary_max": item.get("salary_max"),
                    "employment_type": item.get("contract_type", ""),
                    "is_remote": "remote" in item.get("title", "").lower() or "remote" in location_name.lower(),
                    "employer_logo": "",
                    "highlights": {},
                }
                all_jobs.append(job)

            time.sleep(0.5)

    print(f"  [Adzuna] Fetched {len(all_jobs)} jobs total.")
    return all_jobs


# ---------------------------------------------------------------------------
# Source: RemoteOK — Canada location compatibility
# ---------------------------------------------------------------------------
_CANADA_COMPATIBLE_LOCATIONS = {
    "worldwide", "anywhere", "remote", "global", "north america",
    "americas", "canada", "us / canada", "usa / canada", "us/canada",
    "na", "apac", "emea",
}

_CANADA_INCOMPATIBLE_LOCATIONS = {
    "usa only", "us only", "us-only", "us-based", "united states only",
    "europe only", "eu only", "uk only", "apac only", "latam only",
    "brazil", "india", "australia only", "germany", "france",
}


def _remote_job_compatible_with_canada(location: str) -> bool:
    """Check if a RemoteOK job location is compatible with a Canadian candidate.

    Returns True if the location is clearly compatible or ambiguous (conservative).
    Returns False only for explicitly incompatible locations.
    """
    if not location:
        return True  # No location info — assume compatible

    loc_lower = location.lower().strip()

    # Check explicit incompatibility first
    for incompat in _CANADA_INCOMPATIBLE_LOCATIONS:
        if incompat in loc_lower:
            return False

    # Check explicit compatibility
    for compat in _CANADA_COMPATIBLE_LOCATIONS:
        if compat in loc_lower:
            return True

    # Ambiguous — be conservative and include it
    return True


# ---------------------------------------------------------------------------
# Source: RemoteOK (free, no auth)
# ---------------------------------------------------------------------------
def fetch_remoteok(config: dict) -> list[dict]:
    """Fetch jobs from RemoteOK API (single JSON feed, remote jobs only).

    No auth required. User-Agent header is mandatory.
    """
    src = config["sources"].get("remoteok", {})
    if not src.get("enabled", True):
        print("  [RemoteOK] Disabled in config — skipping.")
        return []

    search = config["search"]
    base_url = src.get("base_url", "https://remoteok.com/api")
    queries_lower = [q.lower() for q in search["queries"]]

    print(f"  [RemoteOK] Fetching remote jobs feed...")
    try:
        resp = requests.get(
            base_url,
            headers={"User-Agent": "RESUME_GEN Pipeline/1.0"},
            timeout=30,
        )
        if resp.status_code == 429:
            print("  [RemoteOK] Rate limited (429). Stopping.")
            return []
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"  [RemoteOK] Error: {e}")
        return []

    # First element is metadata — skip it
    if data and isinstance(data[0], dict) and "id" not in data[0]:
        data = data[1:]

    all_jobs = []
    limit = search.get("results_per_query", 10) * len(search["queries"])

    for item in data:
        if len(all_jobs) >= limit:
            break

        title = item.get("position", "Unknown")
        company = item.get("company", "Unknown")
        description = item.get("description", "")
        tags = [t.lower() for t in item.get("tags", [])]

        # Client-side keyword filter: match against title, company, tags, description
        searchable = f"{title.lower()} {company.lower()} {' '.join(tags)} {description.lower()}"
        if not any(q in searchable for q in queries_lower):
            continue

        # Location filter: skip jobs incompatible with Canada
        job_location = item.get("location", "")
        if not _remote_job_compatible_with_canada(job_location):
            continue

        # Parse salary
        salary_min = None
        salary_max = None
        salary_raw = ""
        sal_str = item.get("salary", "")
        if sal_str:
            salary_raw = sal_str
            nums = re.findall(r"[\d]+", sal_str.replace(",", "").replace("k", "000").replace("K", "000"))
            if len(nums) >= 2:
                salary_min = int(nums[0])
                salary_max = int(nums[1])
            elif len(nums) == 1:
                salary_min = int(nums[0])

        job = {
            "source": "remoteok",
            "source_id": str(item.get("id", "")),
            "title": title,
            "company": company,
            "location": item.get("location", "Remote"),
            "description": strip_html(description),
            "apply_url": item.get("url", ""),
            "posted_date": item.get("date", ""),
            "salary_min": salary_min,
            "salary_max": salary_max,
            "employment_type": "",
            "is_remote": True,
            "employer_logo": item.get("company_logo", ""),
            "highlights": {},
        }
        if salary_raw:
            job["salary_raw"] = salary_raw

        all_jobs.append(job)

    print(f"  [RemoteOK] Fetched {len(all_jobs)} jobs (filtered from {len(data)} total).")
    return all_jobs


# ---------------------------------------------------------------------------
# Source: The Muse (free, no auth required)
# ---------------------------------------------------------------------------
def fetch_themuse(config: dict) -> list[dict]:
    """Fetch jobs from The Muse API (company profiles + jobs).

    Free, registration optional. Good for PM/tech roles.
    """
    src = config["sources"].get("themuse", {})
    if not src.get("enabled", True):
        print("  [TheMuse] Disabled in config — skipping.")
        return []

    search = config["search"]
    base_url = src.get("base_url", "https://www.themuse.com/api/public/jobs")
    queries_lower = [q.lower() for q in search["queries"]]
    all_jobs = []
    seen_ids = set()

    # Iterate over all configured locations, 2 pages each
    locations = search.get("locations", [])
    if not locations:
        locations = [""]  # Fetch without location filter if none configured

    rate_limited = False
    for location in locations:
        if rate_limited:
            break

        for page in range(1, 3):
            params = {
                "page": page,
                "descending": "true",
            }
            if location:
                params["location"] = location

            location_label = f" in {location}" if location else ""
            print(f"  [TheMuse] Fetching page {page}{location_label}...")
            try:
                resp = requests.get(base_url, params=params, timeout=30)
                if resp.status_code == 429:
                    print("  [TheMuse] Rate limited (429). Stopping.")
                    rate_limited = True
                    break
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results", [])
            except requests.RequestException as e:
                print(f"  [TheMuse] Error: {e}")
                rate_limited = True
                break

            if not results:
                break

            for item in results:
                item_id = str(item.get("id", ""))
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)

                title = item.get("name", "Unknown")

                # Client-side keyword filter on title
                if not any(q in title.lower() for q in queries_lower):
                    continue

                company_data = item.get("company", {})
                company_name = company_data.get("name", "Unknown") if isinstance(company_data, dict) else str(company_data)

                locations_list = item.get("locations", [])
                location_str = ", ".join(
                    loc.get("name", "") for loc in locations_list if isinstance(loc, dict)
                ) if locations_list else "N/A"

                description = strip_html(item.get("contents", ""))
                apply_url = ""
                refs = item.get("refs", {})
                if isinstance(refs, dict):
                    apply_url = refs.get("landing_page", "")

                levels = item.get("levels", [])
                level_str = ", ".join(
                    lv.get("name", "") for lv in levels if isinstance(lv, dict)
                ) if levels else ""

                job = {
                    "source": "themuse",
                    "source_id": item_id,
                    "title": title,
                    "company": company_name,
                    "location": location_str,
                    "description": description,
                    "apply_url": apply_url,
                    "posted_date": item.get("publication_date", ""),
                    "salary_min": None,
                    "salary_max": None,
                    "employment_type": level_str,
                    "is_remote": "remote" in location_str.lower() or "flexible" in location_str.lower(),
                    "employer_logo": "",
                    "highlights": {},
                }

                # The Muse has no salary API — extract from description text
                sal_min, sal_max, sal_raw = _extract_salary_from_description(description)
                if sal_min:
                    job["salary_min"] = sal_min
                    job["salary_max"] = sal_max
                    job["salary_raw"] = sal_raw

                all_jobs.append(job)

            time.sleep(1)

    print(f"  [TheMuse] Fetched {len(all_jobs)} jobs total.")
    return all_jobs


# ---------------------------------------------------------------------------
# Deduplication & saving
# ---------------------------------------------------------------------------
def deduplicate(jobs: list[dict]) -> list[dict]:
    """Remove duplicates by fingerprint (title + company)."""
    seen = {}
    unique = []
    for job in jobs:
        fp = job_fingerprint(job["title"], job["company"])
        if fp not in seen:
            seen[fp] = True
            job["fingerprint"] = fp
            unique.append(job)
    return unique


def save_jd_markdown(job: dict, jd_dir: Path, relevance_config: dict | None = None, validate_urls: bool = False) -> Path | None:
    """Save a job description as a structured markdown file.

    Returns None if the JD is filtered out by URL expiration or relevance scoring.
    """
    # URL validation — check before spending time on relevance scoring
    if validate_urls:
        apply_url = job.get("apply_url", "")
        is_alive, reason = check_url_alive(apply_url)
        if not is_alive:
            job["_expired_reason"] = reason
            return None

    company_safe = sanitize_filename(job["company"])
    title_safe = sanitize_filename(job["title"])
    filename = f"{company_safe}_{title_safe}.md"
    filepath = jd_dir / filename

    salary_line = ""
    if job.get("salary_min") or job.get("salary_max"):
        parts = []
        if job.get("salary_min"):
            parts.append(f"${job['salary_min']:,}")
        if job.get("salary_max"):
            parts.append(f"${job['salary_max']:,}")
        salary_line = f"**Salary:** {' – '.join(parts)}"
    elif job.get("salary_raw"):
        salary_line = f"**Salary:** {job['salary_raw']}"
    else:
        # Final fallback: extract salary from description text
        sal_min, sal_max, sal_raw = _extract_salary_from_description(job.get("description", ""))
        if sal_min:
            parts = [f"${sal_min:,}"]
            if sal_max:
                parts.append(f"${sal_max:,}")
            salary_line = f"**Salary:** {' – '.join(parts)} *(extracted from description)*"

    # Relevance scoring — filter and annotate
    relevance_line = ""
    if relevance_config:
        combined_text = f"{job['title']} {job.get('description', '')}"
        score, breakdown = compute_relevance_score(combined_text, job["title"], relevance_config)
        min_score = relevance_config.get("min_score", 0.0)
        if score < min_score:
            return None
        # Build breakdown string: "Risk & Governance: 85%, Security Assessment: 40%"
        total_weight = sum(a.get("weight", 1) for a in relevance_config.get("focus_areas", []))
        parts = []
        for area in relevance_config.get("focus_areas", []):
            area_val = breakdown.get(area["name"], 0.0)
            # Normalize area score to percentage (divide by weight, show as %)
            area_pct = (area_val / area.get("weight", 1)) * 100 if area.get("weight", 1) else 0
            parts.append(f"{area['name']}: {area_pct:.0f}%")
        breakdown_str = ", ".join(parts)
        relevance_line = f"| **Relevance** | {score:.0%} ({breakdown_str}) |"
        job["_relevance_score"] = score

    highlights_section = ""
    if job.get("highlights"):
        hl = job["highlights"]
        if hl.get("Qualifications"):
            highlights_section += "\n## Qualifications\n"
            for q in hl["Qualifications"]:
                highlights_section += f"- {q}\n"
        if hl.get("Responsibilities"):
            highlights_section += "\n## Responsibilities\n"
            for r in hl["Responsibilities"]:
                highlights_section += f"- {r}\n"
        if hl.get("Benefits"):
            highlights_section += "\n## Benefits\n"
            for b in hl["Benefits"]:
                highlights_section += f"- {b}\n"

    # Build metadata table — add relevance row if scored
    relevance_row = f"\n{relevance_line}" if relevance_line else ""

    md = f"""# {job['title']} — {job['company']}

| Field | Details |
|-------|---------|
| **Company** | {job['company']} |
| **Location** | {job['location']} |
| **Type** | {job.get('employment_type', 'N/A')} |
| **Remote** | {'Yes' if job.get('is_remote') else 'No'} |
| **Posted** | {job.get('posted_date', 'N/A')} |
| **Source** | {job['source']} |
| **Apply** | {job.get('apply_url', 'N/A')} |{relevance_row}

{salary_line}

---

## Full Job Description

{job['description']}
{highlights_section}
---

*Fetched on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by job-pipeline*
""".lstrip()

    filepath.write_text(md, encoding="utf-8")
    return filepath


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def run(dry_run: bool = False, source_filter: str | None = None, validate_urls: bool = True):
    print("=" * 60)
    print(f"  Job Fetcher — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # Load config & state
    config = load_config()
    state_path = ROOT / config["output"]["state_file"]
    state = load_state(state_path)
    jd_dir = ROOT / config["output"]["jd_directory"]
    jd_dir.mkdir(exist_ok=True)
    filters = config.get("filters", {})
    relevance_config = config.get("relevance")

    # Fetch from all enabled sources
    all_jobs: list[dict] = []

    sources_to_run = {
        "fantastic_jobs": fetch_fantastic_jobs,
        "jsearch": fetch_jsearch,
        "remotive": fetch_remotive,
        "adzuna": fetch_adzuna,
        "remoteok": fetch_remoteok,
        "themuse": fetch_themuse,
    }

    print("\n[1/4] Fetching jobs from sources...")
    for name, fetcher in sources_to_run.items():
        if source_filter and name != source_filter:
            continue
        jobs = fetcher(config)
        all_jobs.extend(jobs)

    if not all_jobs:
        print("\nNo jobs fetched. Check your API keys and config.")
        return

    # Deduplicate
    print(f"\n[2/4] Deduplicating {len(all_jobs)} jobs...")
    unique_jobs = deduplicate(all_jobs)
    print(f"  {len(unique_jobs)} unique jobs after dedup.")

    # Filter out already-seen jobs
    print("\n[3/4] Filtering...")
    candidate_location = config.get("candidate", {}).get("location", "")
    candidate_province = _extract_province(candidate_location)
    if candidate_province:
        print(f"  Province filter active: {candidate_province} (non-remote jobs outside this province will be skipped)")

    new_jobs = []
    for job in unique_jobs:
        fp = job["fingerprint"]
        if fp in state["seen_jobs"]:
            continue
        if not matches_filters(job, filters, candidate_province):
            continue
        new_jobs.append(job)

    print(f"  {len(new_jobs)} new jobs after filtering (skipped {len(unique_jobs) - len(new_jobs)} seen/filtered).")

    if not new_jobs:
        print("\nNo new jobs found this run. Try again later or broaden your search.")
        return

    # Save JDs
    print(f"\n[4/4] Saving {len(new_jobs)} new job descriptions...")
    if relevance_config:
        min_rel = relevance_config.get("min_score", 0.0)
        print(f"  Relevance filter active (min: {min_rel:.0%})")
    if validate_urls:
        print(f"  URL expiration check active (timeout: 8s)")

    saved = []
    skipped_relevance = 0
    skipped_expired = 0
    for job in new_jobs:
        if dry_run:
            # URL expiration check in dry-run
            if validate_urls:
                apply_url = job.get("apply_url", "")
                is_alive, reason = check_url_alive(apply_url)
                if not is_alive:
                    print(f"  [DRY RUN] Skipped (expired: {reason}): {job['title']} @ {job['company']}")
                    skipped_expired += 1
                    continue
            # Show relevance score in dry-run output
            rel_tag = ""
            if relevance_config:
                combined_text = f"{job['title']} {job.get('description', '')}"
                score, _ = compute_relevance_score(combined_text, job["title"], relevance_config)
                min_score = relevance_config.get("min_score", 0.0)
                if score < min_score:
                    print(f"  [DRY RUN] Skipped (relevance {score:.0%}): {job['title']} @ {job['company']}")
                    skipped_relevance += 1
                    continue
                rel_tag = f" [rel: {score:.0%}]"
            print(f"  [DRY RUN] Would save: {job['title']} @ {job['company']}{rel_tag}")
        else:
            filepath = save_jd_markdown(job, jd_dir, relevance_config, validate_urls=validate_urls)
            if filepath is None:
                # Distinguish expired vs relevance-skipped
                if job.get("_expired_reason"):
                    reason = job["_expired_reason"]
                    print(f"  Skipped (expired: {reason}): {job['title']} @ {job['company']}")
                    skipped_expired += 1
                    state["seen_jobs"][job["fingerprint"]] = {
                        "title": job["title"],
                        "company": job["company"],
                        "source": job["source"],
                        "first_seen": datetime.now(timezone.utc).isoformat(),
                        "skipped_expired": True,
                        "expired_reason": reason,
                    }
                else:
                    score = job.get("_relevance_score", 0)
                    print(f"  Skipped (relevance {score:.0%}): {job['title']} @ {job['company']}")
                    skipped_relevance += 1
                    state["seen_jobs"][job["fingerprint"]] = {
                        "title": job["title"],
                        "company": job["company"],
                        "source": job["source"],
                        "first_seen": datetime.now(timezone.utc).isoformat(),
                        "skipped_relevance": True,
                    }
                continue
            print(f"  Saved: {filepath.name}")
            saved.append(filepath)

            # Mark as seen
            state["seen_jobs"][job["fingerprint"]] = {
                "title": job["title"],
                "company": job["company"],
                "source": job["source"],
                "first_seen": datetime.now(timezone.utc).isoformat(),
            }

    if not dry_run:
        save_state(state, state_path)

    # Summary
    print("\n" + "=" * 60)
    print(f"  SUMMARY")
    print("=" * 60)
    print(f"  Total fetched:  {len(all_jobs)}")
    print(f"  After dedup:    {len(unique_jobs)}")
    print(f"  New this run:   {len(new_jobs)}")
    if skipped_expired:
        print(f"  Skipped (expired URL):   {skipped_expired}")
    if skipped_relevance:
        print(f"  Skipped (low relevance): {skipped_relevance}")
    if not dry_run:
        print(f"  Saved to:       {jd_dir}/")
    print()

    for i, job in enumerate(new_jobs, 1):
        salary = ""
        if job.get("salary_min") or job.get("salary_max"):
            parts = []
            if job.get("salary_min"):
                parts.append(f"${job['salary_min']:,}")
            if job.get("salary_max"):
                parts.append(f"${job['salary_max']:,}")
            salary = f"  💰 {' – '.join(parts)}"
        elif job.get("salary_raw"):
            salary = f"  💰 {job['salary_raw']}"

        rel_tag = ""
        if job.get("_relevance_score") is not None:
            rel_tag = f"  [Relevance: {job['_relevance_score']:.0%}]"

        remote_tag = " [REMOTE]" if job.get("is_remote") else ""
        print(f"  {i}. {job['title']} @ {job['company']}{remote_tag}{salary}{rel_tag}")
        print(f"     📍 {job['location']}  |  Source: {job['source']}")
        if job.get("apply_url"):
            print(f"     🔗 {job['apply_url']}")
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch PM & Scrum Master job openings")
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving files")
    parser.add_argument("--source", choices=["fantastic_jobs", "jsearch", "remotive", "adzuna", "remoteok", "themuse"], help="Fetch from a single source only")
    parser.add_argument("--no-url-check", action="store_true", help="Skip URL expiration validation")
    args = parser.parse_args()

    run(dry_run=args.dry_run, source_filter=args.source, validate_urls=not args.no_url_check)
