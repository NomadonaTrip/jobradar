"""
Simple JSearch (RapidAPI) query for quick research.

Usage:
    python jsearch_research.py "Scrum Master in Toronto"
    python jsearch_research.py "Product Manager" --remote
    python jsearch_research.py "DevOps Engineer in Vancouver" --num-pages 2
"""

import argparse
import json
import os
import sys

import requests
import yaml


def load_api_key() -> str:
    """Load JSearch API key from config.yaml or environment."""
    if key := os.environ.get("JSEARCH_API_KEY"):
        return key

    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    if os.path.exists(config_path):
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        return cfg.get("api_keys", {}).get("jsearch_rapidapi", "")

    return ""


def search_jobs(query: str, remote_only: bool = False, num_pages: int = 1, date_posted: str = "week") -> dict:
    """Run a JSearch query and return the raw API response."""
    api_key = load_api_key()
    if not api_key:
        print("No API key found. Set JSEARCH_API_KEY env var or add to config.yaml.")
        sys.exit(1)

    resp = requests.get(
        "https://jsearch.p.rapidapi.com/search",
        headers={
            "X-RapidAPI-Key": api_key,
            "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
        },
        params={
            "query": query,
            "page": "1",
            "num_pages": str(num_pages),
            "date_posted": date_posted,
            "remote_jobs_only": str(remote_only).lower(),
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def print_results(data: dict):
    """Print job results in a readable format."""
    jobs = data.get("data", [])
    print(f"\n{'='*60}")
    print(f"Found {len(jobs)} job(s)")
    print(f"{'='*60}\n")

    for i, job in enumerate(jobs, 1):
        title = job.get("job_title", "N/A")
        company = job.get("employer_name", "N/A")
        city = job.get("job_city") or ""
        state = job.get("job_state") or ""
        country = job.get("job_country") or ""
        location = ", ".join(p for p in [city, state, country] if p) or "N/A"
        remote = job.get("job_is_remote", False)
        posted = job.get("job_posted_at_datetime_utc", "N/A")
        apply_url = job.get("job_apply_link", "")
        emp_type = job.get("job_employment_type", "N/A")
        sal_min = job.get("job_min_salary")
        sal_max = job.get("job_max_salary")

        salary = "N/A"
        if sal_min and sal_max:
            salary = f"${sal_min:,.0f} - ${sal_max:,.0f}"
        elif sal_min:
            salary = f"${sal_min:,.0f}+"

        print(f"  [{i}] {title}")
        print(f"      Company:  {company}")
        print(f"      Location: {location} {'(Remote)' if remote else ''}")
        print(f"      Type:     {emp_type}")
        print(f"      Salary:   {salary}")
        print(f"      Posted:   {posted}")
        if apply_url:
            print(f"      Apply:    {apply_url}")
        print()


def main():
    parser = argparse.ArgumentParser(description="Quick JSearch API query")
    parser.add_argument("query", help='Search query, e.g. "Scrum Master in Toronto"')
    parser.add_argument("--remote", action="store_true", help="Remote jobs only")
    parser.add_argument("--num-pages", type=int, default=1, help="Number of pages (default: 1)")
    parser.add_argument("--date-posted", default="week", choices=["all", "today", "3days", "week", "month"],
                        help="Date posted filter (default: week)")
    parser.add_argument("--raw", action="store_true", help="Print raw JSON response")
    args = parser.parse_args()

    print(f"Querying JSearch: \"{args.query}\"")
    data = search_jobs(args.query, remote_only=args.remote, num_pages=args.num_pages, date_posted=args.date_posted)

    if args.raw:
        print(json.dumps(data, indent=2))
    else:
        print_results(data)


if __name__ == "__main__":
    main()
