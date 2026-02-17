#!/usr/bin/env python3
"""
Job Fetcher — Phase 1 of the autonomous resume-tailoring pipeline.

Pulls Product Manager and Scrum Master job openings from:
  1. JSearch (via RapidAPI) — aggregates Google for Jobs
  2. Remotive — free, no auth, remote roles

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


def _extract_province(location: str) -> str:
    """Extract Canadian province from a location string like 'Brantford, Ontario'."""
    provinces = [
        "Alberta", "British Columbia", "Manitoba", "New Brunswick",
        "Newfoundland and Labrador", "Northwest Territories", "Nova Scotia",
        "Nunavut", "Ontario", "Prince Edward Island", "Quebec", "Saskatchewan",
        "Yukon",
    ]
    for prov in provinces:
        if prov.lower() in location.lower():
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


def matches_filters(job: dict, filters: dict, candidate_province: str = "") -> bool:
    """Return True if the job passes all configured filters."""
    description = job.get("description", "").lower()
    title = job.get("title", "").lower()
    company = job.get("company", "").lower()
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

    # Province filter — reject out-of-province jobs unless fully remote
    if candidate_province and not _job_is_remote(job):
        job_location = job.get("location", "")
        if job_location and candidate_province.lower() not in job_location.lower():
            return False

    return True


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

    # Fetch first 2 pages (20 results per page)
    for page in range(1, 3):
        params = {
            "page": page,
            "descending": "true",
        }
        # Add location filter if available
        locations = search.get("locations", [])
        if locations:
            params["location"] = locations[0]

        print(f"  [TheMuse] Fetching page {page}...")
        try:
            resp = requests.get(base_url, params=params, timeout=30)
            if resp.status_code == 429:
                print("  [TheMuse] Rate limited (429). Stopping.")
                break
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
        except requests.RequestException as e:
            print(f"  [TheMuse] Error: {e}")
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


def save_jd_markdown(job: dict, jd_dir: Path) -> Path:
    """Save a job description as a structured markdown file."""
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

    md = f"""# {job['title']} — {job['company']}

| Field | Details |
|-------|---------|
| **Company** | {job['company']} |
| **Location** | {job['location']} |
| **Type** | {job.get('employment_type', 'N/A')} |
| **Remote** | {'Yes' if job.get('is_remote') else 'No'} |
| **Posted** | {job.get('posted_date', 'N/A')} |
| **Source** | {job['source']} |
| **Apply** | {job.get('apply_url', 'N/A')} |

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
def run(dry_run: bool = False, source_filter: str | None = None):
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

    # Fetch from all enabled sources
    all_jobs: list[dict] = []

    sources_to_run = {
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
    saved = []
    for job in new_jobs:
        if dry_run:
            print(f"  [DRY RUN] Would save: {job['title']} @ {job['company']}")
        else:
            filepath = save_jd_markdown(job, jd_dir)
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

        remote_tag = " [REMOTE]" if job.get("is_remote") else ""
        print(f"  {i}. {job['title']} @ {job['company']}{remote_tag}{salary}")
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
    parser.add_argument("--source", choices=["jsearch", "remotive", "adzuna", "remoteok", "themuse"], help="Fetch from a single source only")
    args = parser.parse_args()

    run(dry_run=args.dry_run, source_filter=args.source)
